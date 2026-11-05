// fake-model runs a local deterministic upstream for Suez development.
package main

import (
	"flag"
	"log"
	"net/http"
	"time"

	"github.com/jerkeyray/suez/internal/fakemodel"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:8081", "HTTP listen address")
	flag.Parse()
	server := &http.Server{
		Addr: *listen, Handler: fakemodel.New(),
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	log.Printf("fake model listening on %s", *listen)
	log.Fatal(server.ListenAndServe())
}
