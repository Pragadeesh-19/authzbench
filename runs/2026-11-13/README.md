# Run of 2026-11-13, AWS eu-north-1

The delegation race: NodusDB 0.1.2, SpiceDB v1.57.0, OpenFGA v1.22.1 and the token baseline at size S, measured
with AuthzBench `3a4007c`.

| Folder | Dataset | Relationships | Checks verified before timing |
|---|---|---:|---:|
| [S](S) | `authzbench generate --scale S --seed 1` (`authzbench.dataset/2`) | 5,570 | 2,029 |

Every engine agreed with the reference evaluator on every check, and every results file follows
[results.schema.json](../../src/authzbench/results.schema.json) (`authzbench.results/3`).

## Revoking a delegation

A person delegates to an agent, which delegates down a tree of 3, 9 and 27 agents. Each race removes the person's
one delegation and times, from the moment the write is acknowledged, how long until every one of the 27 leaf agents
is denied. Each leaf is checked again no sooner than 50 ms after its previous check started (A19). 30 races per
mode, so the longest race is also the p99.

| Engine and consistency mode | One check, p50 at 100 checks/s | All 27 leaves denied after, p50 | Longest of 30 | Every leaf denied on its first check |
|---|---:|---:|---:|---|
| NodusDB, in process | 13 µs | 0.18 ms | 0.49 ms | yes |
| NodusDB follower, with the write's token | 17 µs | never allowed; see below | | yes |
| NodusDB follower, without a token | 15 µs | 0.15 s | 0.30 s | no |
| OpenFGA `HIGHER_CONSISTENCY` | 2.5 ms | 0.32 ms | 0.49 ms | yes |
| OpenFGA `MINIMIZE_LATENCY`, check cache on | 1.6 ms | 10.0 s | 10.0 s | no |
| SpiceDB `at_least_as_fresh` | 2.9 ms | 0.17 ms | 0.21 ms | yes |
| SpiceDB `fully_consistent` | 3.4 ms | 0.15 ms | 0.68 ms | yes |
| SpiceDB `minimize_latency` | 2.6 ms | 0.20 s | 5.2 s | no |
| Tokens, 5 s lifetime | 63 µs | 0.95 s | 3.9 s | no |
| Tokens, 60 s lifetime | 36 µs | 15.1 s | 42.3 s | no |

SpiceDB's `minimize_latency` ran twice, once in each SpiceDB file. The table shows `spicedb.json`;
`spicedb-token.json` has 0.25 s and 5.15 s. No race reached its cap.

## Machines

Two c5.2xlarge instances (8 vCPU, Intel Xeon Platinum 8275CL, 15 GiB, Ubuntu 26.04.1, kernel 7.0.0-1016-aws) in
eu-north-1c, on one private network. [machine-1.txt](machine-1.txt) and [machine-2.txt](machine-2.txt) record each
one, with the container image digests, which are the ones of the 2026-11-07 run.

This is not the CPU of the [run of 2026-11-07](../2026-11-07), which had a Xeon Platinum 8124M and kernel
7.0.0-1014-aws. Compare numbers across the two runs with that in mind.

- Machine 1 ran the engines: SpiceDB or OpenFGA, each on its own Postgres 17, started from
  [services/compose.yaml](../../services/compose.yaml), and the NodusDB writer behind `authzbench serve`. Only the
  engine being measured ran during its phase, and each SpiceDB and OpenFGA phase started from a freshly created
  database.
- Machine 2 ran both drivers, NodusDB in process, the NodusDB follower and the token baseline, so every check that
  crosses machines is timed on one clock.

Machine 2's record has no Go version or driver hash. The results files record Go 1.27.3; the SHA-256 of the Go
driver binary was not taken before the machines were terminated.

## What the folder holds

