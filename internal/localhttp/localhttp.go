// Package localhttp runs the real HTTP stack over in-memory connections.
// It is a development transport, not a substitute for TCP validation.
package localhttp

import (
	"context"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"sync"
	"time"
)

// listener hands each server-side pipe to http.Server.Accept. done unblocks
// both acceptance and pending dials on shutdown without needing a bound socket.
type listener struct {
	connections chan net.Conn
	done        chan struct{}
	once        sync.Once
}

func (l *listener) Accept() (net.Conn, error) {
	select {
	case conn := <-l.connections:
		return conn, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}
func (l *listener) Close() error   { l.once.Do(func() { close(l.done) }); return nil }
func (l *listener) Addr() net.Addr { return &net.TCPAddr{} }

// Endpoint owns an HTTP server and the transport/client used to reach it.
// Its transport routes every dial to the same handler regardless of URL host;
// it is intended only for controlled fixtures. Call Close when finished.
type Endpoint struct {
	Transport *http.Transport
	Client    *http.Client
	server    *http.Server
	listener  *listener
	stopped   chan struct{}
}

// New starts handler on in-memory connections using the real HTTP/1 stack.
// The client has a five-second deadline to bound stuck fixture exchanges.
func New(handler http.Handler) *Endpoint {
	l := &listener{connections: make(chan net.Conn), done: make(chan struct{})}
	e := &Endpoint{listener: l, stopped: make(chan struct{})}
	e.server = &http.Server{Handler: handler, ErrorLog: log.New(io.Discard, "", 0)}
	e.Transport = &http.Transport{DisableCompression: true, DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		// Each successful dial transfers the server end to Accept; failed
		// transfers must close both ends because neither side then owns them.
		client, upstream := net.Pipe()
		select {
		case l.connections <- upstream:
			return client, nil
		case <-ctx.Done():
			client.Close()
			upstream.Close()
			return nil, ctx.Err()
		case <-l.done:
			client.Close()
			upstream.Close()
			return nil, net.ErrClosed
		}
	}}
	e.Client = &http.Client{Transport: e.Transport, Timeout: 5 * time.Second}
	go func() { defer close(e.stopped); _ = e.server.Serve(l) }()
	return e
}

// Close closes idle/active connections and waits for the server accept loop.
// Handler finalizers may still be running; their owners must await them before
// closing shared resources, as trace.Store.Close does for capture records.
func (e *Endpoint) Close() error {
	e.Transport.CloseIdleConnections()
	e.listener.Close()
	err := e.server.Close()
	select {
	case <-e.stopped:
		return err
	case <-time.After(5 * time.Second):
		return errors.New("in-memory HTTP server did not stop")
	}
}
