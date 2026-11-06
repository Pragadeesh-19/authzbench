package measure

import (
	"context"
	"fmt"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

type OpenLoopResult struct {
	RatePerSecond int64          `json:"rate_per_second"`
	Latency       timing.Summary `json:"latency"`
	ScheduleLag   timing.Summary `json:"schedule_lag"`
}

type RevocationResult struct {
	StalenessNs  *int64
	ResolutionNs int64
	WriteNs      int64
	CapNs        int64
}

func (r RevocationResult) Capped() bool { return r.StalenessNs == nil }

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
	ratePerSecond, duration int64, clock timing.Clock) (OpenLoopResult, error) {
	latency, lag := timing.NewHistogram(), timing.NewHistogram()
	count := duration * ratePerSecond / timing.NanosPerSecond
	firstDue := clock.Now()
	for index := int64(0); index < count; index++ {
		due := firstDue + index*timing.NanosPerSecond/ratePerSecond
		clock.WaitUntil(due)
		started := clock.Now()
		if _, err := checker.Check(ctx, queries[index%int64(len(queries))], consistency, nil); err != nil {
			return OpenLoopResult{}, err
		}
		if err := latency.Record(clock.Now() - due); err != nil {
			return OpenLoopResult{}, err
		}
		if err := lag.Record(started - due); err != nil {
			return OpenLoopResult{}, err
		}
	}
	return OpenLoopResult{RatePerSecond: ratePerSecond, Latency: latency.Summary(), ScheduleLag: lag.Summary()}, nil
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
			staleness := started - acknowledged
			result.StalenessNs = &staleness
			return result, nil
		}
		previousStart = started
	}
}
