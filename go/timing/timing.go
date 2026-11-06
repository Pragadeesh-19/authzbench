package timing

import (
	"fmt"
	"time"

	"github.com/HdrHistogram/hdrhistogram-go"
)

const (
	NanosPerSecond         = int64(time.Second)
	spinBeforeDeadline     = int64(2 * time.Millisecond)
	lowestRecordable       = int64(1)
	highestRecordable      = 600 * NanosPerSecond
	significantValueDigits = 3
)

type Clock interface {
	Now() int64
	WaitUntil(deadline int64)
}

type SystemClock struct {
	origin int64
}

func NewSystemClock() *SystemClock {
	return &SystemClock{origin: monotonicNanos()}
}

func (c *SystemClock) Now() int64 {
	return monotonicNanos() - c.origin
}

func (c *SystemClock) WaitUntil(deadline int64) {
	for {
		remaining := deadline - c.Now()
		if remaining <= 0 {
			return
		}
		if remaining > spinBeforeDeadline {
			time.Sleep(time.Duration(remaining - spinBeforeDeadline))
		}
	}
}

type Summary struct {
	Count int64 `json:"count"`
	P50   int64 `json:"p50_ns"`
	P99   int64 `json:"p99_ns"`
	P999  int64 `json:"p999_ns"`
	Max   int64 `json:"max_ns"`
}

type Histogram struct {
	recorded *hdrhistogram.Histogram
}

func NewHistogram() *Histogram {
	return &Histogram{recorded: hdrhistogram.New(lowestRecordable, highestRecordable, significantValueDigits)}
}

func (h *Histogram) Record(nanoseconds int64) error {
	if nanoseconds < 0 || nanoseconds > highestRecordable {
		return fmt.Errorf("%d ns is outside the recordable range", nanoseconds)
	}
	return h.recorded.RecordValue(max(nanoseconds, lowestRecordable))
}

func (h *Histogram) Summary() Summary {
	return Summary{
		Count: h.recorded.TotalCount(),
		P50:   h.recorded.ValueAtQuantile(50),
		P99:   h.recorded.ValueAtQuantile(99),
		P999:  h.recorded.ValueAtQuantile(99.9),
		Max:   h.recorded.Max(),
	}
}