| File | What it is |
|---|---|
| `calibration.json` | the calibration engine in process: known staleness 500 ms, known extra latency 2 ms |
| `remote-calibration.json` | the same engine served from machine 1, measured across the network |
| `nodus.json` | NodusDB in process, an in-memory graph |
| `nodus-follower.json` | a NodusDB follower on machine 2, fed through S3 by a durable writer on machine 1 |
| `spicedb.json` | SpiceDB with `fully_consistent` as its fresh read, and `minimize_latency` |
| `spicedb-token.json` | SpiceDB with `at_least_as_fresh` the latest write's ZedToken as its fresh read, and `minimize_latency` again |
| `openfga.json` | OpenFGA with `HIGHER_CONSISTENCY` and `MINIMIZE_LATENCY`, check query cache on |
| `tokens-5.json`, `tokens-60.json` | the token baseline with 5 s and 60 s lifetimes |
| `*.log` | each phase's start and end time and the driver's output |
| `*.machine-2.vmstat` | `vmstat -n -t 1` on machine 2 for the phase |
| `machine-1-monitor/` | `vmstat -n -t 1` and per-container `docker stats` on machine 1 for each remote phase |

Each file also holds the open-loop, service-time and single-revocation measurements of its modes, run before the
delegation race.

## How it was run

Each phase ran on machine 2 through [scripts/phase.sh](scripts/phase.sh) with the default rates (100, 500, 1,000 and
10,000 checks a second), 30 revocation races and 30 delegation races per mode, a 50 ms delegation interval and a
30 s cap (90 s for 60 s tokens). [scripts/monitor.sh](scripts/monitor.sh) sampled machine 1. In order:

1. `calibration` and `nodus`, in process on machine 2.
2. `authzbench serve --engine calibration` on machine 1, then `remote-calibration`.
3. `authzbench serve --engine nodus-writer` on machine 1 with a fresh signing key, shipping to the bucket under the
   prefix `act3/s-20261113-174401`, then `nodus-follower`.
4. `docker compose down -v`, then `up spicedb`, then `spicedb`; the same again for `spicedb-token`.
5. `docker compose down -v`, then `up openfga`, then `openfga`.
6. `tokens-5`, then `tokens-60`, on machine 2.

The calibration engine shows the timing is exact on these machines. In process its fresh mode denied the last leaf
at 52.03 ms (26 checks of 2 ms each before it) and its stale mode at 552.29 ms (expected 552 ms); across the network
59.56 ms (26 checks of 2.29 ms) and 559.93 ms.

## What to read with care

- The strict modes of SpiceDB and OpenFGA deny every leaf on its first check, as NodusDB does. Where they differ from
  NodusDB is the cost of each check, and in what their faster modes give up: SpiceDB's `minimize_latency` kept the tree
  allowed for up to 5.2 s and OpenFGA's check cache for 10.0 s.
- NodusDB in process answers in the caller's process, with no network between the check and the data. The follower is
  the NodusDB that runs on another machine than its writer, and without a token it trails the writer by up to 0.30 s.
- The Python driver (NodusDB, tokens) checks the 27 leaves in turn; the Go driver (SpiceDB, OpenFGA) checks them at
  once, one goroutine per leaf. A Python mode that denies on the first check is charged the sweep, 27 checks of about
  15 µs for NodusDB. A Go mode is charged the time to start 27 goroutines. Each results file also holds `denied_by`,
  the time until that check returned: 14 to 26 ms for SpiceDB's strict modes and 9 to 16 ms for OpenFGA's, with 27
  strict checks in flight at once.
- NodusDB follower with the write's token: no leaf was ever allowed after the revoke. Its 147 ms p50 (268 ms longest)
  is how long the first leaf's check waited inside the call for the follower to reach the write's token. The other
  leaves are checked after it.
- While 27 leaves polled a service at the 50 ms interval, machine 1 used 13 to 19% of its CPU in bursts, so the cached
  modes' times are their cache windows, not queueing. At 1,000 checks a second and above in the open-loop part,
  SpiceDB and OpenFGA used up to 97 to 99% of machine 1, as in the 2026-11-07 run.
- The token baseline is generous to tokens (A20): each child's expiry is capped by its parent's, tokens are signed
  with HMAC, and the default lifetime is 60 s.
