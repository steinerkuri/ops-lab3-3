# 实验 3 记录：第 3 组

评分时依据本文件和 `runs/` 中的原始数据核对。本文件中的每一个数值，都须能在某个 `runs/<名字>/` 的文件中找到：表格用 `scripts/table.sh` 生成或从 `summary.md` 中复制后粘贴，其他数值注明出处（例如“`runs/b2-cpu/summary.md` 缺口表第 1 行”）。不要只贴截图。将 `<填写>` 替换为本组的内容。

标有“预测”的小节须在对应的测量之前写好，写好后不要修改。预测错误不扣分，与实测不一致之处在分析中讨论。

## 1. 基本信息

| 项 | 内容 |
|---|---|
| 小组 | 第 3 组 |
| 仓库地址 | <https://github.com/steinerkuri/ops-lab3-3> |
| 主机 | 华为云弹性云服务器 ECS，通用计算型 x1.4u.8g，4 vCPU、8 GiB |
| CPU | General Purpose Processor；4 vCPU |
| 内存与 swap | `free -m` 的 total 为 7556 MiB；无 swap |
| 操作系统、内核、systemd | Ubuntu 24.04.5 LTS；`6.8.0-147-generic`；`systemd 255 (255.4-1ubuntu8.17)` |
| cgroup | `cgroup2fs`，即 cgroup v2；控制器为 `cpuset cpu io memory hugetlb pids rdma misc` |
| Go、Prometheus、node_exporter | `go version go1.26.9 linux/amd64`；Prometheus 3.13.4；node_exporter 1.12.1 |
| 分核 | 实例：CPU 2；loadgen、lbd、scaler、Prometheus、node_exporter：CPU 0-1。任务 A 的 `lab3-a` 按指导书不单独绑核，与 loadgen 共用 CPU 0，作为测量局限说明。 |

## 2. 任务 A：配额，节流与 OOM

### 2.1 A1 配额算术

预测（测量之前写）：配额 0.5 核、周期 100 ms 时，1 个和 2 个线程跑满，每个线程每个周期运行多久、被停多久？本机上 enrolld 的 GOMAXPROCS 会是几，为什么？

配额为 0.5 核、周期为 100 ms，即整个 cgroup 每周期最多获得 50 ms CPU 时间。只有 1 个忙线程时，预计该线程每周期运行 50 ms、被停 50 ms；有 2 个忙线程时，两者共享 50 ms 配额，平均每个线程运行 25 ms、被停 75 ms。本机有 4 个逻辑 CPU，enrolld 未额外绑核，而 cgroup 限额向上取整为 1；Go 1.25 起默认 GOMAXPROCS 不小于 2（单核主机除外），因此预测 GOMAXPROCS 为 2。

两次 `-burn` 的输出：

```text
===== burn=1 =====
cgroup：/sys/fs/cgroup/system.slice/run-rc225e1ac05f84b0a86ec4d8ae9742783.scope
cpu.max：配额 0.50 核，周期 100ms（每个周期最多 50 ms CPU 时间）
GOMAXPROCS=2，忙线程 1 个，计数 10.0 秒
实际用核 0.50；周期 100 个，被节流 100 个（100%）；被停共 5.06 秒（按 CPU 累计）
每个线程每个周期被停：实测 50.6 ms，算术 50.0 ms
===== burn=2 =====
cgroup：/sys/fs/cgroup/system.slice/run-r152d0f0ccac84c43b4908363c4e91f96.scope
cpu.max：配额 0.50 核，周期 100ms（每个周期最多 50 ms CPU 时间）
GOMAXPROCS=2，忙线程 2 个，计数 10.0 秒
实际用核 0.50；周期 100 个，被节流 100 个（100%）；被停共 15.18 秒（按 CPU 累计）
每个线程每个周期被停：实测 75.9 ms，算术 75.0 ms
```

