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

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-29T19:02:06.111672
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`PipelineGraphFunction::parallel_consume` 批消费框架已经在两个业务代码库中落地使用，并不是全新引入能力。`feeda-mv-grg` 与 `feeda-mv-grc` 均发现约 10 个相关文件命中，其中分别包含各自的基类实现：`process/base/pipeline_function.hpp` 与 `processor/base/pipeline_function.h`。这说明当前业务链路已经具备批量消费、`QueueContext` 承载、bthread 并发处理、尾批本地处理以及请求收束的基础结构。

- 从迁移潜力看，`feeda-mv-grc` 的代码规模更大，`std::vector`、`std::string`、`std::unordered_map` 使用分布更广，说明召回汇聚侧存在大量候选集合、依赖图、日志聚合和中间态管理场景，更容易受益于统一的批消费与上下文收束模型。`feeda-mv-grg` 侧则已经在序列生成、特征服务、候选生成等链路中使用 pipeline 基类，更适合围绕现有 `process/base/pipeline_function.hpp` 做规范化改造，重点检查 `publish(valid_num)`、`commit()`、`batch_size` 与 `concurrents` 的配置是否匹配业务吞吐。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现 `PipelineGraphFunction` 相关使用，命中文件包括：
  - `process/feature_service/doc_feature_pipeline.cpp`
  - `process/feature_service/doc_feature_with_cache_pipeline.cpp`
  - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
  - `process/common_predict.cpp`
  - `process/base/pipeline_function.hpp`

- 其中 `process/base/pipeline_function.hpp` 可作为 GRG 侧适配的核心参考实现：
  - 输入/输出通过模板参数 `PipelineGraphFunction<T_I, T_O>` 抽象。
  - `parallel_consume()` 中 worker 分支和 tail 分支都会执行 `process(queue_context)`。
  - GRG 基类负责在处理完成后执行 `publish(valid_num)` 与 `commit()`。
  - `processor()` 负责串起 `pre_process()`、publisher 打开、`parallel_consume()`、`emit_normal_data()` 与 `post_process()`。

- 业务侧现有容器使用规模较大：
  - `std::vector`：1969 次，分布在 356 个文件。
  - `std::string`：2443 次，分布在 425 个文件。
  - `std::unordered_map`：734 次，分布在 205 个文件。

- 典型代码如 `model/model.h`、`model/paddle_model.h` 中大量使用 `std::vector<RidTmpInfoPtr>& candidate_vec` 承载候选集合。这类候选列表本身不一定需要直接迁移到 pipeline，但如果其上游或下游存在按候选分片、按 batch 推理、按 doc 特征拉取的逻辑，就适合接入 `parallel_consume()` 的批消费模型。

#### feeda-mv-grc：召回汇聚服务

- 已发现 `PipelineGraphFunction` 相关使用，命中文件包括：
  - `processor/video_launch/ds_to_ridinfo_pipeline.cpp`
  - `processor/video_launch/cp_setaware_attention_pipeline.cpp`
  - `processor/video_launch/sketchy_rpc_config.cpp`
  - `processor/video_launch/sketchy_rpc_pipeline.cpp`
  - `processor/base/pipeline_function.h`

- 其中 `processor/base/pipeline_function.h` 是 GRC 侧适配的核心参考实现：
  - `parallel_consume()` 负责清空并预分配 `context.queue_contexts`。
  - 前 `concurrents` 个 batch 通过 `bthread_async()` 异步处理。
  - 超出并发窗口的 tail batches 在当前线程直接处理。
  - 基类负责消费和日志聚合，但不统一负责业务结果发布。
  - 结果输出和下游写入需要由具体 pipeline 子类完成。

- 业务侧容器和图依赖结构使用规模更大：
  - `std::vector`：8520 次，分布在 1290 个文件。
  - `std::string`：7267 次，分布在 1247 个文件。
  - `std::unordered_map`：2860 次，分布在 646 个文件。

- 典型代码如 `service/grc_http_service.cpp`：
  - `std::unordered_map<std::string, std::vector<int>> depend_map` 用于图依赖关系整理。
  - `std::vector<std::string>` 用于请求参数、颜色列表、访问控制开关等集合。
  - 这类管理型代码未必适合直接接入 `parallel_consume()`，但召回候选转换、RPC 批请求、特征补充、结果聚合等路径更适合使用该框架。

---

### 3. 💡 适用性评估与建议

- **建议一：以现有基类作为迁移模板，避免重复实现批处理并发逻辑**
  - GRG 侧建议优先参考 `process/base/pipeline_function.hpp`。
  - GRC 侧建议优先参考 `processor/base/pipeline_function.h`。
  - 对于已经命中的业务文件，例如：
    - `process/feature_service/doc_feature_pipeline.cpp`
    - `process/feature_service/doc_feature_with_cache_pipeline.cpp`
    - `processor/video_launch/ds_to_ridinfo_pipeline.cpp`
    - `processor/video_launch/sketchy_rpc_pipeline.cpp`
  - 如果这些文件内部存在手写的循环消费、手写 bthread 分发、手写结果聚合逻辑，建议逐步收敛到基类的 `parallel_consume()` 模型，避免多个 pipeline 各自维护 batch 切分、future 等待和日志收束逻辑。

