# 2026-08-16 周日代码理解：feeda-pcs 业务分支与特征装配

> 日期：2026-08-16  
> 主题来源：2026-06-01 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 `feeda-pcs` 的业务 processor 分支与特征装配链路；KU/业务背景需人工补充。  
> 范围：只分析 `shoubai/processor/diver_feature_sample_processor.cpp` 与 `microvideo/processor/mv_diver_xgb_feature_processor.cpp`，关注业务分支、GCMS/DeepES 依赖、mid_data 绑定与结果装配，不扩展到全部 recall/merge 逻辑。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937">
  <div style="font-size:20px;font-weight:800;margin-bottom:10px;">feeda-pcs 业务特征装配全景</div>
  <div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">Shoubai branch</div>
      <div style="font-size:13px;line-height:1.5;">以 `GCMS` 和多类子类字典为依赖，组装图上的图像/内容相关特征。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">Microvideo branch</div>
      <div style="font-size:13px;line-height:1.5;">依赖 `DeepES`、`parameter_result_cache`、`mid_data_cache` 和预测器输出节点。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">AOPProcessor</div>
      <div style="font-size:13px;line-height:1.5;">各 processor 只做 graph 节点绑定与选择性写入，真正业务数据来自外部组件和字典。</div>
    </div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:#334155;">
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">GCMS</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">DeepES</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">mid_data_cache</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">feature response</span>
  </div>
</div>

## 1. 业务分支的核心判断
`DiverFeatureSampleProcessor` 更像一个带条件的 feature 装配器。它在 `setup()` 中绑定 `author_graph_similarity_news_info`、`author_graph_similarity_video_info` 等 graph dependency，在 `process()` 里则直接用 `return -1` / `return 0` 控制是否继续向下游传播。`EXPERIMENT_HIT_SID(...)` 虽然被注释掉，但注释位置说明这个 processor 以前或计划中就有 sid 门控。证据见 `shoubai/processor/diver_feature_sample_processor.cpp:35-52`、`shoubai/processor/diver_feature_sample_processor.cpp:61-62`。

`MvDiverXgbFeatureProcessor` 的结构更偏向模型特征侧。它引入 `parameter_result_cache`、`feature_info`、`nids_gcf_data`、`recall_gcf_data` 和 `deepes_component`，说明这个分支要做的不只是拼字段，而是把召回、特征和预测器输入一起接到图上。证据见 `microvideo/processor/mv_diver_xgb_feature_processor.cpp:1-13`、`microvideo/processor/mv_diver_xgb_feature_processor.cpp:44-60`。

## 2. 调用链时序图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "DiverFeatureSampleProcessor" as S
participant "MvDiverXgbFeatureProcessor" as M
participant "GCMS / DeepES" as C
participant "mid_data_cache" as Cache
participant "GraphDependency" as G
S -> G : bind author similarity nodes
S -> S : sid / experiment gate
S -> G : emit downstream feature references
M -> G : bind FeatureResponse / FeatureLuopan
M -> C : read GCMS / DeepES derived inputs
M -> Cache : read/write mid_data
M -> G : expose prediction-ready dependencies
@enduml
```

## 3. 业务结构信息图
```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title Processor branch comparison
  desc One branch is selective and sid-gated, the other is model-feature heavy and dependency-rich.
  items
    - label DiverFeatureSampleProcessor
      desc Shoubai side, graph similarity feature wiring
      children
        - label GCMS component
          desc Provides show / content side signals
        - label subcate dicts
          desc video / news / micro dictionaries
        - label gate
          desc return -1 or 0 based on sid or experiment conditions
    - label MvDiverXgbFeatureProcessor
      desc Microvideo side, prediction feature assembly
      children
        - label DeepES component
          desc Model-side external dependency
        - label parameter_result_cache
          desc Reuses parameter evaluation results
        - label mid_data_cache
          desc Carries intermediate graph data
        - label FeatureResponse / FeatureLuopan
          desc Downstream dependencies for final assembly
theme
  palette #0f766e #2563eb #7c3aed
