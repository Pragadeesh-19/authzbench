# AuthzBench

A neutral, reproducible benchmark for authorization engines that answer Zanzibar-style permission checks.

It measures two things that matter when access is revoked:

- **Check latency when the answer must be fully up to date**, issued on an open-loop schedule so that slow stretches
  are counted, not skipped, and a closed-loop "service time" beside it.
- **Time to revocation**: how long a removed permission keeps being allowed after the write that removed it was
  acknowledged.

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
| `src/authzbench/results.schema.json` | The format every results file follows |

## Running it

```sh
pip install -e ".[nodus,test]"
authzbench generate --scale S --seed 1 --out data/S
authzbench run --engine nodus --dataset data/S --out results/nodus-S.json
pytest
```

## License

Apache License 2.0.
