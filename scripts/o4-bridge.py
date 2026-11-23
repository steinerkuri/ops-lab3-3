#!/usr/bin/env python3
"""仅在本机 lbd 和 lab3-o4 命名空间之间同步后端，不决定副本数。"""
import json
import pathlib
import subprocess
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path('data/o4')
ROOT.mkdir(parents=True, exist_ok=True)
BASE = 'http://127.0.0.1:8090'

def api(path, method='GET'):
    req = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(req, timeout=4) as response:
        body = response.read()
        return json.loads(body) if method == 'GET' else None

def record(**fields):
    print(json.dumps({'unix': time.time(), **fields}, ensure_ascii=False), flush=True)

try:
    while True:
        tick = time.monotonic()
        try:
            result = subprocess.run(
                ['sudo', '-n', 'k3s', 'kubectl', '-n', 'lab3-o4', 'get', 'pods',
                 '-l', 'app=enrolld', '-o', 'json', '--request-timeout=5s'],
                capture_output=True, text=True, check=True, timeout=8)
            pods = json.loads(result.stdout)['items']
            # 预热中的 Running Pod 也登记，init=down；由同一个 lbd 的 /readyz 检查把关。
            wanted = {
                p['status']['podIP'] + ':8081': p['metadata']['name']
                for p in pods if p['status'].get('podIP')
                and p['status'].get('phase') == 'Running'
                and not p['metadata'].get('deletionTimestamp')
            }
            current = {b['addr']: b for b in api('/backends')}
            for addr, pod in wanted.items():
                if addr not in current:
                    api('/backends?' + urllib.parse.urlencode({'addr': addr, 'init': 'down'}), 'POST')
                    record(action='added', addr=addr, pod=pod)
            for addr, backend in current.items():
                if addr not in wanted:
                    query = urllib.parse.urlencode({'addr': addr})
                    api('/drain?' + query, 'POST')
                    if backend.get('inflight', 0) == 0:
                        api('/backends?' + query, 'DELETE')
                        record(action='removed', addr=addr)
            targets = [{'targets': [addr], 'labels': {'pod': pod}}
                       for addr, pod in sorted(wanted.items())]
            tmp = ROOT / 'targets.json.tmp'
            tmp.write_text(json.dumps(targets), encoding='utf-8')
            tmp.replace(ROOT / 'targets.json')
        except Exception as exc:
            # API 不可用时保留现有后端，不把“取不到列表”当成“全部 Pod 消失”。
            record(action='error', detail=str(exc))
        time.sleep(max(0.1, 2 - (time.monotonic() - tick)))
except KeyboardInterrupt:
    record(action='bridge-stopped')
