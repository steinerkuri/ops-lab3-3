#!/usr/bin/env bash
# 在压测前运行；等待下一次 run3.sh 开始，再在约第 30 秒触发发布。
set -euo pipefail
name=${1:?用法：bash scripts/d-rollout.sh 实验名 naive或graceful 版本}
mode=${2:?缺少模式}
version=${3:?缺少版本}
[[ $name =~ ^[A-Za-z0-9._-]+$ ]] || exit 2
[[ $mode == naive || $mode == graceful ]] || exit 2
[[ $version =~ ^[A-Za-z0-9._-]+$ ]] || exit 2
[[ ! -e runs/$name ]] || { echo "实验名已存在，请给压测和触发脚本同时换名"; exit 1; }
old=$(cat data/run-start 2>/dev/null || true)
echo "已待命：现在去终端 D 启动 $name；不要在这个窗口按 Ctrl-C。"
deadline=$(( $(date +%s) + 300 ))
while :; do
  now=$(cat data/run-start 2>/dev/null || true)
  if [[ $now =~ ^[0-9]+$ && $now != "$old" && -d runs/$name ]]; then break; fi
  [[ $(date +%s) -lt $deadline ]] || { echo "5 分钟内未检测到压测启动，请重新待命"; exit 1; }
  sleep 1
done
while [[ $(date +%s) -lt $((now + 30)) ]]; do sleep 1; done
{
  date -Iseconds
  echo "trigger_unix_ms=$(date +%s%3N)"
  echo "run_start=$now mode=$mode version=$version"
  curl -fsS --max-time 10 -X POST "http://127.0.0.1:8091/rollout?version=$version&mode=$mode"
  echo
  echo "仅表示已受理；完成时间请查看 scaler 日志与 instances.csv。"
} | tee "runs/$name/rollout-control.txt"
