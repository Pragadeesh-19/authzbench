package enginetest

import (
	"context"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/measure"
	"github.com/Pragadeesh-19/authzbench/go/runner"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

const revocationCapNs = 30 * timing.NanosPerSecond

func Endpoint(t *testing.T, variable string) string {
	t.Helper()
	endpoint := os.Getenv(variable)
	if endpoint == "" {
		t.Skipf("%s is not set; start services/compose.yaml and set it to run this test", variable)
	}
	return endpoint
}

func Schema(t *testing.T, file string) string {
	t.Helper()
	content, err := os.ReadFile(filepath.Join("..", "..", "..", "generated", file))
	if err != nil {
		t.Fatal(err)
	}
	return string(content)
}

func Tiny(t *testing.T) *dataset.Dataset {
	t.Helper()
	data, err := dataset.Read(filepath.Join("..", "..", "dataset", "testdata", "tiny"))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func ForgetsWhatAnEarlierLoadLeftBehind(t *testing.T, target engine.Engine) {
	t.Helper()
	ctx, data := context.Background(), Tiny(t)
	if err := target.Load(ctx, data); err != nil {
		t.Fatal(err)
	}
	denied := data.Expectations[slices.IndexFunc(data.Expectations, func(expectation dataset.Expectation) bool {
		return !expectation.Allowed
	})].Query
	leftover := dataset.Relationship{Resource: denied.Resource, Relation: "viewer",
		Subject: dataset.Subject{Object: denied.Subject}}
	if _, err := target.Write(ctx, engine.Change{Operation: engine.Add, Relationship: leftover}); err != nil {
		t.Fatal(err)
	}
	if err := target.Load(ctx, data); err != nil {
		t.Fatal(err)
	}
	if _, err := runner.VerifyConformance(ctx, target, data); err != nil {
		t.Fatal(err)
	}
}

func AgreesWithTheReferenceAndSeesItsOwnRevokeWhenFresh(t *testing.T, target engine.Engine) {
	t.Helper()
	ctx, data := context.Background(), Tiny(t)
	if err := target.Load(ctx, data); err != nil {
		t.Fatal(err)
	}
	if _, err := runner.VerifyConformance(ctx, target, data); err != nil {
		t.Fatal(err)
	}
	result, err := measure.RaceRevocation(ctx, target, target, data.Revocation, engine.Fresh, revocationCapNs,
		timing.NewSystemClock())
	if err != nil {
		t.Fatal(err)
	}
	if result.Capped() {
		t.Fatal("a fresh check never saw the revoke")
	}
	if *result.StalenessNs != result.ResolutionNs {
		t.Fatalf("the first check after the revoke allowed: staleness = %d, resolution = %d",
			*result.StalenessNs, result.ResolutionNs)
	}
	if target.Version() == "" {
		t.Fatal("the engine reports no version")
	}
}
