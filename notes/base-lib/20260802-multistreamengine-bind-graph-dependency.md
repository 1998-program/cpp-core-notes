# 2026-08-02 周日代码理解：MultiStreamEngine bind_graph_dependency 绑定外层 GraphDependency

> 日期：2026-08-02  
> 主题来源：2026-08-02 daily-plan 缺失，按历史未覆盖主题 fallback 到 `MultiStreamEngine` 依赖绑定链路  
> 范围：GRG 侧 `MultiStreamEnginePlugin` / `bind_graph_dependency()` 如何把外层 `GraphDependency`、`GraphVertex` 和引擎对象串起来；本文只讨论这条绑定链路，不扩展到全部业务策略。  
> 内网文档：当前环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:18px;margin:16px 0;color:#1f2937"><style scoped>.arch-wrap{display:grid;grid-template-columns:1.1fr 1.4fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.arch-col h3{margin:0 0 8px 0;font-size:14px;color:#102a43}.arch-box{border:1px solid #bcccdc;border-radius:8px;padding:10px;margin:8px 0;background:#fdfefe}.arch-box strong{display:block;margin-bottom:4px;color:#102a43}.arch-mid{display:grid;grid-template-rows:auto auto;gap:12px}.arch-flow{border:1px dashed #bcccdc;border-radius:10px;padding:12px;background:#fbfdff}</style><div class="arch-wrap"><div class="arch-col"><h3>输入层</h3><div class="arch-box"><strong>GraphDependency</strong>外层依赖对象，承载顶点、参数、插件配置</div><div class="arch-box"><strong>GraphVertex</strong>顶点级绑定点，提供图节点的运行上下文</div><div class="arch-box"><strong>MultiStreamEnginePlugin</strong>插件入口，负责把图配置装配成运行对象</div></div><div class="arch-mid"><div class="arch-flow"><div class="arch-box"><strong>绑定主链路</strong><br/>`bind_graph_dependency()` 把外层依赖挂到执行引擎侧，再通过顶点对象把图节点、依赖输入、运行时参数连成一条闭环。</div><div class="arch-box"><strong>运行时闭环</strong><br/>插件构图后，`ExecEngine` 按图内队列、函数和依赖关系执行；依赖缺失时，后续节点只能看到部分输入。</div></div><div class="arch-flow"><div class="arch-box"><strong>结果出口</strong><br/>图执行完成后，输出回写到引擎上下文，再交给上层合并或响应函数。</div></div></div><div class="arch-col"><h3>输出层</h3><div class="arch-box"><strong>GraphData / Runtime State</strong>绑定后的运行态数据，供后续节点直接读取</div><div class="arch-box"><strong>ExecEngine Graph</strong>完整执行图，包含 dependency / vertex / function 三层关系</div><div class="arch-box"><strong>Response Boundary</strong>上层仅消费执行结果，不再关心底层绑定细节</div></div></div></div>

## 1. 核心调用链

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "GRG Service" as S
participant "MultiStreamEnginePlugin" as P
participant "bind_graph_dependency()" as B
participant "GraphDependency" as D
participant "GraphVertex" as V
participant "ExecEngine" as E
participant "Graph Runtime" as R
S -> P : 读取 graph config / scene
P -> B : 传入外层依赖与顶点映射
B -> D : 绑定依赖输入
B -> V : 注入 vertex context
B -> E : 装配执行引擎对象
E -> R : 构建可执行图
R -> E : 运行节点并写回 state
E -> S : 返回结果与中间态
@enduml
```

## 2. 结构信息图

```infographic
infographic list-grid-badge-card
data
  title MultiStreamEngine 绑定链路拆解
  desc 这条链路的核心不是“执行”，而是把执行所需的上下文提前挂好。
  items
    - label GraphDependency
      desc 外层依赖容器，负责把图级输入集中交给引擎。
      value 1
    - label GraphVertex
      desc 节点级上下文，承接图内每个算子的运行状态。
      value 2
    - label bind_graph_dependency()
      desc 把外部依赖、顶点和引擎对象连成一条可执行路径。
      value 3
    - label ExecEngine
      desc 读取绑定结果，完成真正的调度和运行。
      value 4
    - label Response boundary
      desc 只看输出，不再回溯依赖装配过程。
      value 5
