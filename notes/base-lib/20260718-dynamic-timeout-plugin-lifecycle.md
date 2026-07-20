# 2026-07-18 周六代码理解：DynamicTimeOutPlugin 控制器生命周期与分位超时分配

> 本文基于本地 `feed-general/framework` 代码阅读生成；KU 未能逐篇读取正文，业务背景与线上策略需人工补充。

## 1. 架构全景图：插件初始化、控制器池与分位预算分配

<style>.arch-wrap{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:14px;padding:18px;margin:16px 0;color:#243b53}.arch-title{font-size:22px;font-weight:850;margin-bottom:12px;color:#102a43}.arch-grid{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #d9e2ec;border-radius:12px;padding:12px}.arch-layer h3{margin:0 0 10px;font-size:14px;color:#102a43}.arch-box{border:1px solid #cbd5e1;border-radius:10px;padding:10px 12px;background:#f8fbff;margin-bottom:8px}.arch-box strong{display:block;font-size:13px;margin-bottom:4px}.arch-note{font-size:12px;line-height:1.55;color:#486581}.arch-rail{margin-top:10px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.arch-chip{background:#eef4fb;border:1px solid #d9e2ec;border-radius:999px;padding:6px 10px;font-size:12px;text-align:center;color:#334e68}.arch-tag{font-size:11px;font-weight:700;letter-spacing:0;text-transform:uppercase;color:#627d98;margin-bottom:6px}.arch-main{background:linear-gradient(180deg,#ffffff 0%,#f8fbff 100%)}.arch-side{background:#fbfdff}.arch-focus{border-top:4px solid #3d5a80}.arch-light{border-top:4px solid #94a3b8}.arch-accent{border-top:4px solid #2d6a4f}</style><div class="arch-wrap"><div class="arch-title">DynamicTimeOutPlugin 的控制面</div><div class="arch-grid"><div class="arch-layer arch-side"><div class="arch-tag">配置</div><div class="arch-box arch-light"><strong>dynamic_timeout.conf</strong><div class="arch-note">由 `DynamicTimeOutPlugin::initialize()` 从 `./conf/plugins` 读取，失败则回退为不可用插件状态。</div></div><div class="arch-box"><strong>控制器池</strong><div class="arch-note">`controller_pool.size` 决定可复用 controller 数量，避免每次请求重新构造整条超时状态。</div></div><div class="arch-box"><strong>场景映射</strong><div class="arch-note">按 scene 解析 `stage_timeout_map`，把 RPC 总时限拆给 stage / concurrent 阶段。</div></div></div><div class="arch-layer arch-main"><div class="arch-tag">核心链路</div><div class="arch-box arch-focus"><strong>FullLinkDynamicTimeOutController</strong><div class="arch-note">保存 `_scene`、`_remain_rpc_time`、`_total_rpc_time`，并持有 `_stage_latency`、`_stage_timeout`、`_concurrent_rpc_time`、`_done_num`、`_stage_states` 等执行态。</div></div><div class="arch-box"><strong>FullLinkDynamicTimeOut</strong><div class="arch-note">提供 `get_stage_latency()` 与 stage 描述，作为 controller 的只读输入，避免调度阶段重复扫描原始配置。</div></div><div class="arch-box"><strong>超时分配</strong><div class="arch-note">用分位 latency 和 stage 优先级估算每阶段预算，把尾延迟压力限制在局部阶段，而不是让整条链路共享一个静态 timeout。</div></div><div class="arch-rail"><div class="arch-chip">p0-p4 分位</div><div class="arch-chip">stage concurrency</div><div class="arch-chip">CPU cost 扣减</div><div class="arch-chip">controller pool</div></div></div><div class="arch-layer arch-side"><div class="arch-tag">边界</div><div class="arch-box arch-accent"><strong>请求入口</strong><div class="arch-note">controller 只关心 RPC 剩余预算，不关心上游业务语义；scene 与 stage 信息由插件配置装配。</div></div><div class="arch-box"><strong>可恢复性</strong><div class="arch-note">配置读取失败时返回 0 并打 warning，调用侧需要接受动态超时不可用的降级路径。</div></div><div class="arch-box"><strong>复用约束</strong><div class="arch-note">对象池复用要求 controller 清理内部状态，否则旧 stage 数据会污染下次请求。</div></div></div></div></div>

## 2. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor RPC as rpc
participant "DynamicTimeOutPlugin" as plugin
participant "ObjectPool<Controller>" as pool
participant "FullLinkDynamicTimeOutController" as ctl
participant "FullLinkDynamicTimeOut" as fdtl
participant "Request stage data" as stage
rpc -> plugin : initialize()
plugin -> pool : load conf / init pool
rpc -> pool : get()
pool -> ctl : pooled controller
ctl -> fdtl : set_dynamic_timeout_object()
ctl -> fdtl : get_stage_latency()
rpc -> ctl : set_total_timeout(scene, timeout)
ctl -> ctl : subtract cpu_cost_time
ctl -> stage : split budget by stage priority
stage --> rpc : adjusted timeout per stage
@enduml
```

## 3. 配置/结构信息图

```infographic
sequence-ascending-steps
data
  title Dynamic timeout config flow
  items
    - label 1. Load plugin config
      desc dynamic_timeout.conf from ./conf/plugins
    - label 2. Build controller pool
      desc controller_pool.size controls reuse depth
    - label 3. Parse scene stages
      desc stage_timeout_map and concurrent groups
    - label 4. Assign stage budget
      desc use latency percentiles and cpu cost
    - label 5. Return controller to pool
      desc reset state before next request
```

## 4. Pitfalls 卡片

<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0;"><div style="background:#fff;border:1px solid #d9e2ec;border-top:4px solid #b08968;border-radius:12px;padding:12px;"><div style="font-weight:800;margin-bottom:6px;color:#102a43">配置缺失</div><div style="font-size:13px;line-height:1.55;color:#334e68">`initialize()` 直接读本地 conf，路径错或文件缺失时插件退化，不会替业务层自动补齐。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-top:4px solid #3d5a80;border-radius:12px;padding:12px;"><div style="font-weight:800;margin-bottom:6px;color:#102a43">池化污染</div><div style="font-size:13px;line-height:1.55;color:#334e68">controller 复用后如果未清理 stage 相关 vector，旧请求状态会被带到下一次分配。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-top:4px solid #2d6a4f;border-radius:12px;padding:12px;"><div style="font-weight:800;margin-bottom:6px;color:#102a43">预算误算</div><div style="font-size:13px;line-height:1.55;color:#334e68">总 timeout 先扣 CPU cost 再拆 stage，若上游时钟/统计偏差大，剩余预算会被高估或低估。</div></div></div>

## 5. 调试 Checklist

```infographic
list-column-done-list
data
  title Debug checklist
  items
    - label Confirm plugin config path
      desc ./conf/plugins/dynamic_timeout.conf exists
      done true
    - label Inspect controller pool size
      desc match expected concurrency
      done true
    - label Verify stage latency map
      desc stage list matches target scene
      done true
    - label Check cpu cost subtraction
      desc total timeout minus get_cpu_cost_time()
      done true
    - label Validate reset path
      desc reused controller starts clean
      done true
```

## 6. 证据来源

- `feed-general/framework/src/dynamic_timeout_plugin.cpp:9-22`
- `feed-general/framework/src/dynamic_timeout.cpp:20-32`
- `feed-general/framework/include/dynamic_timeout.h:16-27`
- `feed-general/framework/test/conf/plugins/dynamic_timeout.conf:1-31`
- `feed-general/framework/test/test_dynamic_timeout.cpp:17-19`
- `feed-general/framework/test/test_dynamic_timeout_plugin.cpp:14-23`

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-20T19:12:58.289246
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：DynamicTimeOutPlugin / FullLinkDynamicTimeOutController

### 1. 分析摘要

- 从扫描结果看，`DynamicTimeOutPlugin` 相关能力已经在两个业务代码库中出现，但覆盖面仍然较小：`feeda-mv-grg` 仅发现 1 个文件使用，`feeda-mv-grc` 发现 9 个文件使用。说明动态超时能力已经有业务接入基础，但尚未形成统一的链路级治理范式，更多业务逻辑仍可能依赖静态 timeout、局部兜底或隐式默认值。

- 两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 使用规模都很大，尤其是 `feeda-mv-grc`，`std::vector` 出现 8442 次、`std::string` 出现 7170 次、`std::unordered_map` 出现 2834 次。这说明业务侧存在大量阶段配置、召回结果、依赖图、特征集合、候选集等结构化数据流转场景。对于这些多阶段、多 RPC、多召回路径的业务链路，引入或扩大 `DynamicTimeOutPlugin` 的使用，有较明显的收益空间：可以把全链路 timeout 拆分为 stage 级预算，降低单个慢阶段拖垮整体请求的风险。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标库使用：1 个文件  
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`

- 现有标准库容器使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型代码特征：
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

- 适配判断：
  - `feeda-mv-grg` 的核心场景是序列生成、模型预测、规则调整和候选集处理，这类链路通常存在候选集规模波动、模型推理耗时波动、规则链路串并行混合等问题。
  - 当前仅 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 发现目标库使用，可作为业务侧接入动态超时的参考入口。
  - `model/model.h`、`model/paddle_model.h` 这类模型预测接口虽然没有直接展示 timeout 参数，但从性能治理角度看，是后续纳入动态超时分配的重点区域。

#### feeda-mv-grc：召回汇聚服务

- 已发现目标库使用：9 个文件，扫描结果列出的代表文件包括：
  - `processor/new_adjust/precise_score_init.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`

- 现有标准库容器使用规模：
  - `std::vector`：8442 次，分布在 1279 个文件
  - `std::string`：7170 次，分布在 1234 个文件
  - `std::unordered_map`：2834 次，分布在 639 个文件

- 典型代码特征：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
  - `service/grc_http_service.cpp`
    ```cpp
    static std::vector<std::string> colors{
        "#FFB6C1", "#DC143C", "#DB7093", "#FF1493", "#FF00FF", "#800080",
        ...
    };
    ```
  - `service/grc_http_service.cpp`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    ```

- 适配判断：
  - `feeda-mv-grc` 是召回汇聚服务，天然存在多召回源、多算子、多阶段图执行、特征生成、过滤、调权等复杂链路。
  - 已有 9 个文件发现目标库使用，说明该代码库已经具备一定接入经验。
  - `service/grc_http_service.cpp` 中存在图依赖、HTTP 参数解析、依赖关系构建等逻辑，虽然未在扫描结果中列为目标库使用文件，但其依赖图和子链路控制场景非常适合纳入动态超时治理。

---

### 3. 💡 适用性评估与建议

- **建议一：以已有接入文件为样板，统一动态超时 controller 的获取、初始化和归还方式**
  - 参考文件：
    - `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp`
    - `feeda-mv-grc/processor/new_adjust/precise_score_init.cpp`
    - `feeda-mv-grc/processor/filter/low_agile_goodrate_filter_operator.cc`
  - 建议内容：
    - 这些文件已经发现目标库使用，可作为业务侧接入 `DynamicTimeOutPlugin` 的参考样板。
    - 建议抽象一层业务工具函数，例如 `get_dynamic_timeout_controller(scene, total_timeout)`，统一完成：
      - 从插件或对象池获取 `FullLinkDynamicTimeOutController`
      - 设置 scene
      - 设置总 timeout
      - 扣减 CPU cost
      - 获取 stage 级 timeout
      - 请求结束后归还 controller
    - 这样可以避免每个业务文件自行管理 controller 生命周期，降低对象池污染和状态未清理的风险。

- **建议二：在 `feeda-mv-grc` 的多阶段处理链路中优先扩大接入**
  - 重点文件：
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
    - `processor/multi_factor/subcate_future_factor_gen.cpp`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
  - 适用场景：
    - 多因子生成
    - 多路召回结果汇聚
    - 队列调权
    - 需要串并行混合执行的 processor/operator
  - 建议内容：
    - 这些模块通常属于请求后半段的精排、调权或因子增强阶段，容易受到上游耗时波动影响。
    - 建议按业务阶段拆分 scene，例如：
      - `recall`
      - `multi_factor`
      - `filter`
      - `adjust`
      - `response_build`
    - 在 `dynamic_timeout.conf` 中为这些 stage 配置分位 latency 和优先级，避免某个低优先级增强阶段占满剩余 RPC 时间。
    - 对于可降级的因子生成逻辑，可在 stage timeout 不足时跳过或使用缓存/默认值。

- **建议三：在 `service/grc_http_service.cpp` 的图依赖和子访问控制场景中引入 stage 预算观测**
  - 目标文件：
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 相关代码特征：
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    ```
  - 建议内容：
    - 该文件中存在 graph vertex、depends、sub access 参数解析等逻辑，说明业务请求可能会根据图结构动态决定执行路径。
    - 建议为图执行的关键节点增加 stage 名称，并将每个 vertex 或 operator 映射到动态超时配置中的 stage。
    - 如果当前图执行已经有静态 timeout，建议迁移为：
      - 请求入口保留一个总 timeout
      - graph 层按依赖拓扑拆分 stage timeout
      - 每个下游 RPC 或 operator 使用 controller 分配后的剩余预算
    - 这样可以降低图上某个慢 vertex 导致整体请求超时的概率。

- **建议四：在 `feeda-mv-grg` 的模型预测路径中评估动态超时接入**
  - 目标文件：
    - `feeda-mv-grg/model/model.h`
    - `feeda-mv-grg/model/paddle_model.h`
  - 相关代码特征：
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```
  - 建议内容：
    - 模型预测通常是序列生成链路中的高耗时阶段，且耗时会受候选集数量、模型类型、特征完整度影响。
    - 建议先不要直接修改基础虚接口 `Model::predict()`，以免引发大面积签名变更。
    - 更稳妥的迁移方式是：
      - 在调用 `predict()` 的上层业务 processor/rule 中获取 stage timeout
      - 将 timeout 放入上下文对象或 predict option 中
      - 模型内部按上下文判断是否执行完整预测、轻量预测或直接降级
    - 对 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 中已有目标库使用方式进行复用，作为 `grg` 内部推广模板。

- **建议五：补充动态超时配置与监控，避免只接入代码、不接入治理**
  - 目标配置：
    - `./conf/plugins/dynamic_timeout.conf`
  - 参考框架文件：
    - `feed-general/framework/src/dynamic_timeout_plugin.cpp`
    - `feed-general/framework/src/dynamic_timeout.cpp`
    - `feed-general/framework/include/dynamic_timeout.h`
  - 建议内容：
    - 业务代码接入后，需要同步维护 scene 与 stage 配置。
    - 建议每个业务服务至少配置：
      - scene 名称
      - stage 列表
      - stage 分位 latency
      - stage 优先级
      - concurrent group
      - controller pool size
    - 对 `feeda-mv-grc` 这类高并发召回汇聚服务，应重点调大并压测 `controller_pool.size`，避免池不足导致频繁构造或复用异常。
    - 对 `feeda-mv-grg` 这类模型/规则链路，应重点校准 CPU cost 扣减逻辑，避免模型耗时被重复扣减或未扣减。

---

### 4. ⚠️ 引入风险与限制

- **风险一：controller 对象池复用导致状态污染**
  - `FullLinkDynamicTimeOutController` 内部保存 `_scene`、`_remain_rpc_time`、`_total_rpc_time`、`_stage_latency`、`_stage_timeout`、`_concurrent_rpc_time`、`_done_num`、`_stage_states` 等执行态。
  - 如果 controller 归还对象池前没有完整 reset，旧请求的 stage 信息可能污染下一次请求。
  - 建议在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 和 `feeda-mv-grc` 已接入的 9 个文件中重点检查：
    - 是否每次请求都重新设置 scene
    - 是否每次请求都重新设置 total timeout
    - 异常返回路径是否也会归还 controller
    - controller 归还前是否清理 vector/map 状态

- **风险二：配置缺失或 scene/stage 不匹配会导致动态超时失效**
  - 框架中 `DynamicTimeOutPlugin::initialize()` 直接读取 `./conf/plugins/dynamic_timeout.conf`。
  - 如果业务部署环境缺少该配置，或 scene 名称与代码中传入的不一致，插件可能退化为不可用状态。
  - 建议在两个业务仓库中统一检查：
    - `./conf/plugins/dynamic_timeout.conf` 是否随服务发布
    - 测试环境、预发环境、线上环境配置是否一致
    - 新增 processor/operator 后是否同步补充 stage 配置

- **风险三：总 timeout 与 CPU cost 扣减不准确会造成预算误分配**
  - 当前机制会基于总 RPC timeout 扣减 CPU cost，再拆给后续 stage。
  - 如果上游传入的总 timeout 不准确，或者业务侧 CPU cost 统计口径与框架不一致，可能导致：
    - 下游阶段预算过小，频繁提前降级
    - 下游阶段预算过大，整体请求仍然超时
  - 对 `feeda-mv-grc/processor/multi_factor/session_ltr_dibar_factor_gen.cpp`、`feeda-mv-grc/processor/multi_factor/subcate_future_factor_gen.cpp` 这类耗时波动较大的阶段，建议先以观测模式接入，记录分配结果和真实耗时，再逐步启用强限制。

- **风险四：基础接口直接改造可能引发大范围兼容成本**
  - 例如 `feeda-mv-grg/model/model.h` 中的：
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
  - 如果直接给 `predict()` 增加 timeout 参数，会影响所有派生模型实现和调用方。
  - 建议优先采用上下文透传或 wrapper 包装方式，不直接修改大量虚函数签名。
  - 待核心路径验证收益后，再考虑在模型接口层标准化 timeout option。

---

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
