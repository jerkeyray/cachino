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
//
// With a token budget (SetBudget), the count limit is joined by a size limit.
// Each active session's KV footprint is estimated from its latest request's
// Content-Length, since a request carries the whole conversation; the proxy
// does not tokenize. A turn proceeds only if its session's footprint, plus
// those of the active sessions admitted before it, fits the budget. The oldest
// active session always proceeds once younger requests in flight finish, so
// some session can always make progress. A
// younger session whose turn does not fit is suspended: it leaves the active
// set and waits at the front of the queue, before sessions that never started,
// until an older session's idle expiry frees room. A session is only stopped
// between its own turns, never while a request is in flight, so a turn that
// fits after its older sessions but not alongside younger requests already in
// flight waits for those to finish, and no younger turn starts meanwhile.
// Requests without a Content-Length count as zero bytes.
package activelimit

import (
	"fmt"
	"log"
	"math"
	"net/http"
	"sort"
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

	// budget is the KV budget in tokens (0 disables the size limit), and
	// perByte converts request bytes to estimated tokens.
	budget  int
	perByte float64

	mu       sync.Mutex
	sessions map[string]*session
	waiters  []*waiter
	admitted int // admission counter, giving each session its age
	arrivals int // queue arrival counter, ordering sessions that have not started
}

type session struct {
	order     int // admission order; lower is older
	footprint int // estimated tokens of the session's latest request
	inflight  int
	expiry    *time.Timer
	// generation invalidates an idle timer when the session became busy again.
	generation int
}

