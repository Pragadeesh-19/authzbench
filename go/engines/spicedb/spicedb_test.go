package spicedb_test

import (
	"testing"

	"github.com/Pragadeesh-19/authzbench/go/engines/spicedb"
	"github.com/Pragadeesh-19/authzbench/go/internal/enginetest"
)

const presharedKey = "authzbench"

func dial(t *testing.T) *spicedb.Engine {
	t.Helper()
	endpoint := enginetest.Endpoint(t, "AUTHZBENCH_SPICEDB_ENDPOINT")
	target, err := spicedb.Dial(endpoint, presharedKey, enginetest.Schema(t, "model.zed"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { target.Close() })
	return target
}

func TestSpiceDBAgreesWithTheReferenceAndSeesItsOwnRevokeWhenFullyConsistent(t *testing.T) {
	enginetest.AgreesWithTheReferenceAndSeesItsOwnRevokeWhenFresh(t, dial(t))
}

func TestSpiceDBForgetsWhatAnEarlierLoadLeftBehind(t *testing.T) {
	enginetest.ForgetsWhatAnEarlierLoadLeftBehind(t, dial(t))
}
