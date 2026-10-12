# 2026-08-20 周四代码理解：GRG 散度合并执行边界与结果回收

> 日期：2026-08-20  
> 主题来源：2026-06-01 daily-plan 文件没有可直接执行的当日计划，按历史未覆盖主题 fallback 到 GRG 散度合并执行边界；KU/业务背景需人工补充。  
> 范围：只分析 `src/process/diversity_merge.cpp`，聚焦 `process()` 的结果容器准备、执行上下文建立、散度结果输出、以及融合结束时的回收边界。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:14px;background:#f8fafc;line-height:1.45;">
  <div style="display:grid;grid-template-columns:1.1fr 1.2fr 1fr;gap:12px;align-items:stretch;">
    <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
      <div style="font-size:12px;color:#475569;font-weight:700;text-transform:uppercase;">输入层</div>
      <div style="margin-top:8px;font-size:16px;font-weight:700;color:#111827;">ExecEngine 上下文</div>
      <div style="margin-top:8px;color:#334155;">从执行引擎拿到 `custom_context`，并把散度相关状态挂到当前图函数实例上。</div>
    </div>
    <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
      <div style="font-size:12px;color:#475569;font-weight:700;text-transform:uppercase;">处理层</div>
      <div style="margin-top:8px;font-size:16px;font-weight:700;color:#111827;">process() / emit() / clear()</div>
      <div style="margin-top:8px;color:#334155;">先把结果容器和统计容器取出来，再清空输出容器，确保本次合并是干净输入、干净输出。</div>
    </div>
    <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
      <div style="font-size:12px;color:#475569;font-weight:700;text-transform:uppercase;">输出层</div>
      <div style="margin-top:8px;font-size:16px;font-weight:700;color:#111827;">_diversity_merge_result</div>
      <div style="margin-top:8px;color:#334155;">结果列表在执行完后回到引擎可见的输出槽位，后续调用链继续消费这些结果。</div>
    </div>
  </div>
  <div style="margin-top:12px;border-top:1px solid #dbe3eb;padding-top:12px;color:#334155;">
    <strong>关键关系：</strong> `process() -> emit() -> clear() -> context setup -> downstream merge output`
  </div>
</div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
start
:enter process();
:emit result / stats containers;
:clear diversity_merge_result;
:prepare exec_context;
:run merge logic;
:write output to emitted containers;
stop
@enduml
```

## 2. 结构信息图
```infographic
infographic sequence-snake-steps-simple
data
  title GRG 合并执行的 4 个关键动作
  items
    - label 取出输出槽
      desc `emit()` 先拿到结果容器，再做本次写入准备
      icon mdi/export
    - label 清空旧结果
      desc `div_result->clear()` 避免上一轮残留影响当前轮
      icon mdi/broom
    - label 准备执行上下文
      desc `get_context()` 之后挂接执行所需状态
      icon mdi/clipboard-text-outline
    - label 写回合并结果
      desc 输出容器在结束时承接新的散度列表
      icon mdi/arrow-right-bold
