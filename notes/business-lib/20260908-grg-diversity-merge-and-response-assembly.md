# 2026-09-08 周一业务库理解：DiversityMerge 并行切片、结果汇聚与响应组装边界

> 日期：2026-09-08  
> 主题来源：当前 daily-plan 仍未覆盖本日；按历史候选回退到 `GRG 成本/CPU 性能优化热点复盘` + `bthread_async 批量并行模式与捕获安全`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/process/diversity_merge.cpp`、`src/process/response_function.cpp`，聚焦 extmsg 回填、并行软规则、Future 汇聚、结果生成与输出约束。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">输入组装层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/process/response_function.cpp:180-239`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先写 attachment、content、extmsg 和请求派生字段，再进入后续规则链。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">并行调权层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/process/diversity_merge.cpp:251-260` / `743-774`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">`_input_map` 预留通道后，按固定切片发射 `bthread_async` worker。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">结果回收层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`future.get()` + `output.next_all()` + `_div_nid_set.reserve()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先收敛并行结果，再生成最终 `div_result`，避免输出和中间态不同步。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">extmsg</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">DiversityMerge</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">bthread_async</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">result / response</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRG diversity merge and response assembly boundary
participant "response_function.cpp" as RF
participant "extmsg / attachment" as EXT
participant "DiversityMergeFunction" as DM
participant "bthread_async" as ASYNC
participant "Future<void, Mutex>" as FUT
participant "output.next_all()" as OUT
participant "div_result" as RES
RF -> EXT : fill attachment / content / extmsg
EXT -> EXT : SerializeToString()
RF -> RF : base64_encode()
RF -> RF : set_predictor_extmsg()
DM -> DM : reserve input_map / ignore indexes
DM -> ASYNC : spawn workers by slice
ASYNC --> FUT : future handles
DM -> FUT : future.get()
DM -> OUT : collect next_all()
OUT --> DM : result_vec
DM -> RES : push_item_to_result()
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title GRG 业务链上的 6 个关键点
  desc 这些点决定了字段回填何时结束、并行切片如何展开，以及结果何时可以安全生成
  items
    - label extmsg 组装
      desc `src/process/response_function.cpp:193-230` 先补 attachment，再读请求 pass-through
      icon mdi-archive-edit-outline
    - label 结果字段填充
      desc `src/process/response_function.cpp:233-260` 补 local_city、sample_rate、set2set_key 等请求派生数据
      icon mdi-form-textbox
    - label 输入预留
      desc `src/process/diversity_merge.cpp:232-255` 先 `reserve()` 再填 `_input_map`
      icon mdi-database-plus
    - label 并行切片
      desc `src/process/diversity_merge.cpp:749-755` 固定 `items_per_thread` 并发派发 worker
      icon mdi-call-split
    - label future 汇聚
      desc `src/process/diversity_merge.cpp:770-774` 统一等待 worker 完成
      icon mdi-vector-link
    - label 结果生成
      desc `src/process/diversity_merge.cpp:808-814` 通过 `output.next_all()` 组装最终结果
      icon mdi-playlist-check
