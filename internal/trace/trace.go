// Package trace observes HTTP body reads/writes and persists bounded raw copies
// with lifecycle events. Offline analysis consumes these files; forwarding does
// not wait for a complete body to be collected. See docs/notes/trace-format.md.
package trace

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"hash"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"
)

// Body describes the bytes observed by Suez, which can be partial on disconnect.
type Body struct {
	Path         string `json:"path"`
	Observed     int64  `json:"observed_bytes"`
	Stored       int64  `json:"stored_bytes"`
	SHA256       string `json:"sha256_observed"`
	StoredSHA256 string `json:"sha256_stored"`
	// Complete describes body consumption or handler completion, not storage
	// fidelity. A complete exchange may still have a truncated or failed copy.
	Complete     bool `json:"complete"`
	Truncated    bool `json:"truncated"`
	CaptureError bool `json:"capture_error"`
}

// Record is a request's lifecycle snapshot. Sequence orders capture acceptance;
// Session, Turn and Attempt preserve explicit caller labels rather than guesses.
// Transport completion is separate from HTTP success or an SSE finish event.
type Record struct {
	Version         int       `json:"schema_version"`
	ID              string    `json:"request_id"`
	Sequence        uint64    `json:"sequence"`
	Session         string    `json:"session_id,omitempty"`
	Turn            *int64    `json:"turn,omitempty"`
	Attempt         *int64    `json:"attempt,omitempty"`
	Started         time.Time `json:"started_at"`
	Method          string    `json:"method"`
	Path            string    `json:"path"`
	Status          int       `json:"http_status"`
	ContentType     string    `json:"response_content_type"`
	ContentEncoding string    `json:"response_content_encoding,omitempty"`
	State           string    `json:"state"`
	DurationMS      float64   `json:"duration_ms"`
	// FirstWriteMS measures the first nonempty downstream Write, which may
	// contain a role-only SSE event. It does not measure model TTFT.
	FirstWriteMS *float64 `json:"first_response_write_ms"`
	Request      Body     `json:"request"`
	Response     Body     `json:"response"`
}

// Event wraps a started or finished snapshot in the append-only JSONL log.
// A started record without a matching finish remains an unfinished capture.
type Event struct {
	Kind   string `json:"event"`
	Record Record `json:"record"`
}

// Store owns a new run directory. JSONL start/finish pairs allow interrupted
// captures to remain visible. A run directory cannot be reused or overwritten.
type Store struct {
	root    string
	limit   int64
	session string
	// mu serializes log appends and guards admission/shutdown state. active
	// counts admitted handlers; body files have independent per-capture locks.
	mu       sync.Mutex
	sequence uint64
	events   *os.File
	failed   error
	closing  bool
	active   sync.WaitGroup
	closed   bool
	closeErr error
}

