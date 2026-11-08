package activelimit

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

const idle = 40 * time.Millisecond

// serve sends one request for label through h and returns when it completes.
func serve(h http.Handler, label string) *httptest.ResponseRecorder {
	r := httptest.NewRequest("POST", "/v1/chat/completions", nil)
	if label != "" {
		r.Header.Set("X-Cachino-Session", label)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func newLimiter(t *testing.T, max int) *Limiter {
	t.Helper()
	l, err := New(max, idle, "default")
	if err != nil {
		t.Fatal(err)
	}
	l.logf = func(string, ...any) {}
	return l
}

func TestRejectsInvalidConfiguration(t *testing.T) {
	if _, err := New(0, idle, ""); err == nil {
		t.Fatal("max 0 accepted")
	}
	if _, err := New(1, 0, ""); err == nil {
		t.Fatal("zero idle accepted")
	}
}

func TestThirdSessionWaitsForAnIdleSlot(t *testing.T) {
	l := newLimiter(t, 2)
	h := l.Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	serve(h, "a")
	serve(h, "b")
	if l.Active() != 2 {
		t.Fatalf("active = %d, want 2", l.Active())
	}
	started := time.Now()
	serve(h, "c")
	if waited := time.Since(started); waited < idle/2 {
		t.Fatalf("third session admitted after %v, before a slot expired", waited)
	}
}

func TestActiveSessionPassesWhileOthersWait(t *testing.T) {
	l := newLimiter(t, 1)
	h := l.Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	serve(h, "a")
	done := make(chan struct{})
	go func() { serve(h, "b"); close(done) }()
	time.Sleep(idle / 4)
	started := time.Now()
	serve(h, "a") // a's next turn arrives within its idle period
	if time.Since(started) > idle/2 {
		t.Fatal("an active session's next request waited")
	}
	select {
	case <-done:
		t.Fatal("b admitted while a was still active")
	case <-time.After(idle / 4):
	}
	<-done
}

func TestNewTurnCancelsIdleExpiry(t *testing.T) {
	l := newLimiter(t, 1)
	h := l.Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	for i := 0; i < 4; i++ { // turns spaced inside the idle period keep a active
		serve(h, "a")
		time.Sleep(idle / 2)
	}
	if l.Active() != 1 {
		t.Fatalf("active = %d, want 1", l.Active())
	}
	time.Sleep(2 * idle)
	if l.Active() != 0 {
		t.Fatalf("idle session still active: %d", l.Active())
	}
}

func TestWaitersAreAdmittedInArrivalOrder(t *testing.T) {
	l := newLimiter(t, 1)
	h := l.Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	serve(h, "first")
	var mu sync.Mutex
	var order []string
	var wg sync.WaitGroup
	for _, label := range []string{"b", "c"} {
		wg.Add(1)
		go func(label string) {
			defer wg.Done()
			serve(h, label)
			mu.Lock()
			order = append(order, label)
			mu.Unlock()
		}(label)
		time.Sleep(idle / 8) // b queues before c
	}
	wg.Wait()
	if len(order) != 2 || order[0] != "b" {
		t.Fatalf("order = %v, want b first", order)
	}
}

func TestLeavingClientDoesNotTakeASlot(t *testing.T) {
	l := newLimiter(t, 1)
	h := l.Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	serve(h, "a")
	ctx, cancel := context.WithCancel(context.Background())
	r := httptest.NewRequest("POST", "/v1/chat/completions", nil).WithContext(ctx)
	r.Header.Set("X-Cachino-Session", "gone")
	finished := make(chan struct{})
	go func() { h.ServeHTTP(httptest.NewRecorder(), r); close(finished) }()
	time.Sleep(idle / 4)
	cancel()
	<-finished
	time.Sleep(2 * idle)
	if l.Active() != 0 {
		t.Fatalf("abandoned waiter left %d active sessions", l.Active())
	}
}

func TestMissingHeaderUsesFallbackLabel(t *testing.T) {
	l := newLimiter(t, 1)
	seen := 0
	h := l.Wrap(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { seen++ }))
	serve(h, "")
	serve(h, "default") // same session as the unlabeled request
	if seen != 2 || l.Active() != 1 {
		t.Fatalf("seen=%d active=%d, want 2 and 1", seen, l.Active())
	}
}
