package calibration

import (
	"context"
	"fmt"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

type Profile struct {
	StalenessNs     int64
	ExtraLatencyNs  int64
	StallPeriodNs   int64
	StallDurationNs int64
}

type pendingChange struct {
	visibleAt int64
	change    engine.Change
}

// Engine answers from a dataset's expected answers; the revocation probe is allowed exactly while the revoke
// relationship is visible. Writes reach fastest-mode checks after Profile.StalenessNs, every check takes at least
// Profile.ExtraLatencyNs, and the engine stops answering for StallDurationNs once every StallPeriodNs.
type Engine struct {
	profile  Profile
	clock    timing.Clock
	started  int64
	answers  map[dataset.Query]bool
	scenario dataset.Revocation
	revoked  bool
	pending  []pendingChange
	revision int64
}

func New(profile Profile, clock timing.Clock) *Engine {
	return &Engine{profile: profile, clock: clock, started: clock.Now()}
}

func (e *Engine) Name() string    { return "calibration" }
func (e *Engine) Version() string { return "1" }

func (e *Engine) Modes() []engine.Mode {
	return []engine.Mode{{Consistency: engine.Fresh, Name: "fresh"}, {Consistency: engine.Fastest, Name: "stale"}}
}

func (e *Engine) Load(_ context.Context, data *dataset.Dataset) error {
	e.answers = make(map[dataset.Query]bool, len(data.Expectations)+1)
	for _, expectation := range data.Expectations {
		e.answers[expectation.Query] = expectation.Allowed
	}
	e.answers[data.Nesting.Query] = data.Nesting.Allowed
	e.scenario = data.Revocation
	return nil
}

func (e *Engine) Write(_ context.Context, change engine.Change) (engine.Revision, error) {
	if change.Relationship != e.scenario.Revoke {
		return nil, fmt.Errorf("the calibration engine only writes %s", e.scenario.Revoke)
	}
	e.pending = append(e.pending, pendingChange{visibleAt: e.clock.Now() + e.profile.StalenessNs, change: change})
	e.revision++
	return e.revision, nil
}

func (e *Engine) Check(_ context.Context, query dataset.Query, consistency engine.Consistency, _ engine.Revision) (bool, error) {
	started := e.clock.Now()
	e.waitOutStall(started)
	if consistency == engine.Fresh {
		e.applyPending(func(pendingChange) bool { return true })
	} else {
		now := e.clock.Now()
		e.applyPending(func(next pendingChange) bool { return next.visibleAt <= now })
	}
	allowed, err := e.answer(query)
	e.clock.WaitUntil(started + e.profile.ExtraLatencyNs)
	return allowed, err
}

func (e *Engine) Close() error {
	e.pending = nil
	return nil
}

func (e *Engine) answer(query dataset.Query) (bool, error) {
	if query == e.scenario.Probe {
		return !e.revoked, nil
	}
	allowed, known := e.answers[query]
	if !known {
		return false, fmt.Errorf("the calibration engine has no answer for %s", query)
	}
	return allowed, nil
}

func (e *Engine) waitOutStall(now int64) {
	if e.profile.StallPeriodNs == 0 {
		return
	}
	phase := (now - e.started) % e.profile.StallPeriodNs
	if phase < e.profile.StallDurationNs {
		e.clock.WaitUntil(now + e.profile.StallDurationNs - phase)
	}
}

func (e *Engine) applyPending(visible func(pendingChange) bool) {
	for len(e.pending) > 0 && visible(e.pending[0]) {
		e.revoked = e.pending[0].change.Operation == engine.Remove
		e.pending = e.pending[1:]
	}
}
