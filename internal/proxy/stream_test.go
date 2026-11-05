package proxy_test

import (
	"bufio"
	"context"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"
)

// net.Pipe carries real HTTP bytes without binding a TCP port. Unlike the
// recorder transport, it exposes flushing, truncation and disconnect behavior.
type pipeListener struct {
	connections chan net.Conn
	done        chan struct{}
	once        sync.Once
}

func (l *pipeListener) Accept() (net.Conn, error) {
	select {
	case connection := <-l.connections:
		return connection, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}
func (l *pipeListener) Close() error {
	l.once.Do(func() { close(l.done) })
	return nil
}
func (l *pipeListener) Addr() net.Addr { return &net.TCPAddr{} }

func pipeHTTP(t *testing.T, handler http.Handler) *http.Client {
	t.Helper()
	listener := &pipeListener{connections: make(chan net.Conn), done: make(chan struct{})}
	server := &http.Server{Handler: handler, ErrorLog: log.New(io.Discard, "", 0)}
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		_ = server.Serve(listener)
	}()
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		client, upstream := net.Pipe()
		select {
		case listener.connections <- upstream:
			return client, nil
		case <-ctx.Done():
			client.Close()
			upstream.Close()
			return nil, ctx.Err()
		case <-listener.done:
			client.Close()
			upstream.Close()
			return nil, net.ErrClosed
		}
	}}
	t.Cleanup(func() {
		transport.CloseIdleConnections()
		listener.Close()
		server.Close()
		select {
		case <-stopped:
		case <-time.After(3 * time.Second):
			t.Error("pipe HTTP server did not stop")
		}
	})
	return &http.Client{Transport: transport, Timeout: 3 * time.Second}
}

func openStream(t *testing.T, upstream http.Handler) (*http.Response, context.CancelFunc) {
	t.Helper()
	backend := pipeHTTP(t, upstream)
	withTransport(t, backend.Transport)
	client := pipeHTTP(t, forwarder(t))
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	request, err := http.NewRequestWithContext(ctx, "POST", "http://suez.test/v1/chat/completions", strings.NewReader(`{"model":"fake-model","messages":[{}],"stream":true}`))
	if err != nil {
		t.Fatal(err)
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { response.Body.Close() })
	if response.StatusCode != 200 || response.Header.Get("Content-Type") != "text/event-stream" {
		t.Fatalf("expected SSE response: %d %v", response.StatusCode, response.Header)
	}
	return response, cancel
}

const firstEvent = "data: {\"choices\":[{\"delta\":{\"content\":\"first\"}}]}\n\n"

func readFirstEvent(t *testing.T, response *http.Response) *bufio.Reader {
	t.Helper()
	reader := bufio.NewReader(response.Body)
	line, err := reader.ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	blank, err := reader.ReadString('\n')
	if err != nil || line+blank != firstEvent {
		t.Fatalf("first event changed: %q %q, err=%v", line, blank, err)
	}
	return reader
}

func TestStreamFirstEventBeforeUpstreamCompletion(t *testing.T) {
	release := make(chan struct{})
	var once sync.Once
	unblock := func() { once.Do(func() { close(release) }) }
	defer unblock()
	completed := make(chan struct{})
	response, _ := openStream(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer close(completed)
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, firstEvent)
		http.NewResponseController(w).Flush()
		select {
		case <-release:
			io.WriteString(w, "data: [DONE]\n\n")
		case <-r.Context().Done():
		}
	}))
	reader := readFirstEvent(t, response)
	select {
	case <-completed:
		t.Fatal("upstream completed before client received the first event")
	default:
	}
	unblock()
	rest, err := io.ReadAll(reader)
	if err != nil || string(rest) != "data: [DONE]\n\n" {
		t.Fatalf("stream completion changed: %q, err=%v", rest, err)
	}
}

func TestStreamClientDisconnectCancelsUpstream(t *testing.T) {
	for _, mode := range []string{"cancel-context", "close-body"} {
		t.Run(mode, func(t *testing.T) {
			cancelled := make(chan struct{})
			response, cancel := openStream(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "text/event-stream")
				io.WriteString(w, firstEvent)
				http.NewResponseController(w).Flush()
				<-r.Context().Done()
				close(cancelled)
			}))
			readFirstEvent(t, response)
			if mode == "cancel-context" {
				cancel()
			} else {
				response.Body.Close()
			}
			select {
			case <-cancelled:
			case <-time.After(3 * time.Second):
				t.Fatal("client disconnect did not cancel the upstream request")
			}
		})
	}
}

func TestStreamErrorsAfterHeaders(t *testing.T) {
	for _, mode := range []string{"in-band-error", "truncated-connection"} {
		t.Run(mode, func(t *testing.T) {
			release := make(chan struct{})
			var once sync.Once
			unblock := func() { once.Do(func() { close(release) }) }
			defer unblock()
			const errorEvent = "data: {\"error\":{\"message\":\"fixture error\"}}\n\n"
			response, _ := openStream(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "text/event-stream")
				io.WriteString(w, firstEvent)
				http.NewResponseController(w).Flush()
				select {
				case <-release:
				case <-r.Context().Done():
					return
				}
				if mode == "truncated-connection" {
					panic(http.ErrAbortHandler)
				}
				io.WriteString(w, errorEvent)
			}))
			reader := readFirstEvent(t, response)
			unblock()
			rest, err := io.ReadAll(reader)
			if mode == "truncated-connection" {
				if err == nil || len(rest) != 0 {
					t.Fatalf("expected interrupted body without synthetic completion: %q, err=%v", rest, err)
				}
			} else if err != nil || string(rest) != errorEvent {
				t.Fatalf("in-band error changed: %q, err=%v", rest, err)
			}
		})
	}
}
