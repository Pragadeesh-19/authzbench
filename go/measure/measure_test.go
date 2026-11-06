package measure_test

import (
	"context"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/engines/calibration"
	"github.com/Pragadeesh-19/authzbench/go/internal/virtualclock"
	"github.com/Pragadeesh-19/authzbench/go/measure"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

const (
	micros    = int64(1_000)
	millis    = 1_000 * micros
	second    = 1_000 * millis
	checkCost = 100 * micros
	precision = 0.001
)

func tinyDataset(t *testing.T) *dataset.Dataset {
	t.Helper()
	data, err := dataset.Read(filepath.Join("..", "dataset", "testdata", "tiny"))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func loaded(t *testing.T, data *dataset.Dataset, profile calibration.Profile, clock timing.Clock) *calibration.Engine {
	t.Helper()
	target := calibration.New(profile, clock)
	if err := target.Load(context.Background(), data); err != nil {
		t.Fatal(err)
	}
	return target
}

func queries(data *dataset.Dataset) []dataset.Query {
	result := make([]dataset.Query, len(data.Expectations))
	for index, expectation := range data.Expectations {
		result[index] = expectation.Query
	}
	return result
}

func closeTo(measured, expected int64) bool {
	difference := measured - expected
	return float64(max(difference, -difference)) <= float64(expected)*precision
}

func race(t *testing.T, profile calibration.Profile, consistency engine.Consistency, capNs int64) measure.RevocationResult {
	t.Helper()
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, profile, clock)
	result, err := measure.RaceRevocation(context.Background(), target, target, data.Revocation, consistency, capNs, clock)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestAKnownStalenessIsMeasuredExactly(t *testing.T) {
	result := race(t, calibration.Profile{StalenessNs: 500 * millis, ExtraLatencyNs: checkCost}, engine.Fastest, 5*second)
	if result.StalenessNs == nil || *result.StalenessNs != 500*millis || result.ResolutionNs != checkCost {
		t.Fatalf("result = %+v", result)
	}
}

func TestAFreshCheckDeniesOnTheFirstCheckAfterTheAcknowledgement(t *testing.T) {
	result := race(t, calibration.Profile{StalenessNs: 500 * millis, ExtraLatencyNs: checkCost}, engine.Fresh, 5*second)
	if result.StalenessNs == nil || *result.StalenessNs != 0 || result.ResolutionNs != 0 {
		t.Fatalf("result = %+v", result)
	}
}

func TestARevokeNeverSeenInsideTheCapIsReportedAsCapped(t *testing.T) {
	result := race(t, calibration.Profile{StalenessNs: 150 * millis, ExtraLatencyNs: checkCost}, engine.Fastest, 100*millis)
	if !result.Capped() || result.ResolutionNs != checkCost {
		t.Fatalf("result = %+v", result)
	}
}

func TestTheRaceRestoresTheRevokedRelationship(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{ExtraLatencyNs: checkCost}, clock)
	if _, err := measure.RaceRevocation(context.Background(), target, target, data.Revocation, engine.Fresh, second, clock); err != nil {
		t.Fatal(err)
	}
	if allowed, err := target.Check(context.Background(), data.Revocation.Probe, engine.Fresh, nil); err != nil || !allowed {
		t.Fatalf("probe after the race: %v, %v", allowed, err)
	}
}

func TestARaceWaitsForThePreviousRestoreToBecomeVisible(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{StalenessNs: 300 * millis, ExtraLatencyNs: checkCost}, clock)
	if _, err := measure.RaceRevocation(context.Background(), target, target, data.Revocation, engine.Fastest, second, clock); err != nil {
		t.Fatal(err)
	}
	result, err := measure.RaceRevocation(context.Background(), target, target, data.Revocation, engine.Fastest, second, clock)
	if err != nil || result.StalenessNs == nil || *result.StalenessNs != 300*millis {
		t.Fatalf("second race = %+v, %v", result, err)
	}
}

func TestAProbeDeniedBeforeTheRevokeIsRefused(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{ExtraLatencyNs: checkCost}, clock)
	if _, err := target.Write(context.Background(), engine.Change{Operation: engine.Remove, Relationship: data.Revocation.Revoke}); err != nil {
		t.Fatal(err)
	}
	_, err := measure.RaceRevocation(context.Background(), target, target, data.Revocation, engine.Fresh, second, clock)
	if err == nil || !strings.Contains(err.Error(), "denied before the revoke") {
		t.Fatalf("err = %v", err)
	}
}

type slowWriter struct {
	inner engine.Writer
	clock *virtualclock.Clock
	delay int64
}

func (w slowWriter) Write(ctx context.Context, change engine.Change) (engine.Revision, error) {
	w.clock.WaitUntil(w.clock.Now() + w.delay)
	return w.inner.Write(ctx, change)
}

func TestTheWriteTimeIsMeasuredFromIssueToAcknowledgement(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{ExtraLatencyNs: checkCost}, clock)
	writer := slowWriter{inner: target, clock: clock, delay: 3 * millis}
	result, err := measure.RaceRevocation(context.Background(), writer, target, data.Revocation, engine.Fresh, second, clock)
	if err != nil || result.WriteNs != 3*millis {
		t.Fatalf("result = %+v, err = %v", result, err)
	}
}

func TestAKnownCheckLatencyIsMeasured(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{ExtraLatencyNs: 2 * millis}, clock)
	summary, err := measure.ServiceTime(context.Background(), target, queries(data), engine.Fresh, second, clock)
	if err != nil || !closeTo(summary.P50, 2*millis) || summary.Count != second/(2*millis) {
		t.Fatalf("summary = %+v, err = %v", summary, err)
	}
}

func TestTheOpenLoopCountsAStallThatTheClosedLoopHides(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	quickCheck := 10 * micros
	target := loaded(t, data, calibration.Profile{ExtraLatencyNs: quickCheck, StallPeriodNs: 150 * millis,
		StallDurationNs: 100 * millis}, clock)
	closed, err := measure.ServiceTime(context.Background(), target, queries(data), engine.Fresh, 300*millis, clock)
	if err != nil {
		t.Fatal(err)
	}
	opened, err := measure.OpenLoop(context.Background(), target, queries(data), engine.Fresh, 1_000, second, clock)
	if err != nil {
		t.Fatal(err)
	}
	if !closeTo(closed.P50, quickCheck) {
		t.Errorf("closed loop p50 = %d", closed.P50)
	}
	if opened.Latency.P50 < 20*millis || opened.Latency.P50 > 30*millis || opened.Latency.Count != 1_000 {
		t.Errorf("open loop latency = %+v", opened.Latency)
	}
}

func TestTheSystemClockMeasuresARealStalenessWithinTheStatedResolution(t *testing.T) {
	data, clock := tinyDataset(t), timing.NewSystemClock()
	target := loaded(t, data, calibration.Profile{StalenessNs: 500 * millis}, clock)
	result, err := measure.RaceRevocation(context.Background(), target, target, data.Revocation, engine.Fastest, 5*second, clock)
	if err != nil || result.StalenessNs == nil {
		t.Fatalf("result = %+v, err = %v", result, err)
	}
	staleness := *result.StalenessNs
	if staleness+result.WriteNs < 500*millis || staleness > 500*millis+result.ResolutionNs {
		t.Fatalf("staleness = %d, write = %d, resolution = %d", staleness, result.WriteNs, result.ResolutionNs)
	}
}
