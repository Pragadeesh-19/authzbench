package engine

import (
	"context"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
)

type Consistency string

const (
	Fresh   Consistency = "fresh"
	Fastest Consistency = "fastest"
)

type Revision any

type Operation int

const (
	Add Operation = iota
	Remove
)

type Change struct {
	Operation    Operation
	Relationship dataset.Relationship
}

type Mode struct {
	Consistency Consistency
	Name        string
}

type Writer interface {
	Write(ctx context.Context, change Change) (Revision, error)
}

type Checker interface {
	Check(ctx context.Context, query dataset.Query, consistency Consistency, after Revision) (bool, error)
}

type Engine interface {
	Writer
	Checker
	Name() string
	Version() string
	Modes() []Mode
	Load(ctx context.Context, data *dataset.Dataset) error
	Close() error
}
