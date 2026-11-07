package trace

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/jerkeyray/cachino/internal/localhttp"
	"github.com/jerkeyray/cachino/internal/proxy"
)

func storeFor(t *testing.T, limit int64) *Store {
	t.Helper()
	store, err := New(filepath.Join(t.TempDir(), "run"), limit, "test-session")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

func TestMessagesAndTokenCountingCaptured(t *testing.T) {
	store := storeFor(t, 1024)
	handler := store.Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Content-Encoding", "identity")
		io.WriteString(w, `{"input_tokens":9}`)
	}))
	for _, path := range []string{"/v1/messages", "/v1/messages/count_tokens"} {
		handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", path, strings.NewReader(`{"model":"fixture"}`)))
	}
	events := records(t, store)
	if len(events) != 4 || events[1].Record.Path != "/v1/messages" || events[3].Record.Path != "/v1/messages/count_tokens" {
		t.Fatalf("missing protocol captures: %+v", events)
	}
	for _, index := range []int{1, 3} {
		if events[index].Record.ContentEncoding != "identity" {
			t.Fatal("response encoding metadata missing")
		}
		if events[index].Record.State != "completed" || !events[index].Record.Request.Complete || !events[index].Record.Response.Complete {
			t.Fatalf("incomplete capture: %+v", events[index])
		}
	}
}

func records(t *testing.T, store *Store) []Event {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(store.root, "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var result []Event
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		var event Event
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			t.Fatal(err)
		}
		result = append(result, event)
	}
	return result
}

func TestCaptureBytesMetadataAndPermissions(t *testing.T) {
	store := storeFor(t, 1024)
	body := "{\n \"model\":\"fake\", \"content\":\"héllo 世界\"\n}\n"
	handler := store.Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, err := io.ReadAll(r.Body)
		if err != nil || string(data) != body {
			t.Errorf("changed body: %q %v", data, err)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(201)
		io.WriteString(w, "{\n \"ok\":true\n}\n")
	}))
	request := httptest.NewRequest("POST", "/v1/chat/completions?secret=must-not-persist", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer must-not-persist")
	request.Header.Set("X-Suez-Turn", "3")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	events := records(t, store)
	if len(events) != 2 || events[0].Kind != "started" || events[0].Record.State != "in_progress" || events[1].Kind != "finished" {
		t.Fatalf("invalid lifecycle: %+v", events)
	}
	record := events[1].Record
	if record.Status != 201 || record.State != "completed" || record.Sequence != 1 || record.Session != "test-session" || record.Turn == nil || *record.Turn != 3 || record.FirstWriteMS == nil {
		t.Fatalf("invalid metadata: %+v", record)
	}
	if record.ID != response.Header().Get("X-Suez-Request-ID") {
		t.Fatal("missing request correlation")
	}
	for _, item := range []struct {
		info Body
		want string
	}{{record.Request, body}, {record.Response, response.Body.String()}} {
		data, err := os.ReadFile(filepath.Join(store.root, item.info.Path))
		if err != nil || string(data) != item.want || !item.info.Complete || item.info.Truncated || item.info.CaptureError {
			t.Fatalf("invalid sidecar: %+v %q %v", item.info, data, err)
		}
		digest := sha256.Sum256(data)
		if hex.EncodeToString(digest[:]) != item.info.SHA256 {
			t.Fatal("wrong body hash")
		}
		stat, _ := os.Stat(filepath.Join(store.root, item.info.Path))
		if stat.Mode().Perm() != 0600 {
			t.Fatalf("unsafe permissions: %v", stat.Mode())
		}
	}
	data, _ := os.ReadFile(filepath.Join(store.root, "events.jsonl"))
	if strings.Contains(string(data), "must-not-persist") {
		t.Fatal("headers or query leaked into metadata")
	}
}

func TestLimitsAndWriteFailureDoNotChangeForwarding(t *testing.T) {
	store := storeFor(t, 4)
	response := httptest.NewRecorder()
	store.Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		io.WriteString(w, "long response")
	})).ServeHTTP(response, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader("long request")))
	record := records(t, store)[1].Record
	if response.Body.String() != "long response" || !record.Request.Truncated || !record.Response.Truncated || record.Response.Stored != 4 || record.Response.Observed != 13 {
		t.Fatalf("limit altered flow or metadata: %+v %s", record, response.Body)
	}
	capture, err := newCapture(t.TempDir(), "body", 10)
	if err != nil {
		t.Fatal(err)
	}
	capture.file.Close() // deterministic storage failure, not an HTTP error
	capture.observe([]byte("keep flowing"), false)
	info := capture.finish(true)
	if !info.CaptureError || info.Observed != 12 || info.Stored != 0 {
		t.Fatalf("write failure hidden: %+v", info)
	}
}