```

## 3. 关键理解

`bind_graph_dependency()` 的价值不在于“拷贝字段”，而在于把原本分散在配置、顶点和运行对象之间的信息，统一收束到一个可执行图里。这样做的直接好处是后续节点不需要重新解析外部配置，也不需要反向追溯调用方上下文。

另一个容易忽略的点是：`GraphVertex` 不是纯数据对象，它承担了“图节点身份 + 运行上下文”的双重角色。只要绑定阶段漏掉了这层上下文，后面看起来像是算子没生效，实际上是依赖图没有闭合。

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:14px;margin:16px 0;color:#7c2d12"><div style="font-size:15px;font-weight:800;margin-bottom:6px">常见坑</div><ul style="margin:0;padding-left:18px"><li>`bind_graph_dependency()` 只绑了顶点，没把外层依赖一并挂上，后续节点会出现“能跑但缺输入”的假正常。</li><li>图配置更新后没同步顶点上下文，表现通常是旧参数仍在生效。</li><li>把依赖装配和执行调度混在一起改，容易把问题定位拖成“引擎坏了”，实际只是绑定层缺字段。</li></ul></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 调试 checklist
  items
    - label 检查图配置是否真正进入 plugin
      done true
    - label 检查 GraphDependency 是否完整挂载
      done true
    - label 检查 GraphVertex 是否拿到运行上下文
      done true
    - label 检查 ExecEngine 是否看到绑定后的 state
      done true
    - label 检查输出是否回写到上层边界
      done true
```

## 6. 证据来源

- `notes/base-lib/20260709-multistream-bind-graph-dependency.md`
- `notes/business-lib/20260601-multistreamengine-bind-graph-dependency.md`
- `notes/business/20260529-grg-diversity-soft-rules-in-feeda-mv-grg.md`
- `conf/plugins/exec_engine/multi_stream_diversity.conf`
- `src/plugins/exec_engine/multi_stream_engine_plugin.cpp`
- `src/plugins/exec_engine/multi_stream_engine.cpp`

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-03T19:01:53.454481
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`MultiStreamEngine / bind_graph_dependency()` 相关链路已经在两个业务代码库中出现，但覆盖范围存在明显差异：`feeda-mv-grg` 仅发现 1 个相关文件，主要集中在多样性规则侧；`feeda-mv-grc` 发现 10 个相关文件，分布在多因子、调权、初始化等处理链路中，说明 GRC 侧对图执行 / 依赖绑定模式的接入更广。

- 该技术的核心价值是将外层 `GraphDependency`、`GraphVertex` 与 `ExecEngine` 运行态上下文提前绑定，避免业务算子在执行阶段反复解析配置或临时拼接依赖。结合扫描中 `std::vector`、`std::string`、`std::unordered_map` 的大规模使用情况看，两个代码库中仍有大量“手工维护依赖关系、参数列表、节点输入输出”的代码形态，具备进一步向统一图依赖绑定模型迁移的潜力，尤其适合多阶段、多因子、多规则串联的场景。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标技术相关使用：1 个文件
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`

- 从现有使用规模看：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型代码中可以看到大量候选集、模型输入、预测上下文通过 `std::vector<RidTmpInfoPtr>& candidate_vec` 传递，例如：
  - `model/model.h`
  - `model/paddle_model.h`

- 这类代码通常是序列生成服务中的核心路径，数据从召回 / 粗排 / 精排 / 多样性策略一路传递。如果后续要把部分策略迁移到 `MultiStreamEngine` 图执行模型，重点应关注：
  - 候选集输入是否能映射为 `GraphDependency`
  - 策略节点是否能映射为 `GraphVertex`
  - 多样性规则、模型预测结果、上下文参数是否能在绑定阶段一次性挂载到执行图中

- 当前 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 可以作为 GRG 侧已有接入参考。后续新增多样性规则或软规则时，应优先复用这一类接入方式，避免每个规则单独维护一套外部依赖解析逻辑。

#### feeda-mv-grc：召回汇聚服务

- 已发现目标技术相关使用：10 个文件，扫描结果中列出的代表文件包括：
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`

