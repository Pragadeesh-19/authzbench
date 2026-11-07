package spicedb_test

import (
	"testing"

	"github.com/Pragadeesh-19/authzbench/go/engines/spicedb"
	"github.com/Pragadeesh-19/authzbench/go/internal/enginetest"
)

const presharedKey = "authzbench"

func dial(t *testing.T, freshRead spicedb.FreshRead) *spicedb.Engine {
	t.Helper()
	endpoint := enginetest.Endpoint(t, "AUTHZBENCH_SPICEDB_ENDPOINT")
	target, err := spicedb.Dial(endpoint, presharedKey, enginetest.Schema(t, "model.zed"), freshRead)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { target.Close() })
	return target
}

func TestSpiceDBAgreesWithTheReferenceAndSeesItsOwnRevokeWhenFullyConsistent(t *testing.T) {
	enginetest.AgreesWithTheReferenceAndSeesItsOwnRevokeWhenFresh(t, dial(t, spicedb.FullyConsistent))
}

func TestSpiceDBAgreesWithTheReferenceAndSeesItsOwnRevokeAtLeastAsFreshAsItsToken(t *testing.T) {
	enginetest.AgreesWithTheReferenceAndSeesItsOwnRevokeWhenFresh(t, dial(t, spicedb.AtLeastAsFresh))
}

func TestSpiceDBSeesItsOwnWritesWhenFresh(t *testing.T) {
	for _, freshRead := range spicedb.FreshReads {
		t.Run(string(freshRead), func(t *testing.T) {
			enginetest.SeesItsOwnWritesWhenFreshWithoutTheirRevisions(t, dial(t, freshRead))
		})
	}
}

func TestSpiceDBForgetsWhatAnEarlierLoadLeftBehind(t *testing.T) {
	enginetest.ForgetsWhatAnEarlierLoadLeftBehind(t, dial(t, spicedb.FullyConsistent))
}

func TestSpiceDBForgetsWhatAnEarlierLoadLeftBehindAtLeastAsFreshAsItsToken(t *testing.T) {
	enginetest.ForgetsWhatAnEarlierLoadLeftBehind(t, dial(t, spicedb.AtLeastAsFresh))
}

func TestAnUnknownFreshReadIsRefused(t *testing.T) {
	if _, err := spicedb.Dial("localhost:1", presharedKey, "", "eventually"); err == nil {
		t.Fatal("an unknown fresh read was accepted")
	}
}
