package timing

import (
	"fmt"
	"syscall"
	"unsafe"
)

var (
	kernel32                = syscall.NewLazyDLL("kernel32.dll")
	queryPerformanceCounter = kernel32.NewProc("QueryPerformanceCounter")
	performanceFrequency    = readPerformanceFrequency()
)

func readPerformanceFrequency() int64 {
	var frequency int64
	succeeded, _, err := syscall.SyscallN(kernel32.NewProc("QueryPerformanceFrequency").Addr(),
		uintptr(unsafe.Pointer(&frequency)))
	if succeeded == 0 || frequency <= 0 {
		panic(fmt.Sprintf("QueryPerformanceFrequency: %v", err))
	}
	return frequency
}

func monotonicNanos() int64 {
	var counter int64
	syscall.SyscallN(queryPerformanceCounter.Addr(), uintptr(unsafe.Pointer(&counter)))
	whole, fraction := counter/performanceFrequency, counter%performanceFrequency
	return whole*NanosPerSecond + fraction*NanosPerSecond/performanceFrequency
}