type waiter struct {
	label string
	size  int
	// order is a suspended session's original admission order, kept so it
	// resumes with its age; -1 for a session that has not started.
	order int
	seq   int
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

// SetBudget enables the size limit: tokens is the shared KV budget, and
// perByte the estimated prompt tokens per request byte. Call it before Wrap
// serves requests.
func (l *Limiter) SetBudget(tokens int, perByte float64) error {
	if tokens < 1 || perByte <= 0 {
		return fmt.Errorf("token budget requires tokens >= 1 and a positive tokens-per-byte ratio")
	}
	l.budget, l.perByte = tokens, perByte
	return nil
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
		size := 0
		if l.budget > 0 && r.ContentLength > 0 {
			size = int(float64(r.ContentLength) * l.perByte)
		}
		if wait := l.acquire(label, size); wait != nil {
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

// acquire counts the request against its session at once when the session
// already has a request in flight, returning nil. Otherwise it queues the
// request and schedules; it returns nil if the request was admitted
// immediately, or the waiter whose channel closes once it is admitted.
func (l *Limiter) acquire(label string, size int) *waiter {
	l.mu.Lock()
	defer l.mu.Unlock()
	s, active := l.sessions[label]
	if active && s.inflight > 0 {
		l.busy(s)
		return nil
	}
	w := &waiter{label: label, size: size, order: -1, seq: l.arrivals, ready: make(chan struct{})}
	l.arrivals++
	if active {
		// A waiting turn keeps its session active, so it is not expired
		// while it waits for younger requests to drain.
		w.order = s.order
		l.stopIdle(s)
	}
	l.waiters = append(l.waiters, w)
	l.schedule()
	select {
	case <-w.ready:
		return nil
	default:
		return w
	}
}

// schedule admits queued turns, oldest session first, then sessions that have
// not started in arrival order. It holds the lock. With a token budget:
//
//   - an active session's turn that no longer fits after its older sessions
//     is suspended, freeing its slot, and waits as an inactive session;
//   - a turn that fits after its older sessions but would overflow the budget
//     together with younger sessions' requests in flight waits for those to
//     finish, and while it waits no younger turn starts. In-flight requests
//     cannot be paused, so this is what keeps a growing older session from
//     evicting the younger sessions' KV that is in use.
//
// Without a budget every check passes and only the count limit applies.
func (l *Limiter) schedule() {
	queue := append([]*waiter(nil), l.waiters...)
	sort.SliceStable(queue, func(i, j int) bool { return l.rank(queue[i]) < l.rank(queue[j]) })
	draining, newBlocked := false, false
	for _, w := range queue {
		if s, active := l.sessions[w.label]; active {
			older := l.older(s.order)
			if !l.fits(older, w.size) {
				l.stopIdle(s)
				delete(l.sessions, w.label)
				continue
			}
			if draining || !l.drained(s.order, older+w.size) {
				draining = true
				continue
			}
			s.footprint = max(s.footprint, w.size)
			l.grant(w, s)
			continue
		}
		if draining || (w.order < 0 && newBlocked) {
			continue
		}
		age := l.ageOf(w)
		older := l.older(age)
		if len(l.sessions) >= l.max || !l.fits(older, w.size) {
			newBlocked = true // sessions that have not started wait behind it
			continue
		}
		if !l.drained(age, older+w.size) {
			draining = true
			continue
		}
		l.grant(w, l.open(w.label, w.size, w.order))
	}
}

// rank orders waiters by session age; sessions that have not started come
// after every started one, in arrival order.
func (l *Limiter) rank(w *waiter) int {
	if s, active := l.sessions[w.label]; active {
		return s.order
	}
	if w.order >= 0 {
		return w.order
	}
	return math.MaxInt/2 + w.seq
}

// drained reports whether total tokens for a session of the given order fit
// alongside the younger sessions' requests now in flight.
func (l *Limiter) drained(order, total int) bool {
	if l.budget == 0 {
		return true
	}
	inflight := 0
	for _, s := range l.sessions {
		if s.order > order && s.inflight > 0 {
			inflight += s.footprint
		}
	}
	return inflight == 0 || total+inflight <= l.budget
}

// grant admits a queued request into its session.
func (l *Limiter) grant(w *waiter, s *session) {
	l.busy(s)
	for i, queued := range l.waiters {
		if queued == w {
			l.waiters = append(l.waiters[:i], l.waiters[i+1:]...)
			break
		}
	}
	close(w.ready)
}

// older sums the footprints of active sessions admitted before order.
func (l *Limiter) older(order int) int {
	total := 0
	for _, s := range l.sessions {
		if s.order < order {
			total += s.footprint
		}
	}
	return total
}

// fits reports whether a session of size fits after older tokens. With no
// older session it always fits, so the oldest session is never held.
func (l *Limiter) fits(older, size int) bool {
	return l.budget == 0 || older == 0 || older+size <= l.budget
}

// open activates a session; order -1 gives it the next admission order.
func (l *Limiter) open(label string, size, order int) *session {
	if order < 0 {
		order = l.admitted
		l.admitted++
	}
	s := &session{order: order, footprint: size}
	l.sessions[label] = s
	return s
}

// ageOf is the admission order a waiter would hold if admitted now.
func (l *Limiter) ageOf(w *waiter) int {
	if w.order >= 0 {
		return w.order
	}
	return l.admitted
}

// busy records one more request in flight and cancels any idle expiry.
func (l *Limiter) busy(s *session) {
	s.inflight++
	l.stopIdle(s)
}

// stopIdle cancels a pending idle expiry and invalidates any that already fired.
func (l *Limiter) stopIdle(s *session) {
	s.generation++
	if s.expiry != nil {
		s.expiry.Stop()
		s.expiry = nil
	}
}

// startIdle keeps the session active for the idle period from now.
func (l *Limiter) startIdle(label string, s *session) {
	generation := s.generation
	s.expiry = time.AfterFunc(l.idle, func() { l.expire(label, generation) })
}

// release ends one request; the session stays active for the idle period. A
// finished request can let a draining older turn proceed, so it reschedules.
func (l *Limiter) release(label string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	s := l.sessions[label]
	s.inflight--
	if s.inflight > 0 {
		return
	}
	l.startIdle(label, s)
	l.schedule()
}

// expire closes an idle session unless it became busy since, then schedules.
func (l *Limiter) expire(label string, generation int) {
	l.mu.Lock()
	defer l.mu.Unlock()
	s, ok := l.sessions[label]
	if !ok || s.inflight > 0 || s.generation != generation {
		return
	}
	delete(l.sessions, label)
	l.schedule()
}

// abandon removes a waiter whose client left, restarting its session's idle
// period if the session is still active with nothing in flight. If it was
// admitted at the same moment, its request was already counted, so it is
// released instead.
func (l *Limiter) abandon(w *waiter) {
	l.mu.Lock()
	for i, queued := range l.waiters {
		if queued == w {
			l.waiters = append(l.waiters[:i], l.waiters[i+1:]...)
			if s, ok := l.sessions[w.label]; ok && s.inflight == 0 && s.expiry == nil {
				l.startIdle(w.label, s)
			}
			l.schedule()
			l.mu.Unlock()
			return
		}
	}
	l.mu.Unlock()
	l.release(w.label)
}