- 从现有使用规模看：
  - `std::vector`：8520 次，分布在 1290 个文件
  - `std::string`：7267 次，分布在 1247 个文件
  - `std::unordered_map`：2860 次，分布在 646 个文件

- GRC 侧已经出现直接操作图结构的代码，例如：
  - `service/grc_http_service.cpp`

  ```cpp
  std::unordered_map<std::string, std::vector<int>> depend_map;
  auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
  for (int i = 0; i < all_vertex.size(); ++i) {
      for (auto &depend : all_vertex[i].depends) {
  ```

- 这说明 GRC 侧不仅有业务处理链路接入图执行，也存在对图结构、顶点依赖、节点展示 / 调试信息的直接消费。因此，相比 GRG，GRC 更适合优先推进 `bind_graph_dependency()` 相关链路的规范化建设。

- 但也需要注意：`service/grc_http_service.cpp` 中直接使用 `std::unordered_map<std::string, std::vector<int>> depend_map` 构造依赖关系，偏向展示 / 调试视角，不一定等价于运行时绑定链路。迁移时需要区分：
  - 图结构展示依赖
  - 运行时数据依赖
  - `GraphDependency` 外层依赖
  - `GraphVertex` 顶点上下文

---

### 3. 💡 适用性评估与建议

- **建议一：以 GRC 的多因子处理链路作为优先适配对象**
  - 推荐优先关注：
    - `processor/multi_factor/ltr_factor_gen_scene.cpp`
    - `processor/multi_factor/subcate_future_factor_gen.cpp`
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - 这些文件从命名上看属于多因子生成场景，通常存在多个输入源、多个中间特征、多个策略节点串联的问题。
  - 如果当前代码中存在大量手工传递 `std::vector`、`std::unordered_map`、上下文参数的逻辑，可以考虑将公共输入统一收敛到 `GraphDependency`，再通过 `bind_graph_dependency()` 注入到对应 `GraphVertex`。
  - 预期收益：
    - 减少各 factor processor 重复解析配置和上下文
    - 降低漏传参数导致部分因子失效的风险
    - 便于后续通过图结构统一观测因子依赖

