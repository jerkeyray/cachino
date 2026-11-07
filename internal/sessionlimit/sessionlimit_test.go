package sessionlimit

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

const fixture = "{\n \"model\":\"deepseek-flash\", \"max_tokens\":512, \"messages\":[]\n}"

func TestAdmissionPreservesBytesAndCountsFailures(t *testing.T) {
	calls := 0
	h, err := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		data, _ := io.ReadAll(r.Body)
		if string(data) != fixture {
			t.Error("request bytes changed")
		}
		w.WriteHeader(500) // A failed provider request still consumes admission.
	}), Limits{Requests: 2, Bytes: 1024, Output: 512, Model: "deepseek-flash"})
	if err != nil {
		t.Fatal(err)
	}
	for i, want := range []int{500, 500, 403} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/v1/messages", strings.NewReader(fixture)))
		if w.Code != want {
			t.Fatalf("request %d: %d", i, w.Code)
		}
	}
	if calls != 2 {
		t.Fatal(calls)
	}
}

func TestInvalidRequestsNeverReachUpstream(t *testing.T) {
	h, _ := Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { t.Error("invalid request admitted") }), Limits{Requests: 4, Bytes: 1024, Output: 512, Model: "deepseek-flash"})
	for _, body := range []string{
		`{"model":"expensive","max_tokens":1}`, `{"model":"deepseek-flash","max_tokens":513}`,
		`{"model":"deepseek-flash","max_tokens":null}`, `{"model":"deepseek-flash","max_tokens":1.5}`,
		`{"model":"deepseek-flash","max_tokens":1,"max_tokens":9000}`, `[]`, fixture + `{}`,
		strings.Repeat("x", 1025),
	} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/v1/messages", strings.NewReader(body)))
		if w.Code < 400 {
			t.Fatal("accepted invalid body")
		}
	}
	for _, path := range []string{"/v1/chat/completions", "/other"} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", path, strings.NewReader(fixture)))
		if w.Code != 403 {
			t.Fatal(w.Code)
		}
	}
}

func TestConcurrentAdmissionAndTokenCounting(t *testing.T) {
	var calls atomic.Int32
	h, _ := Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }), Limits{Requests: 4, Bytes: 1024, Output: 512, Model: "deepseek-flash"})
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			h.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/v1/messages/count_tokens", strings.NewReader(`{"model":"deepseek-flash"}`)))
		}()
	}
	wg.Wait()
	if calls.Load() != 4 {
		t.Fatal(calls.Load())
	}
}

func TestOpenAIAdmissionPreservesAliasesBytesAndCountsFailures(t *testing.T) {
	bodies := []string{
		"{\n \"model\":\"wire-fixture\", \"max_tokens\":8, \"messages\":[], \"stream\":false\n}",
		`{"model":"wire-fixture","max_completion_tokens":8,"n":1,"messages":[],"stream":true}`,
	}
	calls := 0
	h, err := Wrap(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		data, _ := io.ReadAll(r.Body)
		if string(data) != bodies[calls] {
			t.Error("OpenAI request bytes changed")
		}
		calls++
		w.WriteHeader(http.StatusBadGateway) // Failed dispatches still consume the budget.
	}), Limits{Requests: 2, Bytes: 1024, Output: 8, Model: "wire-fixture", Protocol: ProtocolOpenAI})
	if err != nil {
		t.Fatal(err)
	}
	for i, want := range []int{502, 502, 403} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(bodies[i%2])))
		if w.Code != want {
			t.Fatalf("request %d: status %d, expected %d", i, w.Code, want)
		}
	}
	if calls != 2 {
		t.Fatal(calls)
	}
}

func TestOpenAIRejectsUnboundedOrAmbiguousGenerations(t *testing.T) {
	h, err := Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		t.Error("unbounded OpenAI request reached upstream")
	}), Limits{Requests: 2, Bytes: 256, Output: 8, Model: "wire-fixture", Protocol: ProtocolOpenAI})
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{
		`{"model":"wire-fixture"}`,
		`{"model":"wire-fixture","max_tokens":9}`,
		`{"model":"wire-fixture","max_completion_tokens":9}`,
		`{"model":"wire-fixture","max_tokens":null}`,
		`{"model":"wire-fixture","max_tokens":true}`,
		`{"model":"wire-fixture","max_tokens":"8"}`,
		`{"model":"wire-fixture","max_tokens":1.5}`,
		`{"model":"wire-fixture","max_tokens":0}`,
		`{"model":"wire-fixture","max_tokens":-1}`,
		`{"model":"wire-fixture","max_tokens":8,"max_completion_tokens":8}`,
		`{"model":"wire-fixture","max_tokens":null,"max_completion_tokens":8}`,
		`{"model":"wire-fixture","max_tokens":1,"max_tokens":8}`,
		`{"model":"wire-fixture","max_tokens":8,"n":2}`,
		`{"model":"wire-fixture","max_tokens":8,"n":null}`,
		`{"model":"wire-fixture","max_tokens":8,"n":true}`,
		`{"model":"wire-fixture","max_tokens":8,"best_of":1}`,
		`{"model":"other-model","max_tokens":8}`,
		`{"model":"wire-fixture","max_tokens":8} {}`,
		strings.Repeat("x", 257),
	} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body)))
		if w.Code < 400 {
			t.Fatalf("accepted unbounded request: %s", body)
		}
	}
	for _, path := range []string{"/v1/messages", "/v1/messages/count_tokens", "/other"} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", path, strings.NewReader(`{"model":"wire-fixture","max_tokens":8}`)))
		if w.Code != http.StatusForbidden {
			t.Fatal(w.Code)
		}
	}
}

func TestOpenAIQuotaIsSharedAcrossConcurrentSessions(t *testing.T) {
	var calls atomic.Int32
	h, err := Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }),
		Limits{Requests: 7, Bytes: 1024, Output: 8, Model: "wire-fixture", Protocol: ProtocolOpenAI})
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 40; i++ {
		wg.Add(1)
		go func(session int) {
			defer wg.Done()
			r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"wire-fixture","max_tokens":8}`))
			r.Header.Set("X-Cachino-Session", strconv.Itoa(session))
			h.ServeHTTP(httptest.NewRecorder(), r)
		}(i)
	}
	wg.Wait()
	if calls.Load() != 7 {
		t.Fatal(calls.Load())
	}
}

func TestUnknownSessionProtocolIsRejected(t *testing.T) {
	_, err := Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}),
		Limits{Requests: 1, Bytes: 1024, Output: 8, Model: "wire-fixture", Protocol: "typo"})
	if err == nil {
		t.Fatal("unknown protocol accepted")
	}
}
