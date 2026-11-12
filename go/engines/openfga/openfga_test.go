package openfga_test

import (
	"testing"

	"github.com/Pragadeesh-19/authzbench/go/engines/openfga"
	"github.com/Pragadeesh-19/authzbench/go/internal/enginetest"
)

const (
	version     = "test"
	connections = 16
)

func dial(t *testing.T) *openfga.Engine {
	t.Helper()
	apiURL := enginetest.Endpoint(t, "AUTHZBENCH_OPENFGA_URL")
	target, err := openfga.Dial(apiURL, enginetest.Schema(t, "model.fga"), version, connections)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { target.Close() })
	return target
}

func TestOpenFGAAgreesWithTheReferenceAndSeesItsOwnRevokeAtHigherConsistency(t *testing.T) {
	enginetest.AgreesWithTheReferenceAndSeesItsOwnRevokeWhenFresh(t, dial(t))
}

func TestOpenFGADeniesEveryDelegatedLeafOnItsFirstCheckAtHigherConsistency(t *testing.T) {
	enginetest.DeniesEveryDelegatedLeafOnItsFirstFreshCheck(t, dial(t))
}

func TestOpenFGAForgetsWhatAnEarlierLoadLeftBehind(t *testing.T) {
	enginetest.ForgetsWhatAnEarlierLoadLeftBehind(t, dial(t))
}
