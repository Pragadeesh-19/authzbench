# AuthzBench

A neutral, reproducible benchmark for authorization engines that answer Zanzibar-style permission checks.

It measures three things that matter when access is revoked:

- **Check latency when the answer must be fully up to date**, issued on an open-loop schedule so that slow stretches
  are counted, not skipped, and a closed-loop "service time" beside it.
- **Time to revocation**: how long a removed permission keeps being allowed after the write that removed it was
  acknowledged, as a distribution over 30 races per mode.
- **Time to revoke a delegation**: a person delegates to an agent, which delegates down a tree of 40 agents. The race
  removes the person's one delegation and times how long until every one of the 27 leaf agents is denied. Each leaf is
  checked again no sooner than 50 ms after its previous check, so the tree stays below 540 checks a second. Beside the
  engines it runs a token baseline: short-lived signed tokens, each exchanged from its delegator's and checked locally.

Every engine answers the same schema, generated from one model, over the same seeded dataset, and must agree with a
reference evaluator before anything is timed. Every number in a results file can be reproduced from its seed.

## Results

The [run of 2026-11-07](runs/2026-11-07) measured NodusDB 0.1.1, SpiceDB v1.57.0 and OpenFGA v1.22.1 at three
dataset sizes on two c5.2xlarge machines in AWS eu-north-1. Every engine agreed with the reference evaluator on every
check before it was timed. At 560,497 relationships:

| Engine and consistency mode | At 100 checks/s, p50 | At 1,000 checks/s, p50 | One at a time, p50 | One at a time, p99 | A revoked permission still granted for, longest of 90 races |
|---|---:|---:|---:|---:|---|
| NodusDB, in process | 14 µs | 12 µs | 9 µs | 17 µs | never |
| NodusDB follower, with the write's token | 27 µs | 13 µs | 10 µs | 19 µs | never; a check right after the write waits about 140 ms |
| NodusDB follower, without a token | 16 µs | 12 µs | 9 µs | 18 µs | 0.30 s |
| OpenFGA `HIGHER_CONSISTENCY` | 3.7 ms | 6.6 s | 3.6 ms | 22.7 ms | never |
| OpenFGA `MINIMIZE_LATENCY`, check cache on | 0.62 ms | 6.3 s | 3.2 ms | 21.6 ms | 10.0 s |
| SpiceDB `at_least_as_fresh` | 4.3 ms | 5.6 s | 4.3 ms | 16.2 ms | never |
| SpiceDB `fully_consistent` | 4.6 ms | 5.5 s | 4.5 ms | 16.5 ms | never |
| SpiceDB `minimize_latency` | 4.2 ms | 5.4 s | 4.3 ms | 15.5 ms | 3.5 s |

