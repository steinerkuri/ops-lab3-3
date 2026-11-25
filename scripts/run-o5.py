#!/usr/bin/env python3
"""O5 matched experiments. No sudo; only lab3-o5-* user services are managed.
Run from any directory: python3 scripts/run-o5.py
Requires make build and project-local HAProxy + liblua (see deploy/o5).
Never overwrites run directories. SIGINT/SIGTERM resumes and stops owned services.
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess as sp
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
ENV = dict(os.environ, LD_LIBRARY_PATH=str(ROOT / 'tools/o5-haproxy/usr/lib/x86_64-linux-gnu'))
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
HAP = ROOT / 'tools/o5-haproxy/usr/sbin/haproxy'
PORTS = (8101, 8102, 8103)
UNITS = [f'lab3-o5-{p}' for p in PORTS]
RUNS = [('c1-lbd', 'lbd', 'c1'), ('c1-haproxy', 'haproxy', 'c1'),
        ('c3-lbd', 'lbd', 'c3'), ('c3-haproxy', 'haproxy', 'c3'),
        ('c3-outlier', 'outlier', 'c3')]


def command(args, check=True):
    return sp.run(list(map(str, args)), text=True, stdout=sp.PIPE, stderr=sp.STDOUT,
                  check=check, env=ENV).stdout


def ctl(*args, check=True):
    return command(['systemctl', '--user', *args], check)


def http(port, path):
    with HTTP.open(f'http://127.0.0.1:{port}{path}', timeout=.4) as r:
        return r.read().decode()


def stats():
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(.4)
        s.connect(str(ROOT / 'data/o5-haproxy.sock'))
        s.sendall(b'show stat\n')
        data = b''
        while True:
            b = s.recv(65536)
            if not b:
                break
            data += b
    return data.decode()


def start_instance(port, run):
    command(['systemd-run', '--user', f'--unit=lab3-o5-{port}', '--collect',
             '-p', 'CPUQuota=25%', '-p', 'MemoryMax=128M', '-p', 'MemorySwapMax=0',
             '-p', 'CPUAffinity=2', '-p', 'Restart=no',
             f'--working-directory={ROOT}', ROOT/'bin/enrolld',
             '-addr', f'127.0.0.1:{port}', '-work', '1000000', '-warmup', '60s',
             '-data', ROOT/'data/o5'/run/str(port)])


def shutdown(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except sp.TimeoutExpired:
            process.kill()
            process.wait()


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    # Fail before changing anything if a prior run or listener exists.
    for name, _, _ in RUNS:
        if (ROOT/'runs'/f'o5-{name}').exists():
            raise SystemExit(f'Refusing to overwrite runs/o5-{name}')
    for port in (*PORTS, 8080, 8090):
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('127.0.0.1', port))
    for unit in UNITS:
        state = ctl('show', unit, '-p', 'LoadState', '--value', check=False).strip()
        if state != 'not-found':
            raise SystemExit(f'Refusing to touch pre-existing {unit}: {state}')
    (ROOT/'data').mkdir(exist_ok=True)
    command([HAP, '-c', '-f', ROOT/'deploy/o5/haproxy.cfg'])
    quota = None
    for name, kind, scenario in RUNS:
        run = f'o5-{name}'
        dest = ROOT/'runs'/run
        dest.mkdir()
        print(f'{run}: starting three instances, warming up 60s', flush=True)
        proxy = load = None
        handles = []
        try:
            for p in PORTS:
                start_instance(p, run)
            deadline = time.monotonic()+90
            while True:
                try:
                    ready = all(http(p, '/readyz') == 'ready\n' for p in PORTS)
                except Exception:
                    ready = False
                if ready:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError('instances failed readiness')
                time.sleep(.5)
            unit_props = ''.join(ctl('show', u, '-p', 'ControlGroup', '-p', 'CPUQuotaPerSecUSec',
                                    '-p', 'MemoryMax', '-p', 'MemorySwapMax', '-p', 'CPUAffinity')
                                 for u in UNITS)
            (dest/'unit-properties.txt').write_text(unit_props)
            cg = {}
            for p, u in zip(PORTS, UNITS):
                group = ctl('show', u, '-p', 'ControlGroup', '--value').strip()
                cg[p] = Path('/sys/fs/cgroup') / group.lstrip('/')
            (dest/'cgroup-settings.json').write_text(json.dumps({p:{f:(cg[p]/f).read_text()
                 for f in ('cpu.max','memory.max','memory.swap.max')} for p in PORTS}, indent=2))
            log = open(dest/'proxy.log', 'w'); handles.append(log)
            if kind == 'haproxy':
                shutil.copy(ROOT/'deploy/o5/haproxy.cfg', dest/'haproxy.cfg')
                args = [HAP, '-db', '-f', ROOT/'deploy/o5/haproxy.cfg']
            else:
                args = [ROOT/'bin/lbd', '-backends', ','.join(f'127.0.0.1:{p}' for p in PORTS),
                        '-events', dest/'lbd-events.csv']
                if kind == 'outlier':
                    args.append('-outlier')
            proxy = sp.Popen(list(map(str, ['taskset', '-c', '0-1', *args])),
                             stdout=log, stderr=sp.STDOUT, env=ENV)
            time.sleep(3)
            if proxy.poll() is not None:
                raise RuntimeError('proxy exited; see proxy.log')
            if kind != 'haproxy':
                (dest/'lbd-config.json').write_text(http(8090, '/config'))
            load_args = ['taskset','-c','0-1', str(ROOT/'bin/loadgen'), '-url',
                         'http://127.0.0.1:8080/api/courses/1', '-plan', '168@240s',
                         '-seed','1','-slo','200ms','-timeout','2s','-stop-below','0',
                         '-out',str(dest/'steps.csv'),'-timeline',str(dest/'timeline.csv')]
            meta = {'run':run,'proxy':kind,'scenario':scenario,'hostname':socket.gethostname(),
                    'uname':command(['uname','-a']).strip(),'go':command(['go','version']).strip(),
                    'haproxy':command([HAP,'-v']),'load_command':load_args,
                    'proxy_command':list(map(str,args)), 'systemd_scope':'user',
                    'work':1000000,'warmup_s':60,'cpu_quota':25,'instance_cpu':'2',
                    'auxiliary_cpus':'0-1','scaler':False,'slow_quota':quota,
                    'git_head':command(['git','rev-parse','HEAD']).strip(),
                    'sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              [Path('scripts/run-o5.py'),Path('deploy/o5/haproxy.cfg'),Path('bin/enrolld'),Path('bin/lbd'),Path('bin/loadgen')]}}
            start = time.monotonic(); epoch = time.time(); meta['start_unix'] = epoch
            (dest/'meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
            lf = open(dest/'loadgen.log','w'); handles.append(lf)
            load = sp.Popen(load_args, stdout=lf, stderr=sp.STDOUT)
            raw = open(dest/'proxy-snapshots.jsonl','w'); handles.append(raw)
            resources = open(dest/'resources.jsonl','w'); handles.append(resources)
            marks = open(dest/'marks.csv','w',buffering=1); handles.append(marks)
            writer = csv.writer(marks); writer.writerow(['unix_ms','elapsed_s','action','target','detail'])
            events = set(); cpu30 = None; last_resource = -1; next_sample = start
            def mark(action, port, detail=''):
                writer.writerow([round(time.time()*1000),f'{time.monotonic()-start:.3f}',action,port,detail])
                print(f'{run}: {time.monotonic()-start:.1f}s {action} {port} {detail}',flush=True)
            print(f'{run}: load started',flush=True)
            while load.poll() is None:
                elapsed = time.monotonic()-start
                schedule = [(60,'kill'),(80,'restart'),(160,'pause'),(180,'resume')] if scenario=='c1' else [(60,'slow'),(180,'restore')]
                for when, action in schedule:
                    if elapsed >= when and action not in events:
                        events.add(action)
                        p = 8103 if action in ('pause','resume') else 8102
                        detail = str(quota) if action=='slow' else ''
                        mark(action,p,detail)
                        if action=='kill':
                            ctl('kill','--kill-whom=main','--signal=SIGKILL',f'lab3-o5-{p}')
                        elif action=='restart':
                            ctl('reset-failed',f'lab3-o5-{p}',check=False)
                            start_instance(p,run)
                        elif action in ('pause','resume'):
                            ctl('kill','--kill-whom=main',f'--signal={"SIGSTOP" if action=="pause" else "SIGCONT"}',f'lab3-o5-{p}')
                        else:
                            ctl('set-property','--runtime',f'lab3-o5-{p}',f'CPUQuota={quota if action=="slow" else 25}%')
                        mark(action+'-done',p,detail)
                row = {'unix':time.time(),'elapsed':elapsed}
                try:
                    row['data'] = stats() if kind=='haproxy' else json.loads(http(8090,'/backends'))
                except Exception as e:
                    row['error'] = str(e)
                raw.write(json.dumps(row)+'\n'); raw.flush()
                if int(elapsed) > last_resource:
                    last_resource = int(elapsed)
                    rr = {'unix':time.time(),'elapsed':elapsed,'instances':{}}
                    for p in PORTS:
                        try:
                            values = {k:int(v) for k,v in (line.split() for line in (cg[p]/'cpu.stat').read_text().splitlines())}
                            rr['instances'][str(p)]={'cpu':values,'memory':(cg[p]/'memory.current').read_text().strip(),
                                                    'cpu_max':(cg[p]/'cpu.max').read_text().strip()}
                        except FileNotFoundError:
                            rr['instances'][str(p)]={'absent':True}
                    resources.write(json.dumps(rr)+'\n'); resources.flush()
                    if scenario=='c3' and quota is None:
                        if elapsed >=30 and cpu30 is None:
                            cpu30=(elapsed,rr['instances']['8102']['cpu']['usage_usec'])
                        if elapsed >=55 and cpu30 is not None:
                            usage=(rr['instances']['8102']['cpu']['usage_usec']-cpu30[1])/1e6/(elapsed-cpu30[0])
                            quota=max(1,min(24,int(usage/.85*100+.5)))
                            (dest/'quota.json').write_text(json.dumps({'U_cores':usage,'window_start':cpu30[0],
                                'window_end':elapsed,'formula':'round(U / 0.85 * 100)','quota_percent':quota},indent=2))
                            meta['slow_quota']=quota
                            print(f'{run}: measured U={usage:.5f}, shared slow quota={quota}%',flush=True)
                    if last_resource % 30 == 0:
                        print(f'{run}: elapsed {elapsed:.0f}s',flush=True)
                next_sample += .2
                time.sleep(max(0,next_sample-time.monotonic()))
            if load.returncode:
                raise RuntimeError(f'loadgen exited {load.returncode}')
            meta['end_unix']=time.time(); meta['completed']=True
            (dest/'meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
            for p in PORTS:
                try:
                    (dest/f'metrics-{p}-end.txt').write_text(http(p,'/metrics'))
                except Exception as e:
                    (dest/f'metrics-{p}-error.txt').write_text(str(e))
            if kind!='haproxy':
                (dest/'lbd-metrics-end.txt').write_text(http(8090,'/metrics'))
            for u in UNITS:
                (dest/f'{u}.log').write_text(command(['journalctl','--user','-u',u,'--since',f'@{int(epoch)-100}', '--no-pager']))
            print(f'{run}: complete',flush=True)
        finally:
            shutdown(load)
            for u in UNITS:
                ctl('kill','--kill-whom=main','--signal=SIGCONT',u,check=False)
            ctl('stop',*UNITS,check=False)
            shutdown(proxy)
            for h in handles:
                h.close()
    print('All five O5 runs complete.',flush=True)


if __name__=='__main__':
    signal.signal(signal.SIGTERM,interrupted)
    main()