| 线程数 | 周期数 | 被节流的周期 | 实际用核 | 每线程每周期被停（实测） | 算术 |
|---|---|---|---|---|---|
| 1 | 100 | 100 | 0.50 | 50.6ms | 50.0ms |
| 2 | 100 | 100 | 0.50 | 75.9ms | 75.0ms |

### 2.2 A2 节流与 p99

本组写的节流比例查询（与 `deploy/queries-red.txt` 一致）：

```text
throttle_ratio    sum(rate(cgroup_cpu_throttled_periods_total{job="enrolld"}[${window}])) / sum(rate(cgroup_cpu_periods_total{job="enrolld"}[${window}]))  
```

配额 100% 时测得的每请求 CPU：4.25536 ms（`runs/a2-base/red.csv` 的 `cpu_ms_per_req`）；据此选定的到达率：94 请求/秒，平均约 0.4 核。

预测（配额改为 50% 之前写）：平均用量低于配额，p99 会不会变？节流比例大约多少？
虽然平均需求约为 0.4 核，低于 0.5 核配额，但 GOMAXPROCS=2，多个请求可能同时在两个核上运行，使 100 ms 周期内的 50 ms CPU 预算提前耗尽。因此预测吞吐仍可能接近目标值，但 `throttle_ratio`、`throttled_s_per_s` 和 p99 会升高。节流比例依赖 Poisson 请求在各周期内的到达时刻，不能仅凭平均 CPU 用量精确计算；预测其明显大于 0，但不会像 A1 的持续满载那样接近 100%。

各次实验的数据（`scripts/table.sh runs/<名字> ok_rps p50_ms p99_ms server_p99_ms gomaxprocs cg_limit_cores cg_cpu_cores throttle_ratio throttled_s_per_s`，每次一张表）：

配额 100% 的基线（`runs/a2-base`）：

| step | target_rps | ok_rps | p50_ms | p99_ms | server_p99_ms | cpu_ms_per_req | gomaxprocs | cg_limit_cores | cg_cpu_cores | throttle_ratio | throttled_s_per_s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 30 | 28.8 | 6.90 | 7.51 | 9.95 | 4.35841 | 2 | 1 | 0.128886 | 0 | 0 |
| 2 | 60 | 63.5 | 6.84 | 9.51 | 9.95 | 4.25536 | 2 | 1 | 0.26664 | 0 | 0 |
| all | - | 46.1 | 6.86 | 9.13 | - | - | - | - | - | - | - |

配额 50%（`runs/a2-q50`）：

| step | target_rps | ok_rps | p50_ms | p99_ms | server_p99_ms | gomaxprocs | cg_limit_cores | cg_cpu_cores | throttle_ratio | throttled_s_per_s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 94 | 96.7 | 6.90 | 40.01 | 41.2559 | 2 | 0.5 | 0.412923 | 0.3 | 0.0884671 |
| 2 | 94 | 91.2 | 6.89 | 35.07 | 19.6824 | 2 | 0.5 | 0.374038 | 0.157143 | 0.037328 |
| all | - | 93.9 | 6.90 | 38.42 | - | - | - | - | - | - |

改法一：按峰值用量除以 0.7 配置配额。根据第一阶段的 `cg_cpu_cores=0.412923`：

```text
0.412923 / 0.7 = 0.58989
```

因此将配额向上取为 60%。结果见 `runs/a2-q60`：

| step | target_rps | ok_rps | p50_ms | p99_ms | server_p99_ms | gomaxprocs | cg_limit_cores | cg_cpu_cores | throttle_ratio | throttled_s_per_s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 94 | 96.7 | 6.89 | 17.17 | 13.05 | 2 | 0.6 | 0.41657 | 0.0928571 | 0.0178504 |
| 2 | 94 | 91.2 | 6.83 | 12.17 | 9.98982 | 2 | 0.6 | 0.37253 | 0.05 | 0.0105905 |
| all | - | 93.9 | 6.86 | 16.40 | - | - | - | - | - | - |

改法二：保持 50% 配额，将 GOMAXPROCS 限制为 1。结果见 `runs/a2-q50-gmp1`：

