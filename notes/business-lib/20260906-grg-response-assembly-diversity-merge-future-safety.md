# 2026-09-06 周日业务库理解：GRG 响应组装、extmsg 序列化与 DiversityMerge 并行边界

> 日期：2026-09-06  
> 主题来源：当前可用 daily-plan 仍停留在旧的周计划，没有 2026-09-06 当日计划；按历史候选回退到 `GRG 成本/CPU 性能优化热点复盘` + `bthread_async 批量并行模式与捕获安全`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/process/response_function.cpp`、`src/process/diversity_merge.cpp`，聚焦 extmsg 序列化、扩展字段回填、并行软规则、Future 汇聚和结果生成。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">输入组装层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/process/response_function.cpp:4218-4223`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">把业务结果写进 `extmsg`，再压成 `predictor_extmsg` 放进响应项。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">并行调权层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/process/diversity_merge.cpp:743-774`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">把 effect_input 切成多个子片，交给 `bthread_async` 并行跑软规则。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">结果回收层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`output.next_all()` + `_div_nid_set.reserve()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">汇聚多流结果、生成输出向量，再做顶层排序和实验分流。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">extmsg</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">parallel soft rules</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">SelectStreamContainer</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">final div_result</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRG response assembly and diversity merge boundary
participant "response_function.cpp" as RF
participant "extmsg" as EXT
participant "DiversityMerge" as DM
participant "bthread_async" as ASYNC
participant "Future<void, Mutex>" as FUT
participant "SelectStreamContainer" as OUT
participant "RidTmpInfo" as RID
RF -> EXT : fill redpoint / score / flags
EXT -> EXT : SerializeToString()
RF -> RF : base64_encode()
RF -> RF : set_predictor_extmsg()
DM -> DM : prepare input_map / reserve()
DM -> ASYNC : spawn workers
ASYNC --> FUT : future handles
DM -> FUT : future.get()
DM -> OUT : run diversity engine
OUT --> DM : next_all()
DM -> RID : push_item_to_result()
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title GRG 业务链上的 6 个关键点
  desc 这些点决定了扩展字段如何回填、并行切片如何分配，以及结果如何从多队列合并成最终输出
  items
    - label extmsg 序列化
      desc `src/process/response_function.cpp:4218-4223` 将扩展信息压成 `predictor_extmsg`
      icon mdi-archive-arrow-down
    - label 热搜卡标记
      desc `src/process/response_function.cpp:4229-4243` 按实验位补 `ip_recall_type`
      icon mdi-tag-outline
    - label 端重排辅助字段
      desc `src/process/response_function.cpp:4256-4294` 写入 collection_id、other_bind_id_vec 等
      icon mdi-shape-plus
    - label async 切片
      desc `src/process/diversity_merge.cpp:743-767` 按 `concurrent_num` 拆成并行 worker
      icon mdi-call-split
    - label future 汇聚
      desc `src/process/diversity_merge.cpp:770-774` 等待所有 worker 完成后再继续
      icon mdi-vector-link
    - label 结果生成
      desc `src/process/diversity_merge.cpp:808-850` 从 `output.next_all()` 组装最终 `div_result`
      icon mdi-playlist-check
