// Command suez wires the proxy, optional raw capture, and HTTP server lifecycle.
package main

import (
	"context"
	"errors"
	"flag"
	"log"
	"net/http"
	"os/signal"
	"syscall"
	"time"

	"github.com/jerkeyray/suez/internal/proxy"
	"github.com/jerkeyray/suez/internal/sessionlimit"
	"github.com/jerkeyray/suez/internal/trace"
)

// run keeps resource cleanup separate from main's process-exiting error log.
// Shutdown drains or cancels HTTP work before deferred trace finalization.
func run() error {
	listen := flag.String("listen", "127.0.0.1:8080", "HTTP listen address")
	upstream := flag.String("upstream", "http://127.0.0.1:8081", "upstream HTTP(S) origin, without /v1")
	messagesPrefix := flag.String("messages-prefix", "", "upstream path prefix for Messages routes, e.g. /anthropic for DeepSeek")
	requestLimit := flag.Int("session-request-limit", 0, "opt-in maximum upstream requests, including retries, failures and admitted token-count calls")
	limitedProtocol := flag.String("session-protocol", sessionlimit.ProtocolMessages, "limited session protocol: messages or openai")
	inputLimit := flag.Int64("session-input-bytes", 128<<10, "maximum request bytes in limited session mode")
	outputLimit := flag.Int64("session-output-tokens", 512, "maximum requested output tokens in limited session mode")
	allowedModel := flag.String("session-model", "deepseek-flash", "only accepted model in limited session mode")
	traceDir := flag.String("trace-dir", "", "new directory for private raw capture (disabled by default)")
	bodyLimit := flag.Int64("trace-body-limit", 16<<20, "maximum stored bytes per request or response body")
	session := flag.String("session", "", "default capture session label (caller may override X-Suez-Session)")
	flag.Parse()
	handler, err := proxy.New(*upstream, proxy.WithMessagesPrefix(*messagesPrefix))
	if err != nil {
		return err
	}
	if *requestLimit != 0 {
		handler, err = sessionlimit.Wrap(handler, sessionlimit.Limits{
			Requests: *requestLimit, Bytes: *inputLimit, Output: *outputLimit, Model: *allowedModel,
			Protocol: *limitedProtocol,
		})
		if err != nil {
			return err
		}
	}
	if *traceDir != "" {
		store, err := trace.New(*traceDir, *bodyLimit, *session)
		if err != nil {
			return err
		}
		defer store.Close()
		handler = store.Wrap(handler)
	}
	server := &http.Server{
		Addr: *listen, Handler: handler,
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	shutdownDone := make(chan struct{})
	serveDone := make(chan struct{})
	go func() {
		defer close(shutdownDone)
		select {
		case <-ctx.Done():
			// Shutdown stops new requests while allowing active streams to drain.
			// Close forces cancellation if that drain exceeds the deadline.
			deadline, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			if err := server.Shutdown(deadline); err != nil {
				server.Close()
			}
		case <-serveDone:
		}
	}()
	log.Printf("suez listening on %s", *listen)
	err = server.ListenAndServe()
	close(serveDone)
	// ListenAndServe can return as soon as shutdown starts. Wait for the drain
	// before returning to the deferred store.Close and its final log writes.
	<-shutdownDone
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
