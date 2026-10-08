#!/bin/bash
# frozen 冒烟测试：候选目录 exe /ping version=7 + 5 模型检视 + /process 样图（16/8bit）
set -u
PY="D:/depth_v7_builder/python.exe"
DIST="D:/depth_pro_photoshop_jsx/0.2.1/dist_v7_candidate/depth_server"
OUT="run_v7_frozen_out.txt"
: > "$OUT"
taskkill //F //IM depth_server.exe >/dev/null 2>&1
sleep 1
"$DIST/depth_server.exe" > "$DIST/server_frozen.log" 2>&1 &
SERVER_PID=$!
for i in $(seq 1 90); do
    curl -s http://127.0.0.1:8766/ping >/dev/null 2>&1 && { echo "== frozen ready ${i}s ==" | tee -a "$OUT"; break; }
    sleep 1
done
echo "--- /ping ---" | tee -a "$OUT"
curl -s http://127.0.0.1:8766/ping | "$PY" -c "import sys,json; d=json.load(sys.stdin); print('version:', d['version']); print('models found:', [m['key'] for m in d['available_models'] if m['found']]); print('default:', d['default_model'])" | tee -a "$OUT"

run_case() {
    echo "===== $* =====" | tee -a "$OUT"
    (cd "D:/depth_pro_photoshop_jsx/0.2.1/源码" && "$PY" test_client.py process "$@" 2>&1 | grep -viE "loading weights|it/s" | tail -5 | tee -a "$OUT")
}

run_case --model depthpro --upscale gf --radius 2 --algo S1 --bits 16 --target 1024 683
run_case --model distillanydepth --upscale bilinear --algo S1 --bits 16 --target 1024 683
run_case --model depthpro --upscale none --algo none --bits 8 --target 1024 683

taskkill //F //PID $SERVER_PID >/dev/null 2>&1
taskkill //F //IM depth_server.exe >/dev/null 2>&1
echo "== frozen smoke done ==" | tee -a "$OUT"
