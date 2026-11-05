// Package fakemodel provides a deterministic HTTP fixture, not a model or cache simulator.
package fakemodel

import (
	"encoding/json"
	"io"
	"net/http"
	"time"
)

// New returns a chat-completion fixture supporting JSON and SSE. Usage is deliberately
// absent: this endpoint performs no tokenization or cache measurement.
func New() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /v1/chat/completions", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		var request struct {
			Model    string            `json:"model"`
			Messages []json.RawMessage `json:"messages"`
			Stream   bool              `json:"stream"`
		}
		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
		err := decoder.Decode(&request)
		if err == nil {
			// A successful first Decode does not reject a second JSON value or
			// trailing garbage. The fixture accepts exactly one request object.
			var extra any
			if decoder.Decode(&extra) != io.EOF {
				err = io.ErrUnexpectedEOF
			}
		}
		if err != nil || request.Model == "" || len(request.Messages) == 0 {
			w.WriteHeader(http.StatusBadRequest)
			_, _ = w.Write([]byte(`{"error":{"message":"expected one JSON object with model and nonempty messages","type":"invalid_request_error"}}`))
			return
		}
		if request.Stream {
			stream(w, r)
			return
		}
		_, _ = w.Write([]byte(`{"id":"chatcmpl-fake","object":"chat.completion","created":0,"model":"fake-model","choices":[{"index":0,"message":{"role":"assistant","content":"Hello from the fake model."},"finish_reason":"stop"}]}`))
	})
	return mux
}

// stream emits a fixed completion as independently flushed SSE events.
// Pacing makes delivery inspectable; it is unrelated to inference performance.
func stream(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	// Fixed IDs and chunks make direct/proxied byte comparisons reproducible.
	frames := []string{
		`{"id":"chatcmpl-fake","object":"chat.completion.chunk","created":0,"model":"fake-model","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}`,
		`{"id":"chatcmpl-fake","object":"chat.completion.chunk","created":0,"model":"fake-model","choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}]}`,
		`{"id":"chatcmpl-fake","object":"chat.completion.chunk","created":0,"model":"fake-model","choices":[{"index":0,"delta":{"content":"from the fake model."},"finish_reason":null}]}`,
		`{"id":"chatcmpl-fake","object":"chat.completion.chunk","created":0,"model":"fake-model","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}`,
		"[DONE]",
	}
	controller := http.NewResponseController(w)
	for index, frame := range frames {
		if r.Context().Err() != nil {
			return
		}
		if index > 0 {
			timer := time.NewTimer(100 * time.Millisecond)
			select {
			case <-r.Context().Done():
				timer.Stop()
				return
			case <-timer.C:
			}
		}
		if _, err := io.WriteString(w, "data: "+frame+"\n\n"); err != nil {
			return
		}
		if err := controller.Flush(); err != nil {
			return
		}
	}
}
