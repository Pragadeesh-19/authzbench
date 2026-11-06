package openfga

import (
	"net/http"
	"testing"
)

func TestTheClientKeepsAnIdleConnectionForEveryCheckInFlight(t *testing.T) {
	transport := pooledClient(256).Transport.(*http.Transport)
	if transport.MaxIdleConnsPerHost != 256 || transport.MaxIdleConns != 256 {
		t.Fatalf("idle connections: %d per host, %d in all", transport.MaxIdleConnsPerHost, transport.MaxIdleConns)
	}
	if transport == http.DefaultTransport {
		t.Fatal("the shared default transport was changed")
	}
}
