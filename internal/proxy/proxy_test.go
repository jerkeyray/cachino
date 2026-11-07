package proxy_test

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jerkeyray/cachino/internal/fakemodel"
	"github.com/jerkeyray/cachino/internal/proxy"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// Tests are intentionally sequential: ReverseProxy uses http.DefaultTransport
// when no explicit transport is set. Substitute a handler-backed transport so
// the same forwarding path can be checked in environments without TCP access.
func withTransport(t *testing.T, transport http.RoundTripper) {
	t.Helper()
	previous := http.DefaultTransport
	http.DefaultTransport = transport
	t.Cleanup(func() { http.DefaultTransport = previous })
}

func handlerTransport(handler http.Handler) http.RoundTripper {
	return roundTripFunc(func(r *http.Request) (*http.Response, error) {
		// The real HTTP transport serializes an empty Host from URL.Host.
		r = r.Clone(r.Context())
		if r.Host == "" {
			r.Host = r.URL.Host
		}
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, r)
		return response.Result(), nil
	})
}

func forwarder(t *testing.T) http.Handler {
	t.Helper()
	handler, err := proxy.New("http://upstream.test")
	if err != nil {
		t.Fatal(err)
	}
	return handler
}

func serve(handler http.Handler, method, path, body string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, strings.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Authorization", "Bearer fixture-key")
	request.Header.Set("X-Request-Id", "fixture-request")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

func TestHandlerDirectAndProxiedAgree(t *testing.T) {
	upstream := fakemodel.New()
	withTransport(t, handlerTransport(upstream))
	handler := forwarder(t)
	for _, body := range []string{
		`{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"stream":false}`,
		`{"model":"fake-model","messages":[{}],"stream":true}`,
		`not json`,
	} {
		direct := serve(upstream, "POST", "/v1/chat/completions", body)
		proxied := serve(handler, "POST", "/v1/chat/completions", body)
		if direct.Code != proxied.Code || direct.Body.String() != proxied.Body.String() || direct.Header().Get("Content-Type") != proxied.Header().Get("Content-Type") {
			t.Fatalf("direct/proxy mismatch: direct=%d %s, proxy=%d %s", direct.Code, direct.Body, proxied.Code, proxied.Body)
		}
	}
}

func TestHandlerPreservesBytesHeadersAndUpstreamError(t *testing.T) {
	body := "{\n \"model\":\"fake-model\", \"messages\":[{\"content\":\"héllo 世界\"}], \"extra\":1\n}\n"
	calls := 0
	withTransport(t, handlerTransport(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		data, err := io.ReadAll(r.Body)
		if err != nil || string(data) != body || r.Method != "POST" || r.URL.Path != "/v1/chat/completions" ||
			r.URL.RawQuery != "x=a%2Fb&x=c" || r.Host != "upstream.test" ||
			r.Header.Get("Authorization") != "Bearer fixture-key" || r.Header.Get("Content-Type") != "application/json" ||
			r.Header.Get("X-Request-Id") != "fixture-request" {
			t.Errorf("request changed: %s %s host=%s headers=%v body=%q err=%v", r.Method, r.URL, r.Host, r.Header, data, err)
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Retry-After", "7")
		w.WriteHeader(429)
		_, _ = w.Write([]byte("{\n  \"error\": \"busy\"\n}\n"))
	})))
	response := serve(forwarder(t), "POST", "/v1/chat/completions?x=a%2Fb&x=c", body)
	if calls != 1 || response.Code != 429 || response.Header().Get("Retry-After") != "7" ||
		response.Header().Get("Content-Type") != "application/json" || response.Body.String() != "{\n  \"error\": \"busy\"\n}\n" {
		t.Fatalf("upstream error changed: calls=%d, response=%d %v %q", calls, response.Code, response.Header(), response.Body.String())
	}
}

