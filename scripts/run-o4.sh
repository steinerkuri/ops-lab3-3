#!/usr/bin/env bash
# 原封不动调用课程 run3.sh，另存 Kubernetes 观测数据。
set -euo pipefail
name=${1:?用法：bash scripts/run-o4.sh 实验名}
source data/manual.env
hpa_file=${HPA_FILE:-deploy/o4/hpa.yaml}
[[ $name =~ ^[A-Za-z0-9._-]+$ ]] || exit 2
[[ ! -e runs/$name && ! -e data/o4/record-$name ]] || { echo "实验名已存在，请换名"; exit 1; }
[[ ! -e data/o4/instances.csv && ! -e data/o4/decisions.csv ]] || { echo "O4 数据目录混入了 scaler 数据，先排查"; exit 1; }
sudo -v
curl -fsS http://127.0.0.1:8090/backends
echo
tmp=data/o4/record-$name
mkdir "$tmp"
sudo -n taskset -c "$GEN_CPUS" python3 scripts/o4-collect.py "$tmp" &
collector=$!
finish() {
  if [[ -d $tmp ]]; then touch "$tmp/STOP"; fi
  wait "$collector" 2>/dev/null || true
}
trap finish EXIT
for attempt in {1..30}; do
  [[ -s $tmp/k8s-snapshots.jsonl ]] && break
  kill -0 "$collector" 2>/dev/null || { echo "采集器未启动"; exit 1; }
  sleep 1
done
[[ -s $tmp/k8s-snapshots.jsonl ]] || { echo "采集器没有生成数据"; exit 1; }
status=0
DATA=data/o4 GEN_CPUS="$GEN_CPUS" scripts/run3.sh "$name" \
  -url http://127.0.0.1:8080/api/courses/1 -plan "$PLAN" || status=$?
[[ -d runs/$name ]] || exit "$status"
# 负载停止后再观察缩容 6 分钟，保存在同一快照文件，分析时按负载时段裁剪。
if [[ $status -eq 0 ]]; then
  echo "630 秒负载已结束；继续采集缩容约 6 分钟，此时不要停止 HPA 或桥接器。"
  for minute in {1..6}; do sleep 60; echo "缩容观察：已过 $minute 分钟"; done
fi
finish
trap - EXIT
cp "$tmp/k8s-snapshots.jsonl" "runs/$name/"
cp deploy/o4/enrolld.yaml deploy/o4/hpa.yaml deploy/o4/prometheus.yml "runs/$name/"
cp "$hpa_file" "runs/$name/hpa-used.yaml"
cp data/o4/bridge.jsonl "runs/$name/bridge.jsonl"
sudo -v
sudo k3s kubectl -n lab3-o4 get events --sort-by=.metadata.creationTimestamp >"runs/$name/k8s-events.txt"
sudo k3s kubectl -n lab3-o4 get hpa enrolld -o yaml >"runs/$name/hpa-end.yaml"
sudo k3s kubectl version -o json >"runs/$name/k8s-version.json"
k3s --version >"runs/$name/k3s-version.txt"
cp data/manual.env "runs/$name/parameters.env"
python3 scripts/manual-report.py "runs/$name" >"runs/$name/manual-summary.md"
echo "完成；客户端汇总 summary.md，Kubernetes 补充汇总 manual-summary.md。"
exit "$status"