```

## 4. 关键实现细节
Shoubai 分支里最重要的信号是“字典 + GCMS + 选择性门控”的组合。头部同时包含 `gcms_component.h`、`video_subcate_mid_dict.h`、`news_subcate_mid_dict.h`、`micro_subcate_mid_dict.h`，说明它不是单一来源的数据路径，而是把多个内容域的中间字典合并后再决定是否放行。证据见 `shoubai/processor/diver_feature_sample_processor.cpp:9-17`。

Microvideo 分支则把 `feature_response` 和 `feature_luopan` 作为显式 `GraphDependency` 挂出来，同时把 `deepes_component`、`parallel_task`、`mid_data_cache` 拉进来。这个形态说明后续不仅会读取缓存，还会把模型或策略所需的中间态原样传给下游。证据见 `microvideo/processor/mv_diver_xgb_feature_processor.cpp:37-60`。

## 5. Pitfalls
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0;">
  <div style="border:1px solid #cbd5e1;border-left:4px solid #f59e0b;border-radius:10px;background:#fff;padding:12px;">
    <div style="font-weight:800;margin-bottom:6px;">sid 门控可能被误判为无业务</div>
    <div style="font-size:13px;line-height:1.6;">`EXPERIMENT_HIT_SID` 被注释不代表逻辑不存在，真正的门控仍可能在上游图节点或外部配置里生效。</div>
  </div>
  <div style="border:1px solid #cbd5e1;border-left:4px solid #ef4444;border-radius:10px;background:#fff;padding:12px;">
    <div style="font-weight:800;margin-bottom:6px;">依赖很多不等于会全部写入</div>
    <div style="font-size:13px;line-height:1.6;">`FeatureResponse`、`FeatureLuopan`、`mid_data_cache` 这些节点是挂载点，不要把 include 列表直接等同于最终输出字段。</div>
  </div>
</div>

## 6. 调试 Checklist
```infographic
infographic sequence-ascending-steps
data
  title Debug checklist
  items
    - label Check branch-specific include stack
      desc GCMS for shoubai, DeepES for microvideo
      done true
    - label Confirm graph dependencies are bound in setup()
      desc Named dependencies must exist before process()
      done true
    - label Trace sid gate or return -1 early exit
      desc Branch may short-circuit before any feature write
      done true
    - label Inspect mid_data_cache and parameter_result_cache usage
      desc Verify whether values are reused or recomputed
      done true
    - label Keep evidence paths short and relative
      desc Use src/... or conf/... only
      done true
theme
  palette #2563eb #7c3aed #64748b
