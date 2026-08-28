# PipelineGraphFunction parallel_consume 批消费框架

> 日期：2026-08-28  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 Friday base-lib slot  
> 范围：`src/processor/base/pipeline_function.h` 与 `src/process/base/pipeline_function.h` 的批消费、bthread 并发、QueueContext 聚合、发布语义和请求闭环。

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:16px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.05fr 1fr;gap:12px;"><div style="background:#fff;border:1px solid #dbe4ee;border-radius:12px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">入口装配</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/service/grc_service.cpp:151-220` / `src/service/grg_service.cpp:35-91`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">服务先把 request、graph、timeout、context 绑好，再进入 pipeline 处理。</div></div><div style="background:#fff;border:1px solid #dbe4ee;border-radius:12px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">批消费核心</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`parallel_consume()` + `bthread_async()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">前段并发、尾段本地串行，避免把所有批次都丢进线程池。</div></div><div style="background:#fff;border:1px solid #dbe4ee;border-radius:12px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">结果归位</div><div style="margin-top:8px;font-size:14px;color:#102a43;">GRC 侧聚合日志，GRG 侧还要 publish/commit</div><div style="margin-top:8px;font-size:12px;color:#52606d;">两边都要在请求结束时把队列上下文、输出通道和图状态收束干净。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 72px 1fr 72px 1fr 72px 1fr;gap:10px;align-items:center;"><div style="background:#eef6ff;border:1px solid #bfdbfe;border-radius:10px;padding:10px;text-align:center;color:#1d4ed8;">channel consume</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:10px;padding:10px;text-align:center;color:#0f766e;">QueueContext 构造</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:10px;padding:10px;text-align:center;color:#a16207;">bthread 并发</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:10px;padding:10px;text-align:center;color:#166534;">post_process / publish</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title PipelineGraphFunction parallel_consume lifecycle
actor Caller
participant "GRC PipelineGraphFunction" as GRC
participant "GRG PipelineGraphFunction" as GRG
participant "ChannelConsumer" as CC
participant "QueueContext[]" as QC
participant "bthread_async" as BT
participant "ChannelPublisher" as PUB
participant "post_process" as POST
Caller -> GRC : processor()
GRC -> GRC : pre_process()
GRC -> CC : consume(batch_size)
GRC -> QC : resize(concurrents)
loop first N batches
  GRC -> QC : fill queue_context[i]
  GRC -> BT : async process(queue_context)
  BT -> GRC : return ret
end
loop tail batches
  GRC -> GRC : local process(queue_context)
  GRC -> QC : move local_queue_contexts
end
GRC -> GRC : future.get()
GRC -> POST : aggregate logs
POST --> Caller : ret
Caller -> GRG : processor()
GRG -> GRG : pre_process()
GRG -> CC : consume(batch_size)
GRG -> QC : resize(concurrents)
loop first N batches
  GRG -> BT : async process(queue_context)
  BT -> PUB : publish(valid_num) / commit()
end
loop tail batches
  GRG -> PUB : publish(valid_num) / commit()
end
GRG -> POST : emit_normal_data() + post_process()
POST --> Caller : ret
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title parallel_consume 的 6 个关键结构点
  desc 这条链路的重点不是“并发”本身，而是输入、上下文、发布和收束的边界
  items
    - label batch_size
      desc GRC/GRG 都从 consumer.consume 拉批；批大小决定局部吞吐
      icon mdi/package-variant
    - label concurrents
      desc 只把前几个批次交给 bthread_async，控制并发窗口
      icon mdi/source-branch
    - label QueueContext
      desc 载体包括 queue_index、output_vec、日志和业务中间态
      icon mdi/database-outline
    - label publisher
      desc GRG 在基类内 publish/commit；GRC 侧发布语义留给子类
      icon mdi/publish
    - label tail batches
      desc 溢出的批次走当前线程，避免在高并发下扩大调度成本
      icon mdi/arrow-collapse-down
    - label post_process
      desc 统一汇总日志、SIA、RPC 统计，收束请求态
      icon mdi/chart-timeline-variant
