package timing_test

import (
	"math"
	"testing"

	"github.com/Pragadeesh-19/authzbench/go/timing"
)

const readings = 1_000_000

func TestTheSystemClockNeverRunsBackwardsAndResolvesLessThanAMicrosecond(t *testing.T) {
	clock := timing.NewSystemClock()
	smallestStep := int64(math.MaxInt64)
	previous := clock.Now()
	for range readings {
		now := clock.Now()
		if now < previous {
			t.Fatalf("the clock went from %d to %d", previous, now)
		}
		if now > previous {
			smallestStep = min(smallestStep, now-previous)
		}
		previous = now
	}
	if smallestStep > 1_000 {
		t.Fatalf("smallest step = %d ns", smallestStep)
	}
}