```

## 3. 代码证据
- `src/process/diversity_merge.cpp:80-97`：`process()` 入口，先 `emit()` 结果容器并 `clear()`，再准备 `exec_context`
- `src/process/diversity_merge.cpp:83-90`：`_diversity_merge_result`、`_sid_rollback_cnt`、`_div_succ_size`、`_redpoint_topn` 四个输出/统计槽位一次性取出
- `src/process/diversity_merge.cpp:344-385`：执行过程中使用 `merge_pos()` 等字段做槽位级写入/判断
- `src/process/diversity_merge.cpp:521-548`：调试日志里输出 `diversity_merge_result`，说明结果向外部可见
- `src/process/diversity_merge.cpp:693`：`GRAPH_FUNCTION_EMIT_DATA(...)` 将最终结果暴露给执行引擎

## 4. Pitfalls
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
  <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
    <div style="font-weight:700;color:#111827;">结果容器要先清空</div>
    <div style="margin-top:6px;color:#334155;">如果只 `emit()` 不 `clear()`，旧的结果会和新结果叠在一起，导致散度输出错位。</div>
  </div>
  <div style="border:1px solid #cbd5e1;border-radius:8px;padding:12px;background:#fff;">
    <div style="font-weight:700;color:#111827;">输出槽位依赖固定顺序</div>
    <div style="margin-top:6px;color:#334155;">`sid_rollback_cnt`、`div_succ_size`、`redpoint_topn` 这些槽位都在入口一次性准备，顺序错了会影响下游字段绑定。</div>
  </div>
</div>

## 5. 调试 Checklist
```infographic
infographic list-column-done-list
data
  title GRG 合并链路排查清单
  items
    - label 检查结果容器是否清空
      done true
      desc 入口是否执行了 `div_result->clear()`
      icon mdi/eraser
    - label 检查上下文是否建立
      done true
      desc `get_context()` 后是否完成必要引用挂接
      icon mdi/account-cog-outline
    - label 检查槽位写入是否稳定
      done true
      desc `merge_pos()` 等字段是否按预期落点
      icon mdi/table-arrow-right
    - label 检查最终 emit
      done true
      desc 最终结果是否通过 `GRAPH_FUNCTION_EMIT_DATA` 暴露
      icon mdi/share-variant-outline
```

## 6. 证据来源
- `src/process/diversity_merge.cpp:80-97`
- `src/process/diversity_merge.cpp:344-385`
- `src/process/diversity_merge.cpp:521-548`
- `src/process/diversity_merge.cpp:693`

> 备注：KU 业务背景未逐篇读取正文，需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-01T19:09:55.360582
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 从扫描结果看，这个“散度合并执行边界与结果回收”能力在两个业务代码库里都处于**局部落地**状态：`feeda-mv-grg` 只有 1 个文件命中，`feeda-mv-grc` 有 10 个文件命中，说明它更像是一类**可复用的处理模式**，而不是已经全库统一的基础设施。
- 两个库都大量使用 `std::vector`、`std::string`、`std::unordered_map`，说明底层容器和状态承载方式已经成熟。若迁移的是“先清空输出容器、再建立执行上下文、最后回收结果”的边界控制方式，**迁移收益主要体现在正确性、可维护性和调试可观测性**，性能收益次之；其中 `feeda-mv-grc` 的适配潜力更高，因为它已经有更广泛的处理链路和中间态汇聚场景。

---

## 2. 代码库详情

### `feeda-mv-grg`：序列生成服务

- 目标能力当前只发现 1 个落点：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 这表明该库里与“多样性/散度”相关的逻辑主要集中在**策略层**，更适合做局部封装，而不是大规模改造。
- 现有基础容器使用非常充分：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 可作为参考的现有代码：
  - `model/model.h`
    ```cpp
    class Model {
    public:
      virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    };
    ```
  - `model/paddle_model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```
  - `model/paddle_model.h`
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```
- 结论：
  - `grg` 已具备较好的容器和模型接口基础，适合把“结果容器清空 + 状态初始化 + 输出回收”收敛到策略执行入口。
  - 但当前直接命中点少，说明这类能力仍偏局部，需要更谨慎地做抽象提炼。

### `feeda-mv-grc`：召回汇聚服务

