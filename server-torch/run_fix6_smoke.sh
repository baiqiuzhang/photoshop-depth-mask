#!/bin/bash
# 0.2.1 修复批次 6 冒烟链：
#   1) 源码级复现：distill S1 × {none,bilinear,gf} 保存 PNG + 区域统计（白区变灰诊断），
#      depthpro S1 bilinear 作对照
#   2) 重建 exe（spec 已补 requests 元数据）
#   3) 冻结包 Iris 实测（验证 requests 元数据修复）
set -u
# 路径可用环境变量覆盖；默认值按仓库布局推导（本脚本位于 server-torch/，即源码目录）。
SRC_DIR="${DEPTH_SRC_DIR:-$(cd "$(dirname "$0")" && pwd)}"
REPO_ROOT="$(cd "$SRC_DIR/.." && pwd)"
PY="${DEPTH_PY:-python}"
DIST="${DEPTH_EXE_DIR:-$REPO_ROOT/dist_v7_candidate/depth_server}"
cd "$SRC_DIR" || exit 1
LOG="server_fix6_test.log"
OUT="run_fix6_out.txt"
STATS="fix6_stats"
mkdir -p "$STATS"
: > "$OUT"

taskkill //F //IM depth_server.exe >/dev/null 2>&1
sleep 1
"$PY" depth_server.py > "$LOG" 2>&1 &
SERVER_PID=$!
READY=0
for i in $(seq 1 90); do
    if curl -s http://127.0.0.1:8766/ping >/dev/null 2>&1; then READY=1; break; fi
    sleep 1
done
if [ "$READY" != "1" ]; then echo "FAIL: server not ready" | tee -a "$OUT"; tail -20 "$LOG" >> "$OUT"; exit 1; fi

rs_case() {
    echo "===== $* =====" | tee -a "$OUT"
    NAME="$1"; shift
    "$PY" region_stats.py --out "$STATS/$NAME.png" "$@" 2>&1 | grep -viE "loading weights|it/s" | tail -4 | tee -a "$OUT"
}

rs_case dad_none      --model distillanydepth --upscale none     --algo S1
rs_case dad_bilinear  --model distillanydepth --upscale bilinear --algo S1
rs_case dad_gf        --model distillanydepth --upscale gf --radius 2 --algo S1
rs_case dp_bilinear   --model depthpro        --upscale bilinear --algo S1

kill -9 $SERVER_PID >/dev/null 2>&1
wait $SERVER_PID 2>/dev/null

echo "== phase 2: rebuild exe ==" | tee -a "$OUT"
"$PY" build_depth_server_v5.py --clean > build_fix6.log 2>&1
BUILD_RC=$?
echo "build exit=$BUILD_RC" | tee -a "$OUT"
if [ "$BUILD_RC" != "0" ]; then echo "FAIL: build" | tee -a "$OUT"; tail -30 build_fix6.log >> "$OUT"; exit 1; fi
tail -3 build_fix6.log | tee -a "$OUT"

echo "== phase 3: frozen iris test ==" | tee -a "$OUT"
EXE="$DIST/depth_server.exe"
cd "$DIST" || exit 1
"./depth_server.exe" > "$SRC_DIR/server_fix6_frozen.log" 2>&1 &
FROZEN_PID=$!
cd "$SRC_DIR" || exit 1
READY=0
for i in $(seq 1 90); do
    if curl -s http://127.0.0.1:8766/ping >/dev/null 2>&1; then READY=1; break; fi
    sleep 1
done
if [ "$READY" != "1" ]; then echo "FAIL: frozen server not ready" | tee -a "$OUT"; exit 1; fi
"$PY" test_client.py process --model iris --upscale none --algo none --bits 16 2>&1 | grep -viE "loading weights|it/s" | tail -5 | tee -a "$OUT"
kill -9 $FROZEN_PID >/dev/null 2>&1
wait $FROZEN_PID 2>/dev/null
echo "== all done ==" | tee -a "$OUT"