| step | target_rps | ok_rps | p50_ms | p99_ms | server_p99_ms | gomaxprocs | cg_limit_cores | cg_cpu_cores | throttle_ratio | throttled_s_per_s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 94 | 96.7 | 8.46 | 42.69 | 39.0333 | 1 | 0.5 | 0.404794 | 0.278571 | 0.0456931 |
| 2 | 94 | 91.2 | 8.39 | 40.32 | 21.62 | 1 | 0.5 | 0.36396 | 0.114286 | 0.0207024 |
| all | - | 93.9 | 8.43 | 41.77 | - | - | - | - | - | - |

### 2.3 A3 OOM

预测（测量之前写）：`/api/report?mb=8&ms=500` 在到达率 10、20、30 时平均和最多各有几个在途请求？RSS 大约多少？在 `MemoryMax=200M` 下会在哪一个阶段被杀？
每个报表请求额外占用 8 MB，并保持 500 ms。按照 Little's Law，平均在途请求数为 `L = λW`，因此 10、20、30 RPS 时预计平均分别有 5、10、15 个在途请求。最大在途数无法仅由 Little's Law 确定；由于采用 Poisson 到达，预计会高于平均值，并随短时突发变化。

A2 中服务基线 RSS 约为 13 MB。考虑 `GOGC=100` 时 Go 堆可能增长到存活数据约 2 倍才触发 GC，初步估算：

```text
10 RPS：13 + 5 × 8 × 2 = 93 MB
20 RPS：13 + 10 × 8 × 2 = 173 MB
30 RPS：13 + 15 × 8 × 2 = 253 MB
```

因此测量前预测 10、20 RPS 可以存活，30 RPS 会超过 `MemoryMax=200M` 而被杀。该估算没有完整考虑 Poisson 突发、goroutine 栈、Go 运行时元数据和非堆内存，实际可能更早触发 OOM。

原始 OOM 实验（`runs/a3-oom`）：

| step | target_rps | ok_rps | error_rate | p99_ms | inflight_avg | report_inflight_max | rss_max_mb | heap_max_mb | mem_current_max_mb | mem_limit_mb |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 10 | 9.1 | 0.0000 | 505.23 | 4.73333 | 8 | 138.434 | 113.421 | 133.488 | 200 |
| 2 | 20 | 1.8 | 0.9091 | 505.03 | - | - | - | - | - | - |
| all | - | 5.5 | 0.6228 | 505.18 | - | - | - | - | - | - |

实测在 20 RPS 阶段即被 OOM kill，早于预测的 30 RPS。服务死亡较快，Prometheus 来不及抓取第二阶段完整的服务指标。第一阶段实测最大在途数已经达到 8，高于平均值 4.73333；RSS 为 138.434 MB，也明显高于仅按平均存活数据得到的简单估算，说明 GC 前堆膨胀和瞬时峰值不可忽略。

改法一：按实测峰值提高 `MemoryMax`。最终成功配置为 370M，结果见 `runs/a3-mem370`：

| step | target_rps | ok_rps | error_rate | p99_ms | inflight_avg | report_inflight_max | rss_max_mb | heap_max_mb | mem_current_max_mb | mem_limit_mb |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 20 | 18.6 | 0.0000 | 505.31 | 9.53333 | 18 | 306.922 | 297.578 | 302.211 | 370 |
| 2 | 20 | 21.1 | 0.0000 | 505.12 | 9.86667 | 16 | 298.918 | 273.579 | 294.262 | 370 |
| all | - | 19.9 | 0.0000 | 505.25 | - | - | - | - | - | - |

容量估算经历了两次修正。第一次根据 10 RPS 的最大在途数 8，按比例估计 20 RPS 峰值为 16，得到约 300M，但 `runs/a3-mem300` 中实测最大在途达到 18，服务仍被杀。随后取 340M，`runs/a3-mem340` 中服务存活更久后观测到最大在途 20，RSS 达到 338.941 MB，仍被杀。最终按最新峰值计算：

