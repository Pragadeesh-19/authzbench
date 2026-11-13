#!/bin/sh
ENGINE="$1"
PREFIX="${2:-none}"
. "$HOME/act3.env"
R="$HOME/results/act3-S"
D="$HOME/data/S"
WRITER=http://172.31.13.107:7700
RATES=100,500,1000,10000
mkdir -p "$R"
rm -f "$R/$ENGINE.done"
vmstat -n -t 1 > "$R/$ENGINE.machine-2.vmstat" &
MONITOR=$!
date -u +"start %Y-%m-%dT%H:%M:%SZ"
PY="$HOME/bench/bin/authzbench"
GO="$HOME/authzbench-go"
GOARGS="-placement second_machine -rates $RATES -schemas $HOME/authzbench/generated -dataset $D -out $R/$ENGINE.json"

case "$ENGINE" in
  calibration|nodus)
    "$PY" run --engine "$ENGINE" --rates "$RATES" --dataset "$D" --out "$R/$ENGINE.json" ;;
  tokens-60)
    "$PY" run --engine tokens --token-lifetime-seconds 60 --revocation-cap-seconds 90 --rates "$RATES" \
        --dataset "$D" --out "$R/$ENGINE.json" ;;
  tokens-5)
    "$PY" run --engine tokens --token-lifetime-seconds 5 --rates "$RATES" --dataset "$D" --out "$R/$ENGINE.json" ;;
  remote-calibration)
    "$PY" run --engine remote-calibration --placement second_machine --writer "$WRITER" \
        --rates "$RATES" --dataset "$D" --out "$R/$ENGINE.json" ;;
  nodus-follower)
    "$PY" run --engine nodus-follower --placement second_machine --writer "$WRITER" \
        --public-key "$HOME/signing.pub" --bucket nodus-benchmark-act1 --region eu-north-1 --prefix "$PREFIX" \
        --rates "$RATES" --dataset "$D" --out "$R/$ENGINE.json" ;;
  spicedb)
    "$GO" -engine spicedb -spicedb-endpoint 172.31.13.107:50051 $GOARGS ;;
  spicedb-token)
    "$GO" -engine spicedb -spicedb-fresh at_least_as_fresh -spicedb-endpoint 172.31.13.107:50051 $GOARGS ;;
  openfga)
    "$GO" -engine openfga -openfga-version v1.22.1 -openfga-url http://172.31.13.107:8080 $GOARGS ;;
  *)
    echo "unknown engine $ENGINE"; false ;;
esac
STATUS=$?

date -u +"end %Y-%m-%dT%H:%M:%SZ"
kill "$MONITOR"
echo "$STATUS" > "$R/$ENGINE.done"
