package calibration

import (
	"context"
	"fmt"
	"maps"
	"slices"
	"sync"

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

// Engine answers from a dataset's expected answers; the revocation probe is allowed exactly while its revoke
// relationship is visible, every delegation leaf exactly while the person's delegation is, and the person always. Writes reach fastest-mode checks after Profile.StalenessNs, every check takes at least
// Profile.ExtraLatencyNs, and the engine stops answering for StallDurationNs once every StallPeriodNs. Checks may run
// concurrently; each spends its latency outside the lock.
type Engine struct {
	profile  Profile
	clock    timing.Clock
	started  int64
	answers  map[dataset.Query]bool
	revokes  map[dataset.Query]dataset.Relationship
	state    sync.Mutex
	revoked  map[dataset.Relationship]bool
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
	e.answers[data.Delegation.Kept()] = true
	e.revokes = map[dataset.Query]dataset.Relationship{data.Revocation.Probe: data.Revocation.Revoke}
	for _, probe := range data.Delegation.Probes {
		e.revokes[probe] = data.Delegation.Revoke
	}
	e.revoked = map[dataset.Relationship]bool{}
	return nil
}

func (e *Engine) Write(_ context.Context, change engine.Change) (engine.Revision, error) {
	if !slices.Contains(slices.Collect(maps.Values(e.revokes)), change.Relationship) {
		return nil, fmt.Errorf("the calibration engine only writes its scenarios' revokes, not %s", change.Relationship)
	}
	e.state.Lock()
	defer e.state.Unlock()
	e.pending = append(e.pending, pendingChange{visibleAt: e.clock.Now() + e.profile.StalenessNs, change: change})
	e.revision++
	return e.revision, nil
}

func (e *Engine) Check(_ context.Context, query dataset.Query, consistency engine.Consistency, _ engine.Revision) (bool, error) {
	started := e.clock.Now()
	e.waitOutStall(started)
	allowed, err := e.answer(query, consistency)
	e.clock.WaitUntil(started + e.profile.ExtraLatencyNs)
	return allowed, err
}

func (e *Engine) Close() error {
	e.state.Lock()
	defer e.state.Unlock()
	e.pending = nil
	return nil
}

func (e *Engine) answer(query dataset.Query, consistency engine.Consistency) (bool, error) {
	e.state.Lock()
	defer e.state.Unlock()
	if consistency == engine.Fresh {
		e.applyPending(func(pendingChange) bool { return true })
	} else {
		now := e.clock.Now()
		e.applyPending(func(next pendingChange) bool { return next.visibleAt <= now })
	}
	if revoke, probed := e.revokes[query]; probed {
		return !e.revoked[revoke], nil
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
		change := e.pending[0].change
		e.revoked[change.Relationship] = change.Operation == engine.Remove
		e.pending = e.pending[1:]
	}
}