```text
(基线 13 + 最大在途 20 × 8 MB × 2) × 1.10
= 366.3 MB
```

向上取 `MemoryMax=370M`。两段 20 RPS 均零错误，服务保持存活，验证成功。较低的内存上限会提前杀死服务，从而截断对真实峰值的观察，因此容量规划需要迭代测量。

改法二：保持 `MemoryMax=200M`，使用 `-report-max 10` 限制报表并发。结果见 `runs/a3-report10`：

| step | target_rps | ok_rps | error_rate | p99_ms | inflight_avg | report_inflight_max | rss_max_mb | heap_max_mb | mem_current_max_mb | mem_limit_mb |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 10 | 9.1 | 0.0000 | 505.46 | 4.2 | 8 | 134.867 | 105.403 | 130.156 | 200 |
| 2 | 20 | 15.4 | 0.2222 | 504.95 | 7.33333 | 10 | 170.504 | 129.458 | 165.594 | 200 |
| 3 | 30 | 17.9 | 0.4270 | 504.62 | 9.13333 | 10 | 170.805 | 153.46 | 165.879 | 200 |
| all | - | 14.1 | 0.2948 | 505.13 | - | - | - | - | - | - |

被杀的证据（`systemctl show -p ActiveState,Result,ExecMainStatus lab3-a` 和 `journalctl -u lab3-a` 的相关行，`dmesg` 或 `/var/log/kern.log` 中的 oom-kill 行）：

```text
# systemctl show lab3-a -p ActiveState -p Result -p ExecMainStatus
Result=oom-kill
ExecMainStatus=9
ActiveState=failed

# journalctl -u lab3-a 的相关行
Nov 12 17:26:40 ecs-course-01 enrolld[814656]: 2026/11/12 17:26:40 enrolld v1 监听 127.0.0.1:8081；GOMAXPROCS=4；cgroup /sys/fs/cgroup/system.slice/lab3-a.service；预热 0s；读：work=200000、pool=32、db-ms=2；写：sync=each
Nov 12 17:28:49 ecs-course-01 systemd[1]: lab3-a.service: A process of this unit has been killed by the OOM killer.
Nov 12 17:28:49 ecs-course-01 systemd[1]: lab3-a.service: Main process exited, code=killed, status=9/KILL
Nov 12 17:28:49 ecs-course-01 systemd[1]: lab3-a.service: Failed with result 'oom-kill'.

# dmesg 的相关行
[255262.852182] enrolld invoked oom-killer: gfp_mask=0xcc0(GFP_KERNEL), order=0, oom_score_adj=0
[255262.852272] memory: usage 204800kB, limit 204800kB, failcnt 72
[255262.852274] swap: usage 0kB, limit 0kB, failcnt 0
[255262.852332] oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=/,mems_allowed=0,oom_memcg=/system.slice/lab3-a.service,task_memcg=/system.slice/lab3-a.service,task=enrolld,pid=814656,uid=1000
[255262.852361] Memory cgroup out of memory: Killed process 814656 (enrolld) total-vm:1529284kB, anon-rss:203764kB, file-rss:6016kB, shmem-rss:0kB, UID:1000 pgtables:504kB oom_score_adj=0
```

两种改法：按最坏情况定的 limit 是 370M（算式：`(13 + 20 × 8 × 2) × 1.10 = 366.3 MB`，向上取整为 370M）；限制并发时 `-report-max` 取 10（依据：`13 + 10 × 8 × 2 = 173 MB < 200 MB`。理论计算允许的 N 小于 11.69，取 10 为运行时元数据、栈、网络缓冲区和负载波动留出余量。）。

## 3. 任务 B：伸缩控制环

### 3.1 B0 单个实例的能力

<粘贴 `runs/b0-…/steps.csv` 的主要列，或 `scripts/table.sh runs/b0-… ok_rps p99_ms` 的输出>

