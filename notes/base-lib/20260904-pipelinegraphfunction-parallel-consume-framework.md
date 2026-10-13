# 2026-09-04 周五基础库理解：PipelineGraphFunction parallel_consume 批消费框架
<!-- topic-nav:start -->
> 主题：`base/pipeline-consume` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260612-pipelinegraphfunction-parallel-consume.md)
<!-- topic-nav:end -->


> 日期：2026-09-04  
> 主题来源：当前 canonical 笔记仓库未找到 2026-09-04 当日 daily-plan；按旧计划 `notes/weekly-topic-selection/daily-plan-20260529.json` 的 Friday 配对回退到 `PipelineGraphFunction parallel_consume 批消费框架`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/processor/base/pipeline_function.h`、`src/processor/base/pipeline_function.cpp`、`src/process/base/pipeline_function.h`、`src/process/base/pipeline_function.hpp`，聚焦 Channel 批消费、bthread 并发、QueueContext 汇总、publisher 发布和 post_process 日志收束。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">Graph Vertex 依赖</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`setup()` 绑定 log_id / uid / SidInfo / ExpInfo</div><div style="margin-top:8px;font-size:12px;color:#52606d;">GRC 与 GRG 都在 setup 阶段把 vertex named_dependency 注入 Pipeline context，并把 custom_context 暴露给下游 processor。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">并发批消费</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`parallel_consume()` + `bthread_async()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先按 concurrents 启动并发批，再把剩余 range 本地串行处理，避免无限扩张 bthread 数量。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">输出与观测</div><div style="margin-top:8px;font-size:14px;color:#102a43;">publisher / queue_contexts / post_process</div><div style="margin-top:8px;font-size:12px;color:#52606d;">GRG 在消费线程内发布 output_vec，GRC 在 post_process 汇总 SIA add / vertex / rpc 日志。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 64px 1fr 64px 1fr 64px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">setup 绑定</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">pre_process</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">parallel_consume</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">post_process</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title PipelineGraphFunction parallel_consume batch lifecycle
participant "Graph vertex" as V
participant "PipelineGraphFunction::setup" as SETUP
participant "Context / QueueContext" as CTX
participant "ChannelConsumer" as CONSUMER
participant "bthread_async" as BT
participant "process(queue_context)" as PROC
participant "ChannelPublisher" as PUB
participant "post_process" as POST
V -> SETUP : named_dependency(log_id / uid / SidInfo / ExpInfo)
SETUP -> CTX : set context and custom_context.ref(context)
SETUP -> SETUP : read batch_size / concurrents / mutable_input
SETUP -> PROC : init(config)
V -> CONSUMER : processor() opens input and output
CONSUMER -> CTX : consume(batch_size)
CTX -> BT : launch up to concurrents futures
BT -> PROC : process(queue_context)
PROC --> CTX : ret / output_vec / logs
CTX -> PROC : local process remaining ranges
PROC -> PUB : GRG publish output_vec and commit
CTX -> POST : wait future.get and merge local contexts
POST -> POST : aggregate SIA add / vertex / rpc logs
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title parallel_consume 的 6 个合同点
  desc 这些合同决定批消费框架能否同时保持并发、顺序收束和日志可观测。
  items
    - label setup 绑定依赖
      desc `src/processor/base/pipeline_function.cpp:7-30` 与 `src/process/base/pipeline_function.hpp:9-30` 将 vertex 依赖接入 context
      icon mdi/link-variant
    - label batch_size
      desc `src/processor/base/pipeline_function.cpp:33-45` / `src/process/base/pipeline_function.hpp:31-43` 从配置读取批大小与并发度
      icon mdi/table-row
    - label 并发窗口
      desc `src/processor/base/pipeline_function.h:51-67` 与 `src/process/base/pipeline_function.h:58-83` 只启动 concurrents 个异步任务
      icon mdi/source-branch
    - label 本地尾批
      desc `src/processor/base/pipeline_function.h:70-78` / `src/process/base/pipeline_function.h:86-103` 串行处理剩余 range
      icon mdi/playlist-check
    - label publisher 发布
      desc `src/process/base/pipeline_function.h:74-79` 与 `:95-100` 在 output_vec 非空时 publish / copy_n / commit
      icon mdi/publish
    - label 日志汇总
      desc `src/processor/base/pipeline_function.cpp:113-198` 与 `src/process/base/pipeline_function.hpp:77-161` 聚合线程日志
      icon mdi/chart-timeline-variant
