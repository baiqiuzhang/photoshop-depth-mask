#!/bin/bash
# 流水线审计：GF r 1/2/4/8 柔化对比 + SR×直方图×位深矩阵（frozen exe）
set -u
# 路径可用环境变量覆盖；默认值按仓库布局推导（本脚本位于 server-torch/，即源码目录）。
SRC_DIR="${DEPTH_SRC_DIR:-$(cd "$(dirname "$0")" && pwd)}"
REPO_ROOT="$(cd "$SRC_DIR/.." && pwd)"
PY="${DEPTH_PY:-python}"
DIST="${DEPTH_EXE_DIR:-$REPO_ROOT/dist_v7_candidate/depth_server}"
OUT="run_v7_audit_out.txt"
: > "$OUT"
taskkill //F //IM depth_server.exe >/dev/null 2>&1
sleep 1
"$DIST/depth_server.exe" > "$DIST/server_frozen.log" 2>&1 &
SERVER_PID=$!
for i in $(seq 1 90); do
    curl -s http://127.0.0.1:8766/ping >/dev/null 2>&1 && { echo "== audit server ready ${i}s ==" | tee -a "$OUT"; break; }
    sleep 1
done
(cd "$SRC_DIR" && "$PY" audit_v7.py --server http://127.0.0.1:8766 2>&1 | grep -viE "loading weights|it/s" | tee -a "$OUT")
taskkill //F //PID $SERVER_PID >/dev/null 2>&1
taskkill //F //IM depth_server.exe >/dev/null 2>&1
echo "== audit done ==" | tee -a "$OUT"
