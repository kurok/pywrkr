#!/usr/bin/env bash
# Re-record the README demo GIF (docs/assets/demo.gif).
#
# Requires:
#   pip install -e .                  # puts `pywrkr` on PATH
#   asciinema (https://docs.asciinema.org) + agg (https://github.com/asciinema/agg)
#
# Benchmarks a throwaway local HTTP server so the demo is self-contained.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT=/tmp/pywrkr-demo-root

mkdir -p "$ROOT"
echo '<html><body><h1>pywrkr demo</h1></body></html>' > "$ROOT/index.html"
( cd "$ROOT" && python -m http.server 8000 >/tmp/pywrkr-httpd.log 2>&1 ) &
HTTPD=$!
trap 'kill $HTTPD 2>/dev/null || true' EXIT
sleep 1

cat > /tmp/pywrkr-demo-run.sh <<'RUN'
P=$'\033[1;32m$\033[0m '
typ(){ printf '%s' "$P"; local s="$1" i; for ((i=0;i<${#s};i++)); do printf '%s' "${s:i:1}"; sleep 0.02; done; printf '\n'; }
clear; sleep 0.4
typ "pywrkr http://localhost:8000/ -c 10 -d 5"
pywrkr http://localhost:8000/ -c 10 -d 5
sleep 2.5
RUN

asciinema rec --window-size 100x34 -c "bash /tmp/pywrkr-demo-run.sh" --overwrite /tmp/pywrkr.cast
agg --font-size 15 /tmp/pywrkr.cast "$REPO/docs/assets/demo.gif"
echo "wrote $REPO/docs/assets/demo.gif"