单个实例（CPU 配额 <填写>%）的上限约 <填写> 请求/秒，按 0.7 的容量线可承担 <填写> 请求/秒；依据：<填写>。

本组的到达曲线：平时 <填写>、峰值 <填写> 请求/秒（`scripts/plan.sh` 的输出：`<填写>`）。

预测（B1～B3 之前写）：峰值需要几个实例？反应式从爬升开始到新实例就绪大约要多久，缺口大约多长？定时预热最晚何时开始？<填写>

### 3.2 三种方案的对照

| 方案 | `runs/` 目录 | 参数 | 缺口（秒） | 错误数 | 峰值阶段的 p99（ms） | 实例·分钟 |
|---|---|---|---|---|---|---|
| B1 常驻 | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> |
| B2 反应式（信号一） | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> |
| B2 反应式（信号二） | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> |
| B3 定时加反应式 | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> |

### 3.3 控制环各段的实测

以 B2（信号一）为例，从 `summary.md` 的“伸缩决策”和“实例”两张表读出：

| 段 | 起止 | 实测（秒） | L-11 第 10 页的设定 |
|---|---|---|---|
| 指标延迟与等待决策（爬升开始 → 第一次扩容决策） | <填写> | <填写> | 指标约 20 s + 决策 ≤ 15 s |
| 拉镜像（创建 → 启动） | <填写> | <填写> | 约 20 s |
| 启动、预热与健康检查（启动 → 就绪） | <填写> | <填写> | 约 60 s |
| 合计（爬升开始 → 新实例就绪） | <填写> | <填写> | 约 2 min |

B2 缺口中的错误：客户端超时 <填写> 个，lbd 返回的状态码 <填写>（`steps.csv` 的 `timeout`、`codes`）；这段时间 `lbd-events.csv` 中有 <填写> 次 `down`。

## 4. 任务 C：故障注入

| 注入 | `runs/` 目录 | lbd 的参数 | 判定用时（秒） | 恢复用时（秒） | 注入到判定之间的错误 | 全程错误 | 其他 |
|---|---|---|---|---|---|---|---|
| C1 停止（kill） | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> | 判定是 `removed` 还是 `down`：<填写>；补建的实例何时就绪：<填写> |
| C1 卡住（pause 一台 20 秒） | 同上 | 同上 | <填写> | <填写> | <填写> | 同上 | 判定是 `removed` 还是 `down`：<填写> |
| C2 暂停 2 秒（灵敏） | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> |
| C2 暂停 2 秒（宽松） | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> |
| C3 变慢（不摘除） | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> | 全站 p99：<填写> |
| C3 变慢（离群摘除） | <填写> | <填写> | <填写> | <填写> | <填写> | <填写> | 全站 p99：<填写> |

预测（C1 之前写）：按 lbd 的参数，一个实例停止或卡住以后多久被判定？其间大约有多少请求出错？<填写>

C1 中停止与卡住的判定用时各是多少，分别是谁发现的？与“间隔 × 失败阈值”相比如何？<填写>

C3 中把配额调到了多少，依据的用量 U 是多少：<填写>

C3 中健康检查为什么全部通过？用 `runs/<名字>/lbd-metrics-end.txt` 或 Prometheus 中按实例的数据，找出是哪一台变慢，并说明依据：<填写>

## 5. 任务 D：发布与自愈

### 5.1 D1 滚动发布

预测：按 L-05 第 24 页的公式，3 个实例逐个替换要多久？直接替换时大约多少请求出错？<填写>

| 做法 | `runs/` 目录 | 发布总时长（秒） | 错误数 | 出错的时段 | 说明 |
|---|---|---|---|---|---|
| 直接替换（naive） | <填写> | <填写> | <填写> | <填写> | <填写> |
| 排空加就绪（graceful） | <填写> | <填写> | <填写> | <填写> | <填写> |

### 5.2 D2 自愈

