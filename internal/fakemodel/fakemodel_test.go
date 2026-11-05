package fakemodel_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/jerkeyray/suez/internal/fakemodel"
)

func TestCompletion(t *testing.T) {
	handler := fakemodel.New()
	var previous string
	for range 2 {
		request := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"fake-model","messages":[{"role":"user","content":"hi"}],"extra":true}`))
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		if response.Code != 200 || response.Header().Get("Content-Type") != "application/json" {
			t.Fatalf("unexpected response: %d %v", response.Code, response.Header())
		}
		var result struct {
			Object  string          `json:"object"`
			Usage   json.RawMessage `json:"usage"`
			Choices []struct {
				Message      struct{ Role, Content string }
				FinishReason string `json:"finish_reason"`
			}
		}
		if err := json.Unmarshal(response.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if result.Object != "chat.completion" || len(result.Choices) != 1 || result.Usage != nil {
			t.Fatalf("invalid completion or fabricated usage: %+v", result)
		}
		choice := result.Choices[0]
		if choice.Message.Role != "assistant" || choice.Message.Content != "Hello from the fake model." || choice.FinishReason != "stop" {
			t.Fatalf("unexpected choice: %+v", choice)
		}
		if previous != "" && previous != response.Body.String() {
			t.Fatal("fixture response is not deterministic")
		}
		previous = response.Body.String()
	}
}

func TestInvalidRequests(t *testing.T) {
	for _, body := range []string{
		`not json`, `null`, `{}`, `{"model":"fake-model","messages":[]}`,
		`{"messages":[{}]}`, `{"model":"fake-model","messages":[{}]} {}`,
		`{"model":"fake-model","messages":[{}],"stream":"yes"}`,
		`{"model":"fake-model","messages":[{"content":"` + strings.Repeat("x", 1<<20) + `"}]}`,
	} {
		response := httptest.NewRecorder()
		fakemodel.New().ServeHTTP(response, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body)))
		if response.Code != 400 || !json.Valid(response.Body.Bytes()) {
			t.Fatalf("expected JSON 400, got %d %q", response.Code, response.Body.String())
		}
	}
}

func TestStream(t *testing.T) {
	response := httptest.NewRecorder()
	fakemodel.New().ServeHTTP(response, httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"fake-model","messages":[{}],"stream":true}`)))
	if response.Code != 200 || response.Header().Get("Content-Type") != "text/event-stream" || !response.Flushed {
		t.Fatalf("expected flushed SSE: %d %v", response.Code, response.Header())
	}
	frames := strings.Split(strings.TrimSuffix(response.Body.String(), "\n\n"), "\n\n")
	if len(frames) != 5 || frames[4] != "data: [DONE]" {
		t.Fatalf("unexpected frames: %q", frames)
	}
	var content, role, finish string
	for _, frame := range frames[:4] {
		if !strings.HasPrefix(frame, "data: ") {
			t.Fatalf("invalid SSE frame: %q", frame)
		}
		var chunk struct {
			Object  string          `json:"object"`
			Usage   json.RawMessage `json:"usage"`
			Choices []struct {
				Delta        struct{ Role, Content string }
				FinishReason *string `json:"finish_reason"`
			}
		}
		if err := json.Unmarshal([]byte(strings.TrimPrefix(frame, "data: ")), &chunk); err != nil {
			t.Fatal(err)
		}
		if chunk.Object != "chat.completion.chunk" || chunk.Usage != nil || len(chunk.Choices) != 1 {
			t.Fatalf("invalid chunk: %+v", chunk)
		}
		choice := chunk.Choices[0]
		role += choice.Delta.Role
		content += choice.Delta.Content
		if choice.FinishReason != nil {
			finish = *choice.FinishReason
		}
	}
	if role != "assistant" || content != "Hello from the fake model." || finish != "stop" {
		t.Fatalf("unexpected completion: role=%q content=%q finish=%q", role, content, finish)
	}
}

type cancelOnFlush struct {
	*httptest.ResponseRecorder
	cancel context.CancelFunc
}

func (w cancelOnFlush) Flush() {
	w.ResponseRecorder.Flush()
	w.cancel()
}

func TestStreamStopsOnCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	response := httptest.NewRecorder()
	request := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"fake-model","messages":[{}],"stream":true}`)).WithContext(ctx)
	fakemodel.New().ServeHTTP(cancelOnFlush{response, cancel}, request)
	if strings.Count(response.Body.String(), "data: ") != 1 || strings.Contains(response.Body.String(), "[DONE]") {
		t.Fatalf("continued stream after cancellation: %s", response.Body)
	}
}
