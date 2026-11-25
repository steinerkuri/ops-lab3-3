#!/usr/bin/env python3
"""Derive O5 summaries from preserved CSV/JSONL files; never alter raw data."""
import csv
import io
import json
from pathlib import Path
import statistics
import sys


def read_csv(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def summarize(root):
    meta = json.loads((root/'meta.json').read_text())
    if not meta.get('completed'):
        raise ValueError(f'{root}: incomplete experiment')
    timeline = read_csv(root/'timeline.csv')
    overall = next(r for r in read_csv(root/'steps.csv') if r['step']=='all')
    marks = read_csv(root/'marks.csv')
    snapshots = [json.loads(s) for s in (root/'proxy-snapshots.jsonl').read_text().splitlines()]
    start = meta['start_unix']
    previous = {}; transitions = []; sample_errors = 0
    for sample in snapshots:
        if 'error' in sample:
            sample_errors += 1
            continue
        if meta['proxy']=='haproxy':
            servers = [r for r in csv.DictReader(io.StringIO(sample['data'].removeprefix('# ')))
                       if r['pxname']=='enrolld' and r['svname'].startswith('s81')]
            states = {r['svname'][1:]:r['status'].split()[0]=='UP' for r in servers}
        else:
            states = {r['addr'].split(':')[-1]:r['eligible'] for r in sample['data']}
        for port, up in states.items():
            if port in previous and previous[port]!=up:
                transitions.append({'elapsed':sample['unix']-start,'port':port,'up':up})
            previous[port] = up
    detections = []
    for mark in marks:
        if mark['action'] not in ('kill','pause','slow'):
            continue
        t = int(mark['unix_ms'])/1000-start
        down = next((e for e in transitions if e['port']==mark['target'] and not e['up'] and e['elapsed']>=t),None)
        up = next((e for e in transitions if down and e['port']==mark['target'] and e['up'] and e['elapsed']>down['elapsed']),None)
        detections.append({'action':mark['action'],'target':mark['target'],'injected_s':t,
                           'detected_after_s':None if not down else down['elapsed']-t,
                           'up_after_s':None if not up else up['elapsed']-t})
    # Phase bins use loadgen relative seconds; subsecond injection edges remain approximate.
    phase_stats = {}
    for name, lo, hi in [('before',0,60),('during',60,180),('after',180,240)]:
        rows = [r for r in timeline if lo<=int(r['sec'])<hi]
        p99 = [float(r['p99_ms']) for r in rows if int(r['ok'])>0]
        phase_stats[name] = {'seconds':len(rows),'errors':sum(int(r['errors']) for r in rows),
                            'median_second_p99_ms':statistics.median(p99) if p99 else None,
                            'max_second_p99_ms':max(p99) if p99 else None}
    summary = {'run':root.name,'overall':overall,
               'gap_seconds':sum(int(r['errors'])>0 or float(r['p99_ms'] or 0)>200 for r in timeline),
               'slow':sum(int(r['slow']) for r in timeline),'sample_errors':sample_errors,
               'max_sample_interval_s':max(b['unix']-a['unix'] for a,b in zip(snapshots,snapshots[1:])),
               'transitions':transitions,'detections':detections,'phases':phase_stats}
    (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    lines = [f'# {root.name} 实测汇总','',
             f"发出 {overall['sent']}；成功 {overall['ok']}；HTTP错误 {overall['http_err']}；超时 {overall['timeout']}；网络错误 {overall['net_err']}。",
             f"全程成功请求p99：{overall['p99_ms']} ms；慢请求：{summary['slow']}；缺口：{summary['gap_seconds']}秒。",'',
             '缺口定义：该秒存在错误或成功请求p99大于200ms。逐秒p99的中位数不是全程p99。','',
             '| 注入 | 实例 | 注入后检出（秒） | 注入后恢复（秒） |',
             '|---|---|---:|---:|']
    def fmt(v):
        return '未观察到' if v is None else f'{v:.3f}'
    for d in detections:
        lines.append(f"| {d['action']} | {d['target']} | {fmt(d['detected_after_s'])} | {fmt(d['up_after_s'])} |")
    lines += ['', '两种代理均以约200ms状态采样首次观察到不可用为检出，不等同于日志精确事件时刻。恢复从注入起计，包含故障持续和预热；未检出时恢复列不适用，不表示故障未恢复。',
              f"采样错误：{sample_errors}；最大采样间隔：{summary['max_sample_interval_s']:.3f}秒。",'',
              '| 时段 | 采样秒数 | 错误 | 逐秒p99中位数（ms） | 逐秒p99最大值（ms） |',
              '|---|---:|---:|---:|---:|']
    for name,r in phase_stats.items():
        lines.append(f"| {name} | {r['seconds']} | {r['errors']} | {fmt(r['median_second_p99_ms'])} | {fmt(r['max_second_p99_ms'])} |")
    lines += ['', '阶段按loadgen相对秒划分为[0,60)、[60,180)、[180,240)，秒级桶与精确注入边界存在不到一秒的对齐误差。', '',
              '原始依据：steps.csv、timeline.csv、marks.csv、proxy-snapshots.jsonl、meta.json；lbd精确事件另见lbd-events.csv，HAProxy日志另见proxy.log。','']
    (root/'summary.md').write_text('\n'.join(lines))
    print(root.name, 'gap',summary['gap_seconds'],'p99',overall['p99_ms'],'detections',detections)


if __name__=='__main__':
    for arg in sys.argv[1:]:
        summarize(Path(arg))