| 项 | 数值与出处 |
|---|---|
| 参数（`-restart`、`-args`、`-memory`） | <填写> |
| 每个实例的泄漏速度、预计多久 OOM 一次 | <填写> |
| 实测的重启次数（`systemctl show -p NRestarts lab3-inst-…`、`summary.md` 的“实例”表） | <填写> |
| 各实例重启的时刻（`instances.csv` 的 restarted 行）与缺口的时刻 | <填写> |
| 全程的成功率、错误数 | <填写> |

本组写的告警查询，以及它在这次实验中的值：

```text
<填写>
```

## 6. 分析问题

每题先列出所用数据（引用上面的表或 `runs/` 中的文件），再写结论，并说明结论的成立条件。每题 300～600 字。

**问题 1：配额。** A1 实测的每线程每周期被停时间与 L-09 第 12 页的算术相差多少，差异来自何处？A2 中平均用量低于配额时，p99 为什么变大？两种改法各改变了什么，各有什么代价？A3 中 RSS 的推算与实测是否一致？Go 的垃圾回收对“基线 + 在途数 × 每请求内存”这个估计有什么影响？`MemoryMax` 为什么要和 `MemorySwapMax=0` 一起设？按最坏情况定 limit 与限制并发，各适合什么条件？

<填写>

**问题 2：控制环。** 各段延迟的实测值是多少，哪一段最长，能压缩多少？三种方案的缺口与实例·分钟各是多少？两种信号的差别在哪里，为什么？B2 的缺口中除了超时，还有 lbd 返回的 503：过载为什么会让健康检查也失败，这使缺口变得更严重还是更轻？你们会为“开放日”选哪个方案，结论在什么条件下成立？（可选：思考题 3 第四幕中你们的判断，与这里测到的现象是否一致？）

<填写>

**问题 3：健康检查。** 停止、卡住、暂停、变慢四次注入中，各是谁、用多久发现的，健康检查看到了什么？检出快与误判少之间怎样取舍？离群摘除解决了什么问题，又带来什么风险？为什么要限制同时摘除的比例？

<填写>

**问题 4：发布与自愈。** 直接替换时，失败的请求来自哪几步？排空加就绪消除了哪几类失败，代价是什么？D2 中三个实例的重启时刻有什么关系，为什么？这对“有冗余，一台重启不影响服务”的假设意味着什么？自愈解决了什么、掩盖了什么？应当在哪一层、用什么指标告警？

<填写>

## 7. 选做记录

| 选做 | 完成情况与证据 |
|---|---|
| O1 GOMEMLIMIT | 见下 |
| O2 两层伸缩 | <未做 / 参数、`runs/` 目录、结论> |
| O3 扩错层 | <未做 / 预测、`runs/` 目录、结论> |
| O4 Kubernetes 的 HPA | <未做 / 环境、配置文件、数据与结论> |
| O5 换负载均衡器 | <未做 / 配置文件、`runs/` 目录、结论> |
| O6 两台主机 | <未做 / 拓扑、`runs/` 目录、结论> |
| O7 迁移到小组项目 | <未做 / 仓库链接、做了什么、结论> |

### O1：`GOMEMLIMIT` 能否避免 OOM

#### 测量前预测

保持 cgroup 的硬限制 `MemoryMax=200M`、`MemorySwapMax=0`，不设置 `-report-max`，仅设置 `GOMEMLIMIT=170MiB`。预计 Go 运行时会在堆内存接近 170 MiB 时更积极地执行垃圾回收，从而降低堆和 RSS 的峰值，使服务能够承受比未设置 `GOMEMLIMIT` 时更高的负载。

代价是垃圾回收次数和 CPU 开销可能增加。由于 `GOMEMLIMIT` 只是 Go 运行时的软内存目标，不包含全部非 Go 堆内存，也不是 cgroup 的硬保证，因此高并发下进程仍可能达到 200 MiB 的 `MemoryMax` 并被 OOM killer 杀死。预测设置后可以通过 10 RPS，并有可能通过或延后 20 RPS 时的 OOM，但在 30 RPS 时仍存在被杀的风险。