func TestHandlerTransportFailure(t *testing.T) {
	withTransport(t, roundTripFunc(func(r *http.Request) (*http.Response, error) {
		return nil, errors.New("fixture transport failure with private details")
	}))
	response := serve(forwarder(t), "POST", "/v1/chat/completions", `{}`)
	if response.Code != 502 || response.Header().Get("Content-Type") != "application/json" ||
		response.Body.String() != `{"error":{"message":"upstream request failed","type":"proxy_error"}}` {
		t.Fatalf("unexpected transport failure: %d %s", response.Code, response.Body)
	}
}

func TestHandlerRoutes(t *testing.T) {
	withTransport(t, roundTripFunc(func(r *http.Request) (*http.Response, error) {
		t.Error("unsupported request reached upstream")
		return nil, errors.New("unexpected request")
	}))
	handler := forwarder(t)
	for _, tc := range []struct {
		method, path string
		status       int
	}{
		{"GET", "/v1/chat/completions", 405},
		{"POST", "/other", 404},
		{"POST", "/v1/chat/completions/extra", 404},
	} {
		if response := serve(handler, tc.method, tc.path, `{}`); response.Code != tc.status {
			t.Errorf("%s %s: got %d, want %d", tc.method, tc.path, response.Code, tc.status)
		}
	}
}

func TestOriginValidation(t *testing.T) {
	for _, origin := range []string{"", "://", "localhost:8081", "ftp://localhost", "http:///", "http://user:secret@localhost", "http://localhost/v1", "http://localhost?x=1", "http://localhost?", "http://localhost/#fragment"} {
		if _, err := proxy.New(origin); err == nil {
			t.Errorf("accepted invalid origin %q", origin)
		}
	}
	for _, origin := range []string{"http://localhost:8081", "http://localhost:8081/", "https://example.com"} {
		if _, err := proxy.New(origin); err != nil {
			t.Errorf("rejected valid origin %q: %v", origin, err)
		}
	}
}

func TestMessagesPrefixPreservesProtocol(t *testing.T) {
	const body = "{\n \"model\": \"deepseek-flash\", \"messages\": []\n}"
	const stream = "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
	for _, path := range []string{"/v1/messages", "/v1/messages/count_tokens", "/v1/chat/completions"} {
		t.Run(path, func(t *testing.T) {
			transport := handlerTransport(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				want := path
				if path != "/v1/chat/completions" {
					want = "/anthropic" + path
				}
				data, _ := io.ReadAll(r.Body)
				if r.URL.Path != want || r.URL.RawQuery != "beta=true" || string(data) != body {
					t.Errorf("route or bytes changed: %s %q", r.URL, data)
				}
				for key, value := range map[string]string{"X-Api-Key": "fixture-key", "Anthropic-Version": "2023-06-01", "Anthropic-Beta": "fixture-beta"} {
					if r.Header.Get(key) != value {
						t.Errorf("missing protocol header %s", key)
					}
				}
				if r.Header.Get("X-Suez-Session") != "" {
					t.Error("local label reached upstream")
				}
				w.Header().Set("Content-Type", "text/event-stream")
				io.WriteString(w, stream)
			}))
			handler, err := proxy.New("https://upstream.test", proxy.WithMessagesPrefix("/anthropic"), proxy.WithTransport(transport))
			if err != nil {
				t.Fatal(err)
			}
			request := httptest.NewRequest("POST", path+"?beta=true", strings.NewReader(body))
			request.Header.Set("X-Api-Key", "fixture-key")
			request.Header.Set("Anthropic-Version", "2023-06-01")
			request.Header.Set("Anthropic-Beta", "fixture-beta")
			request.Header.Set("X-Suez-Session", "local")
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, request)
			if response.Code != 200 || response.Body.String() != stream || !response.Flushed {
				t.Fatalf("stream altered or unflushed: %d %q", response.Code, response.Body)
			}
		})
	}
}

func TestMessagesPrefixValidation(t *testing.T) {
	for _, prefix := range []string{"anthropic", "/anthropic/", "/a//b", "/a/../b", "/a?key=x", "/a%2Fb", "/a#fragment", "/a\\b"} {
		if _, err := proxy.New("https://upstream.test", proxy.WithMessagesPrefix(prefix)); err == nil {
			t.Errorf("accepted invalid prefix %q", prefix)
		}
	}
}
