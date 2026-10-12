# 2026-09-15 基础库理解：SIA/BVAR 请求耗时与性能归因

> 日期：2026-09-15  
> 主题选择：未发现本日有效 daily-plan；工作区中的旧计划日期为 2026-05-29，且近期笔记已覆盖 Graph reset、ObjectPool、Closure 等主题，因此回退到邻近未覆盖的 **SIA/BVAR 热点监控与请求性能归因**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：GRC RPC 服务入口的总耗时、Graph 耗时、请求/响应大小、Graph arena 使用量，以及 video_launch 数据合并节点的 SIA 计数。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">入口测量</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">GRC query → graph begin</div><div style="margin-top:8px;font-size:12px;color:#52606d;">请求初始化后写入 graph_begin_us，保留请求开始点和路由上下文。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">执行与分解</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">Graph::run → vertex / SIA</div><div style="margin-top:8px;font-size:12px;color:#52606d;">Graph monitor 记录节点链路；业务函数以 SIA_START/END 暴露阶段结果与计数。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#102a43;text-transform:uppercase;letter-spacing:.04em;">出口归因</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">LatencyRecorder + BVAR + logs</div><div style="margin-top:8px;font-size:12px;color:#52606d;">按 ua/flow_loc 记录总耗时，另记请求响应大小和 Graph arena 使用/容量。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 48px 1fr 48px 1fr 48px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">request</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:6px;padding:10px;text-align:center;color:#047857;font-size:13px;">graph timing</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">response / SIA</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:10px;text-align:center;color:#991b1b;font-size:13px;">diagnosis</div></div></div>

**一句话结论：** 总延迟、Graph 延迟、响应体大小和 arena 容量是四条不同观测线；只有把它们按同一请求和同一 `ua/flow_loc` 维度对齐，才能区分业务计算慢、响应膨胀和池化内存增长。

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRC 请求性能观测与归因时序
participant "brpc query" as RPC
participant "GRCSessionContext" as CTX
participant "Graph" as GRAPH
participant "GraphMonitor" as MON
participant "SIA vertex" as SIA
participant "LatencyRecorder" as LAT
participant "BVAR size/arena" as BVAR
RPC -> CTX : init request / set graph_begin_us
CTX -> GRAPH : run(end, err)
GRAPH -> MON : cal_graph_cost / vertex log
GRAPH -> SIA : vertex counters and stage costs
GRAPH --> CTX : response + graph_end_us
CTX -> LAT : record total cost by ua, flow_loc
CTX -> BVAR : request/response size
CTX -> BVAR : arena SpaceUsed / SpaceAllocated
MON --> RPC : dapper and critical-path evidence
@enduml
```

## 2. 指标结构信息图

```infographic
infographic list-grid-badge-card
data
  title GRC 性能归因的四组指标
  desc 统一日志阶段把请求结果映射到不同观测维度，避免用一个数字解释所有问题
  items
    - label total latency
      desc LatencyRecorder 按 ua 与 flow_loc 记录 sctx._end_us - sctx._begin_us
      icon mdi:timer-outline
    - label graph latency
      desc graph_cost_us 来自 graph_end_us - graph_begin_us，用于隔离图执行成本
      icon mdi:graph-outline
    - label payload size
      desc 请求和响应 JSON 大小分别进入 request_size 与 response_size BVAR
      icon mdi:database-arrow-up
    - label arena footprint
      desc Graph arena 同时上报 SpaceUsed 与 SpaceAllocated，观察复用容量与实际占用
      icon mdi:memory
    - label vertex counters
      desc 业务节点用 SIA_ADD 暴露结果数、保量数、兜底数等结构化计数
      icon mdi:counter
    - label dapper path
      desc 指定 ua 额外上传 graph 与 vertex 观测，支持跨节点链路核对
      icon mdi:timeline-text-outline
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 服务构造阶段注册稳定的 BVAR 名称

`src/service/grc_service.cpp:49-60` 建立 `ua`、`flow_loc` 两个标签维度，并暴露 `mv_grc_service_latency`、`bvar_grc_request_size`、`bvar_grc_response_size`、`grc_graph_arena_usage` 和 `grc_graph_arena_capacity`。这组名称是监控查询的稳定入口；新增维度时要评估时间序列数量，不能把高基数字段直接作为 label。

### 3.2 时间起点必须和 Graph 数据一起写入

`src/service/grc_service.cpp:126-149` 在 `emit_common_data()` 中记录 `_graph_begin_us`，同时将请求身份、路由和实验信息写入 Graph Data。这样 Graph 内部节点可以使用同一时间起点；排查时应检查请求开始点是否在 graph 获取和执行前建立。

### 3.3 统一日志阶段同时计算总耗时和图耗时

