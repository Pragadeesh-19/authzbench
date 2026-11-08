#!/bin/sh
SCALE="$1"
ENGINE="$2"
PREFIX="$3"
. "$HOME/act1.env"
R="$HOME/results/act1-$SCALE"
D="$HOME/data/$SCALE"
WRITER=http://172.31.1.245:7700
RATES=100,500,1000,10000
mkdir -p "$R"
rm -f "$R/$ENGINE.done"
vmstat -n -t 1 > "$R/$ENGINE.machine-2.vmstat" &
MONITOR=$!
date -u +"start %Y-%m-%dT%H:%M:%SZ"

case "$ENGINE" in
  calibration|nodus)
    "$HOME/bench/bin/authzbench" run --engine "$ENGINE" --rates "$RATES" --dataset "$D" --out "$R/$ENGINE.json" ;;
  remote-calibration)
    "$HOME/bench/bin/authzbench" run --engine remote-calibration --placement second_machine --writer "$WRITER" \
        --rates "$RATES" --dataset "$D" --out "$R/$ENGINE.json" ;;
  nodus-follower)
    "$HOME/bench/bin/authzbench" run --engine nodus-follower --placement second_machine --writer "$WRITER" \
        --public-key "$HOME/signing.pub" --bucket nodus-benchmark-act1 --region eu-north-1 --prefix "$PREFIX" \
        --rates "$RATES" --dataset "$D" --out "$R/$ENGINE.json" ;;
  spicedb)
    "$HOME/authzbench-go" -engine spicedb -placement second_machine -spicedb-endpoint 172.31.1.245:50051 \
        -rates "$RATES" -schemas "$HOME/authzbench/generated" -dataset "$D" -out "$R/$ENGINE.json" ;;
  spicedb-token)
    "$HOME/authzbench-go" -engine spicedb -spicedb-fresh at_least_as_fresh -placement second_machine         -spicedb-endpoint 172.31.1.245:50051         -rates "$RATES" -schemas "$HOME/authzbench/generated" -dataset "$D" -out "$R/$ENGINE.json" ;;
  openfga)
    "$HOME/authzbench-go" -engine openfga -openfga-version v1.22.1 -placement second_machine \
        -openfga-url http://172.31.1.245:8080 \
        -rates "$RATES" -schemas "$HOME/authzbench/generated" -dataset "$D" -out "$R/$ENGINE.json" ;;
  *)
    echo "unknown engine $ENGINE"; false ;;
esac
STATUS=$?

date -u +"end %Y-%m-%dT%H:%M:%SZ"
kill "$MONITOR"
echo "$STATUS" > "$R/$ENGINE.done"