```

## 3. 代码链路拆解
### 3.1 响应组装先于并行调权，字段要在序列化前一次写完
- `src/process/response_function.cpp:193-206`：先处理 `_pass_though_response->mutable_attachment()`，把短剧合集的 `duanju_7d_tgi`、`heji_7d_tgi` 填进去。
- `src/process/response_function.cpp:210-231`：再写 `content_ptr`、`extmsg`、`sample_dnn_q`，并读取 `red_point_info_pass_through()` 作为 `recall_by` 输入。
- `src/process/response_function.cpp:233-260`：继续补 `local_city`、`local_city_ml`、`sample_rate` 等派生字段。这个顺序说明响应组装的边界在“字段写完”之前，不能先序列化后回头补。

### 3.2 并行切片的核心不在线程数，而在输入预留与边界划分
- `src/process/diversity_merge.cpp:232-255`：先把 `_ignore_rule_indexes`、`_ignore_loads_indexes` 做 `reserve()`，再构建 `_input_map`，说明输入侧的容量契约是显式的。
- `src/process/diversity_merge.cpp:749-755`：根据 `total_items` 和 `concurrent_num` 算 `items_per_thread`，随后把每段切片交给 `bthread_async`。worker 只负责自己那一段，不碰其他区间。
- `src/process/diversity_merge.cpp:751-767`：lambda 捕获了 `effect_input`、`general_adjust_rule_operators` 和 `exec_context`，因此这些对象的生命周期和只读/局部写语义必须保持稳定。

### 3.3 future 汇聚后才进入结果生成，reserve 是输出契约的一部分
- `src/process/diversity_merge.cpp:770-774`：`future.valid()` 后统一 `get()`，先确保所有并行段都结束，再向下走。
- `src/process/diversity_merge.cpp:808-811`：`_div_nid_set.reserve(result_num + effect_input_size)` 之后才调用 `output.next_all()`，说明结果生成前必须先把输出容量准备好。
- `src/process/diversity_merge.cpp:811-814`：`next_all()` 返回的 `result_vec` 再逐项拆解到 `div_result`，这里的输出不是边算边出，而是收敛后一次性组装。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">并行边界错一次，结果集会安静地漂移，不一定立刻崩</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`bthread_async` 捕获了外层输入和执行上下文，若 `future.get()` 前把这些对象提前释放，异常不会总是马上暴露，但结果会间歇性损坏。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`SerializeToString()` 之后再改 `extmsg` 的字段，会让 `predictor_extmsg` 和内存对象不一致。所有字段必须先回填完，再进入编码输出。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 排查顺序：attachment/content/extmsg → SerializeToString → base64 → predictor_extmsg → reserve → async 切片 → future.get() → output.next_all()</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GRG 业务链排查清单
  desc 适用于 extmsg 缺字段、predictor_extmsg 不一致、并行任务悬挂、结果顺序漂移和 reserve 不足
  items
    - label 检查字段顺序
      desc 所有业务字段必须在 `SerializeToString()` 前写完
      done true
    - label 检查 base64 输出
      desc `predictor_extmsg` 要和序列化后的 payload 一致
      done true
    - label 检查输入预留
      desc `_input_map.reserve()` 和忽略索引的 reserve 要先于填充
      done true
    - label 检查 async 切片
      desc `items_per_thread` 与 `total_items` 要匹配
      done true
    - label 检查 future 收敛
      desc 所有有效 future 都要 `get()` 完
      done true
    - label 检查结果生成
      desc `output.next_all()` 之后再组装 `div_result`
      done true
```

## 6. 证据来源
- `src/process/response_function.cpp:193-260`
- `src/process/response_function.cpp:4218-4294`
- `src/process/diversity_merge.cpp:232-255`
- `src/process/diversity_merge.cpp:749-774`
- `src/process/diversity_merge.cpp:808-814`

## 7. 说明
当前运行环境没有 2026-09-08 的 daily-plan 文件；本笔记基于历史候选回退生成，KU 正文未读取，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-10T19:05:02.587340
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析

## 1. 分析摘要

本次技术笔记聚焦于 `DiversityMerge` 的并行切片、`bthread_async` 任务派发、Future 统一汇聚、结果批量生成，以及 `response_function.cpp` 中 extmsg 的字段回填与序列化边界。扫描结果表明，`feeda-mv-grg` 和 `feeda-mv-grc` 已经大量使用 `std::vector`、`std::string`、`std::unordered_map` 等标准容器，但目前只有少量文件被识别为与目标技术直接相关：GRG 侧 1 个文件，GRC 侧 10 个文件。因此，容器层面的迁移和改造基础较好，但 `bthread_async + Future + 结果汇聚` 这套并行模式是否已经形成成熟经验，仍需要结合具体源码进一步确认。

