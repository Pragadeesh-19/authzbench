package virtualclock

import "sync/atomic"

// Clock moves only when something waits, so timing logic can be checked against exact values. It is safe to share
// between goroutines; with more than one waiting, it moves to the latest deadline any of them asked for.
type Clock struct {
	now atomic.Int64
}

func (c *Clock) Now() int64 { return c.now.Load() }

func (c *Clock) WaitUntil(deadline int64) {
	for current := c.now.Load(); deadline > current && !c.now.CompareAndSwap(current, deadline); {
		current = c.now.Load()
	}
}
