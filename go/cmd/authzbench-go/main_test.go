package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Pragadeesh-19/authzbench/go/results"
)

func tinyArguments(t *testing.T, extra ...string) ([]string, string) {
	t.Helper()
	out := filepath.Join(t.TempDir(), "calibration.json")
	arguments := []string{
		"-engine", "calibration",
		"-dataset", filepath.Join("..", "..", "dataset", "testdata", "tiny"),
		"-out", out,
		"-rates", "500",
		"-service-seconds", "0.1",
		"-open-loop-seconds", "0.2",
		"-revocation-cap-seconds", "30",
		"-revocation-trials", "3",
	}
	return append(arguments, extra...), out
}

func TestTheCalibrationEngineMeasuresItsKnownStalenessThroughTheCommand(t *testing.T) {
	arguments, out := tinyArguments(t, "-placement", "second_machine")
	if err := run(context.Background(), arguments); err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}
	var document results.Document
	if err := json.Unmarshal(content, &document); err != nil {
		t.Fatal(err)
	}
	fresh, stale := document.Modes[0].Revocation, document.Modes[1].Revocation
	for _, trial := range fresh.Samples {
		if trial.StalenessNs == nil || *trial.StalenessNs != trial.ResolutionNs {
			t.Fatalf("a fresh check did not deny first: %+v", trial)
		}
	}
	if stale.Placement != "second_machine" || stale.Trials != 3 || stale.Capped != 0 {
		t.Fatalf("revocation = %+v", stale)
	}
	for _, trial := range stale.Samples {
		if staleness := *trial.StalenessNs; staleness > demonstrationProfile.StalenessNs+trial.ResolutionNs {
			t.Fatalf("staleness = %d beyond the engine's %d by more than the resolution %d",
				staleness, demonstrationProfile.StalenessNs, trial.ResolutionNs)
		}
	}
}

func TestTheCommandRefusesWhatItCannotMeasure(t *testing.T) {
	for name, extra := range map[string][]string{
		"an unknown engine":           {"-engine", "nodus"},
		"an in-process placement":     {"-placement", "in_process"},
		"a zero rate":                 {"-rates", "0"},
		"no check in flight":          {"-in-flight", "0"},
		"OpenFGA without its version": {"-engine", "openfga"},
	} {
		arguments, _ := tinyArguments(t, extra...)
		if err := run(context.Background(), arguments); err == nil {
			t.Errorf("%s: accepted %s", name, strings.Join(extra, " "))
		}
	}
}
