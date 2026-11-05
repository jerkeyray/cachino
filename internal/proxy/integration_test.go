//go:build integration

package proxy_test

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/jerkeyray/suez/internal/fakemodel"
	"github.com/jerkeyray/suez/internal/proxy"
)

// This complements the in-memory tests with actual HTTP transport over TCP.
func TestTCPDirectAndProxiedAgree(t *testing.T) {
	upstream := httptest.NewServer(fakemodel.New())
	defer upstream.Close()
	handler, err := proxy.New(upstream.URL)
	if err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(handler)
	defer server.Close()
	client := &http.Client{Timeout: 3 * time.Second}
	post := func(origin, body string) (int, string, string) {
		t.Helper()
		response, err := client.Post(origin+"/v1/chat/completions", "application/json", strings.NewReader(body))
		if err != nil {
			t.Fatal(err)
		}
		defer response.Body.Close()
		data, err := io.ReadAll(response.Body)
		if err != nil {
			t.Fatal(err)
		}
		return response.StatusCode, response.Header.Get("Content-Type"), string(data)
	}
	for _, body := range []string{
		`{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"stream":false}`,
		`{"model":"fake-model","messages":[{}],"stream":true}`,
		`not json`,
	} {
		directStatus, directType, directBody := post(upstream.URL, body)
		status, contentType, result := post(server.URL, body)
		if status != directStatus || result != directBody || contentType != directType {
			t.Fatalf("direct/proxy mismatch: direct=(%d,%s,%s), proxy=(%d,%s,%s)",
				directStatus, directType, directBody, status, contentType, result)
		}
	}
}
