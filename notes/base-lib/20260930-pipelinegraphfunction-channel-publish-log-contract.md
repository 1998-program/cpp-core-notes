# 2026-09-30 基础库代码理解：PipelineGraphFunction 并行发布与日志汇总合同
<!-- topic-nav:start -->
> 主题：`base/pipeline-consume` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260612-pipelinegraphfunction-parallel-consume.md)
<!-- topic-nav:end -->


> 日期：2026-09-30（周三）
> 主题选择：canonical 笔记仓库未发现今日 daily-plan；本地仅找到过期的 `daily-plan-20260529.json`。该计划的周三推荐项已在近期被覆盖，本次按相邻未充分展开的框架边界回退到 **GRG PipelineGraphFunction 的 Channel 发布、Future join、_pipeline_done 与线程日志汇总合同**。
> 配对关系：业务库笔记分析 GRC FillMeta 的 GCMS 命中/删除决策门。两者共同说明：框架侧如何稳定发布批处理结果，业务侧如何决定候选是否继续进入下游。
> KU 状态：未读取 KU 正文；业务背景、线上收益和发布策略需人工补充。本文只依据当前代码快照。
> 范围：`baidu/feed-gr/feeda-mv-grg/src/process/base/pipeline_function.h`、`baidu/feed-gr/feeda-mv-grg/src/process/base/pipeline_function.hpp`、`baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/pipeline.conf`。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.05em;">PipelineGraphFunction channel publish contract</div><div style="display:grid;grid-template-columns:1fr 24px 1.1fr 24px 1.1fr 24px 1fr;gap:8px;align-items:stretch;margin-top:12px;"><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #3d5a80;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">配置入口</div><div style="margin-top:7px;font-size:12px;color:#52606d;">setup 读取 batch_size、concurrents，绑定 log_id、uid、SidInfo、ExpInfo 等 graph 全局依赖。</div></div><div style="text-align:center;color:#64748b;font-size:20px;padding-top:34px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #2d6a4f;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">消费分片</div><div style="margin-top:7px;font-size:12px;color:#52606d;">parallel_consume 按 batch_size 拉取 Range，前 concurrents 批进入 bthread，剩余批在本线程处理。</div></div><div style="text-align:center;color:#64748b;font-size:20px;padding-top:34px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #9a5b28;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">结果发布</div><div style="margin-top:7px;font-size:12px;color:#52606d;">每个 QueueContext 成功后用 publisher->publish(valid_num)，copy_n 输出，再 commit。</div></div><div style="text-align:center;color:#64748b;font-size:20px;padding-top:34px;">→</div><div style="background:#fff;border:1px solid #cbd5e1;border-left:4px solid #b8432f;border-radius:6px;padding:11px;"><div style="font-size:15px;color:#102a43;font-weight:700;">完成与观测</div><div style="margin-top:7px;font-size:12px;color:#52606d;">future join 后写 _pipeline_done，post_process 汇总线程级 SIA / vertex / RPC 日志。</div></div></div><div style="margin-top:12px;font-size:12px;color:#62748a;">核心边界：业务子类只负责 pre_process、input_data_construct、process、post_process 的扩展点；批消费、发布、完成信号和日志收口由模板统一控制。</div></div>

## 1. 核心结论

