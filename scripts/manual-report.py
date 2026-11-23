#!/usr/bin/env python3
"""只读原始输出，生成独立派生报告，不修改原始文件。"""
import csv
import datetime as dt
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
with (root / 'timeline.csv').open() as f:
    rows = list(csv.DictReader(f))
if not rows:
    raise SystemExit('timeline.csv 没有数据')
t0 = min(int(r['unix']) for r in rows)
t1 = max(int(r['unix']) for r in rows) + 1
sent = sum(int(r['sent']) for r in rows)
ok = sum(int(r['ok']) for r in rows)
errors = sum(int(r['errors']) for r in rows)
bad = [r for r in rows if int(r['errors']) > 0 or (r['p99_ms'] and float(r['p99_ms']) > 200)]
print(f'# {root.name} 补充汇总\n')
print(f'发出 {sent}；成功 {ok}；错误 {errors}；成功率 {ok/sent:.4%}；缺口 {len(bad)} 秒。\n')
print('缺口阈值为 200 ms；p99 只统计成功请求。不可平均逐秒 p99 得到全程 p99。\n')

instfile = root / 'instances.csv'
if instfile.exists():
    with instfile.open() as f:
        events = list(csv.DictReader(f))
    print('## 负载期间的替换、就绪和重启事件\n')
    print('| 负载后秒数 | 实例 | 版本 | 事件 | 详情 |\n|---|---|---|---|---|')
    for r in events:
        sec = int(r['unix_ms']) / 1000 - t0
        if 0 <= sec < t1-t0 and r['event'] in {'created','started','ready','draining','stopped','restarted','assumed-up'}:
            detail = r['detail'].replace('|', '/')
            print(f"| {sec:.3f} | {r['instance']} | {r['version']} | {r['event']} | {detail} |")

snapfile = root / 'k8s-snapshots.jsonl'
if snapfile.exists():
    records = [json.loads(line) for line in snapfile.read_text().splitlines() if line.strip()]
    samples = sorted((r for r in records if 'pods' in r), key=lambda r:r['unix'])
    print('\n## HPA / Deployment 变化的观测时刻\n')
    print('| 负载后秒数 | HPA 当前 | HPA 期望 | Deployment 期望 | Ready |\n|---|---|---|---|---|')
    previous = None
    for s in samples:
        h = (s['hpas'] or [{}])[0].get('status', {})
        d = (s['deployments'] or [{}])[0]
        state = (h.get('currentReplicas'),h.get('desiredReplicas'),d.get('spec',{}).get('replicas'),d.get('status',{}).get('readyReplicas',0))
        if state != previous:
            print('| ' + f"{s['unix']-t0:.3f}" + ' | ' + ' | '.join(map(str,state)) + ' |')
            previous = state
    area = 0.0
    uncovered = max(0, samples[0]['unix']-t0) if samples else t1-t0
    for a,b in zip(samples,samples[1:]):
        left,right = max(t0,a['unix']),min(t1,b['unix'])
        if right > left:
            area += (right-left)*len(a['pods'])
            if b['unix']-a['unix'] > 5:
                uncovered += right-left
    if samples:
        uncovered += max(0,t1-samples[-1]['unix'])
    print(f'\n负载期间 Pod 存在·分钟（快照左端矩形近似，含 Terminating）：{area/60:.3f}。')
    print(f'采样缺失或间隔超过 5 秒的覆盖警告：{uncovered:.3f} 秒；明显缺失时不要把成本数当作可靠结论。')
    print('此口径不含 scaler 模拟拉镜像等待，与课程实例·分钟不能直接视作完全相同的成本。\n')
    pods = {}
    def epoch(value):
        return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
    for s in samples:
        for p in s['pods']:
            item = pods.setdefault(p['uid'], {'name':p['name'],'created':p['created'],'started':None,'ready':None})
            for c in p['containers']:
                value = c.get('state',{}).get('running',{}).get('startedAt')
                if value and (item['started'] is None or value < item['started']): item['started']=value
            for c in p['conditions']:
                value = c.get('lastTransitionTime')
                if c['type']=='Ready' and c['status']=='True' and value:
                    if item['ready'] is None or value < item['ready']: item['ready']=value
    print('| Pod | 创建（秒） | 容器启动（秒） | 首次观察到的 Ready 转换（秒） |\n|---|---|---|---|')
    for p in pods.values():
        values = [f'{epoch(p[k])-t0:.3f}' if p[k] else '未观测到' for k in ('created','started','ready')]
        print('| '+p['name']+' | '+' | '.join(values)+' |')
    print('\n负数代表压测前已存在。状态快照不能保证捕捉到两次采样之间的所有短暂变化。')
