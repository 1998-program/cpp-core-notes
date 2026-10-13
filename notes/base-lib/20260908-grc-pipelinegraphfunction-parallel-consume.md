# 2026-09-08 周一基础库理解：PipelineGraphFunction 并行消费骨架与队列上下文收敛
<!-- topic-nav:start -->
> 主题：`base/pipeline-consume` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260612-pipelinegraphfunction-parallel-consume.md)
<!-- topic-nav:end -->


> 日期：2026-09-08  
> 主题来源：当前 daily-plan 仍未覆盖本日；按历史候选回退到 `PipelineGraphFunction parallel_consume 批消费框架`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/processor/base/pipeline_function.h`，聚焦批消费、`bthread_async` 并行、剩余任务本地兜底与 `QueueContext` 收敛。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">输入层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/processor/base/pipeline_function.h:50-67`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">consumer 每次吐出一个 range，函数按 batch_size 把输入拆成若干并行片段。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">并行层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`bthread_async([this, &queue_context])`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">前段任务进入 Future 池；每个 worker 处理独立 `QueueContext`。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">收敛层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`future.get()` + 本地 `QueueContext` 回填</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先等并行段完成，再把余量补齐到 `context.queue_contexts`，避免结果分散。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">consumer</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">QueueContext</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">bthread_async</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">future / local fallback</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title PipelineGraphFunction parallel consume boundary
participant consumer as CON
participant "PipelineGraphFunction" as PF
participant "QueueContext[]" as QC
participant "bthread_async" as ASYNC
participant "Future<int32_t>" as FUT
participant "local QueueContext" as LOCAL
participant "context.queue_contexts" as CXT
CON -> PF : consume(batch_size)
PF -> QC : resize(concurrents)
loop while range not empty and i < concurrents
  PF -> QC : input_data_construct(queue_context, range)
  PF -> ASYNC : spawn process(queue_context)
  ASYNC --> FUT : future handle
  PF -> CON : consume(next batch)
end
loop remaining range
  PF -> LOCAL : construct local QueueContext
  PF -> PF : process(queue_context)
  PF -> LOCAL : emplace_back(move(queue_context))
end
loop futures
  PF -> FUT : get()
end
PF -> CXT : append local_queue_contexts
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title PipelineGraphFunction 的 6 个关键点
  desc 这条骨架把批消费、并行处理、结果回收和兜底路径放在同一个 contract 里
  items
    - label 批消费入口
      desc `src/processor/base/pipeline_function.h:50-57` 先清空上下文，再按 `consumer.consume()` 拉取第一批
      icon mdi-download-box-outline
    - label 并行队列
      desc `src/processor/base/pipeline_function.h:57-66` 预分配 `context.queue_contexts`，为并行 worker 留出槽位
      icon mdi-vector-arrange-below
    - label worker 分发
      desc `src/processor/base/pipeline_function.h:63-66` 每个 future 绑定一个 `QueueContext`
      icon mdi-call-split
    - label 本地兜底
      desc `src/processor/base/pipeline_function.h:69-77` 剩余 range 不再等并行队列，直接本地处理
      icon mdi-lan-pending
    - label future 收敛
      desc `src/processor/base/pipeline_function.h:79-84` 所有并行段完成后再汇总错误码
      icon mdi-vector-link
    - label 上下文回填
      desc `src/processor/base/pipeline_function.h:85-87` 把本地保存的队列上下文补回 `context.queue_contexts`
      icon mdi-database-sync
```

## 3. 代码链路拆解
### 3.1 并行骨架先预分配，再发射任务
- `src/processor/base/pipeline_function.h:51-58`：先清空 `context.queue_contexts`，再按 `concurrents` 预设槽位。这说明并行数量是显式契约，不是运行时临时扩容。
- `src/processor/base/pipeline_function.h:59-67`：`consumer.consume()` 每次拉一个 range，前 `concurrents` 个分片交给 `bthread_async`，其余 range 在同一个入口函数里继续推进。
- `src/processor/base/pipeline_function.h:63-65`：`queue_context` 以引用方式进入 lambda，说明其生命周期必须被外层 `context.queue_contexts` 保住。

### 3.2 剩余输入走本地处理，不把所有压力都压给 future
- `src/processor/base/pipeline_function.h:69-77`：并行槽位用满后，剩余 range 直接本地 `process(queue_context)`。这一步是重要的吞吐保护，不让小批次和尾部流量都被线程调度放大。
- `src/processor/base/pipeline_function.h:76-77`：本地处理的 `queue_context` 被移动进 `local_queue_contexts`，后面统一回填，避免丢失在局部变量生命周期内。

### 3.3 future 只负责收敛，不负责重排业务逻辑
- `src/processor/base/pipeline_function.h:79-84`：`future.get()` 只做等待和错误码归并，没有在这里再拆分业务分支。这个边界很清楚：并行阶段完成后，业务排序和结果解释应该在 `process()` 内部结束。
- `src/processor/base/pipeline_function.h:85-87`：本地缓存的 `QueueContext` 再次并入 `context.queue_contexts`，说明最终上下文集合必须完整保留，后续 post_process 才能继续消费。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">这类骨架最容易出问题的点，不在并行本身，而在引用生命周期和回填顺序</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`bthread_async([this, &queue_context])` 依赖外层 `context.queue_contexts` 的稳定存活。若 future 还没 `get()` 就提前清掉外层容器，worker 会拿到悬挂引用。</div><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">尾部 range 必须走本地处理并回填 `local_queue_contexts`，否则只并行前半段、丢掉余量，结果集会悄悄缺项。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：`consume()` → `resize()` → `input_data_construct()` → `bthread_async()` → `future.get()` → `local_queue_contexts` 回填</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title PipelineGraphFunction 排查清单
  desc 适用于并行任务丢失、future 悬挂、context 被提前清空和尾部输入未处理的场景
  items
    - label 检查 consume
      desc 确认 `consumer.consume()` 的 batch_size 与预期一致
      done true
    - label 检查上下文槽位
      desc `context.queue_contexts.resize(concurrents)` 必须在发射任务前完成
      done true
    - label 检查引用生命周期
      desc lambda 中引用的 `queue_context` 不能早于 future 完成被销毁
      done true
    - label 检查尾部兜底
      desc 剩余 range 必须继续本地 `process()`，不能直接丢弃
      done true
    - label 检查错误收敛
      desc 所有 future 都要执行 `get()`，并归并非 0 返回码
      done true
    - label 检查最终回填
      desc `local_queue_contexts` 要补回 `context.queue_contexts`
      done true
```

## 6. 证据来源
- `src/processor/base/pipeline_function.h:50-67`
- `src/processor/base/pipeline_function.h:69-89`
- `src/processor/base/pipeline_function.h:90-102`

## 7. 说明
当前运行环境没有 2026-09-08 的 daily-plan 文件；本笔记基于历史候选回退生成，KU 正文未读取，业务背景需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260908-grc-pipelinegraphfunction-parallel-consume.md)。