`src/service/grc_service.cpp:553-559` 计算 `cost` 与 `graph_cost`，并把总耗时写入按 `ua/flow_loc` 分桶的 `LatencyRecorder`。`src/service/grc_service.cpp:561-575` 又把 `cost_us`、`graph_cost_us`、`errno` 写进请求日志。若总耗时明显高于 Graph 耗时，优先排查入口解析、响应组装、日志或下游等待，而不是直接修改 Graph 算子。

### 3.4 GraphMonitor 与 Dapper 是链路观测，不替代业务计数

`src/service/grc_service.cpp:520-538` 调用 `cal_graph_cost()`、`print_vertex_log()`、按指定 ua 上传 Dapper，并填充 critical path。它们解释“哪里花时间”，但不说明候选为何变少；业务节点的 `SIA_ADD` 仍需与结果数量、错误码和请求参数一起看。

### 3.5 数据合并节点的 SIA 形成业务归因闭环

`src/processor/video_launch/data_merge.cpp:48-75` 以 `SIA_START(data_merge_vertex)` 开始，检查依赖、合并数据，并以 `SIA_END_WITH_VAL` 写入最终结果数。`src/processor/video_launch/data_merge.cpp:153-178` 分别记录保量、效果队列和最终兜底计数；这些计数可以和服务总延迟一起判断“慢在计算”还是“结果不足触发兜底”。

## 4. 指标对账信息图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title 延迟异常的两侧对账
  desc 总耗时与 Graph 耗时必须配合 payload、arena 和业务计数解释
  items
    - label latency side
      desc total cost、graph cost、errno、graph monitor vertex cost
      value 1
      icon mdi:speedometer
      children
        - label gap
          desc total latency - graph latency 反映图外成本
        - label bucket
          desc 先按 ua、flow_loc 对齐再比较分位数
        - label path
          desc 指定 ua 核对 dapper 与 critical path
    - label result side
      desc response size、arena footprint、SIA result/quota/fallback counters
      value 2
      icon mdi:chart-box-outline
      children
        - label payload
          desc JSON 或 protobuf 变大可能放大出口成本
        - label memory
          desc SpaceUsed 与 SpaceAllocated 的差值反映池化余量
        - label business
          desc merge count 与 final output 说明结果链是否退化
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d7dee8;border-left:6px solid #b8432f;border-radius:8px;padding:18px;margin:18px 0;color:#1f2937;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#b8432f;font-weight:800;">Pitfalls · Performance Attribution</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">单看平均延迟，无法定位真正的成本</div><div style="display:grid;grid-template-columns:1.4fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:6px;padding:12px;font-size:14px;"><b>四个常见误判：</b><br>① 把 total latency 当成 Graph latency；② 把 arena capacity 增长直接当成泄漏；③ 只看响应大小，忽略业务节点的兜底计数；④ 把 Dapper 上传成功当成业务结果正确。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>logid → ua/flow_loc → total/graph gap → errno → payload → arena → SIA counts。</div></div><div style="margin-top:12px;font-weight:900;color:#3d5a80;">∎ 先建立同请求的指标切片，再讨论优化方向。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title SIA/BVAR 性能排查清单
  desc 适用于延迟抖动、Graph 变慢、响应膨胀和 arena 容量异常
  items
    - label 固定请求切片
      desc 使用 logid、ua、flow_loc、graph_name 和 errno 对齐样本
      done true
    - label 核对时间边界
      desc 确认 graph_begin_us、graph_end_us、request begin/end 来自同一次请求
      done true
    - label 计算图外成本
      desc 比较 total latency 与 graph latency 的差值
      done false
    - label 检查 payload
      desc 对照 request_size、response_size 与结果数量，确认是否存在序列化膨胀
      done false
    - label 检查 arena
      desc 同时比较 SpaceUsed 和 SpaceAllocated，并按复用请求观察趋势
      done false
    - label 对账业务 SIA
      desc 核对 baoliang_count、effect_merge_count、final_doudi_count 和最终结果数
      done false
    - label 验证链路采样
      desc 对指定 ua 对照 GraphMonitor、vertex log、Dapper 与 critical path
      done false
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/service/grc_service.cpp:49-60`：LatencyRecorder 与 BVAR 指标注册。
- `src/service/grc_service.cpp:126-149`：Graph 起始时间和请求上下文写入 Graph Data。
- `src/service/grc_service.cpp:520-541`：GraphMonitor、Dapper、critical path 与执行收束。
- `src/service/grc_service.cpp:553-621`：总耗时、Graph 耗时、请求日志、响应大小和 arena 指标。
- `src/processor/video_launch/data_merge.cpp:48-75`：数据合并节点 SIA 生命周期。
- `src/processor/video_launch/data_merge.cpp:153-178`：保量、效果和最终兜底计数。

> KU 正文未读取，需人工补充线上指标面板、采样率、告警阈值、Dapper 聚合口径以及不同 ua 的性能基线。
