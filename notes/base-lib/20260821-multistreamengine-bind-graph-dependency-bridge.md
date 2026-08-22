# 2026-08-21 周五代码理解：MultiStreamEngine bind_graph_dependency 桥接外层 GraphData

> 日期：2026-08-21  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的高优先级候选项回退；本次 cron 未发现可直接执行的当日 daily-plan，KU/业务背景需人工补充。  
> 范围：只分析 `src/processor/video_launch/diversity_merge.cpp` 与 `src/process/diversity_merge.cpp` 中的 `bind_graph_dependency`、外层 `GraphData` 绑定、`engine_vertex` 取数、以及依赖注入后的执行闭环；不扩展到完整 diversity 规则。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.1fr 1fr;gap:12px}.arch-col{border:1px solid #d9e2ec;border-radius:10px;padding:12px;background:#fff}.arch-h{font-size:18px;font-weight:800;margin:0 0 8px}.arch-s{font-size:12px;color:#64748b;margin:0 0 10px}.arch-box{border:1px solid #cbd5e1;border-radius:10px;padding:10px 12px;margin:8px 0;background:#f8fafc}.arch-box strong{display:block;font-size:13px;margin-bottom:4px}.arch-arrow{font-size:12px;color:#475569;margin:4px 0;text-align:center}.arch-tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:#dbeafe;color:#1d4ed8;margin-bottom:8px}.arch-note{font-size:12px;color:#475569;line-height:1.45}</style><div class="arch-wrap"><div class="arch-col"><div class="arch-h">外层数据桥接</div><div class="arch-s">把服务级 GraphData 变成 engine 可消费的输入绑定</div><div class="arch-box"><span class="arch-tag">Request Layer</span><strong>video_launch / diversity_merge 请求上下文</strong><span class="arch-note">携带场景、召回结果、配置开关与后续执行所需的图依赖。</span></div><div class="arch-arrow">↓ bind_graph_dependency</div><div class="arch-box"><span class="arch-tag">Bridge Layer</span><strong>MultiStreamEngine 依赖绑定</strong><span class="arch-note">把外层 GraphData、节点输入、图内依赖关系写入 engine_vertex 可见的状态。</span></div><div class="arch-arrow">↓</div><div class="arch-box"><span class="arch-tag">Execution Layer</span><strong>engine_vertex / function chain</strong><span class="arch-note">基于已绑定依赖执行各个 function，决定 merge、filter、output 的顺序。</span></div></div><div class="arch-col"><div class="arch-h">边界与约束</div><div class="arch-s">依赖绑定做的是“接线”，不是业务决策</div><div class="arch-box"><strong>输入</strong><span class="arch-note">GraphData、scene、上下文、上游回填结果。</span></div><div class="arch-box"><strong>输出</strong><span class="arch-note">绑定后的 engine 输入、可运行的 vertex 依赖、后续 stage 可读的状态。</span></div><div class="arch-box"><strong>风险</strong><span class="arch-note">依赖缺失、绑定顺序错位、复用对象残留旧状态、上层把业务判断塞进桥接层。</span></div></div></div></div>

## 1. 入口链路
1. 业务请求进入 `video_launch` 或 GRG 的 `diversity_merge` 处理链。
2. 上层先构造或拿到 `GraphData`，再进入 `bind_graph_dependency`。
3. `bind_graph_dependency` 将外层图数据注入 engine 侧，建立 `engine_vertex` 能消费的依赖关系。
4. engine 执行阶段读取这些绑定，驱动后续 merge / response / cleanup。
5. 执行完成后，桥接层不保留业务状态，只保留本次运行所需的最小上下文。

## 2. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
start
:接收 video_launch / diversity_merge 请求;
:准备外层 GraphData;
:调用 bind_graph_dependency;
:将 GraphData 绑定到 engine_vertex;
if (依赖完整?) then (yes)
  :进入 engine 执行;
  :读取 vertex 输入;
  :执行 merge / filter / output;
  :清理本轮绑定状态;
  stop
else (no)
  :记录依赖缺口;
  :返回空执行或降级结果;
  stop
endif
@enduml
```

## 3. 依赖结构信息图
infographic list-grid-badge-card
data
  title bind_graph_dependency 的关键信息
  desc 这个阶段只负责把外层数据桥接成 engine 可读状态，不负责复杂策略判断。
  items
    - label 外层输入
      desc GraphData、scene、上下文、召回结果
      value 1
      icon mdi/inbox-arrow-down
    - label 绑定对象
      desc engine_vertex、node dependency、stage input
      value 2
      icon mdi/link-variant
    - label 结果形态
      desc 可执行的 engine 图与本轮状态快照
      value 3
      icon mdi/play-circle
    - label 典型风险
      desc 旧状态残留、依赖缺口、顺序错配
      value 4
      icon mdi/alert-circle

theme
  palette #2563eb #0f766e #f59e0b

## 4. 关键观察
<div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;margin:14px 0;">
<div style="border:1px solid #dbe4ee;border-radius:12px;padding:14px;background:#fff;">
<strong>边界结论</strong>
<p style="margin:8px 0 0;color:#334155;line-height:1.6">`bind_graph_dependency` 的价值在于把外层调用上下文和执行引擎解耦。它应该稳定、窄而清晰，所有业务分歧都留在上游策略或下游 stage。</p>
</div>
<div style="border:1px solid #dbe4ee;border-radius:12px;padding:14px;background:#fff;">
<strong>实现提醒</strong>
<p style="margin:8px 0 0;color:#334155;line-height:1.6">如果这个层做了太多条件判断，后续的 engine 复用、排查和回收都会被污染，尤其是对象池复用场景。</p>
</div>
</div>

## 5. Pitfalls
<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0;">
<div style="border:1px solid #d9e2ec;border-left:4px solid #2563eb;border-radius:10px;padding:12px;background:#fff;"><strong>状态污染</strong><div style="margin-top:6px;color:#475569;line-height:1.55">复用 `GraphData` 或 vertex 时没有清掉旧依赖，下一轮会读到错误输入。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #0f766e;border-radius:10px;padding:12px;background:#fff;"><strong>绑定顺序错位</strong><div style="margin-top:6px;color:#475569;line-height:1.55">先执行后绑定，或只绑定部分节点，都会让执行图出现隐性空值。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #f59e0b;border-radius:10px;padding:12px;background:#fff;"><strong>职责外溢</strong><div style="margin-top:6px;color:#475569;line-height:1.55">把 scene 选择、业务兜底、实验逻辑塞进桥接层，会破坏可维护性。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #dc2626;border-radius:10px;padding:12px;background:#fff;"><strong>空依赖降级缺失</strong><div style="margin-top:6px;color:#475569;line-height:1.55">缺少完整校验时，执行层可能继续跑到一个无意义的半成品图。</div></div>
</div>

## 6. 调试 checklist
infographic list-column-done-list
data
  title 调试 checklist
  items
    - label 确认 GraphData 在绑定前已完整填充
      done true
    - label 确认 engine_vertex 能读到绑定结果
      done true
    - label 确认复用对象在本轮结束后完成 reset
      done true
    - label 确认缺依赖时走了预期降级路径
      done true
    - label 确认没有把业务分支塞进桥接层
      done true

theme
  palette #2563eb #0f766e #f59e0b

## 7. 结论
`bind_graph_dependency` 是 MultiStreamEngine 的接线层。它的正确目标是稳定传递依赖、隔离执行引擎、并保持每轮运行的状态可回收。本文的业务背景仍需 KU/人工补充，但代码边界已经足够清楚：桥接层只做绑定，不做决策。

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-22T19:01:43.662152
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

`bind_graph_dependency` 这类“外层 GraphData → engine_vertex 依赖绑定”的技术，本质上是把**请求层数据**和**执行引擎图**之间的接线逻辑收敛成一个稳定入口，适合处理图式流水线、规则编排、候选集传递和多 stage 执行。就你提供的业务扫描结果看，`feeda-mv-grc` 已经出现了较多图/依赖相关代码，且有 `graph_engine->get_vertexs_message(graph_name)` 这类依赖遍历逻辑，说明它对这类桥接模式的适配基础更强；而 `feeda-mv-grg` 目前只发现 1 个目标文件，属于更早期或更局部的落点，适合作为试点而不是全量改造。

从规模上看，两边都大量使用 `std::vector`、`std::string`、`std::unordered_map`，说明现有代码本身已经在用“容器承载上下文/依赖”的方式工作。迁移到 `GraphData` / 依赖绑定模式的收益主要不在容器替换，而在于**把手工串联的参数、依赖 map、vertex 输入整理为统一入口**，减少顺序错位、状态残留和职责外溢。综合判断：`grc` 适合优先落地，`grg` 适合先做局部验证。

---

## 2. 代码库详情

### `feeda-mv-grg` 扫描发现

- 目标技术的已发现使用：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 现有 `std` 等价物使用规模：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件

#### 观察
- 当前只发现 **1 个目标文件**，说明这套“图依赖桥接/绑定”能力在 `grg` 中还不是通用基础设施，更像单点落地。
- 但 `std` 容器使用量很大，说明业务代码中本来就存在大量“上下文 + 候选集 + 映射关系”的手工组织逻辑，具备向 `GraphData` 绑定模式迁移的土壤。
- `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 可以作为**试点文件**，验证是否能把规则输入、依赖和执行状态从局部函数参数中抽出来。

### `feeda-mv-grc` 扫描发现

- 目标技术的已发现使用：
  - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/new_adjust/precise_score_init.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - 其余扫描结果共计 10 个文件
- 可作为参考的相关代码：
  - `service/grc_http_service.cpp:62`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
- 现有 `std` 等价物使用规模：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件

#### 观察
- `grc` 已经在多个模块里出现相关使用，覆盖调优、因子生成、场景处理和服务层，说明它更像一个**依赖图/规则图驱动**的业务代码库。
- `service/grc_http_service.cpp` 已经出现 `graph_engine` 和 `depend_map` 的构建逻辑，这与 `bind_graph_dependency` 的“桥接层”思路高度接近，适合作为**抽取公共绑定层**的参考点。
- 由于 `std::unordered_map`、`std::vector` 使用量非常大，如果把这些地方的手工依赖拼装统一收敛，收益会比 `grg` 更明显。

---

## 3. 💡 适用性评估与建议

- **优先在 `feeda-mv-grc/service/grc_http_service.cpp` 抽象公共绑定入口**
  - 这里已经有 `graph_engine->get_vertexs_message(graph_name)` 和 `depend_map` 组装逻辑，最适合抽成类似 `bind_graph_dependency` 的函数。
  - 建议把“请求参数解析 / 图依赖构建 / vertex 输入绑定”拆成三层，避免 HTTP 层直接操纵执行图细节。
  - 适合先做为 `graph` 相关请求的统一桥接入口，再逐步复用到其他 processor。

- **在 `feeda-mv-grc/operator/adjuster/function_queue/youzhi_queue_adjust.cpp` 里收敛队列依赖传递**
  - 这类 adjuster/queue 代码通常会沿着多个函数传递候选数据、场景和控制参数，容易出现参数膨胀。
  - 建议引入 `GraphData` 或轻量依赖上下文对象，把队列排序、过滤依赖和输入状态统一绑定后再执行。
  - 如果当前存在多处手工拼接 `vector/map` 的逻辑，可以优先替换成一个绑定层入口。

- **在 `feeda-mv-grc/processor/new_adjust/precise_score_init.cpp` 和 `processor/multi_factor/subcate_future_factor_gen.cpp` 统一初始化依赖**
  - 这类“初始化 + 因子生成”链路很适合用先绑定、后执行的方式组织。
  - 建议将前置数据准备、依赖完整性校验、执行阶段分离，避免在生成函数里夹杂业务兜底。
  - 对对象复用场景，务必在每轮结束后 reset 绑定状态，防止旧依赖残留。

- **在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 做试点验证**
  - `grg` 目前只看到 1 个目标文件，适合先验证“规则输入 → 依赖绑定 → 执行输出”的闭环是否稳定。
  - 建议先只改这一个文件，把原先散落的规则上下文收口到统一 GraphData 结构。
  - 如果效果稳定，再考虑扩展到同目录下其他 diversity 规则。

- **为现有 `std::vector` / `std::unordered_map` 结构增加适配层，而不是直接大改业务接口**
  - 两个代码库都大量使用这些容器，直接改接口代价高。
  - 更稳妥的方式是：保持容器不变，在其外层包一层 `GraphData`/`dependency binding` 适配器。
  - 这样能先降低接入成本，再逐步把手工传参替换成图依赖注入。

---

## 4. ⚠️ 引入风险与限制

- **状态污染风险**
  - `GraphData`、vertex 或依赖 map 如果被复用而没有清理，下一轮执行可能读到旧依赖。
  - 这个问题在对象池、缓存复用和多请求并发场景里尤其明显。

- **绑定顺序错位风险**
  - 如果执行先于绑定，或者只绑定了部分节点，就会出现隐性空值和半成品图执行。
  - 建议把“完整性校验”放在进入 engine 执行前的硬门槛。

- **职责外溢风险**
  - 桥接层只应该做“接线”和状态传递，不应该承载 scene 选择、实验分流、业务兜底等决策。
  - 一旦把业务分支塞进绑定层，后续排查和复用都会变差。

- **迁移成本与兼容性风险**
  - `grc` 和 `grg` 都有大量基于 `std` 容器的现有代码，若强行全量迁移，改动面会很大。
  - 更适合采用“局部抽取公共绑定层 + 逐步替换调用点”的方式，避免一次性改坏现有链路。

--- 

如果你愿意，我可以继续把这份分析整理成你笔记里可直接粘贴的“**业务代码库适配分析**”标准章节模板，或进一步按 `grg` / `grc` 分别输出落地改造清单。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
