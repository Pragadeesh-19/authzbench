package runner

import (
	"context"
	"fmt"
	"slices"
	"strings"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/engines/noop"
	"github.com/Pragadeesh-19/authzbench/go/measure"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

type Plan struct {
	ServiceTimeNs        int64
	OpenLoopRates        []int64
	OpenLoopNs           int64
	RevocationCapNs      int64
	RevocationTrials     int
	DelegationIntervalNs int64
	InFlight             int
	Placement            string
}

type ModeResult struct {
	Mode             engine.Mode
	ServiceTime      timing.Summary
	OpenLoop         []measure.OpenLoopResult
	Revocations      []measure.RevocationResult
	Delegations      []measure.DelegationResult
	RevocationTrials int
	Placement        string
}

type RunResult struct {
	Engine            string
	EngineVersion     string
	Dataset           *dataset.Dataset
	ConformanceChecks int
	DriverBaseline    []measure.OpenLoopResult
	Modes             []ModeResult
}

type ConformanceError struct {
	Mismatches []dataset.Expectation
}

func (e *ConformanceError) Error() string {
	shown := make([]string, 0, 5)
	for _, mismatch := range e.Mismatches[:min(5, len(e.Mismatches))] {
		shown = append(shown, mismatch.String())
	}
	return fmt.Sprintf("%d checks disagree with the reference evaluator, first: %s",
		len(e.Mismatches), strings.Join(shown, "; "))
}

// ConformanceExpectations are the dataset's checks, the nesting check, and every delegation leaf and the person
// allowed before any revoke.
func ConformanceExpectations(data *dataset.Dataset) []dataset.Expectation {
	delegation := data.Delegation
	expectations := make([]dataset.Expectation, 0, len(data.Expectations)+len(delegation.Probes)+2)
	expectations = append(append(expectations, data.Expectations...), data.Nesting)
	for _, query := range append(slices.Clone(delegation.Probes), delegation.Kept()) {
		expectations = append(expectations, dataset.Expectation{Query: query, Allowed: true})
	}
	return expectations
}

func VerifyConformance(ctx context.Context, checker engine.Checker, data *dataset.Dataset) (int, error) {
	expectations := ConformanceExpectations(data)
	var mismatches []dataset.Expectation
	for _, expectation := range expectations {
		allowed, err := checker.Check(ctx, expectation.Query, engine.Fresh, nil)
		if err != nil {
			return 0, err
		}
		if allowed != expectation.Allowed {
			mismatches = append(mismatches, expectation)
		}
	}
	if len(mismatches) > 0 {
		return 0, &ConformanceError{Mismatches: mismatches}
	}
	return len(expectations), nil
}

func Run(ctx context.Context, target engine.Engine, data *dataset.Dataset, plan Plan, clock timing.Clock) (RunResult, error) {
	if err := target.Load(ctx, data); err != nil {
		return RunResult{}, err
	}
	checks, err := VerifyConformance(ctx, target, data)
	if err != nil {
		return RunResult{}, err
	}
	queries := make([]dataset.Query, len(data.Expectations))
	for index, expectation := range data.Expectations {
		queries[index] = expectation.Query
	}
	baseline, err := openLoops(ctx, noop.Checker{}, queries, engine.Fresh, plan, clock)
	if err != nil {
		return RunResult{}, err
	}
	result := RunResult{Engine: target.Name(), EngineVersion: target.Version(), Dataset: data,
		ConformanceChecks: checks, DriverBaseline: baseline}
	for _, mode := range target.Modes() {
		measured, err := measureMode(ctx, target, data, queries, mode, plan, clock)
		if err != nil {
			return RunResult{}, fmt.Errorf("%s: %w", mode.Name, err)
		}
		result.Modes = append(result.Modes, measured)
	}
	return result, nil
}

func measureMode(ctx context.Context, target engine.Engine, data *dataset.Dataset, queries []dataset.Query,
	mode engine.Mode, plan Plan, clock timing.Clock) (ModeResult, error) {
	serviceTime, err := measure.ServiceTime(ctx, target, queries, mode.Consistency, plan.ServiceTimeNs, clock)
	if err != nil {
		return ModeResult{}, err
	}
	openLoop, err := openLoops(ctx, target, queries, mode.Consistency, plan, clock)
	if err != nil {
		return ModeResult{}, err
	}
	revocations, err := measure.RaceRevocations(ctx, target, target, data.Revocation, mode.Consistency,
		plan.RevocationCapNs, plan.RevocationTrials, clock)
	if err != nil {
		return ModeResult{}, err
	}
	delegations, err := measure.RaceDelegations(ctx, target, target, data.Delegation, mode.Consistency,
		plan.RevocationCapNs, plan.DelegationIntervalNs, plan.RevocationTrials, clock)
	if err != nil {
		return ModeResult{}, err
	}
	return ModeResult{Mode: mode, ServiceTime: serviceTime, OpenLoop: openLoop, Revocations: revocations,
		Delegations: delegations, RevocationTrials: plan.RevocationTrials, Placement: plan.Placement}, nil
}

func openLoops(ctx context.Context, checker engine.Checker, queries []dataset.Query, consistency engine.Consistency,
	plan Plan, clock timing.Clock) ([]measure.OpenLoopResult, error) {
	results := make([]measure.OpenLoopResult, 0, len(plan.OpenLoopRates))
	for _, rate := range plan.OpenLoopRates {
		result, err := measure.OpenLoop(ctx, checker, queries, consistency, rate, plan.OpenLoopNs, plan.InFlight, clock)
		if err != nil {
			return nil, err
		}
		results = append(results, result)
	}
	return results, nil
}