// New creates a private run directory and event log. root must not exist;
// bodyLimit caps each stored body, not the request/response sent over HTTP.
// The caller owns the returned store and must call Close during shutdown.
func New(root string, bodyLimit int64, session string) (*Store, error) {
	if bodyLimit <= 0 || len(session) > 256 {
		return nil, fmt.Errorf("positive capture limit and session of at most 256 bytes required")
	}
	if err := os.MkdirAll(filepath.Dir(root), 0700); err != nil {
		return nil, err
	}
	if err := os.Mkdir(root, 0700); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(filepath.Join(root, "events.jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return nil, err
	}
	return &Store{root: root, limit: bodyLimit, session: session, events: file}, nil
}

// Close rejects new captures and waits for active handlers before closing the log.
func (s *Store) Close() error {
	s.mu.Lock()
	s.closing = true
	s.mu.Unlock()
	// Do not hold mu while waiting: active handlers need it to persist finishes.
	s.active.Wait()
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.closed {
		s.closeErr = s.events.Close()
		s.closed = true
	}
	return s.closeErr
}

// append persists one complete event while the caller holds s.mu. A failed
// append latches the store: later events must not follow a possibly torn line.
func (s *Store) append(event Event) error {
	if s.failed != nil {
		return s.failed
	}
	data, err := json.Marshal(event)
	if err != nil {
		return err
	}
	_, err = s.events.Write(append(data, '\n'))
	if err == nil {
		err = s.events.Sync()
	}
	s.failed = err
	return err
}

// ordinal preserves a missing label as nil; a supplied zero is a valid ordinal.
func ordinal(r *http.Request, header string) (*int64, error) {
	value := r.Header.Get(header)
	if value == "" {
		return nil, nil
	}
	n, err := strconv.ParseInt(value, 10, 64)
	if err != nil || n < 0 {
		return nil, fmt.Errorf("invalid capture ordinal")
	}
	return &n, nil
}

// begin prepares sidecars and persists a start before admitting forwarding.
// A failure may leave orphan sidecars, but never an accepted unrecorded request.
func (s *Store) begin(r *http.Request) (Record, *capture, *capture, error) {
	turn, err := ordinal(r, "X-Suez-Turn")
	if err != nil {
		return Record{}, nil, nil, err
	}
	attempt, err := ordinal(r, "X-Suez-Attempt")
	if err != nil {
		return Record{}, nil, nil, err
	}
	session := r.Header.Get("X-Suez-Session")
	if session == "" {
		session = s.session
	}
	if len(session) > 256 {
		return Record{}, nil, nil, fmt.Errorf("capture session too long")
	}
	id := make([]byte, 16)
	if _, err := rand.Read(id); err != nil {
		return Record{}, nil, nil, err
	}
	record := Record{Version: 1, ID: hex.EncodeToString(id), Session: session, Turn: turn, Attempt: attempt,
		Started: time.Now().UTC(), Method: r.Method, Path: r.URL.Path, State: "in_progress"}
	if err := os.Mkdir(filepath.Join(s.root, record.ID), 0700); err != nil {
		return record, nil, nil, err
	}
	request, err := newCapture(s.root, record.ID+"/request.body", s.limit)
	if err != nil {
		return record, nil, nil, err
	}
	response, err := newCapture(s.root, record.ID+"/response.body", s.limit)
	if err != nil {
		request.finish(false)
		return record, nil, nil, err
	}
	record.Request.Path, record.Response.Path = request.path, response.path
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closing {
		request.finish(false)
		response.finish(false)
		return record, nil, nil, fmt.Errorf("capture store closed")
	}
	s.sequence++
	record.Sequence = s.sequence
	if err := s.append(Event{"started", record}); err != nil {
		request.finish(false)
		response.finish(false)
		return record, nil, nil, err
	}
	// Admission and the shutdown check share mu, so Close cannot start waiting
	// on an empty WaitGroup while a new handler is about to join it.
	s.active.Add(1)
	return record, request, response, nil
}

// Wrap captures supported completion, Messages and token-count routes. Headers and query strings
// are not persisted. Body content can still be sensitive and is never sanitized.
func (s *Store) Wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" || (r.URL.Path != "/v1/chat/completions" &&
			r.URL.Path != "/v1/messages" && r.URL.Path != "/v1/messages/count_tokens") {
			next.ServeHTTP(w, r)
			return
		}
		started := time.Now()
		record, request, response, err := s.begin(r)
		if err != nil {
			// Reject before forwarding when a durable start is unavailable.
			// Midstream copy failures instead preserve HTTP flow and flag the trace.
			log.Print("trace: could not begin capture; request not forwarded")
			http.Error(w, "capture could not start", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("X-Suez-Request-ID", record.ID)
		body := &observedBody{ReadCloser: r.Body, capture: request}
		r.Body = body
		writer := &observedWriter{ResponseWriter: w, capture: response, start: started}
		defer func() {
			defer s.active.Done()
			// Record even when ReverseProxy aborts an interrupted stream. Re-panic
			// afterward so net/http still terminates the connection as intended.
			failure := recover()
			body.Close()
			record.State = "completed"
			if failure != nil {
				record.State = "aborted"
			}
			writer.mu.Lock()
			if writer.failed {
				record.State = "downstream_error"
			}
			if r.Context().Err() != nil {
				record.State = "cancelled"
			}
			record.Status = writer.status
			if record.Status == 0 && failure == nil {
				record.Status = http.StatusOK
			}
			record.ContentType = w.Header().Get("Content-Type")
			record.ContentEncoding = w.Header().Get("Content-Encoding")
			record.DurationMS = float64(time.Since(started)) / float64(time.Millisecond)
			record.FirstWriteMS = writer.first
			writer.mu.Unlock()
			// Finalize sidecars before the finish event claims their hashes and
			// lengths. Without a finish, recovery treats the start as unfinished.
			record.Request = request.finish(request.complete(r.ContentLength))
			record.Response = response.finish(record.State == "completed")
			s.mu.Lock()
			err := s.append(Event{"finished", record})
			s.mu.Unlock()
			if err != nil || record.Request.CaptureError || record.Response.CaptureError {
				log.Printf("trace: capture write failed for request %s", record.ID)
			}
			if failure != nil {
				panic(failure)
			}
		}()
		next.ServeHTTP(writer, r)
	})
}