```

## 7. 证据来源
- `src/shoubai/processor/diver_feature_sample_processor.cpp:9-17`
- `src/shoubai/processor/diver_feature_sample_processor.cpp:35-52`
- `src/shoubai/processor/diver_feature_sample_processor.cpp:61-62`
- `src/microvideo/processor/mv_diver_xgb_feature_processor.cpp:1-13`
- `src/microvideo/processor/mv_diver_xgb_feature_processor.cpp:37-60`

## 8. 结论
`feeda-pcs` 的业务层不是单一路径，而是按内容域拆成多个 processor 分支。Shoubai 分支偏 GCMS 与字典驱动的选择性放行，Microvideo 分支偏 DeepES、cache 和模型输入装配。两者共同点是都把业务复杂度下沉到图依赖和 processor 的边界上，而不是塞进入口函数。

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-28T01:56:13.813589
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 从扫描结果看，这套技术在两个业务库里都**已有一定基础**，但分布不均衡：`feeda-mv-grg` 只有 1 个目标文件命中，而 `feeda-mv-grc` 已发现 10 个文件命中，且 `std::vector`、`std::string`、`std::unordered_map` 的使用规模都很大。说明业务代码本身已经高度依赖 C++ 标准容器，后续迁移或增强时，**技术门槛不高，适配成本主要在接口统一和局部重构**，而不是引入全新基础设施。

- 结合样例代码来看，`grc` 更适合作为优先落地点：`service/grc_http_service.cpp` 已经在做依赖图、请求参数、集合分组等操作，天然适合继续向“标准容器 + 显式依赖组织”的方式收敛；`grg` 则更像局部试点库，适合先在单点规则链路验证效果，再决定是否推广。整体判断是：**迁移潜力中高，收益主要体现在可维护性、接口一致性和局部性能可控性**。

---

## 2. 代码库详情

### `feeda-mv-grg` 扫描发现

- 已发现目标库使用：**1 个文件**
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 现有 std 等价物使用规模：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 典型参考代码：
  - `model/model.h:9`
    ```cpp
    class Model {
    public:
      virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    };
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
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```

- 结论：
  - `grg` 已经广泛使用 STL 容器，说明业务侧对标准容器接受度高。
  - 但目标技术的直接命中只有 1 个文件，说明**还处于局部使用/局部试点阶段**。
  - 更适合围绕 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 做规则链路优化和接口统一。

### `feeda-mv-grc` 扫描发现

- 已发现目标库使用：**10 个文件**
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
- 现有 std 等价物使用规模：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 典型参考代码：
  - `service/grc_http_service.cpp:62`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
  - `service/grc_http_service.cpp:81`
    ```cpp
    std::set<std::pair<int, int>, decltype(comp_pair)> p_set(comp_pair);
    static std::vector<std::string> colors{"#FFB6C1", "#DC143C", "#DB7093", "#FF1493", "#FF00FF", "#800080",
                                           "#4B0082", "#7B68EE", "#0000FF", "#4169E1", "#778899", "#4682B4",
    ```
  - `service/grc_http_service.cpp:152`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on")...
    ```

- 结论：
  - `grc` 的目标技术覆盖面更广，且已进入多个处理器/服务层文件。
  - `service/grc_http_service.cpp` 是很好的参考样例，已经体现出依赖图、集合映射、字符串解析等典型场景。
  - 结合极高的 STL 使用量，继续迁移或强化这类技术的收益更明确，**适合做规模化推广**。

---

## 3. 💡 适用性评估与建议

- **优先在 `feeda-mv-grc/service/grc_http_service.cpp` 继续标准化集合组织方式**
  - 场景：当前已经有 `std::unordered_map<std::string, std::vector<int>> depend_map`、`std::vector<std::string>`、`std::set<std::pair<int,int>>` 等结构。
  - 建议：统一采用 `std::unordered_map` 做依赖索引、`std::vector` 做连续结果收集，减少手写容器封装和中间转换。
  - 价值：该文件是依赖图和请求参数处理入口，改造收益最直接，也最容易验证。

- **在 `feeda-mv-grc/processor/multi_factor/subcate_future_factor_gen.cpp` 做局部重构试点**
  - 场景：多因子生成通常涉及大量 key-value 聚合和特征列表拼装。
  - 建议：如果当前存在自定义映射结构或中间缓存类，可逐步替换成 `std::unordered_map` + `std::vector` 的组合，统一接口风格。
  - 价值：减少多层封装后的拷贝成本，便于后续做特征调试和性能分析。

- **在 `feeda-mv-grc/processor/filter/user_explore_interest_ugc_filter_operator.cc` 和 `processor/filter/low_agile_goodrate_filter_operator.cc` 统一字符串与候选集处理**
  - 场景：过滤器链路通常会频繁处理标签、原因码、策略名和候选列表。
  - 建议：对小粒度字符串集合优先使用 `std::vector<std::string>` 保存顺序，对 key 查询使用 `std::unordered_map<std::string, ...>`，避免自定义容器导致的读写分叉。
  - 价值：过滤器逻辑更清晰，后续增加规则时改动面更小。

- **在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为单点试验**
  - 场景：该文件是 `grg` 中唯一命中的目标文件，适合做最小风险试点。
  - 建议：如果这里存在自定义候选排序、分组或权重缓存结构，可先替换为 STL 容器，验证行为一致性后再推广到其他 rule 文件。
  - 价值：降低一次性迁移风险，便于回归验证。

- **参考现有高频容器使用，避免重复造轮子**
  - 场景：`grg` 中 `model/model.h`、`model/paddle_model.h` 已广泛使用 `std::vector` 参数传递。
  - 建议：后续新增接口优先沿用现有 `std::vector<RidTmpInfoPtr>&` 这类签名，避免引入新的集合抽象。
  - 价值：接口一致性更强，调用链更容易维护。

---

## 4. ⚠️ 引入风险与限制

- **行为一致性风险**
  - 将自定义容器或封装替换为 STL 后，可能改变遍历顺序、哈希分布或拷贝语义。
  - 尤其是 `std::unordered_map`，如果原逻辑依赖稳定顺序，需要额外补 `std::vector` 或 `std::map` 保障。

- **性能并非“默认更快”**
  - `std::unordered_map` 在高频插入/查询场景通常更优，但如果 key 很少、访问极热，哈希开销也可能带来抖动。
  - `grc` 的高频依赖图和服务层逻辑建议先做基准测试，再决定是否全面替换。

- **接口迁移容易扩散**
  - 一旦 `service/grc_http_service.cpp` 或 `model/paddle_model.h` 的函数签名改动，调用链可能大量连锁修改。
  - 建议先做局部适配层，再逐步收敛到统一签名，避免一次性大改。

- **测试覆盖要求较高**
  - 过滤器、规则、因子生成这类模块往往依赖隐式业务规则。
  - 在 `processor/filter/*` 和 `strategy/diversity/rule/*` 里替换容器后，必须补充回归测试，重点覆盖边界空值、重复 key、顺序敏感场景。

如果你愿意，我可以继续把这份内容整理成你笔记里可直接粘贴的 **“业务代码库适配分析”标准章节模板**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