- 目标能力命中更多，共 10 个文件：
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
- 这说明 `grc` 中已经存在多个**中间结果生成、筛选、调整、汇聚**环节，和“执行边界 + 结果回收”模式天然更贴近。
- 现有基础容器使用规模更大：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 可作为参考的现有代码：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    ```
    ```cpp
    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    ```
- 结论：
  - `grc` 的处理链更适合引入统一的执行上下文、结果槽位和最终 emit 机制。
  - 由于已有大量 `vector/map/string` 用法，落地新边界逻辑时，**不需要改容器体系，只需要统一状态生命周期管理**。

---

## 3. 💡 适用性评估与建议

- **建议 1：在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 中优先补齐“入口清空 + 上下文初始化”模式**
  - 适用场景：该策略若存在多轮合并、重入执行或局部复用结果容器的情况。
  - 建议做法：参照 `process()` 的思路，在进入策略前显式清空结果容器，避免旧结果残留。
  - 价值：减少多轮执行的脏数据问题，提升散度结果稳定性。

- **建议 2：在 `feeda-mv-grg/model/model.h` 和 `feeda-mv-grg/model/paddle_model.h` 中保留现有 `std::vector` 传参模式，但补充结果回收边界说明**
  - 适用场景：`predict(std::vector<...>& candidate_vec, uint32_t pos)` 这类以候选集为核心输入的接口。
  - 建议做法：如果后续要承接散度合并结果，优先在模型调用前后增加“输入候选集 / 输出结果集”的边界注释和封装函数，而不是直接改底层预测签名。
  - 价值：降低接口震荡，便于逐步迁移。

- **建议 3：在 `feeda-mv-grc/processor/multi_factor/subcate_future_factor_gen.cpp` 与 `feeda-mv-grc/processor/multi_factor/ltr_factor_gen_scene.cpp` 中引入统一执行上下文对象**
  - 适用场景：多 factor 生成链路中存在多个中间态、多个输出槽位时。
  - 建议做法：把输入、统计信息、临时结果、最终输出统一挂到一个 `exec_context` 风格对象上，避免散落在多个局部变量里。
  - 价值：更接近技术笔记里的“先取出输出槽，再准备上下文，再写回结果”的边界控制方式，便于排查。

- **建议 4：在 `feeda-mv-grc/operator/adjuster/sketchy/duanju_adjuster.cpp` 和 `feeda-mv-grc/operator/adjuster/sketchy/ltv_factor_cp_opt.cpp` 中统一结果写回协议**
  - 适用场景：调整器需要消费上游合并结果，并向下游传递更新后的列表时。
  - 建议做法：明确“输入列表不变 / 输出列表重建”还是“原地修改”的约定；若采用结果回收模式，优先使用统一的 `emit` 风格接口。
  - 价值：降低下游读取到半初始化数据的风险。

- **建议 5：在 `feeda-mv-grc/processor/filter/low_agile_goodrate_filter_operator.cc` 中增加槽位顺序校验**
  - 适用场景：过滤逻辑依赖多个统计字段，如回滚数、成功数、topn 等。
  - 建议做法：把输出字段绑定顺序固定下来，并增加 schema 校验或断言。
  - 价值：防止“字段顺序变了但编译仍通过”的隐性线上问题。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：输出槽位顺序依赖强**
  - 技术笔记里已经明确有类似 `_sid_rollback_cnt`、`_div_succ_size`、`_redpoint_topn` 这种入口一次性准备的槽位。
  - 一旦业务代码里字段绑定顺序不一致，就可能出现结果错位，问题不容易在编译期暴露。

- **风险 2：`clear()` 语义会影响复用缓存**
  - 如果业务代码依赖容器复用来减少分配次数，直接清空结果容器可能带来额外的分配/拷贝开销。
  - 需要区分“逻辑清空”和“物理释放”，不要误伤性能路径。

- **风险 3：`feeda-mv-grg` 目前命中点少，抽象过早会放大改造成本**
  - 只有 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 这一个显式落点，说明尚未形成全局统一模式。
  - 若强行做统一封装，可能需要额外改动大量策略调用链，收益不一定立刻释放。

- **风险 4：执行上下文若被共享，需注意线程安全**
  - 如果 `exec_context`、结果容器或统计容器被多个请求复用，必须确认是否存在并发写入。
  - 否则容易出现数据串写、旧状态污染、以及调试难度显著上升的问题。

---

## 结论

- `feeda-mv-grc` 更适合优先落地这类“执行边界 + 结果回收”能力，因为它已有更多处理链路和中间态汇聚场景。
- `feeda-mv-grg` 则更适合做**局部策略级封装**，重点是保证结果容器清空、上下文初始化和输出回收一致。
- 两个库都已经大量使用标准容器，因此迁移时重点不是替换基础类型，而是**统一生命周期管理与输出协议**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