```

## 3. 代码链路拆解
### 3.1 响应组装的边界在 extmsg 序列化，而不是 item 字段写入
- `src/process/response_function.cpp:4218-4223`：先把 `extmsg` `SerializeToString()`，再做 `base64_encode()`，最后写到 `predictor_extmsg`。这说明扩展信息最终是一个独立协议块，不是散落在多个字段里的松散信息。
- `src/process/response_function.cpp:4229-4243`：当 `newhot_resou_card_replace_flag` 命中实验位时，直接修改 `ip_recall_type`。这个步骤证明 extmsg 不只是展示层元数据，也承载路由或标记语义。
- `src/process/response_function.cpp:4246-4294`：`intent_score_qsc`、`short_trigger_id`、`collection_id`、`other_bind_id_vec` 逐项回填，说明响应组装在这里已经接近最终出网结构。

### 3.2 多流调权的主成本在并行切片和结果汇聚之间
- `src/process/diversity_merge.cpp:743-747`：固定 `concurrent_num = 8`，并提前 `reserve(concurrent_num)`，说明线程数是显式控制的，不是动态扩展。
- `src/process/diversity_merge.cpp:751-767`：每个 worker 只处理 `effect_input` 的一个区间，然后把子列表传给 `run_soft_rule()`。这类设计把大任务拆成多个局部操作，避免单线程长尾。
- `src/process/diversity_merge.cpp:770-774`：先把所有 future `get()` 完，再进入后续分支，说明结果一致性优先于边跑边合并。

### 3.3 reserve 不只是性能优化，也是结果生成契约的一部分
- `src/process/diversity_merge.cpp:253-255`：`_input_map.reserve(4)` 后再填 `loads` 和 `rule` 队列，表明输入通道数量是预知的。
- `src/process/diversity_merge.cpp:809-811`：`_div_nid_set.reserve(result_num + effect_input_size)` 后再取 `output.next_all()`，这说明作者希望在生成结果前就把容量准备好。
- `src/process/diversity_merge.cpp:877-895` 和 `src/process/diversity_merge.cpp:903-923`：两个实验分支都直接操作 `div_result`，表明最终排序是多层实验叠加的产物，不是单一引擎输出。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">最容易出问题的不是业务规则本身，而是并行边界和字段回填顺序</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`bthread_async` 的 lambda 捕获了 `effect_input`、`general_adjust_rule_operators` 和 `exec_context`。只要外层对象生命周期比 worker 短，结果就会变成间歇性崩溃，而不是稳定错误。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`SerializeToString()` 之后才去改 `extmsg`，会让 `predictor_extmsg` 和内存里的 `extmsg` 不一致。所有字段补写要在序列化前完成。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 排查顺序：extmsg 赋值 → SerializeToString → base64 → predictor_extmsg → async 切片 → future.get() → result 生成</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GRG 业务链排查清单
  desc 适用于 extmsg 缺字段、predictor_extmsg 不一致、并行任务悬挂、结果顺序漂移和 reserve 不足
  items
    - label 检查 extmsg 顺序
      desc 所有字段必须在 `SerializeToString()` 前写完
      done true
    - label 检查 base64 输出
      desc `predictor_extmsg` 要和序列化后的 payload 一致
      done true
    - label 检查 async 切片
      desc `concurrent_num`、`items_per_thread` 和 `total_items` 要匹配
      done true
    - label 检查 future 收敛
      desc 所有 `future.valid()` 都要 `get()` 完
      done true
    - label 检查结果预留
      desc `_div_nid_set.reserve()` 和 `_input_map.reserve()` 要按预期容量设置
      done true
    - label 检查实验分支
      desc 置顶/effect/loads/rule 的实验改写要在同一个分支模型下验证
      done true
```

## 6. 证据来源
- `src/process/response_function.cpp:4218-4243`
- `src/process/response_function.cpp:4246-4294`
- `src/process/response_function.cpp:4296-4401`
- `src/process/diversity_merge.cpp:233-255`
- `src/process/diversity_merge.cpp:743-774`
- `src/process/diversity_merge.cpp:808-923`

## 7. 说明
当前运行环境没有 2026-09-06 的 daily-plan 文件；本笔记基于历史候选与本地代码回退生成，KU 正文未读取，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-08T19:02:06.722310
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 本次笔记对应的技术点，核心是三类能力：**响应组装中的 extmsg 序列化边界**、**`bthread_async` 的并行切片与 Future 汇聚**、以及**`reserve()` / 容量预分配带来的性能优化**。这类能力对 C++ 业务代码的收益主要体现在：减少临时对象、降低串行长尾、避免序列化后字段不一致。
- 从扫描结果看，两个代码库都大量使用 `std::vector`、`std::string`、`std::unordered_map`，说明业务数据流本身非常适合做“批处理 + 预分配 + 结果汇聚”型优化。其中 **`feeda-mv-grc` 已有 10 个文件命中相关技术点，迁移潜力更高**；**`feeda-mv-grg` 仅 1 个文件命中，适合先做小范围试点再扩展**。

---

## 2. 代码库详情

- ### `feeda-mv-grg`：序列生成服务
  - 发现的目标库使用只有 **1 个文件**：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 现有容器使用规模：
    - `std::vector`：1969 次，分布在 356 个文件
    - `std::string`：2443 次，分布在 425 个文件
    - `std::unordered_map`：734 次，分布在 205 个文件
  - 说明：
    - 该库整体容器使用密集，但当前“目标技术”落点很少，说明**并行规则、序列化边界、结果汇聚**这类模式还没有大面积铺开。
    - 可参考的典型接口是 `model/model.h`、`model/paddle_model.h` 中的候选列表式预测接口，说明该库天然存在批量输入/输出场景，具备做切片并行和容量预分配的基础。
  - 适配判断：
    - 更适合从 **`strategy/diversity/rule/low_clarity_diversity_rule.cpp`** 做试点，先验证规则计算是否可并行化、结果是否可稳定汇聚，再逐步推广到模型预测链路。

- ### `feeda-mv-grc`：召回汇聚服务
  - 发现的目标库使用有 **10 个文件**，其中已扫描到的代表文件包括：
    - `processor/new_adjust/precise_score_init.cpp`
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
    - `processor/multi_factor/subcate_future_factor_gen.cpp`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - 现有容器使用规模更大：
    - `std::vector`：8520 次，分布在 1290 个文件
    - `std::string`：7267 次，分布在 1247 个文件
    - `std::unordered_map`：2860 次，分布在 646 个文件
  - 说明：
    - 该库业务链更长、对象更多、字段拼装更密集，更适合引入**“先组装后序列化”**和**“分片并行 + Future 汇聚”**。
    - `service/grc_http_service.cpp` 中已经可见 `unordered_map + vector + string` 的组合使用，说明这里存在典型的请求参数收集、依赖映射和响应拼装场景，和笔记里的 `response_function.cpp`、`diversity_merge.cpp` 模式高度相似。
  - 适配判断：
    - **迁移收益高于 grg**，尤其适合把“字段回填”“中间态预留”“结果合并”从隐式逻辑整理成显式边界。

