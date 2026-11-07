package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"github.com/Pragadeesh-19/authzbench/go/dataset"
	"github.com/Pragadeesh-19/authzbench/go/engine"
	"github.com/Pragadeesh-19/authzbench/go/engines/calibration"
	"github.com/Pragadeesh-19/authzbench/go/engines/openfga"
	"github.com/Pragadeesh-19/authzbench/go/engines/spicedb"
	"github.com/Pragadeesh-19/authzbench/go/results"
	"github.com/Pragadeesh-19/authzbench/go/runner"
	"github.com/Pragadeesh-19/authzbench/go/timing"
)

const millis = int64(1_000_000)

var (
	demonstrationProfile = calibration.Profile{StalenessNs: 500 * millis, ExtraLatencyNs: 2 * millis}
	placements           = []string{"same_machine", "second_machine"}
	engines              = map[string]func(options, timing.Clock) (engine.Engine, error){
		"calibration": func(_ options, clock timing.Clock) (engine.Engine, error) {
			return calibration.New(demonstrationProfile, clock), nil
		},
		"spicedb": func(chosen options, _ timing.Clock) (engine.Engine, error) {
			schema, err := os.ReadFile(filepath.Join(chosen.schemas, "model.zed"))
			if err != nil {
				return nil, err
			}
			return spicedb.Dial(chosen.spicedbEndpoint, chosen.spicedbKey, string(schema))
		},
		"openfga": func(chosen options, _ timing.Clock) (engine.Engine, error) {
			if chosen.openfgaVersion == "" {
				return nil, errors.New("-openfga-version is required: OpenFGA does not report its version")
			}
			model, err := os.ReadFile(filepath.Join(chosen.schemas, "model.fga"))
			if err != nil {
				return nil, err
			}
			return openfga.Dial(chosen.openfgaURL, string(model), chosen.openfgaVersion, chosen.inFlight)
		},
	}
)

type options struct {
	engine               string
	dataset              string
	out                  string
	schemas              string
	placement            string
	rates                string
	inFlight             int
	serviceSeconds       float64
	openLoopSeconds      float64
	revocationCapSeconds float64
	revocationTrials     int
	spicedbEndpoint      string
	spicedbKey           string
	openfgaURL           string
	openfgaVersion       string
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	if err := run(ctx, os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "authzbench-go:", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, arguments []string) (err error) {
	chosen, err := parse(arguments)
	if err != nil {
		return err
	}
	plan, err := planFrom(chosen)
	if err != nil {
		return err
	}
	data, err := dataset.Read(chosen.dataset)
	if err != nil {
		return err
	}
	clock := timing.NewSystemClock()
	target, err := engines[chosen.engine](chosen, clock)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, target.Close()) }()
	result, err := runner.Run(ctx, target, data, plan, clock)
	if err != nil {
		return err
	}
	return results.Write(results.FromRun(result, results.CurrentEnvironment()), chosen.out)
}

func parse(arguments []string) (options, error) {
	var chosen options
	flags := flag.NewFlagSet("authzbench-go", flag.ContinueOnError)
	flags.StringVar(&chosen.engine, "engine", "", "engine to measure: "+strings.Join(engineNames(), ", "))
	flags.StringVar(&chosen.dataset, "dataset", "", "directory written by `authzbench generate`")
	flags.StringVar(&chosen.out, "out", "", "results document to write")
	flags.StringVar(&chosen.schemas, "schemas", "generated", "directory written by `authzbench emit`")
	flags.StringVar(&chosen.placement, "placement", "same_machine", "where the driver runs: "+strings.Join(placements, ", "))
	flags.StringVar(&chosen.rates, "rates", "1000,10000", "open-loop rates in checks per second")
	flags.IntVar(&chosen.inFlight, "in-flight", 256, "most open-loop checks outstanding at once")
	flags.Float64Var(&chosen.serviceSeconds, "service-seconds", 5, "closed-loop duration per mode")
	flags.Float64Var(&chosen.openLoopSeconds, "open-loop-seconds", 10, "open-loop duration per rate")
	flags.Float64Var(&chosen.revocationCapSeconds, "revocation-cap-seconds", 30, "longest wait for a revoke")
	flags.IntVar(&chosen.revocationTrials, "revocation-trials", 30, "revocation races per mode")
	flags.StringVar(&chosen.spicedbEndpoint, "spicedb-endpoint", "localhost:50051", "SpiceDB gRPC address")
	flags.StringVar(&chosen.spicedbKey, "spicedb-key", "authzbench", "SpiceDB preshared key")
	flags.StringVar(&chosen.openfgaURL, "openfga-url", "http://localhost:8080", "OpenFGA HTTP address")
	flags.StringVar(&chosen.openfgaVersion, "openfga-version", "", "OpenFGA version, as pinned in services/compose.yaml")
	if err := flags.Parse(arguments); err != nil {
		return chosen, err
	}
	if _, known := engines[chosen.engine]; !known {
		return chosen, fmt.Errorf("-engine must be one of %s", strings.Join(engineNames(), ", "))
	}
	if chosen.dataset == "" || chosen.out == "" {
		return chosen, errors.New("-dataset and -out are required")
	}
	if !slices.Contains(placements, chosen.placement) {
		return chosen, fmt.Errorf("-placement must be one of %s", strings.Join(placements, ", "))
	}
	return chosen, nil
}

func planFrom(chosen options) (runner.Plan, error) {
	var rates []int64
	for _, text := range strings.Split(chosen.rates, ",") {
		rate, err := strconv.ParseInt(text, 10, 64)
		if err != nil || rate <= 0 {
			return runner.Plan{}, fmt.Errorf("-rates: %q is not a positive whole number", text)
		}
		rates = append(rates, rate)
	}
	return runner.Plan{
		ServiceTimeNs:    nanoseconds(chosen.serviceSeconds),
		OpenLoopRates:    rates,
		OpenLoopNs:       nanoseconds(chosen.openLoopSeconds),
		RevocationCapNs:  nanoseconds(chosen.revocationCapSeconds),
		RevocationTrials: chosen.revocationTrials,
		InFlight:         chosen.inFlight,
		Placement:        chosen.placement,
	}, nil
}

func nanoseconds(seconds float64) int64 {
	return int64(seconds * float64(timing.NanosPerSecond))
}

func engineNames() []string {
	names := make([]string, 0, len(engines))
	for name := range engines {
		names = append(names, name)
	}
	slices.Sort(names)
	return names
}