// capture owns one bounded sidecar. Reads can arrive from the transport's body
// goroutine, so observation and finalization share a lock. Storage errors are
// metadata, not errors returned to the HTTP reader/writer.
type capture struct {
	mu         sync.Mutex
	file       *os.File
	path       string
	limit      int64
	hash       hash.Hash
	storedHash hash.Hash
	info       Body
	eof        bool
	closed     bool
}

func newCapture(root, path string, limit int64) (*capture, error) {
	file, err := os.OpenFile(filepath.Join(root, path), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return nil, err
	}
	return &capture{file: file, path: path, limit: limit, hash: sha256.New(), storedHash: sha256.New(), info: Body{Path: path}}, nil
}

func (c *capture) observe(data []byte, eof bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		// A late request-body read may race with handler finalization after an
		// early upstream response. Keep the committed snapshot immutable.
		return
	}
	c.eof = c.eof || eof
	// Hash all observed bytes before limiting storage so a capped sidecar still
	// identifies the full observed payload. Stored bytes have a separate hash.
	c.info.Observed += int64(len(data))
	c.hash.Write(data)
	remaining := c.limit - c.info.Stored
	if int64(len(data)) > remaining {
		data = data[:remaining]
	}
	if len(data) > 0 && !c.info.CaptureError {
		n, err := c.file.Write(data)
		c.storedHash.Write(data[:n])
		c.info.Stored += int64(n)
		if err != nil || n != len(data) {
			c.info.CaptureError = true
		}
	}
}

// complete accepts EOF or the full declared length. Transport reads do not
// necessarily make a final Read returning EOF for a known-length request.
func (c *capture) complete(expected int64) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.eof || (expected >= 0 && c.info.Observed == expected)
}

// finish freezes observations, syncs/closes the file, and returns its descriptor.
// complete comes from HTTP lifecycle evidence, independently of storage errors.
func (c *capture) finish(complete bool) Body {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.closed = true
	if err := c.file.Sync(); err != nil {
		c.info.CaptureError = true
	}
	if err := c.file.Close(); err != nil {
		c.info.CaptureError = true
	}
	c.info.SHA256 = hex.EncodeToString(c.hash.Sum(nil))
	c.info.StoredSHA256 = hex.EncodeToString(c.storedHash.Sum(nil))
	c.info.Complete = complete
	c.info.Truncated = c.info.Stored < c.info.Observed
	return c.info
}

// observedBody captures only bytes actually consumed from the incoming body.
// It delegates Close so cancellation can unblock the transport's reader.
type observedBody struct {
	io.ReadCloser
	capture *capture
}

func (b *observedBody) Read(data []byte) (int, error) {
	n, err := b.ReadCloser.Read(data)
	b.capture.observe(data[:n], err == io.EOF)
	return n, err
}

// observedWriter measures writes and preserves ResponseController access to the
// underlying writer. Its lock also covers flushes from the proxy's flush timer.
type observedWriter struct {
	http.ResponseWriter
	mu      sync.Mutex
	capture *capture
	start   time.Time
	status  int
	first   *float64
	failed  bool
}

func (w *observedWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }
func (w *observedWriter) WriteHeader(status int) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.writeHeader(status)
}
func (w *observedWriter) writeHeader(status int) {
	if status >= 200 && w.status == 0 {
		w.status = status
	}
	w.ResponseWriter.WriteHeader(status)
}
func (w *observedWriter) Write(data []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.status == 0 {
		w.writeHeader(http.StatusOK)
	}
	n, err := w.ResponseWriter.Write(data)
	if n > 0 {
		if w.first == nil {
			value := float64(time.Since(w.start)) / float64(time.Millisecond)
			w.first = &value
		}
		// A partial write proves acceptance of only this prefix, not all data
		// supplied by the upstream copy loop.
		w.capture.observe(data[:n], false)
	}
	if err != nil || n != len(data) {
		w.failed = true
	}
	return n, err
}

// FlushError preserves immediate SSE delivery through the capture wrapper and
// exposes failed flushes to both ResponseController and the lifecycle record.
func (w *observedWriter) FlushError() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.status == 0 {
		w.writeHeader(http.StatusOK)
	}
	err := http.NewResponseController(w.ResponseWriter).Flush()
	if err != nil {
		w.failed = true
	}
	return err
}
