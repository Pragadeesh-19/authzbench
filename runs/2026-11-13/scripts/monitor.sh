#!/bin/sh
M="$HOME/monitor"
case "$1" in
  start)
    mkdir -p "$M"
    setsid nohup vmstat -n -t 1 > "$M/$2.vmstat" 2>&1 < /dev/null &
    echo $! > "$M/pids"
    setsid nohup sh -c 'while true; do date -u +%s; docker stats --no-stream --format "{{.Name}} {{.CPUPerc}} {{.MemUsage}}"; sleep 1; done' \
        > "$M/$2.docker" 2>&1 < /dev/null &
    echo $! >> "$M/pids" ;;
  stop)
    [ -f "$M/pids" ] && kill $(cat "$M/pids") 2>/dev/null
    rm -f "$M/pids" ;;
esac
