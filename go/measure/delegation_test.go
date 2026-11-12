package measure_test

import (
	"context"
	"fmt"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/engines/calibration"
	"github.com/Pragadeesh-19/authzbench/go/internal/virtualclock"
	"github.com/Pragadeesh-19/authzbench/go/measure"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

func delegationRace(t *testing.T, profile calibration.Profile, consistency engine.Consistency, capNs int64,
	clock timing.Clock) measure.DelegationResult {
	t.Helper()
	data := tinyDataset(t)
	target := loaded(t, data, profile, clock)
	result, err := measure.RaceDelegation(context.Background(), target, target, data.Delegation, consistency, capNs, clock)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestAFreshCheckDeniesEveryLeafOnItsFirstCheck(t *testing.T) {
	result := delegationRace(t, calibration.Profile{StalenessNs: 500 * millis, ExtraLatencyNs: checkCost},
		engine.Fresh, 5*second, &virtualclock.Clock{})
	if result.Capped() || len(result.LeafStalenessNs) != 2 {
		t.Fatalf("result = %+v", result)
	}
	for leaf, staleness := range result.LeafStalenessNs {
		if staleness >= 2*checkCost || result.LeafDeniedByNs[leaf] != staleness+checkCost {
			t.Fatalf("leaf %d: staleness = %d, denied by = %d", leaf, staleness, result.LeafDeniedByNs[leaf])
		}
	}
	if *result.StalenessNs() != result.ResolutionNs {
		t.Fatalf("the last leaf's first check after the revoke allowed: %+v", result)
	}
}

func TestEveryLeafIsDeniedOnceTheRevokeIsVisible(t *testing.T) {
	clock := timing.NewSystemClock()
	started := clock.Now()
	result := delegationRace(t, calibration.Profile{StalenessNs: 200 * millis}, engine.Fastest, 5*second, clock)
	if result.Capped() {
		t.Fatalf("result = %+v", result)
	}
	if elapsed := clock.Now() - started; elapsed < 200*millis {
		t.Fatalf("the race saw the revoke %d ns after it began, before the engine made it visible", elapsed)
	}
	for leaf, staleness := range result.LeafStalenessNs {
		if staleness > 200*millis+50*millis || result.LeafDeniedByNs[leaf] < staleness {
			t.Fatalf("leaf %d: staleness = %d, denied by = %d", leaf, staleness, result.LeafDeniedByNs[leaf])
		}
	}
}

func TestATreeStillAllowedAtTheCapCountsEveryLeaf(t *testing.T) {
	result := delegationRace(t, calibration.Profile{StalenessNs: 150 * millis, ExtraLatencyNs: checkCost},
		engine.Fastest, 100*millis, &virtualclock.Clock{})
	if !result.Capped() || result.AllowedAtCap != 2 || result.StalenessNs() != nil || result.DeniedByNs() != nil {
		t.Fatalf("result = %+v", result)
	}
	if result.ResolutionNs < checkCost || result.ResolutionNs > 100*millis {
		t.Fatalf("resolution = %d", result.ResolutionNs)
	}
}

func TestACappedDelegationTrialEndsTheTrials(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{StalenessNs: 300 * millis, ExtraLatencyNs: checkCost}, clock)
	trials, err := measure.RaceDelegations(context.Background(), target, target, data.Delegation, engine.Fastest,
		100*millis, 30, clock)
	if err != nil || len(trials) != 1 || !trials[0].Capped() {
		t.Fatalf("trials = %+v, err = %v", trials, err)
	}
}

func TestTheDelegationRaceRestoresTheDelegation(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{ExtraLatencyNs: checkCost}, clock)
	if _, err := measure.RaceDelegation(context.Background(), target, target, data.Delegation, engine.Fresh, second,
		clock); err != nil {
		t.Fatal(err)
	}
	for _, probe := range data.Delegation.Probes {
		if allowed, err := target.Check(context.Background(), probe, engine.Fresh, nil); err != nil || !allowed {
			t.Fatalf("%s after the race: %v, %v", probe, allowed, err)
		}
	}
}

