//go:build !windows

package timing

import "time"

var processStart = time.Now()

func monotonicNanos() int64 {
	return int64(time.Since(processStart))
}