func TestConcurrentAcceptanceAndStartFailure(t *testing.T) {
	store := storeFor(t, 1024)
	handler := store.Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.Copy(w, r.Body) }))
	var group sync.WaitGroup
	for range 12 {
		group.Add(1)
		go func() {
			defer group.Done()
			handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader("body")))
		}()
	}
	group.Wait()
	events := records(t, store)
	seen := map[uint64]bool{}
	finished := 0
	for _, event := range events {
		if event.Kind == "started" {
			if seen[event.Record.Sequence] {
				t.Fatal("duplicate sequence")
			}
			seen[event.Record.Sequence] = true
		} else {
			finished++
		}
	}
	if len(seen) != 12 || finished != 12 {
		t.Fatalf("lost concurrent records: starts=%d finishes=%d", len(seen), finished)
	}
	store.events.Close()
	called := false
	response := httptest.NewRecorder()
	store.Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { called = true })).ServeHTTP(response, httptest.NewRequest("POST", "/v1/chat/completions", nil))
	if called || response.Code != 503 {
		t.Fatal("forwarded without a durable start record")
	}
}

func TestMidstreamStorageFailureKeepsHTTPFlowing(t *testing.T) {
	store := storeFor(t, 1024)
	response := httptest.NewRecorder()
	store.Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		r.Body.(*observedBody).capture.file.Close()
		w.(*observedWriter).capture.file.Close()
		io.Copy(w, r.Body)
	})).ServeHTTP(response, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader("still forwarded")))
	record := records(t, store)[1].Record
	if response.Code != 200 || response.Body.String() != "still forwarded" || !record.Request.CaptureError || !record.Response.CaptureError {
		t.Fatalf("storage failure changed HTTP or was hidden: %+v %s", record, response.Body)
	}
}

func TestCancelledCaptureIsExplicit(t *testing.T) {
	store := storeFor(t, 1024)
	backend := localhttp.New(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: first\n\n")
		http.NewResponseController(w).Flush()
		<-r.Context().Done()
	}))
	defer backend.Close()
	handler, err := proxy.New("http://backend.test", proxy.WithTransport(backend.Transport))
	if err != nil {
		t.Fatal(err)
	}
	endpoint := localhttp.New(store.Wrap(handler))
	defer endpoint.Close()
	request, _ := http.NewRequest("POST", "http://cachino.test/v1/chat/completions", strings.NewReader("body"))
	response, err := endpoint.Client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := bufio.NewReader(response.Body).ReadString('\n'); err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	record := records(t, store)[1].Record
	if record.State != "cancelled" || record.Response.Complete {
		t.Fatalf("cancellation hidden: %+v", record)
	}
}

func TestCaptureSSEDoesNotBufferAndRecordsAbort(t *testing.T) {
	for _, abort := range []bool{false, true} {
		t.Run(map[bool]string{false: "complete", true: "abort"}[abort], func(t *testing.T) {
			store := storeFor(t, 4096)
			release := make(chan struct{})
			var once sync.Once
			unblock := func() { once.Do(func() { close(release) }) }
			defer unblock()
			backend := localhttp.New(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("X-Suez-Session") != "" || r.Header.Get("X-Suez-Turn") != "" {
					t.Error("trace labels forwarded to model")
				}
				io.Copy(io.Discard, r.Body)
				w.Header().Set("Content-Type", "text/event-stream")
				io.WriteString(w, "data: first\n\n")
				http.NewResponseController(w).Flush()
				select {
				case <-release:
				case <-r.Context().Done():
					return
				}
				if abort {
					panic(http.ErrAbortHandler)
				}
				io.WriteString(w, "data: [DONE]\n\n")
			}))
			defer backend.Close()
			handler, err := proxy.New("http://backend.test", proxy.WithTransport(backend.Transport))
			if err != nil {
				t.Fatal(err)
			}
			endpoint := localhttp.New(store.Wrap(handler))
			defer endpoint.Close()
			request, _ := http.NewRequest("POST", "http://cachino.test/v1/chat/completions", strings.NewReader("body"))
			request.Header.Set("X-Suez-Session", "caller-session")
			request.Header.Set("X-Suez-Turn", "0")
			response, err := endpoint.Client.Do(request)
			if err != nil {
				t.Fatal(err)
			}
			defer response.Body.Close()
			reader := bufio.NewReader(response.Body)
			line, err := reader.ReadString('\n')
			if err != nil || line != "data: first\n" {
				t.Fatalf("capture buffered first event: %q %v", line, err)
			}
			if len(records(t, store)) != 1 {
				t.Fatal("capture completed while upstream blocked")
			}
			unblock()
			rest, readErr := io.ReadAll(reader)
			if abort && readErr == nil {
				t.Fatal("truncated stream appeared successful")
			}
			if !abort && (readErr != nil || string(rest) != "\ndata: [DONE]\n\n") {
				t.Fatalf("stream changed: %q %v", rest, readErr)
			}
			// Wait for final persistence before inspecting; Close waits for active handlers.
			if err := store.Close(); err != nil {
				t.Fatal(err)
			}
			record := records(t, store)[1].Record
			if record.Session != "caller-session" || (record.State == "completed") == abort || record.Response.Complete == abort {
				t.Fatalf("wrong stream outcome: %+v", record)
			}
		})
	}
}
