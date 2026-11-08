// Package activelimit caps how many agent sessions are active at once.
//
// A session is identified by its X-Cachino-Session label, or the configured
// default when absent. It is active while it has a request in flight and for an
// idle period after its last request finishes, which covers the gap between an
// agent's turns. Requests from an active session pass at once. A request from
// an inactive session waits, first come first served, until fewer than Max
// sessions are active. Holding whole sessions rather than single requests keeps
// each admitted session's prefix resident between turns, which is the point of
// the limit: it trades waiting for less eviction in a shared prefix cache.
package activelimit

import (
	"fmt"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Limiter is an http.Handler middleware; create it with New.
type Limiter struct {
	max      int
	idle     time.Duration
	fallback string
	logf     func(format string, args ...any)

	mu       sync.Mutex
	sessions map[string]*session
	waiters  []*waiter
}

type session struct {
	inflight int
	expiry   *time.Timer
	// generation invalidates an idle timer when the session became busy again.
	generation int
}

type waiter struct {
	label string
	ready chan struct{}
}

// New returns a limiter for at most max active sessions. idle is how long a
// session stays active after its last request finishes; fallback labels
// requests without X-Cachino-Session.
func New(max int, idle time.Duration, fallback string) (*Limiter, error) {
	if max < 1 || idle <= 0 {
		return nil, fmt.Errorf("active session limit requires max >= 1 and a positive idle period")
	}
	return &Limiter{max: max, idle: idle, fallback: fallback, logf: log.Printf, sessions: map[string]*session{}}, nil
}

// Active reports the number of active sessions, for tests and diagnostics.
func (l *Limiter) Active() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return len(l.sessions)
}

// Wrap admits requests by session. A waiting request whose client goes away is
// dropped from the queue without taking a slot. Each wait is logged with the
// session, the capture request ID when present and its duration, so analysis
// can recover when the request was actually forwarded.
func (l *Limiter) Wrap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		label := strings.TrimSpace(r.Header.Get("X-Cachino-Session"))
		if label == "" {
			label = l.fallback
		}
		started := time.Now()
		if wait := l.acquire(label); wait != nil {
			select {
			case <-wait.ready:
			case <-r.Context().Done():
				l.abandon(wait)
				return
			}
			l.logf("activelimit wait session=%s request=%s waited_ms=%.1f", label,
				w.Header().Get("X-Cachino-Request-ID"), float64(time.Since(started).Microseconds())/1000)
		}
		defer l.release(label)
		next.ServeHTTP(w, r)
	})
}

// acquire counts the request against its session if the session is active or a
// slot is free, returning nil; otherwise it queues and returns a waiter whose
// channel is closed once the session has been admitted and the request counted.
func (l *Limiter) acquire(label string) *waiter {
	l.mu.Lock()
	defer l.mu.Unlock()
	if s, ok := l.sessions[label]; ok {
		l.busy(s)
		return nil
	}
	if len(l.sessions) < l.max && len(l.waiters) == 0 {
		l.busy(l.open(label))
		return nil
	}
	w := &waiter{label: label, ready: make(chan struct{})}
	l.waiters = append(l.waiters, w)
	return w
}

func (l *Limiter) open(label string) *session {
	s := &session{}
	l.sessions[label] = s
	return s
}

// busy records one more request in flight and cancels any idle expiry.
func (l *Limiter) busy(s *session) {
	s.inflight++
	s.generation++
	if s.expiry != nil {
		s.expiry.Stop()
		s.expiry = nil
	}
}

// release ends one request; the session stays active for the idle period.
func (l *Limiter) release(label string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	s := l.sessions[label]
	s.inflight--
	if s.inflight > 0 {
		return
	}
	generation := s.generation
	s.expiry = time.AfterFunc(l.idle, func() { l.expire(label, generation) })
}

// expire closes an idle session unless it became busy since, then admits
// waiters in arrival order while slots are free.
func (l *Limiter) expire(label string, generation int) {
	l.mu.Lock()
	defer l.mu.Unlock()
	s, ok := l.sessions[label]
	if !ok || s.inflight > 0 || s.generation != generation {
		return
	}
	delete(l.sessions, label)
	l.admit()
}

// admit wakes queued requests, oldest first. A request whose session became
// active while it waited joins that session without needing a new slot.
func (l *Limiter) admit() {
	for len(l.waiters) > 0 {
		w := l.waiters[0]
		s, ok := l.sessions[w.label]
		if !ok {
			if len(l.sessions) >= l.max {
				return
			}
			s = l.open(w.label)
		}
		l.busy(s)
		l.waiters = l.waiters[1:]
		close(w.ready)
	}
}

// abandon removes a waiter whose client left. If it was admitted at the same
// moment, its request was already counted, so it is released instead.
func (l *Limiter) abandon(w *waiter) {
	l.mu.Lock()
	for i, queued := range l.waiters {
		if queued == w {
			l.waiters = append(l.waiters[:i], l.waiters[i+1:]...)
			l.mu.Unlock()
			return
		}
	}
	l.mu.Unlock()
	l.release(w.label)
}