```

## 3. 代码链路拆解
### 3.1 GRC：批消费和日志聚合在基类，发布留给业务实现
- `src/processor/base/pipeline_function.h:50-89`：`parallel_consume()` 先清空 `context.queue_contexts`，按 `concurrents` 预分配槽位，再把前几个批次丢进 `bthread_async`。
- `src/processor/base/pipeline_function.h:68-88`：尾部批次在当前线程直接 `process(queue_context)`，并搬进 `local_queue_contexts`，避免完全依赖异步窗口。
- `src/processor/base/pipeline_function.cpp:73-95`：`processor()` 负责打开输出通道、选择 `mutable_input` 或 `input`，然后把处理权交给 `parallel_consume()`。
- `src/processor/base/pipeline_function.cpp:113-198`：`post_process()` 汇总 SIA、vertex 和 RPC 日志，形成请求收束点。

### 3.2 GRG：模板化输入/输出，基类内完成 publish
- `src/process/base/pipeline_function.h:21-114`：GRG 的 `PipelineGraphFunction<T_I, T_O>` 把输入输出都模板化，`parallel_consume()` 在 worker 分支和 tail 分支都显式 `publish(valid_num)` 并 `commit()`。
- `src/process/base/pipeline_function.hpp:51-68`：`processor()` 负责 `pre_process()`、打开 publisher、调用 `parallel_consume()`，再执行 `emit_normal_data()` 和 `post_process()`。
- `src/process/base/pipeline_function.hpp:77-161`：GRG 的日志聚合仍然依赖 `queue_contexts`，但结果对象已经在基类里发出。

### 3.3 服务入口如何把请求送进 pipeline
- `src/service/grc_service.cpp:151-220`：GRC 先做 context 初始化、图实例获取和 request 数据注入，再 `run(graph, graph_name, sctx, done, cntl, res_cnt)`，最后 `graph->reset()`。
- `src/service/grg_service.cpp:35-91`：GRG 先取图、填基础数据、设置动态超时场景，再 `graph->run(end)` 并在 `pooled_graph->reset()` 前完成响应发送。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff7ed;border:1px solid #f3d19e;border-radius:16px;padding:18px;margin:16px 0;color:#3f2d20;"><div style="font-size:12px;font-weight:800;color:#9a3412;text-transform:uppercase;letter-spacing:.08em;">debug pitfalls</div><div style="font-size:24px;font-weight:900;margin:6px 0 12px;letter-spacing:-.02em;">不要把 parallel_consume 当成“统一的并发 for-each”</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#fff;border-top:4px solid #c2410c;border-radius:12px;padding:12px;font-size:14px;line-height:1.65;"><strong>生命周期前提：</strong>前 N 个 queue_context 依赖 `resize(concurrents)` 后的稳定槽位，`bthread_async([this, &queue_context])` 才是安全的。若改成动态 `emplace_back` 后再捕获引用，风险会直接暴露。<br><strong>发布语义差异：</strong>GRG 在基类里 publish/commit，GRC 的基类只负责消费和聚合。排查“process 有结果但下游没数据”时，先确认当前实现属于哪一侧。</div><div style="background:#fff;border-top:4px solid #c2410c;border-radius:12px;padding:12px;font-size:14px;line-height:1.65;"><strong>尾批处理：</strong>tail batches 并不经过线程池，问题常出在 batch_size 变化、input_data_construct 过滤和日志收束顺序，而不是线程数本身。<br><strong>请求收束：</strong>服务入口里的 `reset()` 必须在日志、输出和 closure 收尾之后执行，太早会清掉后续还要读的数据。</div></div><div style="margin-top:10px;font-weight:900;color:#9a3412;">∎ 重点看 batch / concurrents / publish / reset 四个边界</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title Pipeline 批消费排查清单
  desc 适用于吞吐下降、下游空数据、日志缺失、请求结束后状态污染
  items
    - label 确认入口分支
      desc GRC 看 `mutable_input` / `input` 选择；GRG 看实际 `processor()` 中的 publisher 装配
      done true
    - label 打印 batch_size 与 concurrents
      desc 并发窗口过小会退化，过大可能放大下游压力
      done true
    - label 检查 input_data_construct
      desc 是否过滤空指针，queue_index 和 range 是否对齐
      done true
    - label 对齐发布语义
      desc GRG 查 `publish(valid_num)`；GRC 查子类是否负责实际输出
      done true
    - label 校验 future.get 结果
      desc 任一 worker 非 `ERR_OK` 都可能覆盖整体返回值
      done true
    - label 检查 reset 顺序
      desc 服务入口必须在响应发送和日志打印结束后再 reset graph
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/base/pipeline_function.h:50-89`
- `src/process/base/pipeline_function.h:21-114`
- `src/service/grc_service.cpp:151-220`
- `src/service/grg_service.cpp:35-91`

## 7. 说明
本次没有读取 KU 正文，业务背景需人工补充；本笔记只基于本地源码和计划文件整理。
