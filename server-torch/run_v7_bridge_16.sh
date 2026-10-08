#!/bin/bash
# bridge 16-bit 输入 + 16-bit 输出验证（自包含单命令）
set -u
cd "D:/depth_pro_photoshop_jsx/0.2.1/源码" || exit 1
PY="D:/depth_v7_builder/python.exe"
LOG="server_v7_test.log"
OUT="run_v7_bridge16_out.txt"
: > "$OUT"
taskkill //F //IM depth_server.exe >/dev/null 2>&1
sleep 1
"$PY" depth_server.py > "$LOG" 2>&1 &
SERVER_PID=$!
for i in $(seq 1 60); do
    curl -s http://127.0.0.1:8766/ping >/dev/null 2>&1 && { echo "== ready ${i}s ==" | tee -a "$OUT"; break; }
    sleep 1
done
run_case() {
    echo "===== $* =====" | tee -a "$OUT"
    "$PY" test_client.py process "$@" 2>&1 | grep -viE "loading weights|it/s" | tail -6 | tee -a "$OUT"
}
run_case --model bridge --upscale none --algo S1 --bits 16 --target 1024 683
run_case --model bridge --upscale gf --radius 2 --algo S1 --bits 16 --target 1024 683
run_case --model depthpro --upscale bilinear --algo S1 --bits 16 --target 1024 683
run_case --model iris --upscale gf --radius 2 --algo S1 --bits 16 --target 1024 683
taskkill //F //PID $SERVER_PID >/dev/null 2>&1
taskkill //F //IM depth_server.exe >/dev/null 2>&1
wait $SERVER_PID 2>/dev/null
echo "== done ==" | tee -a "$OUT"