1. **PipelineGraphFunction 是批处理算子的并发模板，不是普通 GraphFunction 包装。** setup 阶段把图全局依赖塞进 `Context`，同时从配置读取 `batch_size` 与 `concurrents`；processor 阶段固定执行 `pre_process -> parallel_consume -> _pipeline_done -> emit_normal_data -> post_process`（`src/process/base/pipeline_function.hpp:9-48`、`:51-68`）。
2. **异步分支和本地分支使用同一个发布合同。** 无论 bthread 分支还是本地剩余批，`process(queue_context)` 成功后只要 `output_vec` 非空，就通过 `publisher->publish(valid_num)`、`copy_n`、`commit` 发布（`src/process/base/pipeline_function.h:57-113`）。业务子类不应绕过这个路径直接写下游 channel。
3. **Future join 是错误码收敛点，但不是短路取消点。** 异步任务返回非 `ERR_OK` 时最终 `ret` 会被覆盖；已经 commit 的其他批次不会被撤回。因此这个框架合同适合“批内尽量产出，错误由观测/上游策略处理”的链路，不适合需要全批原子性的链路。
4. **日志汇总发生在 queue_contexts 归并之后。** `post_process` 汇总每个线程的 `sum_related_logs`、`vertex_related_logs`、`rpc_related_logs`，再通过 `SIA_ADD` 和 `Util::add_vertex_log` 写回统一观测面（`src/process/base/pipeline_function.hpp:77-161`）。新增 QueueContext 日志字段时必须确认归并语义。
5. **配置中的 batch_size / concurrents 是运行形态的一部分。** `short_micro_video/pipeline.conf` 中多个预测类节点显式使用 `batch_size: 100`、`concurrents: 4`；调参数会改变 bthread 数、publisher commit 粒度和日志聚合样本（`conf/plugins/graph/short_micro_video/pipeline.conf:75-101`、`:212-214`）。

## 2. 调用链与发布时序

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title PipelineGraphFunction 并行消费与发布时序
participant "Graph Engine" as ENG
participant "PipelineGraphFunction::setup" as SETUP
participant "processor" as PROC
participant "pre_process" as PRE
participant "parallel_consume" as PC
participant "bthread tasks" as BT
participant "ChannelPublisher" as PUB
participant "post_process" as POST
ENG -> SETUP : setup(config)
SETUP -> SETUP : bind named_dependency to Context
SETUP -> SETUP : read batch_size / concurrents
ENG -> PROC : operator() -> processor()
PROC -> PRE : prepare input/output channel
PROC -> PUB : output.open()
PROC -> PC : mutable_input or input
PC -> PC : consumer.consume(batch_size)
loop first N ranges
  PC -> BT : bthread_async(process(queue_context))
  BT -> PUB : publish(valid_num) / copy_n / commit
end
loop remaining ranges
  PC -> PC : process local queue_context
  PC -> PUB : publish(valid_num) / copy_n / commit
end
PC -> BT : future.get()
PC --> PROC : ret
PROC -> PROC : emit _pipeline_done=true
PROC -> POST : aggregate thread logs
POST --> ENG : SIA + vertex/rpc logs
@enduml
```

## 3. 合同拆解

```infographic
infographic sequence-ascending-steps
data
  title PipelineGraphFunction 固定生命周期
  desc 子类只填扩展点，模板掌控消费、发布、完成标志和日志收口
  items
    - label 1. setup
      desc 绑定 log_id、uid、SidInfo、ExpInfo，读取 batch_size 和 concurrents
      icon mdi/cog-outline
    - label 2. pre_process
      desc 子类打开输入 channel、准备输出 channel 或命名依赖
      icon mdi/source-branch
    - label 3. parallel_consume
      desc 按 Range 构造 QueueContext，异步/本地两路执行 process
      icon mdi/lan
    - label 4. publish
      desc output_vec 非空时 publish + copy_n + commit
      icon mdi/publish
    - label 5. done
      desc 写 _pipeline_done，再执行普通数据 emit 和 post_process
      icon mdi/check-circle-outline
theme
  palette #3d5a80 #2d6a4f #b56b32
```

```infographic
infographic list-grid-badge-card
data
  title 需要重点保护的 6 个边界
  items
    - label QueueContext 引用
      desc bthread 捕获预分配 queue_context 引用，queue_contexts resize 后不能在异步结束前重新分配
      icon mdi/vector-link
    - label Publisher commit
      desc publish 后必须 copy_n 再 commit；异常路径不能留下未提交 commiter
      icon mdi/send-check
    - label ERR_OK 语义
      desc 单批失败只影响 ret，不会撤回其他批次已经发布的数据
      icon mdi/alert-circle-outline
    - label _pipeline_done
      desc 只有 processor 尾部统一 emit，业务子类不要提前伪造完成标志
      icon mdi/flag-checkered
    - label 日志归并
      desc SIA、vertex、RPC 日志从所有 QueueContext 汇总到统一图日志
      icon mdi/chart-timeline-variant
    - label 配置粒度
      desc batch_size 和 concurrents 会同时影响吞吐、延迟和日志解释
      icon mdi/tune