整体上，该技术适合优先应用于“候选集合较大、规则或因子之间相对独立、结果可在末尾统一汇聚”的场景，例如 GRG 的多样性规则处理、GRC 的多因子生成和部分过滤算子。迁移收益主要来自降低串行处理耗时、控制任务粒度、减少动态扩容以及规范结果生成边界；但不建议直接对所有 `std::vector` 遍历逻辑进行并行化，必须先确认数据依赖、共享状态、结果顺序和线程安全约束。

---

## 2. 代码库详情

### 2.1 `feeda-mv-grg`：已有相关技术落点，但规模较小

- 扫描发现与目标技术相关的使用文件共 **1 个**：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 该文件位于 `strategy/diversity/rule/` 路径下，与技术笔记中的 `DiversityMerge`、软规则调权、多样性处理场景具有较强的业务对应关系。
- 可以优先将 `low_clarity_diversity_rule.cpp` 作为 GRG 侧的适配入口，重点确认：
  - 规则是否对候选项逐项独立处理；
  - 是否存在固定范围的候选切片；
  - 规则执行过程中是否修改共享的候选集合；
  - 结果是否可以在所有 worker 完成后统一写回。
- GRG 代码库中标准容器使用规模较大：
  - `std::vector`：约 **1969 次**，分布在 **356 个文件**；
  - `std::string`：约 **2443 次**，分布在 **425 个文件**；
  - `std::unordered_map`：约 **734 次**，分布在 **205 个文件**。
- 可参考的标准容器使用代码包括：
  - `model/model.h:9`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
  - `model/paddle_model.h:103`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```
  - `model/paddle_model.h:107`
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                                  general_predict::PredictSample* predict_sample = nullptr,
                                  bool is_from_cube = true) const;
    ```
- 这些代码说明 GRG 已经普遍采用 `std::vector` 传递候选集合，但目前不能仅凭容器使用情况判断其已经具备异步切片、Future 汇聚或并发写回能力。

### 2.2 `feeda-mv-grc`：候选应用面较广，具备较好的并行改造基础

- 扫描发现与目标技术相关的使用文件共 **10 个**，已列出的重点文件包括：
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
- 这些文件分别覆盖：
  - LTR 因子生成；
  - LTV 因子计算或拷贝优化；
  - 短剧相关调权；
  - 低敏捷度/低灵活性候选过滤；
  - 子类目未来因子生成。
- 从业务职责看，`ltr_factor_gen_scene.cpp` 和 `subcate_future_factor_gen.cpp` 更可能存在“对多个候选项或多个因子独立计算”的场景，适合优先评估并行切片。
- `low_agile_goodrate_filter_operator.cc` 适合评估批量过滤，但要重点确认过滤过程中是否原地删除候选、修改共享索引或依赖前序元素状态。
- `duanju_adjuster.cpp` 和 `ltv_factor_cp_opt.cpp` 可能涉及调权结果写回，不能直接套用“worker 内共享写入”的方式，应优先采用局部结果收集、统一汇聚后写回。
- GRC 标准容器使用规模更大：
  - `std::vector`：约 **8520 次**，分布在 **1290 个文件**；
  - `std::string`：约 **7267 次**，分布在 **1247 个文件**；
  - `std::unordered_map`：约 **2860 次**，分布在 **646 个文件**。
