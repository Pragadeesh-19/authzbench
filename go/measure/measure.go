package measure

import (
	"context"
	"errors"
	"fmt"
	"math"
	"slices"
	"sync"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

type OpenLoopResult struct {
	RatePerSecond int64          `json:"rate_per_second"`
	InFlight      int            `json:"in_flight"`
	Latency       timing.Summary `json:"latency"`
	ScheduleLag   timing.Summary `json:"schedule_lag"`
}

type RevocationResult struct {
	StalenessNs  *int64
	ResolutionNs int64
	DeniedByNs   *int64
	WriteNs      int64
	CapNs        int64
}

func (r RevocationResult) Capped() bool { return r.StalenessNs == nil }

// DelegationResult is one delegation race: per leaf, the time from the revoke's acknowledgement to the start of its
// first denying check, and to that check's return. Capped when some leaf was still allowed at the cap; AllowedAtCap
// says how many.
type DelegationResult struct {
	LeafStalenessNs []int64
	LeafDeniedByNs  []int64
	ResolutionNs    int64
	WriteNs         int64
	CapNs           int64
	AllowedAtCap    int
}

func (r DelegationResult) Capped() bool { return r.LeafStalenessNs == nil }

func (r DelegationResult) StalenessNs() *int64 { return maxOf(r.LeafStalenessNs) }

func (r DelegationResult) DeniedByNs() *int64 { return maxOf(r.LeafDeniedByNs) }

func maxOf(values []int64) *int64 {
	if values == nil {
		return nil
	}
	largest := slices.Max(values)
	return &largest
}

type TrialSpread struct {
	Count int   `json:"count"`
	P50   int64 `json:"p50_ns"`
	P99   int64 `json:"p99_ns"`
	Max   int64 `json:"max_ns"`
}

func SpreadOf(values []int64) *TrialSpread {
	if len(values) == 0 {
		return nil
	}
	ordered := slices.Clone(values)
	slices.Sort(ordered)
	return &TrialSpread{Count: len(ordered), P50: nearestRank(ordered, 0.50), P99: nearestRank(ordered, 0.99),
		Max: ordered[len(ordered)-1]}
}

func nearestRank(ordered []int64, quantile float64) int64 {
	return ordered[max(0, int(math.Ceil(quantile*float64(len(ordered))))-1)]
}

// RaceRevocations runs the race up to trials times; a capped trial ends the run, since no further trial can say more
// than "> cap" and its restore may take as long to appear.
func RaceRevocations(ctx context.Context, writer engine.Writer, checker engine.Checker, scenario dataset.Revocation,
	consistency engine.Consistency, capNs int64, trials int, clock timing.Clock) ([]RevocationResult, error) {
	return repeat(trials, func() (RevocationResult, error) {
		return RaceRevocation(ctx, writer, checker, scenario, consistency, capNs, clock)
	})
}

// RaceDelegations runs the delegation race up to trials times; a capped trial ends the run, as for revocations.
func RaceDelegations(ctx context.Context, writer engine.Writer, checker engine.Checker, scenario dataset.Delegation,
	consistency engine.Consistency, capNs int64, trials int, clock timing.Clock) ([]DelegationResult, error) {
	return repeat(trials, func() (DelegationResult, error) {
		return RaceDelegation(ctx, writer, checker, scenario, consistency, capNs, clock)
	})
}

func repeat[T interface{ Capped() bool }](trials int, race func() (T, error)) ([]T, error) {
	if trials < 1 {
		return nil, fmt.Errorf("a revocation needs at least one trial, not %d", trials)
	}
	results := make([]T, 0, trials)
	for range trials {
		result, err := race()
		if err != nil {
			return results, err
		}
		results = append(results, result)
		if result.Capped() {
			break
		}
	}
	return results, nil
}

func ServiceTime(ctx context.Context, checker engine.Checker, queries []dataset.Query, consistency engine.Consistency,
	duration int64, clock timing.Clock) (timing.Summary, error) {
	histogram := timing.NewHistogram()
	deadline := clock.Now() + duration
	for index := 0; ; index++ {
		started := clock.Now()
		if started >= deadline {
			return histogram.Summary(), nil
		}
		if _, err := checker.Check(ctx, queries[index%len(queries)], consistency, nil); err != nil {
			return timing.Summary{}, err
		}
		if err := histogram.Record(clock.Now() - started); err != nil {
			return timing.Summary{}, err
		}
	}
}

func OpenLoop(ctx context.Context, checker engine.Checker, queries []dataset.Query, consistency engine.Consistency,
	ratePerSecond, duration int64, inFlight int, clock timing.Clock) (OpenLoopResult, error) {
	if inFlight < 1 {
		return OpenLoopResult{}, fmt.Errorf("an open loop needs at least one check in flight, not %d", inFlight)
	}
	count := duration * ratePerSecond / timing.NanosPerSecond
	slots := make(chan struct{}, inFlight)
	samples := make(chan scheduledCheck, count)
	firstDue := clock.Now()
	for index := int64(0); index < count; index++ {
		slots <- struct{}{}
		due := firstDue + index*timing.NanosPerSecond/ratePerSecond
		clock.WaitUntil(due)
		query := queries[index%int64(len(queries))]
		go func() {
			started := clock.Now()
			_, err := checker.Check(ctx, query, consistency, nil)
			samples <- scheduledCheck{due: due, started: started, finished: clock.Now(), err: err}
			<-slots
		}()
	}
	for range inFlight {
		slots <- struct{}{}
	}
	close(samples)
	return summarizeOpenLoop(samples, ratePerSecond, inFlight)
}

type scheduledCheck struct {
	due      int64
	started  int64
	finished int64
	err      error
}

func summarizeOpenLoop(samples <-chan scheduledCheck, ratePerSecond int64, inFlight int) (OpenLoopResult, error) {
	latency, lag := timing.NewHistogram(), timing.NewHistogram()
	for sample := range samples {
		if sample.err != nil {
			return OpenLoopResult{}, sample.err
		}
		if err := latency.Record(sample.finished - sample.due); err != nil {
			return OpenLoopResult{}, err
		}
		if err := lag.Record(sample.started - sample.due); err != nil {
			return OpenLoopResult{}, err
		}
	}
	return OpenLoopResult{RatePerSecond: ratePerSecond, InFlight: inFlight, Latency: latency.Summary(),
		ScheduleLag: lag.Summary()}, nil
}

func RaceRevocation(ctx context.Context, writer engine.Writer, checker engine.Checker, scenario dataset.Revocation,
	consistency engine.Consistency, capNs int64, clock timing.Clock) (result RevocationResult, err error) {
	if err := awaitGrant(ctx, checker, scenario.Probe, consistency, capNs, clock); err != nil {
		return result, err
	}
	issued := clock.Now()
	revision, err := writer.Write(ctx, engine.Change{Operation: engine.Remove, Relationship: scenario.Revoke})
	if err != nil {
		return result, err
	}
	acknowledged := clock.Now()
	defer func() {
		if _, restoreErr := writer.Write(ctx, engine.Change{Operation: engine.Add, Relationship: scenario.Revoke}); err == nil {
			err = restoreErr
		}
	}()
	return awaitDenial(ctx, checker, scenario.Probe, consistency, revision, RevocationResult{
		WriteNs: acknowledged - issued,
		CapNs:   capNs,
	}, acknowledged, clock)
}

// RaceDelegation revokes the person's delegation and times, for every leaf agent below it, the first check that
// denies. Each leaf is checked by its own goroutine, as each agent checks for itself, so a leaf's staleness is not
// charged the checks of the leaves before it. The race starts once every leaf is allowed, waiting at most the cap in
// all; the resolution is that of the last leaf to be denied.
func RaceDelegation(ctx context.Context, writer engine.Writer, checker engine.Checker, scenario dataset.Delegation,
	consistency engine.Consistency, capNs int64, clock timing.Clock) (result DelegationResult, err error) {
	deadline := clock.Now() + capNs
	for _, probe := range scenario.Probes {
		if err := awaitGrant(ctx, checker, probe, consistency, deadline-clock.Now(), clock); err != nil {
			return result, err
		}
	}
	issued := clock.Now()
	revision, err := writer.Write(ctx, engine.Change{Operation: engine.Remove, Relationship: scenario.Revoke})
	if err != nil {
		return result, err
	}
	acknowledged := clock.Now()
	defer func() {
		if _, restoreErr := writer.Write(ctx, engine.Change{Operation: engine.Add, Relationship: scenario.Revoke}); err == nil {
			err = restoreErr
		}
	}()
	leaves := make([]RevocationResult, len(scenario.Probes))
	failures := make([]error, len(scenario.Probes))
	var group sync.WaitGroup
	for index, probe := range scenario.Probes {
		group.Add(1)
		go func() {
			defer group.Done()
			leaves[index], failures[index] = awaitDenial(ctx, checker, probe, consistency, revision,
				RevocationResult{CapNs: capNs}, acknowledged, clock)
		}()
	}
	group.Wait()
	if err := errors.Join(failures...); err != nil {
		return result, err
	}
	return summarizeLeaves(leaves, acknowledged-issued, capNs), nil
}

func summarizeLeaves(leaves []RevocationResult, writeNs, capNs int64) DelegationResult {
	result := DelegationResult{WriteNs: writeNs, CapNs: capNs}
	staleness, deniedBy := make([]int64, len(leaves)), make([]int64, len(leaves))
	last := 0
	for index, leaf := range leaves {
		if leaf.Capped() {
			result.AllowedAtCap++
			result.ResolutionNs = max(result.ResolutionNs, leaf.ResolutionNs)
			continue
		}
		staleness[index], deniedBy[index] = *leaf.StalenessNs, *leaf.DeniedByNs
		if staleness[index] > staleness[last] {
			last = index
		}
	}
	if result.AllowedAtCap > 0 {
		return result
	}
	result.LeafStalenessNs, result.LeafDeniedByNs, result.ResolutionNs = staleness, deniedBy, leaves[last].ResolutionNs
	return result
}

func awaitGrant(ctx context.Context, checker engine.Checker, probe dataset.Query, consistency engine.Consistency,
	capNs int64, clock timing.Clock) error {
	deadline := clock.Now() + capNs
	for {
		allowed, err := checker.Check(ctx, probe, consistency, nil)
		if err != nil || allowed {
			return err
		}
		if clock.Now() > deadline {
			return fmt.Errorf("%s is denied before the revoke for longer than the cap", probe)
		}
	}
}

func awaitDenial(ctx context.Context, checker engine.Checker, probe dataset.Query, consistency engine.Consistency,
	revision engine.Revision, result RevocationResult, acknowledged int64, clock timing.Clock) (RevocationResult, error) {
	previousStart := acknowledged
	for {
		started := clock.Now()
		result.ResolutionNs = started - previousStart
		if started-acknowledged > result.CapNs {
			return result, nil
		}
		allowed, err := checker.Check(ctx, probe, consistency, revision)
		if err != nil {
			return result, err
		}
		if !allowed {
			staleness, deniedBy := started-acknowledged, clock.Now()-acknowledged
			result.StalenessNs, result.DeniedByNs = &staleness, &deniedBy
			return result, nil
		}
		previousStart = started
	}
}
