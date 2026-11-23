#!/usr/bin/env python3
"""每约 2 秒保存 API 选定字段的原始观测；时间为采集时刻，不冒充精确决策时刻。"""
import json
import pathlib
import subprocess
import sys
import time

dest = pathlib.Path(sys.argv[1])
dest.mkdir(parents=True, exist_ok=True)
stop = dest / 'STOP'
def kubectl(*args):
    result = subprocess.run(['sudo', '-n', 'k3s', 'kubectl', *args, '--request-timeout=5s'],
                            capture_output=True, text=True, check=True, timeout=9)
    return json.loads(result.stdout)

with (dest / 'k8s-snapshots.jsonl').open('x', encoding='utf-8', buffering=1) as out:
    while not stop.exists():
        tick = time.monotonic()
        try:
            result = kubectl('-n', 'lab3-o4', 'get', 'pods,deployments,hpa',
                             '-l', 'app=enrolld', '-o', 'json')
            record = {'unix': time.time(), 'pods': [], 'deployments': [], 'hpas': []}
            for obj in result['items']:
                m, s = obj['metadata'], obj.get('status', {})
                if obj['kind'] == 'Pod':
                    # 不采集 hostIP / hostIPs，避免把主机地址写进报告证据。
                    record['pods'].append({
                        'name': m['name'], 'uid': m['uid'],
                        'created': m['creationTimestamp'], 'deleting': m.get('deletionTimestamp'),
                        'ip': s.get('podIP'), 'phase': s.get('phase'),
                        'conditions': s.get('conditions', []),
                        'containers': s.get('containerStatuses', [])})
                elif obj['kind'] == 'Deployment':
                    record['deployments'].append({'name': m['name'], 'spec': obj['spec'], 'status': s})
                elif obj['kind'] == 'HorizontalPodAutoscaler':
                    record['hpas'].append({'name': m['name'], 'spec': obj['spec'], 'status': s})
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
        except Exception as exc:
            out.write(json.dumps({'unix': time.time(), 'error': str(exc)}, ensure_ascii=False) + '\n')
        time.sleep(max(0.1, 2 - (time.monotonic() - tick)))
