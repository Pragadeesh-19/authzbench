# AuthzBench

A neutral, reproducible benchmark for authorization engines that answer Zanzibar-style permission checks.

It measures two things that matter when access is revoked:

- **Check latency when the answer must be fully up to date**, issued on an open-loop schedule so that slow stretches
  are counted, not skipped, and a closed-loop "service time" beside it.
- **Time to revocation**: how long a removed permission keeps being allowed after the write that removed it was
  acknowledged, as a distribution over 30 races per mode.

Every engine answers the same schema, generated from one model, over the same seeded dataset, and must agree with a
reference evaluator before anything is timed. Every number in a results file can be reproduced from its seed.

## Layout

| Path | What it holds |
|---|---|
| `src/authzbench/model.py`, `benchmark_model.py` | The permission model types and the one model every engine runs |
| `src/authzbench/emitters.py`, `generated/` | Each engine's schema, generated from the model; CI fails if a generated file is edited by hand |
| `src/authzbench/reference.py` | The reference evaluator every engine is checked against |
| `src/authzbench/dataset.py` | The seeded dataset and its revocation and nesting scenarios |
| `src/authzbench/measure.py`, `timing.py` | Service time, open-loop latency and the revocation race |
| `src/authzbench/engines/` | One adapter per engine, plus the calibration engine that proves the timing |
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
authzbench serve --engine nodus-writer --dataset data/S --listen 10.0.0.5:7700 --allow 10.0.0.6     --graph nodus-writer --signing-key keys/signing.pem --bucket my-bucket --region eu-north-1
```

The writer makes `keys/signing.pem` when it is missing; copy `keys/signing.pub` to machine 2. On machine 2, with the
same credentials:

```sh
authzbench run --engine nodus-follower --placement second_machine --writer http://10.0.0.5:7700     --public-key signing.pub --bucket my-bucket --region eu-north-1 --dataset data/S --out results/follower-S.json
./authzbench-go -engine spicedb -placement second_machine -spicedb-endpoint 10.0.0.5:50051     -dataset data/S -out results/spicedb-S-remote.json
```

`authzbench serve --engine calibration` on machine 1 and `authzbench run --engine remote-calibration` on machine 2
measure the 500 ms calibration engine across the same network, which proves the timing before any engine is timed.
The server answers only the addresses in `--allow`.

## License

Apache License 2.0.