theme
  palette #3d5a80 #2d6a4f #b8432f
```

## 4. 调试口径

- **没有下游候选**：先看 `pre_process` 是否打开正确 input/output；再看 `process` 是否向 `output_vec` 写数据；最后看 `publisher->publish` 是否进入 `valid_num > 0` 分支。
- **局部候选缺失**：检查 `process(queue_context)` 的返回值。一个异步批失败不会自动取消其他批，问题可能只影响某些 Range。
- **_pipeline_done 已完成但结果为空**：这不是矛盾。`_pipeline_done` 表示模板执行完，不表示业务输出非空。
- **SIA/vertex 日志异常**：排查 `QueueContext` 的 `sum_related_logs`、`vertex_related_logs`、`rpc_related_logs` 是否在 process 内正确写入，并确认 `post_process` 的线程数归一逻辑符合预期。

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fffaf5;border:1px solid #ead7c0;border-left:5px solid #b56b32;border-radius:6px;padding:14px;margin:16px 0;color:#3d2b1f;"><div style="font-size:12px;font-weight:800;color:#8a4b20;text-transform:uppercase;letter-spacing:.05em;">Pitfalls / 并行发布边界</div><div style="margin-top:9px;font-size:14px;line-height:1.65;"><strong>引用捕获：</strong> bthread lambda 捕获 `queue_context` 引用，必须保证 `queue_contexts` 在 future.get 前不再 resize 或 clear。<br><strong>完成标志误读：</strong> `_pipeline_done=true` 只代表模板尾部执行到达，不代表每个 batch 都产出有效候选。<br><strong>错误非原子：</strong> ret 可以反映失败，但已 commit 的 channel 输出不会被回滚。<br><strong>观测延迟：</strong> 线程日志在 post_process 才汇总，单个 process 内直接看总 SIA 可能不完整。<br><strong>范围限制：</strong> KU 正文未读取，线上推荐配置与收益需人工补充。</div></div>

```infographic
infographic list-column-done-list
data
  title PipelineGraphFunction 排查 checklist
  items
    - label 检查 config 中 batch_size / concurrents
      desc 确认配置值是否符合当前场景延迟和吞吐要求
      done true
    - label 检查 pre_process 打开的输入输出 channel
      desc mutable_input 与 input 二选一，output 必须能 open publisher
      done true
    - label 检查 QueueContext 构造
      desc queue_index、input range、custom context 和 output_vec 初始状态要一致
      done true
    - label 检查 publish/commit 分支
      desc valid_num 为 0 时不会发布，下游空结果要从这里分叉排查
      done false
    - label 检查 future.get 返回值
      desc 单批失败只覆盖最终 ret，不撤销其他批次输出
      done false
    - label 检查 post_process 汇总
      desc SIA、vertex、RPC 日志是否从所有 queue_contexts 汇总
      done false
theme
  palette #2d6a4f #3d5a80 #b8432f
```

## 5. 证据来源

- `baidu/feed-gr/feeda-mv-grg/src/process/base/pipeline_function.h:21-31`：输入 consumer、输出 channel 与 publisher 类型定义。
- `baidu/feed-gr/feeda-mv-grg/src/process/base/pipeline_function.h:57-113`：`parallel_consume` 的异步/本地消费、publish/commit 和 future join。
- `baidu/feed-gr/feeda-mv-grg/src/process/base/pipeline_function.hpp:9-48`：setup 绑定 Context、读取 `batch_size` / `concurrents`、获取 `_pipeline_done`。
- `baidu/feed-gr/feeda-mv-grg/src/process/base/pipeline_function.hpp:51-68`：processor 固定执行顺序。
- `baidu/feed-gr/feeda-mv-grg/src/process/base/pipeline_function.hpp:77-161`：SIA、vertex、RPC 线程日志汇总。
- `baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/pipeline.conf:75-101`、`:212-214`：业务节点配置 `batch_size: 100`、`concurrents: 4`。
