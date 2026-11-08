# Run of 2026-11-07, AWS eu-north-1

NodusDB 0.1.1, SpiceDB v1.57.0 and OpenFGA v1.22.1 at three dataset sizes, measured with AuthzBench `2ac787c`.

| Folder | Dataset | Relationships | Checks verified before timing |
|---|---|---:|---:|
| [S](S) | `authzbench generate --scale S --seed 1` | 5,487 | 2,001 |
| [M](M) | `authzbench generate --scale M --seed 1` | 55,756 | 10,001 |
| [L](L) | `authzbench generate --scale L --seed 1` | 560,497 | 50,001 |

Every engine agreed with the reference evaluator on every check at every size.

## Machines

Two c5.2xlarge instances (8 vCPU, Intel Xeon Platinum 8124M, 15 GiB, Ubuntu 26.04.1) in eu-north-1c, on one private
network. [machine-1.txt](machine-1.txt) and [machine-2.txt](machine-2.txt) record each one, with the container image
digests, the Go and Python versions and the SHA-256 of the Go driver binary.

- Machine 1 ran the engines: SpiceDB or OpenFGA, each on its own Postgres 17, started from
  [services/compose.yaml](../../services/compose.yaml) with the pool settings of decision A15, and the NodusDB writer
  behind `authzbench serve`. Only the engine being measured ran during its phase, and each SpiceDB and OpenFGA phase
  started from a freshly created database.
- Machine 2 ran both drivers and the NodusDB follower, so every check that crosses machines is timed on one clock.

## What each folder holds

| File | What it is |
|---|---|
| `calibration.json` | the calibration engine in process: known staleness 500 ms, known extra latency 2 ms |
| `remote-calibration.json` | the same engine served from machine 1, measured across the network |
| `nodus.json` | NodusDB in process, an in-memory graph |
| `nodus-follower.json` | a NodusDB follower on machine 2, fed through S3 by a durable writer on machine 1 |
| `spicedb.json` | SpiceDB with `fully_consistent` as its fresh read, and `minimize_latency` |
| `spicedb-token.json` | SpiceDB with `at_least_as_fresh` the latest write's ZedToken as its fresh read, and `minimize_latency` again |
| `openfga.json` | OpenFGA with `HIGHER_CONSISTENCY` and `MINIMIZE_LATENCY`, check query cache on |
| `*.log` | each phase's start and end time and the driver's output |
| `*.machine-2.vmstat` | `vmstat -n -t 1` on machine 2 for the phase |
| `machine-1-monitor/` | `vmstat -n -t 1` and per-container `docker stats` on machine 1 for each remote phase |

Every results file follows [results.schema.json](../../src/authzbench/results.schema.json) (`authzbench.results/2`)
and keeps all 30 revocation races of each mode under `samples`.

## How it was run

Each phase ran on machine 2 through [scripts/phase.sh](scripts/phase.sh) with the default rates (100, 500, 1,000 and
10,000 checks a second), 30 revocation races per mode and a 30 s cap. [scripts/monitor.sh](scripts/monitor.sh)
sampled machine 1. Per size, in order:

1. `calibration` and `nodus`, in process on machine 2.
2. `authzbench serve --engine calibration` on machine 1, then `remote-calibration`.
3. `authzbench serve --engine nodus-writer` on machine 1 with a fresh signing key, shipping to the bucket under a new
   prefix (`act1/s-20261107-163011`, `act1/m-20261107-171913`, `act1/l-20261107-150244`), then `nodus-follower`.
4. `docker compose down -v`, then `up spicedb`, then `spicedb`; the same again for `spicedb-token`.
5. `docker compose down -v`, then `up openfga`, then `openfga`.

L ran first, then S, then M.

## What to read with care

- At 1,000 checks a second and above, SpiceDB and OpenFGA used 93 to 100% of machine 1's CPU. Their open-loop
  numbers there measure a saturated 8-vCPU machine with Postgres beside the service. The CPU traces show it.
- At S, 1,000 checks a second is at SpiceDB's limit: most checks return quickly and bursts queue, so its p50 there is
  lower than its unloaded p50. Read the p99.
- `nodus.json` measures an in-memory graph, so its write times are not comparable with the services'. The follower's
  writes go through a durable writer (`sync_mode="sync"`, shipping to S3) on machine 1.
- The NodusDB driver is Python with one check in flight; SpiceDB and OpenFGA were driven by Go with up to 256.
