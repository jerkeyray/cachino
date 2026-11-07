// cachino-demo produces a synthetic capture without a GPU, provider or TCP listener.
package main

import (
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync/atomic"

	"github.com/jerkeyray/cachino/internal/fakemodel"
	"github.com/jerkeyray/cachino/internal/localhttp"
	"github.com/jerkeyray/cachino/internal/proxy"
	"github.com/jerkeyray/cachino/internal/sessionlimit"
	"github.com/jerkeyray/cachino/internal/trace"
)

// run exercises capture with controlled inputs over real HTTP on net.Pipe.
// The repeated/mutated bodies test fidelity; they do not simulate cache hits.
func run(out string, limited bool) error {
	store, err := trace.New(out, 16<<20, "demo")
	if err != nil {
		return err
	}
	// Register store cleanup first so endpoint connections close before its log.
	defer store.Close()
	var upstreamCalls atomic.Int32
	fixture := fakemodel.New()
	backend := localhttp.New(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		upstreamCalls.Add(1)
		fixture.ServeHTTP(w, r)
	}))
	defer backend.Close()
	handler, err := proxy.New("http://fake.test", proxy.WithTransport(backend.Transport))
	if err != nil {
		return err
	}
	if limited {
		handler, err = sessionlimit.Wrap(handler, sessionlimit.Limits{
			Requests: 2, Bytes: 4096, Output: 8, Model: "fake-model", Protocol: sessionlimit.ProtocolOpenAI,
		})
		if err != nil {
			return err
		}
	}
	endpoint := localhttp.New(store.Wrap(handler))
	defer endpoint.Close()
	bodies := []string{
		`{"model":"fake-model","messages":[{"role":"user","content":"Hello"}]}`,
		`{"model":"fake-model","messages":[{"role":"user","content":"Hello"}]}`,
		`{"model":"fake-model","messages":[{"role":"user","content":"Changed early prefix"}]}`,
		`{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"stream":true}`,
		`invalid JSON`,
	}
	statuses := []int{200, 200, 200, 200, 400}
	if limited {
		bodies = []string{
			"{\n \"model\":\"fake-model\", \"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}], \"max_tokens\":8\n}",
			`{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"max_completion_tokens":8,"n":1,"stream":true}`,
			`{"model":"fake-model","messages":[{"role":"user","content":"Hello"}],"max_tokens":8}`,
		}
		statuses = []int{200, 200, 403}
	}
	for index, body := range bodies {
		request, err := http.NewRequest("POST", "http://cachino.test/v1/chat/completions", strings.NewReader(body))
		if err != nil {
			return err
		}
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("X-Suez-Turn", fmt.Sprint(index))
		request.Header.Set("X-Suez-Attempt", "0")
		response, err := endpoint.Client.Do(request)
		if err != nil {
			return err
		}
		_, readErr := io.Copy(io.Discard, response.Body)
		response.Body.Close()
		if readErr != nil {
			return readErr
		}
		expected := statuses[index]
		if response.StatusCode != expected {
			return fmt.Errorf("turn %d: expected HTTP %d, got %d", index, expected, response.StatusCode)
		}
		fmt.Printf("turn=%d status=%d request_id=%s\n", index, response.StatusCode, response.Header.Get("X-Suez-Request-ID"))
	}
	if limited {
		// Count actual fixture handler entries, not attempted client requests.
		// Token/cache usage remains absent because this is not an inference run.
		if upstreamCalls.Load() != 2 {
			return fmt.Errorf("expected two fixture dispatches, got %d", upstreamCalls.Load())
		}
		fmt.Printf("fixture_upstream_requests=%d engine_validated=false\n", upstreamCalls.Load())
	}
	return nil
}

func main() {
	out := flag.String("out", "traces/demo", "new capture directory; must not already exist")
	limited := flag.Bool("limited-openai", false, "test two admitted JSON/SSE requests and one local quota rejection")
	flag.Parse()
	if err := run(*out, *limited); err != nil {
		log.Fatal(err)
	}
}