---

## 3. 💡 适用性评估与建议

- **建议 1：把“序列化边界”前移到字段全部回填完成之后**
  - 适用文件：
    - `service/grc_http_service.cpp`
    - `processor/new_adjust/precise_score_init.cpp`
  - 建议做法：
    - 参考笔记中的 `src/process/response_function.cpp:4218-4223`，先完成所有扩展字段、标记字段、业务辅助字段的回填，再统一 `SerializeToString()` / 编码输出。
    - 避免“序列化后又改对象”的写法，否则会出现内存对象与最终输出不一致的问题。
  - 适合场景：
    - HTTP 响应组装
    - 调整器输出
    - 需要落盘或跨模块传递的结构化消息

- **建议 2：在高频批量处理链路里引入固定切片并行**
  - 适用文件：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
    - `processor/multi_factor/subcate_future_factor_gen.cpp`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
  - 建议做法：
    - 参考 `src/process/diversity_merge.cpp:743-774`，把输入按固定块大小拆分成多个 worker，而不是单线程顺序扫描全部候选。
    - 并行任务只处理局部片段，最终统一汇总，保证输出一致性。
  - 适合场景：
    - 规则打散
    - 特征计算
    - 候选集重排
    - 大列表过滤/打分

- **建议 3：对 `std::vector` / `std::unordered_map` 做显式 `reserve()`**
  - 适用文件：
    - `model/model.h`
    - `model/paddle_model.h`
    - `service/grc_http_service.cpp`
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - 建议做法：
    - 对已知上界的候选数组、依赖表、结果数组，提前 `reserve()`。
    - 参考 `src/process/diversity_merge.cpp:253-255` 和 `808-811` 的思路，把容量准备变成接口契约的一部分。
  - 预期收益：
    - 减少扩容次数
    - 降低搬移成本
    - 改善高 QPS 场景下的尾延迟

- **建议 4：对可并行模块使用“只读输入 + 局部输出”接口**
  - 适用文件：
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
    - `processor/new_adjust/precise_score_init.cpp`
  - 建议做法：
    - 将函数接口从“修改共享对象”改为“输入只读、输出局部结果、最终归并”。
    - 对共享状态较多的逻辑，优先拆成纯函数式处理单元，再挂到并行框架中。
  - 参考价值：
    - 这和 `diversity_merge.cpp` 里“worker 只处理局部区间，最后再汇聚”的模式一致，更利于后续扩展。

- **建议 5：在 `feeda-mv-grg` 先围绕 `low_clarity_diversity_rule.cpp` 做试点**
  - 适用文件：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 建议做法：
    - 由于当前仅 1 个文件命中目标技术，建议先做最小闭环：
      - 输入切片
      - 局部规则计算
      - Future 汇聚
      - 结果稳定性校验
    - 再决定是否扩展到 `model/` 层或更上游的候选生成链路。
  - 适合原因：
    - 风险低
    - 可控性强
    - 便于对比优化前后的耗时和结果一致性

---

## 4. ⚠️ 引入风险与限制

- **风险 1：异步任务捕获生命周期不安全**
  - 如果 `bthread_async` 或类似异步任务捕获了外层引用对象，例如候选集、规则集合、上下文对象，而外层函数提前返回，就可能出现间歇性崩溃。
  - 这类问题通常不会稳定复现，排查成本很高。
  - 建议优先使用值拷贝、`shared_ptr` 或明确的生命周期托管。

- **风险 2：序列化后再修改对象会造成数据不一致**
  - 一旦先 `SerializeToString()`，后续再改字段，最终输出与内存对象会分裂。
  - 对于 `service/grc_http_service.cpp` 这类响应拼装链路，要严格保证“字段全部写完 -> 再序列化 -> 再编码 -> 再输出”。

- **风险 3：并行化不等于加速，顺序依赖会破坏结果**
  - 某些 filter / adjust / ranking 逻辑对顺序敏感，直接拆片可能导致输出顺序漂移或实验分支结果变化。
  - 特别是 `processor/filter/*` 和 `operator/adjuster/*` 中，如果规则依赖前序状态，就必须先确认可并行边界。

- **风险 4：过度预分配可能带来内存浪费**
  - `reserve()` 不是越大越好，若上界估计偏差过大，会增加常驻内存。
  - 对于波动较大的流量路径，建议按历史分位数或配置阈值设定容量，而不是盲目放大。

---

如果你希望，我可以继续把这份分析**改写成更像技术笔记的正式章节风格**，或者再补一版 **“按代码库分别输出迁移优先级表”**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
