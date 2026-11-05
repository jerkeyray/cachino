// Package sessionlimit bounds opt-in Messages or OpenAI chat captures. It validates
// complete request bytes before admission and replays those exact bytes upstream.
// This buffers requests and changes admission timing, so it is not a benchmark.
package sessionlimit

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
)

const (
	// ProtocolMessages admits Messages and token-count routes.
	ProtocolMessages = "messages"
	// ProtocolOpenAI admits only chat completions with a single output choice.
	ProtocolOpenAI = "openai"
)

// Limits applies to one handler instance across all callers and session labels.
// Requests includes token counting, retries and upstream failures. Bytes bounds
// each input body, not model tokens; Output caps explicitly requested output.
type Limits struct {
	Requests int
	Bytes    int64
	Output   int64
	Model    string
	// Protocol defaults to Messages when empty, preserving existing callers.
	Protocol string
}

// Wrap rejects requests outside limits without modifying accepted payloads.
// Quota is reserved atomically before forwarding and is never refunded. Only
// the selected protocol's routes are allowed in this opt-in mode. OpenAI mode
// requires exactly one output-token field, n absent or 1, and no best_of.
// Rejections use HTTP 400/413 or non-retryable 403, preventing retry loops from
// triggering more paid work.
func Wrap(next http.Handler, limits Limits) (http.Handler, error) {
	if limits.Requests < 1 || limits.Bytes < 1 || limits.Output < 1 || limits.Model == "" {
		return nil, fmt.Errorf("session limits require positive request, byte, output bounds and a model")
	}
	protocol := limits.Protocol
	if protocol == "" {
		protocol = ProtocolMessages
	}
	if protocol != ProtocolMessages && protocol != ProtocolOpenAI {
		return nil, fmt.Errorf("unknown session protocol %q", protocol)
	}
	var mu sync.Mutex
	admitted := 0
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reject := func(status int) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(status)
			io.WriteString(w, `{"type":"error","error":{"type":"invalid_request_error","message":"Suez session limit rejected request"}}`)
		}
		allowedPath := r.URL.Path == "/v1/messages" || r.URL.Path == "/v1/messages/count_tokens"
		if protocol == ProtocolOpenAI {
			allowedPath = r.URL.Path == "/v1/chat/completions"
		}
		if r.Method != "POST" || !allowedPath ||
			(r.Header.Get("Content-Encoding") != "" && r.Header.Get("Content-Encoding") != "identity") {
			reject(http.StatusForbidden)
			return
		}
		data, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limits.Bytes))
		if err != nil {
			reject(http.StatusRequestEntityTooLarge)
			return
		}
		fields, err := uniqueFields(data)
		if err != nil {
			reject(http.StatusBadRequest)
			return
		}
		var model string
		if json.Unmarshal(fields["model"], &model) != nil || model != limits.Model {
			reject(http.StatusForbidden)
			return
		}
		if protocol == ProtocolMessages && r.URL.Path == "/v1/messages" && !boundedOutput(fields["max_tokens"], limits.Output) {
			reject(http.StatusForbidden)
			return
		}
		if protocol == ProtocolOpenAI && !boundedChatOutput(fields, limits.Output) {
			reject(http.StatusForbidden)
			return
		}
		mu.Lock()
		allowed := admitted < limits.Requests
		if allowed {
			admitted++
		}
		mu.Unlock()
		if !allowed {
			reject(http.StatusForbidden)
			return
		}
		// Validation must not normalize JSON, ordering, whitespace or tool schemas.
		r.Body = io.NopCloser(bytes.NewReader(data))
		next.ServeHTTP(w, r)
	}), nil
}

// boundedOutput requires an explicit positive integer; null, booleans, strings
// and fractional values cannot silently become an unbounded provider default.
func boundedOutput(value json.RawMessage, maximum int64) bool {
	var tokens int64
	return json.Unmarshal(value, &tokens) == nil && tokens >= 1 && tokens <= maximum
}

// boundedChatOutput accepts either standard chat output-limit spelling while
// rejecting ambiguous aliases and multiple generations. Request bytes remain
// unchanged; the middleware never rewrites a rejected request into a valid one.
func boundedChatOutput(fields map[string]json.RawMessage, maximum int64) bool {
	output, legacy := fields["max_tokens"]
	completion, modern := fields["max_completion_tokens"]
	if legacy == modern {
		return false
	}
	if modern {
		output = completion
	}
	if !boundedOutput(output, maximum) {
		return false
	}
	if _, exists := fields["best_of"]; exists {
		return false
	}
	if raw, exists := fields["n"]; exists {
		var choices int64
		if json.Unmarshal(raw, &choices) != nil || choices != 1 {
			return false
		}
	}
	return true
}

// uniqueFields rejects duplicate top-level keys so admission cannot disagree
// with a provider about which model or max_tokens value the request contains.
func uniqueFields(data []byte) (map[string]json.RawMessage, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	start, err := decoder.Token()
	if err != nil || start != json.Delim('{') {
		return nil, fmt.Errorf("expected object")
	}
	fields := make(map[string]json.RawMessage)
	for decoder.More() {
		token, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		key, ok := token.(string)
		if !ok {
			return nil, fmt.Errorf("expected key")
		}
		if _, exists := fields[key]; exists {
			return nil, fmt.Errorf("duplicate key")
		}
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			return nil, err
		}
		fields[key] = value
	}
	if _, err := decoder.Token(); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, fmt.Errorf("trailing data")
	}
	return fields, nil
}
