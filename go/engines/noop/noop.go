package noop

import (
	"context"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
)

// Checker answers instantly, so an open loop against it measures the driver alone.
type Checker struct{}

func (Checker) Check(context.Context, dataset.Query, engine.Consistency, engine.Revision) (bool, error) {
	return true, nil
}
