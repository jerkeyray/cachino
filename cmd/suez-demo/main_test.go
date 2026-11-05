package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/jerkeyray/suez/internal/trace"
)

// TestLimitedOpenAITrace exercises production-order capture/admission/proxy
// handlers through real HTTP on net.Pipe. It validates protocol bytes and the
// local quota boundary without claiming TCP, SDK or inference-engine parity.
func TestLimitedOpenAITrace(t *testing.T) {
	out := filepath.Join(t.TempDir(), "capture")
	if err := run(out, true); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(out, "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var finished []trace.Record
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		var event trace.Event
		if err := json.Unmarshal([]byte(line), &event); err != nil {
			t.Fatal(err)
		}
		if event.Kind == "finished" {
			finished = append(finished, event.Record)
		}
	}
	if len(finished) != 3 {
		t.Fatalf("expected three captures, got %d", len(finished))
	}
	for index, want := range []int{200, 200, 403} {
		record := finished[index]
		if record.Status != want || record.State != "completed" || !record.Request.Complete || !record.Response.Complete {
			t.Fatalf("unexpected lifecycle for request %d: %+v", index, record)
		}
		request, err := os.ReadFile(filepath.Join(out, record.Request.Path))
		if err != nil {
			t.Fatal(err)
		}
		if index == 0 && !strings.HasPrefix(string(request), "{\n \"model\"") {
			t.Fatal("admission normalized original JSON whitespace")
		}
	}
	stream, err := os.ReadFile(filepath.Join(out, finished[1].Response.Path))
	if err != nil || !strings.Contains(string(stream), "data: [DONE]\n\n") {
		t.Fatalf("complete streamed body not retained: %v", err)
	}
}
