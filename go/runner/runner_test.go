package runner_test

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/runner"
)

type answers map[dataset.Query]bool

func (a answers) Check(_ context.Context, query dataset.Query, _ engine.Consistency, _ engine.Revision) (bool, error) {
	return a[query], nil
}

func tiny(t *testing.T) *dataset.Dataset {
	t.Helper()
	data, err := dataset.Read(filepath.Join("..", "dataset", "testdata", "tiny"))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func TestAnEngineThatAgreesWithEveryAnswerPasses(t *testing.T) {
	data := tiny(t)
	agreeing := answers{data.Nesting.Query: data.Nesting.Allowed}
	for _, expectation := range data.Expectations {
		agreeing[expectation.Query] = expectation.Allowed
	}
	checks, err := runner.VerifyConformance(context.Background(), agreeing, data)
	if err != nil || checks != len(data.Expectations)+1 {
		t.Fatalf("checks = %d, err = %v", checks, err)
	}
}

func TestAnEngineThatDisagreesIsRefusedBeforeTiming(t *testing.T) {
	data := tiny(t)
	_, err := runner.VerifyConformance(context.Background(), answers{}, data)
	var refused *runner.ConformanceError
	if !errors.As(err, &refused) {
		t.Fatalf("err = %v", err)
	}
	allowed := 1
	for _, expectation := range data.Expectations {
		if expectation.Allowed {
			allowed++
		}
	}
	if len(refused.Mismatches) != allowed {
		t.Fatalf("mismatches = %v", refused.Mismatches)
	}
}
