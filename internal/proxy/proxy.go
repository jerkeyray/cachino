// Package proxy forwards model API requests without interpreting their bodies.
package proxy

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

// New returns a handler for POST chat completions, Messages and token counting.
// upstream must be an HTTP(S) origin such as http://127.0.0.1:8081, not an API
// base path: SetURL joins paths, so accepting /v1 here would duplicate the route.
func New(upstream string, options ...Option) (http.Handler, error) {
	target, err := url.Parse(upstream)
	if err != nil || target.Hostname() == "" ||
		(target.Scheme != "http" && target.Scheme != "https") ||
		target.User != nil || (target.Path != "" && target.Path != "/") ||
		target.RawQuery != "" || target.ForceQuery || target.Fragment != "" {
		return nil, fmt.Errorf("upstream must be an HTTP(S) origin without credentials, path, query or fragment")
	}
	forward := &httputil.ReverseProxy{
		// ReverseProxy flushes text/event-stream responses immediately. Keep
		// the response body streaming; do not collect or parse SSE here.
		Rewrite: func(r *httputil.ProxyRequest) {
			r.SetURL(target)
			// Local trace labels are not model API parameters or credentials.
			for _, header := range []string{"X-Cachino-Session", "X-Cachino-Turn", "X-Cachino-Attempt"} {
				r.Out.Header.Del(header)
			}
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			// Keep transport details and potential credentials out of the response.
			// This handles failures before a response; midstream read failures
			// instead abort the connection because its status is already committed.
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadGateway)
			_, _ = w.Write([]byte(`{"error":{"message":"upstream request failed","type":"proxy_error"}}`))
		},
	}
	for _, option := range options {
		if err := option(forward); err != nil {
			return nil, err
		}
	}
	mux := http.NewServeMux()
	mux.Handle("POST /v1/chat/completions", forward)
	mux.Handle("POST /v1/messages", forward)
	mux.Handle("POST /v1/messages/count_tokens", forward)
	return mux, nil
}

// Option configures the reverse proxy before its handler begins serving.
type Option func(*httputil.ReverseProxy) error

// WithTransport permits controlled local transports without changing globals.
func WithTransport(transport http.RoundTripper) Option {
	return func(p *httputil.ReverseProxy) error { p.Transport = transport; return nil }
}

// WithMessagesPrefix adds an explicit gateway path only to Messages routes.
// For example, /anthropic routes Messages to DeepSeek's compatible endpoint
// while chat completions still use the origin. It never rewrites request bodies.
func WithMessagesPrefix(prefix string) Option {
	return func(p *httputil.ReverseProxy) error {
		if prefix != "" {
			if !strings.HasPrefix(prefix, "/") {
				return fmt.Errorf("messages prefix must be an absolute path")
			}
			for _, segment := range strings.Split(prefix[1:], "/") {
				if segment == "" || segment == "." || segment == ".." || strings.ContainsAny(segment, "?#%\\\\") {
					return fmt.Errorf("messages prefix must contain plain nonempty path segments")
				}
			}
		}
		previous := p.Rewrite
		p.Rewrite = func(r *httputil.ProxyRequest) {
			previous(r)
			if r.In.URL.Path == "/v1/messages" || r.In.URL.Path == "/v1/messages/count_tokens" {
				r.Out.URL.Path = prefix + r.In.URL.Path
				r.Out.URL.RawPath = ""
			}
		}
		return nil
	}
}
