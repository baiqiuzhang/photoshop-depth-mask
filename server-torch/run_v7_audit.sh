#!/bin/bash
# 流水线审计：GF r 1/2/4/8 柔化对比 + SR×直方图×位深矩阵（frozen exe）
set -u
PY="D:/depth_v7_builder/python.exe"
DIST="D:/depth_pro_photoshop_jsx/0.2.1/dist_v7_candidate/depth_server"
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
(cd "D:/depth_pro_photoshop_jsx/0.2.1/源码" && "$PY" audit_v7.py --server http://127.0.0.1:8766 2>&1 | grep -viE "loading weights|it/s" | tee -a "$OUT")
taskkill //F //PID $SERVER_PID >/dev/null 2>&1
taskkill //F //IM depth_server.exe >/dev/null 2>&1
echo "== audit done ==" | tee -a "$OUT"
