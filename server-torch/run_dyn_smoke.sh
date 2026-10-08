#!/bin/bash
# 0.2.1 动态半径机制 冒烟链：源码级 e2e（同图 none/bilinear/gf，记录 k/n/r_eff/r_lp）→ 重建 exe
set -u
cd "D:/depth_pro_photoshop_jsx/0.2.1/源码" || exit 1
PY="D:/depth_v7_builder/python.exe"
LOG="server_dyn_test.log"
OUT="run_dyn_out.txt"
: > "$OUT"

echo "== phase 1: source e2e ==" | tee -a "$OUT"
taskkill //F //IM python.exe //FI "WINDOWTITLE eq depth_server*" >/dev/null 2>&1
sleep 1
"$PY" depth_server.py > "$LOG" 2>&1 &
SERVER_PID=$!
READY=0
for i in $(seq 1 90); do
    if curl -s http://127.0.0.1:8766/ping >/dev/null 2>&1; then READY=1; break; fi
    sleep 1
done
if [ "$READY" != "1" ]; then echo "FAIL: server not ready" | tee -a "$OUT"; tail -20 "$LOG" >> "$OUT"; exit 1; fi
curl -s http://127.0.0.1:8766/ping | "$PY" -c "import sys,json; d=json.load(sys.stdin); print('ping ok, models:', [m['key'] for m in d['available_models'] if m['found']])" | tee -a "$OUT"

run_case() {
    echo "===== $* =====" | tee -a "$OUT"
    "$PY" test_client.py process "$@" 2>&1 | grep -viE "loading weights|it/s" | tail -6 | tee -a "$OUT"
}

run_case --model depthpro --upscale none     --algo S1 --bits 16 --target 1024 683
run_case --model depthpro --upscale bilinear --algo S1 --bits 16 --target 1024 683
run_case --model depthpro --upscale gf --radius 2 --algo S1 --bits 16 --target 1024 683

kill -9 $SERVER_PID >/dev/null 2>&1
wait $SERVER_PID 2>/dev/null

echo "== dynamic radius diagnostics ==" | tee -a "$OUT"
grep -o '"upscale": {[^}]*}' "$LOG" | sort -u | tee -a "$OUT"

echo "== phase 2: rebuild exe ==" | tee -a "$OUT"
"$PY" build_depth_server_v5.py --clean > build_dyn.log 2>&1
BUILD_RC=$?
echo "build exit=$BUILD_RC" | tee -a "$OUT"
if [ "$BUILD_RC" != "0" ]; then echo "FAIL: build" | tee -a "$OUT"; tail -30 build_dyn.log >> "$OUT"; exit 1; fi
tail -3 build_dyn.log | tee -a "$OUT"
echo "== all done ==" | tee -a "$OUT"