- 可参考的代码包括：
  - `service/grc_http_service.cpp:62`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto& all_vertex = graph_engine->get_vertexs_message(graph_name);
    ```
  - `service/grc_http_service.cpp:81`
    ```cpp
    static std::vector<std::string> colors{
        "#FFB6C1", "#DC143C", "#DB7093", "#FF1493"
    };
    ```
  - `service/grc_http_service.cpp:152`
    ```cpp
    std::string resp_str;
    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    ```
- 上述代码可作为容器预分配、批量收集和字符串结果组装的风格参考，但没有证据表明 `service/grc_http_service.cpp` 已经使用了 `bthread_async` 或技术笔记中的 Future 汇聚模式。

---

## 3. 💡 适用性评估与建议

- **优先在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 评估并行切片**
  - 该文件与 Diversity 规则场景最接近，可以参考技术笔记中的 `src/process/diversity_merge.cpp:749-774`。
  - 如果规则处理是对候选项逐项独立计算，建议：
    - 先根据候选总数和并发度计算 `items_per_thread`；
    - 按 `[begin, end)` 划分固定区间；
    - 每个 `bthread_async` worker 只处理自己的切片；
    - 通过 `future.get()` 统一等待；
    - 最后再合并或写回结果。
  - 不建议 worker 直接向共享 `std::vector` 执行 `push_back`，可以先为每个 worker 建立局部结果，汇聚阶段按切片顺序合并，以降低锁竞争和结果乱序风险。

- **在 `feeda-mv-grc/processor/multi_factor/ltr_factor_gen_scene.cpp` 中评估因子生成并行化**
  - 该文件适合检查是否存在大量候选项上的重复因子计算。
  - 如果不同候选项之间没有依赖，可以将候选集合按照固定大小切片，并使用技术笔记中的 Future 汇聚方式。
  - 建议在任务派发前完成输入容器和结果容器的容量准备，例如：
    - 对候选输入使用 `reserve()`；
    - 对每个切片使用独立结果缓冲区；
    - 汇聚时通过固定索引写入，避免并行阶段反复扩容。
  - 如果因子计算会更新公共 `unordered_map`，应改为 worker 私有 map 或先收集 `(key, value)`，在主线程统一合并，不能直接假设 `std::unordered_map` 支持并发写入。

- **在 `feeda-mv-grc/processor/multi_factor/subcate_future_factor_gen.cpp` 中尝试“并行计算、统一生成结果”**
  - 该文件名称表明其可能包含未来因子或子类目相关批量计算逻辑，适合采用“任务执行与结果生成分离”的设计。
  - 可参考技术笔记中：
    - `src/process/diversity_merge.cpp:770-774` 的 Future 统一 `get()`；
    - `src/process/diversity_merge.cpp:808-814` 的 `reserve()` 后再执行 `output.next_all()`。
  - 建议明确划分两个边界：
    - worker 阶段只负责计算中间因子；
    - 所有 Future 完成后，再统一组装最终因子结果。
  - 这样可以避免部分 worker 尚未完成时就生成最终输出，降低中间态和最终态不一致的风险。

- **在 `feeda-mv-grc/operator/adjuster/sketchy/duanju_adjuster.cpp` 和 `ltv_factor_cp_opt.cpp` 中采用局部结果 + 汇聚写回**
  - 这两个文件涉及调权或因子复制优化，适合重点排查是否存在：
    - 对同一候选对象的多个字段写入；
    - 多个规则同时修改同一个权重；
    - 按处理顺序叠加调权结果。
  - 如果规则之间存在顺序依赖，不建议直接并行执行整个 adjuster 链。
  - 更稳妥的方式是：
    - 并行计算每个候选或每个规则的局部调整量；
    - Future 全部完成后，按照既有规则顺序统一应用调整量；
    - 保持最终结果顺序和串行版本一致。
  - 这类改造可以参考 `src/process/diversity_merge.cpp` 的“并行执行、集中生成”边界，但不能直接复制其共享状态处理方式。

- **在 `feeda-mv-grc/processor/filter/low_agile_goodrate_filter_operator.cc` 中先做依赖分析，再决定是否并行**
  - 过滤逻辑只有在“每个候选的保留/剔除结果相互独立”时才适合切片。
  - 如果当前实现使用 `erase()`、双指针压缩或根据前面已保留数量决定后续结果，应改为：
    - worker 阶段只生成 `keep/discard` 标记；
    - 主线程根据原始顺序统一压缩候选集合。
  - 这样可以保留输入顺序，避免多个线程同时修改同一个 `std::vector` 导致迭代器失效或结果顺序漂移。
  - 该文件也适合作为并行化收益验证点：统计候选数量、单项过滤耗时和线程调度开销后，再决定是否启用并发。

- **对于响应和序列化边界，参考 `src/process/response_function.cpp:193-260`，不要在业务库中提前编码**
  - 如果 GRC 的因子或过滤结果最终需要写入响应字段、扩展字段或透传字段，应遵循：
    - 先完成 attachment、content、extmsg 及派生字段填充；
    - 再执行 `SerializeToString()`；
    - 再进行 base64 编码；
    - 最后写入 `predictor_extmsg` 或等价输出字段。
  - 该顺序可作为 `service/grc_http_service.cpp` 中响应组装逻辑的检查标准。
  - 当前扫描结果未显示 GRC 已经使用技术笔记中的 extmsg 组装实现，因此这里更适合作为边界规范迁移，而不是直接替换某个现有 API。

---

## 4. ⚠️ 引入风险与限制

- **当前缺少完整的并发实现证据，不能仅凭标准容器使用量判断迁移收益**
  - `std::vector`、`std::string` 和 `std::unordered_map` 的使用规模只能说明代码库具备较好的 STL 使用基础。
  - 它们并不能证明现有业务逻辑适合 `bthread_async`，也不能说明容器访问已经具备并发安全性。
  - 在正式改造前，应补充扫描 `bthread_async`、Future、线程池、锁以及异步回调的实际使用情况。

- **lambda 捕获对象的生命周期和可变性必须严格确认**
  - 技术笔记中 `bthread_async` worker 捕获了 `effect_input`、`general_adjust_rule_operators` 和 `exec_context`。
  - 对 `low_clarity_diversity_rule.cpp`、`ltr_factor_gen_scene.cpp` 等文件进行迁移时，需要确认：
    - 捕获对象在所有 `future.get()` 完成前不会析构；
    - worker 是否只读访问；
    - 对象内部是否包含非线程安全缓存、临时 buffer 或共享指针状态。
  - 对不确定的对象，建议改为显式传递只读快照、局部副本或不可变引用。

- **并行结果可能出现顺序漂移或重复写回**
  - 不同切片完成顺序不稳定，如果直接向公共结果 `vector` 追加，可能导致结果顺序变化。
  - 如果业务依赖候选原始顺序、规则优先级或稳定排序，应使用切片编号、原始索引或预分配结果数组进行确定性汇聚。
  - 对 `low_agile_goodrate_filter_operator.cc` 这类过滤逻辑，尤其要避免 worker 同时执行 `erase()` 或修改公共候选数组。

- **响应序列化不一致会造成隐蔽的线上问题**
  - 如果在 `SerializeToString()` 或 base64 编码后继续修改 extmsg、attachment 或派生字段，内存对象与输出字段会不一致。
  - 对照 `src/process/response_function.cpp:193-260`，应将字段填充和序列化明确分成两个阶段。
  - 对迁移后的接口需要增加一致性测试，至少验证：
    - extmsg 内字段完整；
    - `predictor_extmsg` 与最终对象内容一致；
    - 并行和串行执行得到相同的字段及顺序。

- **并发调度开销可能抵消计算收益**
  - 当候选数量较少、单个规则执行很轻或请求并发已经较高时，频繁创建异步任务可能增加调度、Future 管理和上下文切换成本。
  - 建议增加并行阈值，例如仅当候选数超过指定规模时启用切片，并通过 P50、P95/P99 延迟、CPU 利用率和结果一致性进行对比。
  - `duanju_adjuster.cpp`、`ltv_factor_cp_opt.cpp` 等轻量调权场景尤其需要先进行基准测试，再决定是否引入异步执行。

---

## 5. 结论

- `feeda-mv-grg` 适合以 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 为切入点，验证 Diversity 规则的切片执行和集中汇聚。
- `feeda-mv-grc` 适合优先评估：
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
- `std::vector`、`std::unordered_map` 等标准容器在两个代码库中使用规模已经很大，容器替换本身的学习和适配成本较低；真正的改造难点在于任务边界、共享状态、结果顺序、生命周期和序列化时机。
- 推荐采用“小范围、可回退”的迁移路径：先实现串行/并行双路径，使用固定输入和固定结果顺序进行一致性校验，再根据基准测试结果决定是否扩大到更多 adjuster、filter 和 factor generator。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