func TestALeafDeniedBeforeTheRevokeIsRefused(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{ExtraLatencyNs: checkCost}, clock)
	if _, err := target.Write(context.Background(), engine.Change{Operation: engine.Remove,
		Relationship: data.Delegation.Revoke}); err != nil {
		t.Fatal(err)
	}
	_, err := measure.RaceDelegation(context.Background(), target, target, data.Delegation, engine.Fresh, second, clock)
	if err == nil || !strings.Contains(err.Error(), "denied before the revoke") {
		t.Fatalf("err = %v", err)
	}
}

type meetsTheOtherLeaves struct {
	inner   engine.Checker
	leaves  int64
	waiting atomic.Int64
}

func (m *meetsTheOtherLeaves) Check(ctx context.Context, query dataset.Query, consistency engine.Consistency,
	after engine.Revision) (bool, error) {
	if after == nil {
		return m.inner.Check(ctx, query, consistency, after)
	}
	m.waiting.Add(1)
	for deadline := time.Now().Add(5 * time.Second); m.waiting.Load() < m.leaves; {
		if time.Now().After(deadline) {
			return false, fmt.Errorf("%s was checked alone; the other leaves never were at the same time", query)
		}
		time.Sleep(time.Millisecond)
	}
	return m.inner.Check(ctx, query, consistency, after)
}

func TestEveryLeafIsCheckedAtTheSameTime(t *testing.T) {
	data, clock := tinyDataset(t), timing.NewSystemClock()
	target := loaded(t, data, calibration.Profile{}, clock)
	checker := &meetsTheOtherLeaves{inner: target, leaves: int64(len(data.Delegation.Probes))}
	result, err := measure.RaceDelegation(context.Background(), target, checker, data.Delegation, engine.Fresh,
		10*second, clock)
	if err != nil || result.Capped() {
		t.Fatalf("result = %+v, err = %v", result, err)
	}
}

// slowToDeny allows its leaf for three more checks of 10 ms each, starting only once every other leaf has had its
// first answer, so the shared virtual clock moves only for this leaf.
type slowToDeny struct {
	inner    engine.Checker
	clock    *virtualclock.Clock
	leaf     dataset.Query
	allowed  atomic.Int64
	answered chan struct{}
}

func (s *slowToDeny) Check(ctx context.Context, query dataset.Query, consistency engine.Consistency,
	after engine.Revision) (bool, error) {
	if after == nil {
		return s.inner.Check(ctx, query, consistency, after)
	}
	if query != s.leaf {
		defer func() { s.answered <- struct{}{} }()
		return s.inner.Check(ctx, query, consistency, after)
	}
	if s.allowed.Load() == 0 {
		select {
		case <-s.answered:
		case <-time.After(5 * time.Second):
			return false, fmt.Errorf("the other leaf was never checked")
		}
	}
	started := s.clock.Now()
	defer s.clock.WaitUntil(started + 10*millis)
	if s.allowed.Add(1) <= 3 {
		return true, nil
	}
	return s.inner.Check(ctx, query, consistency, after)
}

func TestTheResolutionIsThatOfTheLastLeafDenied(t *testing.T) {
	data, clock := tinyDataset(t), &virtualclock.Clock{}
	target := loaded(t, data, calibration.Profile{}, clock)
	checker := &slowToDeny{inner: target, clock: clock, leaf: data.Delegation.Probes[1], answered: make(chan struct{}, 1)}
	result, err := measure.RaceDelegation(context.Background(), target, checker, data.Delegation, engine.Fresh, second,
		clock)
	if err != nil || result.Capped() {
		t.Fatalf("result = %+v, err = %v", result, err)
	}
	if *result.StalenessNs() != 30*millis || result.ResolutionNs != 10*millis || result.LeafStalenessNs[0] != 0 {
		t.Fatalf("result = %+v", result)
	}
}
