# O5：HAProxy 与 lbd 对照

本实验使用同一台主机、相同资源限制和固定种子的负载，依次运行五轮：C1 lbd、C1 HAProxy、C3 lbd、C3 HAProxy、C3 lbd离群摘除。每轮预热60秒后加载168请求/秒，持续240秒。总运行时间约26分钟。

## 准备和运行

需要 Go 1.26、用户级 systemd（已委派 CPU、memory 控制器）、CPU 0–2、Python 3、taskset。8080、8090、8101–8103必须空闲。无需sudo，不安装或修改系统服务。

准备时应先验证用户服务的 `systemctl --user kill --kill-whom=main` 和动态CPUQuota功能。本机对所有辅助进程发信号曾返回异常，正式脚本明确只向服务主进程发信号。未完成的首轮保存在 `runs/o5-preflight-c1-lbd/`，其meta标记completed=false，不参与结果对照。

本轮 HAProxy 来自 Ubuntu 软件包 `haproxy=2.8.30-0ubuntu0.24.04.2`，依赖 `liblua5.4-0=5.4.6-3build2`。下载到 `tools/o5-packages/`，用 `dpkg-deb -x` 解包到 `tools/o5-haproxy/`；程序位置为 `tools/o5-haproxy/usr/sbin/haproxy`。脚本设置项目内的动态库路径。软件包和二进制均被 `.gitignore` 排除。

```bash
make build
PYTHONDONTWRITEBYTECODE=1 taskset -c 0-1 python3 scripts/run-o5.py
python3 scripts/summarize-o5.py runs/o5-c1-lbd runs/o5-c1-haproxy runs/o5-c3-lbd runs/o5-c3-haproxy runs/o5-c3-outlier
```

脚本拒绝覆盖已有结果，拒绝操作已有同名用户服务。若需重复实验，应先修改结果和服务命名，保留原结果。中断时脚本恢复暂停实例，然后停止自己创建的实例和代理。只使用 `lab3-o5-*` 用户服务，不使用原 `lab3-inst-*` 系统服务。

## 控制条件

- 实例使用CPU 2，CPUQuota=25%、MemoryMax=128M、MemorySwapMax=0、work=1000000、warmup=60s。
- 辅助进程使用CPU 0–1；三个后端轮询；主动探针GET /readyz，成功状态200，inter=1s、fall=3、rise=2。
- HAProxy的timeout check=500ms只管连接建立后的检查等待，另设timeout connect=500ms；与lbd的单次检查总超时并非完全等价。
- HAProxy禁用重试（retries=0），业务客户端超时2秒；两种代理服务端超时的语义仍有差别，错误码可能不同。
- 不接scaler，避免控制器先移除故障实例。C1第60秒强杀8102，第80秒重建并预热60秒；第160秒暂停8103，第180秒恢复。恢复时间包含脚本等待与预热，不能等同于原C1的scaler补建耗时。
- C3第60秒降额8102，第180秒恢复25%。降额值由首个C3轮的30～55秒CPU用量按round(U/0.85×100)计算，三轮共用。原始用量见resources.jsonl及quota.json。
- HAProxy只配置主动健康检查，不声称具备lbd的跨实例p99离群摘除规则；第三轮C3用于展示此机制区别。

## 证据与口径

每轮保存：实际配置、版本和二进制校验值、cgroup限制、故障命令开始/结束时间、逐秒请求结果、每200ms代理状态、每秒资源用量、代理与实例日志。summary.md/summary.json由汇总脚本派生，原始数据不修改。

两种代理的检出均取状态快照首次观察到不可用的时刻，分辨率约200ms，具体最大间隔写入汇总。原生事件日志用于复核。C3比较的是各阶段“逐秒成功请求p99的中位数”，不是全程p99；缺口为存在错误或成功请求p99超过200ms的秒数。

HAProxy配置依据：[官方健康检查说明](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/reliability/health-checks/)及[2.8配置手册](https://docs.haproxy.org/2.8/configuration.html)。