The services were driven from a second machine by Go with up to 256 checks in flight, and NodusDB by Python with one.
At 1,000 checks a second SpiceDB and OpenFGA used 93 to 100% of the 8-vCPU machine they shared with their Postgres,
so those columns measure a saturated machine. The [run's README](runs/2026-11-07) lists what to read with care, and
its folders hold every raw results file, the CPU traces and the machine records.

## Vendor contributions welcome

AuthzBench was written by the author of NodusDB. That is a reason to check our work, and we would rather you did it
in the open. If you maintain SpiceDB, OpenFGA or another Zanzibar-style engine, or run one in production, we want your
changes:

- Tuning. If a flag, a pool size, a cache or a deployment shape is how you would run your engine, change
  [services/compose.yaml](services/compose.yaml) or the adapter and open a pull request. Say where the setting is
  documented, or why it is right.
- Consistency modes. If a mode is missing or misdescribed, the adapters in [go/engines](go/engines) are short.
- A new engine. Add an adapter beside the others. It must pass the same conformance check against the reference
  evaluator before it is timed.
- Method. If the measurement itself is wrong, open an issue with the evidence.

Every accepted change gets a row in [docs/decisions.md](docs/decisions.md) and a new run published as a new folder
under [runs/](runs). A published number is never edited in place; a correction is a new run with a note saying what
changed.

## Layout

| Path | What it holds |
|---|---|
| `src/authzbench/model.py`, `benchmark_model.py` | The permission model types and the one model every engine runs |
| `src/authzbench/emitters.py`, `generated/` | Each engine's schema, generated from the model; CI fails if a generated file is edited by hand |
| `src/authzbench/reference.py` | The reference evaluator every engine is checked against |
| `src/authzbench/dataset.py` | The seeded dataset and its revocation and nesting scenarios |
| `src/authzbench/measure.py`, `timing.py` | Service time, open-loop latency and the revocation race |
| `src/authzbench/engines/` | One adapter per engine, the token baseline, and the calibration engine that proves the timing |
| `src/authzbench/results.schema.json` | The format every results file follows, from either driver |
| `go/` | The Go driver for engines that run as services, on their official clients: SpiceDB and OpenFGA |
| `services/compose.yaml` | SpiceDB and OpenFGA, each on its own PostgreSQL, every image pinned by digest |
| `docs/decisions.md` | Every choice that shapes a published number, with its evidence |

## Running it

Nodus is embedded, so the Python driver calls it in the same process:

```sh
pip install -e ".[nodus,test]"
authzbench generate --scale S --seed 1 --out data/S
authzbench run --engine nodus --dataset data/S --out results/nodus-S.json
authzbench run --engine tokens --token-lifetime-seconds 60 --revocation-cap-seconds 90     --dataset data/S --out results/tokens-S.json
pytest
```

SpiceDB and OpenFGA are services, so the Go driver measures them through their official clients (Go 1.25.8 or newer):

```sh
docker compose -f services/compose.yaml up --detach --wait spicedb openfga
go -C go build -o ../authzbench-go ./cmd/authzbench-go
./authzbench-go -engine spicedb -dataset data/S -out results/spicedb-S.json
./authzbench-go -engine spicedb -spicedb-fresh at_least_as_fresh -dataset data/S -out results/spicedb-token-S.json
./authzbench-go -engine openfga -openfga-version v1.22.1 -dataset data/S -out results/openfga-S.json
```

The Go tests run the adapters against the real servers when `AUTHZBENCH_SPICEDB_ENDPOINT=localhost:50051` and
`AUTHZBENCH_OPENFGA_URL=http://localhost:8080` are set, and skip them otherwise:

```sh
cd go && go test ./...
```

## Two machines

The revocation race also runs across a network, timed on one clock. Machine 1 runs every writer; machine 2 runs the
driver, so every revoke and every check is timed on machine 2's clock, and the published number counts the reply's
trip back as part of the race. Both machines sit in one private network.

On machine 1, with S3 credentials in `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`:

```sh
BIND_ADDRESS=10.0.0.5 docker compose -f services/compose.yaml up --detach --wait spicedb openfga
authzbench serve --engine nodus-writer --dataset data/S --listen 10.0.0.5:7700 --allow 10.0.0.6 \
    --graph nodus-writer --signing-key keys/signing.pem --bucket my-bucket --region eu-north-1
```

The writer makes `keys/signing.pem` when it is missing; copy `keys/signing.pub` to machine 2. On machine 2, with the
same credentials:

```sh
authzbench run --engine nodus-follower --placement second_machine --writer http://10.0.0.5:7700 \
    --public-key signing.pub --bucket my-bucket --region eu-north-1 --dataset data/S --out results/follower-S.json
./authzbench-go -engine spicedb -placement second_machine -spicedb-endpoint 10.0.0.5:50051 \
    -dataset data/S -out results/spicedb-S-remote.json
```

`authzbench serve --engine calibration` on machine 1 and `authzbench run --engine remote-calibration` on machine 2
measure the 500 ms calibration engine across the same network, which proves the timing before any engine is timed.
The server answers only the addresses in `--allow`.

## License

Apache License 2.0.