- **建议二：将 `service/grc_http_service.cpp` 中的图依赖展示逻辑与运行时绑定模型对齐**
  - 当前代码中已经存在类似依赖图遍历逻辑：

    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    ```

  - 这部分可以作为图调试入口继续保留，但建议补充运行时绑定状态的校验信息，例如：
    - vertex 是否已经绑定外层 `GraphDependency`
    - vertex context 是否完整
    - dependency 中关键输入是否存在
    - ExecEngine 是否能看到绑定后的 state
  - 如果服务中已有图可视化或 HTTP 调试接口，可以在 `service/grc_http_service.cpp` 增加绑定完整性检查，避免只看到“图结构存在”，但实际运行时依赖缺失。

- **建议三：GRG 侧以 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为迁移参考点**
  - `feeda-mv-grg` 当前只发现 1 个目标技术相关文件，说明接入范围还比较小。
  - 建议不要一开始改动模型预测主链路，而是先围绕多样性规则扩展，例如：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 如果后续有类似清晰度、多样性、软规则、内容约束类策略，可以复用该文件中的接入方式，将规则依赖输入统一绑定到图上下文中。
  - 适合迁移的典型场景：
    - 一个规则依赖多个外部参数
    - 规则执行结果会影响后续节点
    - 当前代码依赖全局上下文或临时 map 传参
    - 参数更新后容易出现旧值仍然生效的问题

- **建议四：模型预测链路暂不建议直接大规模迁移，优先抽取依赖边界**
  - GRG 中 `model/model.h`、`model/paddle_model.h` 里大量使用：

    ```cpp
    std::vector<RidTmpInfoPtr>& candidate_vec
    ```

  - 这类接口处于高频核心路径，直接改成图执行模型风险较高。
  - 更稳妥的方式是先不修改模型接口本身，而是在模型调用前后增加一层适配：
    - 调用前：将候选集、用户上下文、场景参数整理为 `GraphDependency`
    - 调用中：保持原有 `predict()` 接口不变
    - 调用后：将模型输出写回图运行态 state
  - 这样可以逐步验证 `bind_graph_dependency()` 对上下文组织的收益，而不破坏已有模型预测接口。

- **建议五：GRC 调权类算子适合作为第二批迁移对象**
  - 推荐关注：
    - `processor/new_adjust/precise_score_init_first_refresh.cpp`
    - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - 调权类逻辑通常依赖用户画像、item 特征、召回源、历史分数、业务开关等多个输入。如果这些输入当前通过多个局部变量、map、vector 分散传递，可以考虑收敛为图依赖。
  - 迁移时建议按以下步骤进行：
    - 先梳理该 adjuster 的所有输入字段
    - 区分配置依赖、请求依赖、上游节点输出依赖
    - 将稳定输入放入 `GraphDependency`
    - 将节点运行态放入 `GraphVertex` context
    - 最后由 `ExecEngine` 统一读取绑定后的 state 执行

---

### 4. ⚠️ 引入风险与限制

- **风险一：只迁移图结构，不迁移运行时依赖，容易出现“图能跑但结果不对”**
  - `bind_graph_dependency()` 的关键不是简单创建 vertex，而是把外层依赖、顶点上下文和执行引擎状态闭合起来。
  - 如果只在 `service/grc_http_service.cpp` 这类地方看到 vertex / depends 信息，就认为运行时已经绑定完整，可能会误判。
  - 建议迁移时必须同时检查：
    - `GraphDependency` 是否完整挂载
    - `GraphVertex` 是否拿到上下文
    - `ExecEngine` 是否能读取绑定后的 state

- **风险二：核心模型接口改造成本较高，不宜一次性替换**
  - 例如 GRG 的：
    - `model/model.h`
    - `model/paddle_model.h`
  - 这些文件中的 `predict(std::vector<RidTmpInfoPtr>& candidate_vec, ...)` 很可能被大量业务调用。
  - 如果直接调整接口，会引发大范围编译和行为变更风险。
  - 建议优先做外围适配层，不直接修改模型基类和主预测接口。

- **风险三：旧配置与新绑定上下文并存时，可能出现参数来源不一致**
  - 在多因子、多规则、多调权链路中，部分参数可能仍从老配置读取，另一部分参数从 `GraphDependency` 读取。
  - 如果没有统一优先级，可能出现：
    - 图配置已经更新，但节点仍使用旧参数
    - HTTP 调试看到的是新图结构，实际执行使用旧上下文
    - 不同 processor 对同一字段解释不一致
  - 建议迁移期间明确字段来源，并增加日志或 debug dump。

- **风险四：`std::vector` / `std::unordered_map` 使用规模很大，不能简单等价替换**
  - 两个代码库中容器使用规模都很高，尤其是 GRC：
    - `std::vector`：8520 次
    - `std::unordered_map`：2860 次
  - 这些容器不一定都代表图依赖关系，很多只是普通业务数据结构。
  - 因此不建议做机械式替换，而应优先识别以下场景：
    - 用 map 维护节点依赖
    - 用 vector 串联多个处理阶段
    - 多个 processor 重复传递同一批上下文
    - 依赖字段缺失会导致后续算子部分失效

---

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