```

## 3. 代码链路拆解
### 3.1 setup 是运行态依赖注入点
- `src/processor/base/pipeline_function.cpp:7-30`：GRC 侧把 logid、uid、cuid、ua、SidInfo、ExpInfo、VertexContext 等依赖装进 `context`，再执行 `vertex_context->_custom_context.ref(context)`。
- `src/process/base/pipeline_function.hpp:9-30`：GRG 侧采用同样的依赖绑定方式，并额外持有 `_pipeline_done` emit 点，说明这个基类承担图函数和 channel 流之间的适配职责。
- `src/processor/base/pipeline_function.cpp:33-45`：`batch_size`、`concurrents`、`is_queue`、`is_mutable_input` 都来自配置，批消费行为不是硬编码在 processor 里的。

### 3.2 parallel_consume 控制的是并发窗口，不是简单遍历
- `src/processor/base/pipeline_function.h:51-67`：先清空 `queue_contexts`，按 `concurrents` resize，再对每个 batch 构造 `QueueContext` 并 `bthread_async` 调用 `process()`。
- `src/processor/base/pipeline_function.h:70-88`：超过并发窗口的剩余 batch 在本地串行处理，并在 futures 全部 `get()` 后把本地上下文 move 回 `context.queue_contexts`。
- `src/process/base/pipeline_function.h:58-83`：GRG 侧把 publisher 引入异步闭包，`process()` 成功后立即按 `output_vec.size()` publish，说明输出和消费线程绑定得更紧。

### 3.3 post_process 是多线程日志的归并边界
- `src/processor/base/pipeline_function.cpp:113-138`：多个 `queue_context` 的 `sum_related_logs` 先按 key 聚合，再统一 `SIA_ADD`。
- `src/processor/base/pipeline_function.cpp:140-197`：vertex 与 rpc 日志分别合并，rpc 日志还会按有日志的线程数做平均，避免每个线程直接散打日志。
- `src/process/base/pipeline_function.hpp:63-68`：GRG 侧在 `pipeline_done`、`emit_normal_data()` 之后才 `post_process()`，说明“输出完成”和“日志归并”是两个相邻但不同的阶段。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">批消费框架最容易出错的是“上下文引用活得比 batch 短”</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">异步闭包捕获的是 `queue_context` 引用，因此并发窗口内的 `queue_contexts.resize(concurrents)` 必须先稳定住对象位置。不要把它改成临时局部对象后再异步捕获。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">GRG 侧在异步线程里 publish，GRC 侧更多依赖 post_process 汇总日志。排查时要先分清“输出丢了”还是“日志没归并”。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：setup 依赖 → batch_size/concurrents → queue_context 生命周期 → future.get → publisher commit → post_process</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title PipelineGraphFunction 批消费排查清单
  desc 适用于输出缺失、局部 batch 失败、线程日志不一致、publisher 未提交和队列上下文污染
  items
    - label 检查 setup 依赖
      desc `log_id`、`SidInfo`、`ExpInfo`、`VertexContext` 是否在 setup 阶段成功绑定
      done true
    - label 检查 batch 配置
      desc `batch_size` 与 `concurrents` 是否为非负且符合线上 QPS 形态
      done true
    - label 检查输入通道
      desc `is_mutable_input` 要和实际 consumer 类型一致
      done true
    - label 检查闭包捕获
      desc 异步任务捕获的 `queue_context` 必须来自稳定容器
      done true
    - label 检查 publisher
      desc GRG 侧 `publish`、`copy_n`、`commit` 必须成对出现
      done true
    - label 检查日志归并
      desc `sum_related_logs`、`vertex_related_logs`、`rpc_related_logs` 要在 post_process 统一归并
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/base/pipeline_function.h:40-101`
- `src/processor/base/pipeline_function.cpp:7-95`
- `src/processor/base/pipeline_function.cpp:113-198`
- `src/process/base/pipeline_function.h:21-131`
- `src/process/base/pipeline_function.hpp:9-68`
- `src/process/base/pipeline_function.hpp:77-161`

## 7. 说明
当前运行环境未找到 2026-09-04 的 daily-plan 文件；本笔记基于历史 Friday 配对和本地代码回退生成。KU 正文未读取，业务背景需人工补充。