- **建议二：GRG 侧重点检查特征服务与候选生成链路的 publish/commit 语义**
  - 适用文件：
    - `process/feature_service/doc_feature_pipeline.cpp`
    - `process/feature_service/doc_feature_with_cache_pipeline.cpp`
    - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
  - GRG 的基类在 `parallel_consume()` 内部负责 `publish(valid_num)` 和 `commit()`，因此业务 `process(queue_context)` 应重点保证：
    - `valid_num` 与实际有效输出数量一致。
    - `queue_context.output_vec` 中的数据顺序、数量和过滤逻辑一致。
    - 异常 batch 不应发布脏数据。
  - 如果业务代码中仍然手动调用 publisher，需要确认是否与基类发布逻辑重复，避免一次处理被重复提交。

- **建议三：GRC 侧重点梳理结果输出职责，避免误以为基类会自动发布**
  - 适用文件：
    - `processor/video_launch/ds_to_ridinfo_pipeline.cpp`
    - `processor/video_launch/cp_setaware_attention_pipeline.cpp`
    - `processor/video_launch/sketchy_rpc_pipeline.cpp`
  - GRC 的 `processor/base/pipeline_function.h` 主要负责批消费和日志聚合，不统一负责业务结果发布。
  - 因此排查“`process()` 中有结果，但下游无数据”时，应优先检查具体子类：
    - 是否正确写入输出 channel。
    - 是否在 `process(queue_context)` 或后续阶段完成结果搬运。
    - 是否因为 `input_data_construct` 过滤导致 `valid_num` 或输出为空。
  - 对新增 GRC pipeline，建议在代码注释或接口约定中明确“发布由子类负责”。

- **建议四：针对 RPC/特征/候选集合处理场景，优先引入 batch_size 与 concurrents 可观测性**
  - 适用文件：
    - `process/common_predict.cpp`
    - `process/feature_service/doc_feature_pipeline.cpp`
    - `processor/video_launch/sketchy_rpc_pipeline.cpp`
    - `processor/video_launch/sketchy_rpc_config.cpp`
  - 这些场景通常对吞吐和尾延迟敏感，建议补充运行时日志或指标：
    - 当前 `batch_size`
    - 当前 `concurrents`
    - 每个 batch 的输入数量、有效输出数量
    - worker batch 与 tail batch 的数量占比
    - `future.get()` 的错误码分布
  - 这样可以判断性能瓶颈来自 batch 太小、并发窗口不足、下游 RPC 饱和，还是 tail batches 串行比例过高。

- **建议五：大规模容器使用场景不建议盲目迁移，应优先迁移“批处理主链路”**
  - 两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 使用规模都很大，尤其是 `feeda-mv-grc`。
  - 但 `parallel_consume()` 不是容器替代技术，不应因为存在大量 `std::vector` 就直接改造。
  - 更适合迁移的场景是：
    - 候选集合分批处理。
    - 特征批量拉取。
    - 多路召回结果转换。
    - RPC 请求聚合与拆分。
    - 按 batch 生成输出并提交下游。
  - 不适合迁移的场景包括：
    - `service/grc_http_service.cpp` 中图依赖可视化、HTTP 参数解析、静态配置列表等控制面逻辑。
    - `model/model.h` 中单纯定义 `predict(std::vector<RidTmpInfoPtr>&)` 的接口声明。

---

### 4. ⚠️ 引入风险与限制

- **风险一：`QueueContext` 生命周期和引用捕获必须保持稳定**
  - 当前框架依赖 `context.queue_contexts.resize(concurrents)` 后的稳定槽位。
  - 如果后续改成动态 `emplace_back()`，再在 `bthread_async([this, &queue_context])` 中捕获引用，可能因为 vector 扩容导致引用失效。
  - 因此在 `process/base/pipeline_function.hpp` 和 `processor/base/pipeline_function.h` 的改造中，不建议随意调整 `queue_contexts` 的分配方式。

- **风险二：GRG 与 GRC 的发布语义不同，不能直接复制代码**
  - GRG：`process/base/pipeline_function.hpp` 中基类负责 `publish(valid_num)` 和 `commit()`。
  - GRC：`processor/base/pipeline_function.h` 中基类主要负责消费、并发和聚合，具体输出通常由业务子类负责。
  - 因此从 `process/feature_service/doc_feature_pipeline.cpp` 复制模式到 `processor/video_launch/sketchy_rpc_pipeline.cpp` 时，需要特别检查发布职责，否则容易出现重复发布或没有发布。

- **风险三：并发窗口过大可能放大下游压力**
  - `concurrents` 并不是越大越好。
  - 对 RPC、模型预测、缓存查询等场景，例如 `process/common_predict.cpp`、`processor/video_launch/sketchy_rpc_pipeline.cpp`，过大的并发窗口可能导致：
    - 下游服务 QPS 突刺。
    - 请求排队增加。
    - tail latency 变差。
    - 日志和上下文聚合成本上升。
  - 建议结合压测结果调整 `batch_size` 与 `concurrents`，并增加限流或熔断保护。

- **风险四：请求收束顺序不能破坏**
  - 服务入口如 `src/service/grc_service.cpp`、`src/service/grg_service.cpp` 中，`graph->reset()` 或 `pooled_graph->reset()` 必须发生在响应发送、日志聚合、输出提交之后。
  - 如果为了复用对象或提前释放资源而过早 reset，可能导致：
    - `post_process()` 读取到已清理状态。
    - 日志缺失。
    - 输出 channel 未提交完成。
    - 异步任务仍访问已释放上下文。
  - 对业务代码库新增 pipeline 时，应把 reset 顺序作为 code review 必查项。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
