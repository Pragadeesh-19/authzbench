package results

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/measure"
	"github.com/Pragadeesh-19/authzbench/go/runner"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

const Format = "authzbench.results/1"

type Document struct {
	Format         string                   `json:"format"`
	Engine         EngineInfo               `json:"engine"`
	Dataset        DatasetInfo              `json:"dataset"`
	Environment    Environment              `json:"environment"`
	Conformance    Conformance              `json:"conformance"`
	DriverBaseline []measure.OpenLoopResult `json:"driver_baseline"`
	Modes          []Mode                   `json:"modes"`
}

type EngineInfo struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

type DatasetInfo struct {
	Model         string `json:"model"`
	Scale         string `json:"scale"`
	Seed          int64  `json:"seed"`
	Relationships int    `json:"relationships"`
	Checks        int    `json:"checks"`
}

type Environment struct {
	RecordedAt string `json:"recorded_at"`
	Go         string `json:"go"`
	Platform   string `json:"platform"`
	CPUs       int    `json:"cpus"`
}

type Conformance struct {
	Checks     int `json:"checks"`
	Mismatches int `json:"mismatches"`
}

type Mode struct {
	Consistency string                   `json:"consistency"`
	Mode        string                   `json:"mode"`
	ServiceTime timing.Summary           `json:"service_time"`
	OpenLoop    []measure.OpenLoopResult `json:"open_loop"`
	Revocation  Revocation               `json:"revocation"`
}

type Revocation struct {
	Placement    string `json:"placement"`
	StalenessNs  *int64 `json:"staleness_ns"`
	ResolutionNs int64  `json:"resolution_ns"`
	DeniedByNs   *int64 `json:"denied_by_ns"`
	WriteNs      int64  `json:"write_ns"`
	CapNs        int64  `json:"cap_ns"`
}

func CurrentEnvironment() Environment {
	return Environment{
		RecordedAt: time.Now().UTC().Format(time.RFC3339),
		Go:         runtime.Version(),
		Platform:   runtime.GOOS + "/" + runtime.GOARCH,
		CPUs:       runtime.NumCPU(),
	}
}

func FromRun(result runner.RunResult, environment Environment) Document {
	document := Document{
		Format:         Format,
		Engine:         EngineInfo{Name: result.Engine, Version: result.EngineVersion},
		Dataset:        describe(result.Dataset),
		Environment:    environment,
		Conformance:    Conformance{Checks: result.ConformanceChecks},
		DriverBaseline: result.DriverBaseline,
	}
	for _, mode := range result.Modes {
		document.Modes = append(document.Modes, Mode{
			Consistency: string(mode.Mode.Consistency),
			Mode:        mode.Mode.Name,
			ServiceTime: mode.ServiceTime,
			OpenLoop:    mode.OpenLoop,
			Revocation: Revocation{
				Placement:    mode.Placement,
				StalenessNs:  mode.Revocation.StalenessNs,
				ResolutionNs: mode.Revocation.ResolutionNs,
				DeniedByNs:   mode.Revocation.DeniedByNs,
				WriteNs:      mode.Revocation.WriteNs,
				CapNs:        mode.Revocation.CapNs,
			},
		})
	}
	return document
}

func describe(data *dataset.Dataset) DatasetInfo {
	return DatasetInfo{
		Model:         data.Model,
		Scale:         data.Scale,
		Seed:          data.Seed,
		Relationships: len(data.Relationships),
		Checks:        len(data.Expectations),
	}
}

func Write(document Document, path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	content, err := json.MarshalIndent(document, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(content, '\n'), 0o644)
}
