#!/bin/bash
# 协议 7 端到端测试：起服务 -> /ping -> /process 矩阵 -> 杀服务（自包含单命令）
# 输出写入 run_v7_e2e_out.txt，进度实时可见
set -u
cd "D:/depth_pro_photoshop_jsx/0.2.1/源码" || exit 1
PY="D:/depth_v7_builder/python.exe"
LOG="server_v7_test.log"
OUT="run_v7_e2e_out.txt"
: > "$OUT"

taskkill //F //IM depth_server.exe >/dev/null 2>&1
sleep 1
"$PY" depth_server.py > "$LOG" 2>&1 &
SERVER_PID=$!

for i in $(seq 1 60); do
    if curl -s http://127.0.0.1:8766/ping >/dev/null 2>&1; then
        echo "== server ready (${i}s) ==" | tee -a "$OUT"
        break
    fi
    sleep 1
done
curl -s http://127.0.0.1:8766/ping | "$PY" -c "import sys,json; d=json.load(sys.stdin); print('ping ok, models found:', [m['key'] for m in d['available_models'] if m['found']])" | tee -a "$OUT"

run_case() {
    echo "===== $* =====" | tee -a "$OUT"
    "$PY" test_client.py process "$@" 2>&1 | grep -viE "loading weights|it/s" | tail -6 | tee -a "$OUT"
}

run_case --model depthpro --upscale gf --radius 2 --algo S1 --bits 16 --target 1024 683
run_case --model depthpro --upscale bilinear --algo none --bits 8 --target 1024 683
run_case --model distillanydepth --upscale gf --radius 4 --algo S1 --bits 16 --target 1024 683
run_case --model bridge --upscale none --algo S1 --bits 16 --target 1024 683
run_case --model iris --upscale gf --radius 2 --algo S1 --bits 16 --target 1024 683
run_case --model ppd --upscale gf --radius 2 --algo S1 --bits 16 --target 1024 683
run_case --model depthpro --mode fog --upscale bilinear --algo S1 --bits 16 --target 1024 683

taskkill //F //IM depth_server.exe >/dev/null 2>&1
wait $SERVER_PID 2>/dev/null
echo "== e2e done ==" | tee -a "$OUT"
