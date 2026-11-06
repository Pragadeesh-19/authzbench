package virtualclock

// Clock moves only when something waits, so timing logic can be checked against exact values.
type Clock struct {
	now int64
}

func (c *Clock) Now() int64 { return c.now }

func (c *Clock) WaitUntil(deadline int64) { c.now = max(c.now, deadline) }
