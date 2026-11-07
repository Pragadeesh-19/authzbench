package results_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/engines/calibration"
	"github.com/Pragadeesh-19/authzbench/go/internal/virtualclock"
	"github.com/Pragadeesh-19/authzbench/go/results"
	"github.com/Pragadeesh-19/authzbench/go/runner"
)

const millis = int64(1_000_000)

var schemaPath = filepath.Join("..", "..", "src", "authzbench", "results.schema.json")

func shortPlan() runner.Plan {
	return runner.Plan{ServiceTimeNs: 100 * millis, OpenLoopRates: []int64{500}, OpenLoopNs: 200 * millis,
		RevocationCapNs: 100 * millis, RevocationTrials: 3, InFlight: 1, Placement: "same_machine"}
}

func run(t *testing.T, profile calibration.Profile) results.Document {
	t.Helper()
	data, err := dataset.Read(filepath.Join("..", "dataset", "testdata", "tiny"))
	if err != nil {
		t.Fatal(err)
	}
	clock := &virtualclock.Clock{}
	profile.ExtraLatencyNs = max(profile.ExtraLatencyNs, 100_000)
	result, err := runner.Run(context.Background(), calibration.New(profile, clock), data, shortPlan(), clock)
	if err != nil {
		t.Fatal(err)
	}
	return results.FromRun(result, results.CurrentEnvironment())
}

func validate(t *testing.T, document results.Document) error {
	t.Helper()
	schema, err := jsonschema.NewCompiler().Compile(schemaPath)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := jsonschema.UnmarshalJSON(bytes.NewReader(encoded))
	if err != nil {
		t.Fatal(err)
	}
	return schema.Validate(decoded)
}

func TestARunProducesADocumentThatFollowsTheSchema(t *testing.T) {
	document := run(t, calibration.Profile{})
	if err := validate(t, document); err != nil {
		t.Fatal(err)
	}
	if len(document.Modes) != 2 || document.Modes[0].Consistency != string(engine.Fresh) {
		t.Fatalf("modes = %+v", document.Modes)
	}
	if document.Modes[0].Revocation.Placement != shortPlan().Placement {
		t.Fatalf("placement = %q", document.Modes[0].Revocation.Placement)
	}
	if document.Conformance.Checks != 3 || len(document.DriverBaseline) != 1 {
		t.Fatalf("conformance = %+v, baseline = %+v", document.Conformance, document.DriverBaseline)
	}
}

func TestACappedRevocationIsWrittenAsNullStaleness(t *testing.T) {
	document := run(t, calibration.Profile{StalenessNs: 300 * millis})
	if err := validate(t, document); err != nil {
		t.Fatal(err)
	}
	stale := document.Modes[1]
	if stale.Consistency != string(engine.Fastest) || stale.Revocation.StalenessNs != nil {
		t.Fatalf("stale mode = %+v", stale)
	}
	if revocation := stale.Revocation; revocation.TrialsRequested != 3 || revocation.Trials != 1 ||
		revocation.Capped != 1 {
		t.Fatalf("a capped trial did not end the trials: %+v", revocation)
	}
	if fresh := document.Modes[0].Revocation; fresh.Trials != 3 || len(fresh.Samples) != 3 || fresh.Capped != 0 {
		t.Fatalf("fresh trials = %+v", fresh)
	}
	encoded, _ := json.Marshal(stale.Revocation)
	if !bytes.Contains(encoded, []byte(`"staleness_ns":null`)) {
		t.Fatalf("revocation = %s", encoded)
	}
}

func TestTheSchemaRejectsADocumentWithMismatches(t *testing.T) {
	document := run(t, calibration.Profile{})
	document.Conformance.Mismatches = 3
	var invalid *jsonschema.ValidationError
	if err := validate(t, document); !errors.As(err, &invalid) {
		t.Fatalf("err = %v", err)
	}
}