#### 实验配置

```text
MemoryMax=200M
MemorySwapMax=0
GOMEMLIMIT=170MiB
report_max=0
GOMAXPROCS=4
接口：/api/report?mb=8&ms=500
负载阶段：10、20、30 RPS，每阶段 20 秒
```

`report_max=0` 表示未使用有界并发，因此本实验只考察 `GOMEMLIMIT` 的影响。原始数据位于 `runs/o1-gomemlimit/`。

#### 实验结果

| 目标 RPS | 成功 RPS | 错误率 | p99（ms） | 平均在途请求 | 最大报表并发 | RSS 最大值（MiB） | 堆最大值（MiB） | GC 次数/s | 每请求 CPU（ms） | 结果 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 10 | 9.1 | 0.0000 | 505.42 | 4.13333 | 9 | 138.418 | 113.417 | 1.64286 | 2.01721 | 正常完成 |
| 20 | 19.6 | 0.0076 | 505.00 | 8.73333 | 14 | 172.289 | 145.551 | 3.78571 | 1.64169 | 基本完成 |
| 30 | 0.0 | 1.0000 | — | — | — | — | — | — | — | 进程被 OOM killer 杀死 |

#### 与未设置 `GOMEMLIMIT` 的对照

未设置 `GOMEMLIMIT` 的 `runs/a3-oom/` 在 10 RPS 时能够运行，但进入20 RPS阶段后即被 OOM killer 杀死；设置 `GOMEMLIMIT=170MiB` 后，20 RPS 阶段仍取得约 19.6 成功 RPS，错误率仅为 0.0076，直到30 RPS阶段才被杀死。因此，`GOMEMLIMIT` 将发生 OOM 的负载点从20 RPS推迟到了30 RPS。

在10 RPS阶段，两组的 GC 频率都约为 1.64 次/s，每请求 CPU 分别约为 2.02 ms 和 2.02 ms，低负载下差异不明显。设置 `GOMEMLIMIT` 后，20 RPS阶段的 GC 频率上升至约 3.79 次/s，说明运行时为了把堆控制在软限制附近而进行了更积极的垃圾回收。

本次数据没有显示出清晰的“每请求 CPU 时间上升”：20 RPS阶段测得的 `cpu_ms_per_req` 为 1.64 ms。该值会受到请求成功率、采样窗口、GC时机和共享 CPU 等因素影响，因此不能仅凭这一轮实验断言 `GOMEMLIMIT` 降低了 CPU 成本；能够确认的是 GC 频率明显上升，并且 OOM 被推迟。

#### 结论

`GOMEMLIMIT=170MiB` 对200 MiB硬内存限制提供了约30 MiB余量，使 Go 运行时能够在触及 cgroup 硬限制前更积极地回收堆内存。它明显改善了20 RPS阶段的存活情况，但未能在30 RPS阶段避免 OOM。

原因是 `GOMEMLIMIT` 是 Go 运行时的软限制，并不等同于进程 RSS 上限，也不涵盖所有运行时元数据、线程栈和内核计入 cgroup 的内存。当请求到达速度持续高于完成速度、同时存活的报表对象过多时，必须保留的活跃内存无法由 GC 回收，进程仍会触及 `MemoryMax`。

因此，`GOMEMLIMIT` 可以作为避免过早触碰硬限制的安全余量，但不能替代 `MemoryMax`，也不能替代 `-report-max` 这样的有界并发。若目标是在固定的200 MiB限制下稳定服务，应同时设置合适的 `GOMEMLIMIT` 并限制报表并发数；超过容量的请求应快速返回503，而不是让整个进程被 OOM killer 杀死。

## 8. 成员分工

每位成员一行，只填写 GitHub 用户名，无需填写姓名；行数应与小组人数一致。

| GitHub 用户名 | 负责的部分 |
|---|---|
| steinerkuri | 任务0 任务A 选做O1 |
