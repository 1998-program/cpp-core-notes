# 2026-08-07 周五代码理解：GRC `ResponseForGrg` 响应出口契约

> 日期：2026-08-07  
> 主题来源：2026-08-07 daily-plan 缺失，按相邻未覆盖主题 fallback 到 GRC 查询入口与 `ResponseForGrg` 响应出口契约  
> 服务：`feeda-mv-grc`  
> 范围：分析 brpc 查询入口、GraphEngine 执行边界、`ResponseForGrgFunction` 的响应产出与 graph 配置绑定；本文不展开 History Service `gr_state` 存储与业务枚举。  
> 内网文档：当前 cron 环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.grc-arch{display:grid;grid-template-columns:1fr;gap:12px}.grc-layer{border:1px solid #d5dee8;border-radius:10px;background:#fff;padding:12px}.grc-title{font-size:14px;font-weight:800;color:#334155;margin-bottom:8px}.grc-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.grc-box{border:1px solid #cbd5e1;border-left:4px solid #3d5a80;border-radius:8px;background:#f8fafc;padding:10px;min-height:62px}.grc-box strong{display:block;font-size:13px;color:#1f2937;margin-bottom:4px}.grc-box span{font-size:12px;line-height:1.45;color:#475569}.grc-flow{font-size:12px;color:#64748b;margin-top:8px}.grc-warn{border-left-color:#b45309;background:#fff7ed}.grc-ok{border-left-color:#2d6a4f;background:#f4f8f6}@media(max-width:760px){.grc-grid{grid-template-columns:1fr}}</style><div class="grc-arch"><div class="grc-layer"><div class="grc-title">入口层：brpc 服务注册</div><div class="grc-grid"><div class="grc-box"><strong>GenericGRCService</strong><span>`main.cpp` 创建 GRC service 并注册到 brpc server。</span></div><div class="grc-box"><strong>GrcHttpServiceImpl</strong><span>辅助暴露 `/graph_view`，用于观察 graph 结构。</span></div><div class="grc-box grc-ok"><strong>baidu_std_reuse</strong><span>服务协议复用，减少连接层开销。</span></div><div class="grc-box"><strong>ExpManager</strong><span>启动阶段加载 `conf` 下实验参数。</span></div></div></div><div class="grc-layer"><div class="grc-title">执行层：请求上下文与 GraphEngine</div><div class="grc-grid"><div class="grc-box"><strong>SessionContext</strong><span>承载 request、response、sid、日志和中间状态。</span></div><div class="grc-box"><strong>DynamicTimeOutPlugin</strong><span>构造函数从 ApplicationContext 获取，用于查询预算控制。</span></div><div class="grc-box"><strong>GraphEngine</strong><span>`query()` 进入图执行，按 vertex 依赖推进。</span></div><div class="grc-box grc-warn"><strong>Failure Path</strong><span>异常、超时、graph 失败需要落到统一错误响应。</span></div></div></div><div class="grc-layer"><div class="grc-title">响应层：`ResponseForGrg` 出口</div><div class="grc-grid"><div class="grc-box"><strong>ResponseForGrgFunction</strong><span>video_launch 场景下的最终响应组装函数。</span></div><div class="grc-box"><strong>Effect Queue</strong><span>从候选、融合、截断后的结果队列读取。</span></div><div class="grc-box"><strong>Response Extensions</strong><span>补充 ext、策略标记、调试与透传字段。</span></div><div class="grc-box grc-ok"><strong>GRCResponse</strong><span>作为 RPC 返回体交回上游 GRG 或调用方。</span></div></div><div class="grc-flow">关键边界：入口只负责接收请求和调图，`ResponseForGrg` 是把图内候选结果转成 RPC response 的最后契约点。</div></div></div></div>

---

## 1. 入口链路

`main.cpp` 的启动逻辑先初始化插件、全局组件和实验参数，然后创建 `GenericGRCService` 并注册到 brpc server。这里确定了服务的外部身份：请求不是直接调用某个 processor，而是先进入 RPC service，再由 service 组织上下文和 graph 执行。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller as caller
participant "brpc Server" as server
participant "GenericGRCService" as service
participant "SessionContext" as ctx
participant "GraphEngine" as graph
participant "ResponseForGrgFunction" as resp
caller -> server: query(request)
server -> service: dispatch GenericGRCService::query
service -> ctx: build request-scoped context
service -> graph: run configured graph

graph -> resp: trigger ResponseForGrg vertex
resp -> ctx: fill response fields / ext / queues
ctx --> service: finalized GRCResponse
service --> caller: RPC response
@enduml
```

证据：

- `main.cpp:104-118`：创建 `baidu::rpc::Server`，实例化 `GenericGRCService`，注册 service，同时暴露 `/graph_view`。
- `service/grc_service.cpp:51-59`：`GenericGRCService` 构造期从 `ApplicationContext` 获取 `DynamicTimeOutPlugin`。
- `service/grc_service.cpp:152-205`：`GenericGRCService::query()` 是请求入口，负责 RPC controller、request、response 的主流程。
- `service/grc_service.cpp:284-284`：通过 `graph->find_data("ResponseForGrg")` 找响应出口数据，说明 graph 内部把最终响应边界显式命名为 `ResponseForGrg`。

---

## 2. `ResponseForGrg` 的职责边界

`ResponseForGrgFunction` 注册在 `processor/video_launch/response_for_grg.cpp` 中，类名已经暴露了它的定位：不是召回、不是排序、不是存储，而是 video_launch 链路在 GRC 内把图执行结果整理为返回给 GRG 的 response。

```infographic sequence-timeline-simple
data
  title GRC 查询到响应出口的责任切分
  desc 入口、执行、响应三个阶段各自只承担一类职责
  items
    - time 1
      label Service Entry
      desc brpc dispatch 到 GenericGRCService::query，建立请求上下文
      icon mdi/lan-connect
    - time 2
      label Graph Runtime
      desc GraphEngine 根据 vertex.conf 推进依赖和 processor 执行
      icon mdi/graph-outline
    - time 3
      label Candidate Closure
      desc 上游召回、融合、set2set、截断等节点产出可返回队列
      icon mdi/source-branch
    - time 4
      label ResponseForGrg
      desc 汇总队列、ext、策略透传字段，形成 GRCResponse
      icon mdi/package-variant-closed
```

核心判断：

- `ResponseForGrgFunction` 之前的节点负责“算什么候选、怎样融合、怎样截断”。
- `ResponseForGrgFunction` 负责“哪些结果和字段进入 response”。
- 因此排查“上游 GRG 收不到字段、候选数量和预期不一致、ext 透传缺失”时，不能只看召回或融合节点，必须检查这个响应出口是否执行、是否被配置依赖触发、是否命中了截断和字段填充分支。

---

## 3. 配置与代码绑定

`ResponseForGrg` 的风险点在于它既是代码函数，又是 graph data/vertex 命名约定。代码侧注册函数，配置侧通过 graph 依赖把它放在正确位置；任一侧名字漂移都会变成“服务正常返回但内容不符合预期”的问题。

```infographic compare-binary-horizontal-underline-text-vs
data
  title 代码注册 vs Graph 配置
  desc 响应出口需要两边同时对齐
  leftTitle Code Registration
  rightTitle Graph Binding
  items
    - label 注册点
      desc `REGISTER_GRC_FUNCTION(ResponseForGrgFunction)` 让框架可实例化该函数
    - label 数据点
      desc `ResponseForGrg` 作为 graph data 名称被 service 查找
    - label 依赖点
      desc `vertex.conf` 控制哪些前置结果进入响应出口
    - label 排查点
      desc 函数存在不等于链路触发，必须同时看 vertex 依赖
```

证据：

- `processor/video_launch/response_for_grg.cpp:102-102`：定义 `ResponseForGrgFunction`。
- `processor/video_launch/response_for_grg.cpp:506-515`：命中 `set2set_truncate_in_response_for_grg` 时会在响应出口阶段截断 effect queue，并发布截断后的队列。
- `processor/video_launch/response_for_grg.cpp:8790-8830`：文件末尾注册 `ResponseForGrgFunction`。
- `conf/plugins/graph/vertex.conf:2634-2638`：存在 `_user_response`、`_request_response` 等响应相关 graph data；响应出口依赖这些 data 名称组织最终返回。

---

## 4. 调试 Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fafaf8;border:1px solid #ddd6c8;border-radius:12px;padding:16px;margin:16px 0;color:#333"><style>.pit-grid{display:grid;grid-template-columns:1.3fr 1fr;gap:12px}.pit-main{background:#fff;border-left:5px solid #b45309;border-radius:8px;padding:14px}.pit-side{display:grid;gap:10px}.pit-card{background:#fff;border:1px solid #e5dfd5;border-radius:8px;padding:12px}.pit-meta{font-size:11px;font-weight:800;color:#7c6853;text-transform:uppercase;letter-spacing:.04em}.pit-title{font-size:22px;font-weight:850;color:#1f2937;margin:4px 0 8px}.pit-body{font-size:13px;line-height:1.65;color:#4b5563}.pit-card strong{display:block;font-size:13px;color:#1f2937;margin-bottom:4px}@media(max-width:760px){.pit-grid{grid-template-columns:1fr}}</style><div class="pit-grid"><div class="pit-main"><div class="pit-meta">Pitfall</div><div class="pit-title">把响应问题误判成召回问题</div><div class="pit-body">候选召回成功并不代表最终 response 正确。`ResponseForGrgFunction` 仍可能因为 graph 依赖、实验分支、截断逻辑或 ext 填充分支，把上游结果改写成另一个形态。排查线上字段缺失时，应先确认响应出口是否执行以及输出队列大小。</div></div><div class="pit-side"><div class="pit-card"><strong>名字漂移</strong><div class="pit-body">`ResponseForGrgFunction`、`ResponseForGrg` graph data 和配置中的响应 data 名称需要共同检查。</div></div><div class="pit-card"><strong>截断位置</strong><div class="pit-body">`set2set_truncate_in_response_for_grg` 在响应出口阶段生效，问题表现可能像融合结果减少。</div></div><div class="pit-card"><strong>观测入口</strong><div class="pit-body">`/graph_view` 能帮助确认 graph 拓扑，但仍需结合代码分支和实验参数判断实际执行路径。</div></div></div></div></div>

---

## 5. 调试 Checklist

```infographic list-column-done-list
data
  title ResponseForGrg 排查清单
  desc 从入口、图配置、函数执行、响应字段四层验证
  items
    - label 确认服务入口
      desc 查看 brpc 是否注册 GenericGRCService，query 是否收到请求
      done true
      icon mdi/check-network
    - label 确认 graph 数据名
      desc 检查 service 是否查找 `ResponseForGrg`，配置中响应 data 是否一致
      done true
      icon mdi/graph
    - label 确认函数注册
      desc 检查 `REGISTER_GRC_FUNCTION(ResponseForGrgFunction)` 是否存在且加载到插件体系
      done true
      icon mdi/function-variant
    - label 确认截断实验
      desc 检查 `set2set_truncate_in_response_for_grg` 是否命中，比较截断前后队列大小
      done false
      icon mdi/scissors-cutting
    - label 确认 ext 透传
      desc 对照 response 中缺失字段，定位 fill/ext 分支是否执行
      done false
      icon mdi/file-document-check-outline
```

---

## 6. 本次结论

`ResponseForGrg` 是 GRC 里从 graph 世界回到 RPC response 世界的关键出口。它的价值不只是“最后填 response”，而是把候选队列、截断策略、ext 透传和上游 GRG 协议连在一起。后续如果要做字段扩展或排查返回异常，应把 `GenericGRCService::query()`、`ResponseForGrg` graph data、`ResponseForGrgFunction` 注册点和 `vertex.conf` 响应 data 作为一组契约同时检查。

---

## 证据来源

- `main.cpp:104-118`：brpc server 创建、`GenericGRCService` 注册和 `/graph_view` 暴露。
- `service/grc_service.cpp:51-59`：GRC service 构造期获取动态超时插件。
- `service/grc_service.cpp:152-205`：RPC query 入口主流程。
- `service/grc_service.cpp:284-284`：查找 `ResponseForGrg` graph data。
- `processor/video_launch/response_for_grg.cpp:102-102`：`ResponseForGrgFunction` 定义。
- `processor/video_launch/response_for_grg.cpp:506-515`：响应出口阶段的 set2set 截断分支。
- `processor/video_launch/response_for_grg.cpp:8790-8830`：`ResponseForGrgFunction` 注册。
- `conf/plugins/graph/vertex.conf:2634-2638`：响应相关 graph data 配置。

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-09T19:02:05.039132
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，目标技术在两个业务代码库中已经有少量落地，但整体覆盖率仍然偏低：
  - `feeda-mv-grg` 中仅发现 **1 个文件** 已使用目标库。
  - `feeda-mv-grc` 中发现 **10 个文件** 已使用目标库。
- 与此同时，两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 的使用规模非常大：
  - `feeda-mv-grg`：
    - `std::vector`：1969 次 / 356 个文件
    - `std::string`：2443 次 / 425 个文件
    - `std::unordered_map`：734 次 / 205 个文件
  - `feeda-mv-grc`：
    - `std::vector`：8520 次 / 1290 个文件
    - `std::string`：7267 次 / 1247 个文件
    - `std::unordered_map`：2860 次 / 646 个文件

- 这说明当前业务代码仍以标准库容器为主，目标技术尚处于局部试用或分散引入阶段。对于 GRC 这种图执行、候选队列、响应组装链路较重的服务，若目标技术用于优化容器分配、字符串拷贝或哈希表访问，具备较高的迁移潜力，尤其适合优先评估在 `ResponseForGrg` 响应出口、候选队列处理、graph 拓扑展示、规则过滤等高频路径中的收益。

---

### 2. 代码库详情

#### feeda-mv-grg

- 已发现目标库使用：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 该文件可以作为 `feeda-mv-grg` 内部已有实践的参考点，用于确认：
  - 目标库的头文件引入方式
  - 命名空间使用方式
  - 编译依赖是否已经接入
  - 与现有业务对象、候选队列、规则逻辑的兼容方式

- 现有标准库等价物使用规模较大：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型热点方向：
  - `model/model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
    - 模型预测接口直接使用 `std::vector<RidTmpInfoPtr>&` 承载候选集合。
    - 这是跨模块 ABI / API 边界，短期不建议直接替换类型，但可以在内部实现中优化临时容器与中间结构。

  - `model/paddle_model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```
    - `candidate_vec` 是模型预测链路的核心输入。
    - 如果该链路中存在大量候选遍历、临时复制、特征拼接或映射表构造，可以作为后续性能专项分析入口。

#### feeda-mv-grc

- 已发现目标库使用：10 个文件，扫描结果中列出的典型文件包括：
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/new_adjust/precise_score_init.cpp`

- 这些文件可作为 `feeda-mv-grc` 中已有使用经验的参考，尤其适合检查：
  - 是否已经在高频 processor/operator 中验证过稳定性
  - 是否存在统一封装或 typedef
  - 是否有与 graph processor 生命周期、SessionContext、候选队列对象共存的实践

- 现有标准库等价物使用规模显著高于 `feeda-mv-grg`：
  - `std::vector`：8520 次，分布在 1290 个文件
  - `std::string`：7267 次，分布在 1247 个文件
  - `std::unordered_map`：2860 次，分布在 646 个文件

- 典型代码片段：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
    - `/graph_view` 相关逻辑中使用 `std::unordered_map<std::string, std::vector<int>>` 构造 graph 依赖关系。
    - 该链路不是核心请求主路径，但适合作为低风险试点，验证目标容器在 map/vector 组合场景下的兼容性。

  - `service/grc_http_service.cpp`
    ```cpp
    static std::vector<std::string> colors{
        "#FFB6C1", "#DC143C", "#DB7093", "#FF1493", "#FF00FF", "#800080",
        ...
    };
    ```
    - 静态颜色表属于只读小集合，性能敏感度较低。
    - 更适合作为代码风格统一或静态初始化优化对象，而不是第一批性能收益验证对象。

  - `service/grc_http_service.cpp`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    ```
    - HTTP query 参数解析涉及字符串切分、临时 vector、响应字符串拼接。
    - 可作为字符串与小数组优化的非核心链路试点。

---

### 3. 💡 适用性评估与建议

- **建议一：优先在 `feeda-mv-grc` 的非核心但结构典型链路试点，例如 `service/grc_http_service.cpp`**
  - 适用场景：
    - `std::unordered_map<std::string, std::vector<int>> depend_map`
    - `std::vector<std::string> sub_access_off_vec`
    - `std::vector<std::string> sub_access_on_vec`
    - `std::string resp_str`
  - 建议原因：
    - `/graph_view` 属于观测和调试链路，不直接影响线上主查询路径。
    - 该文件同时覆盖 map、vector、string 三类典型标准库对象，适合验证目标技术的 API 兼容性、编译依赖、二进制体积和可读性影响。
  - 建议方式：
    - 先替换局部临时容器，不修改对外函数签名。
    - 保留 `GraphEngine` 返回结构和 brpc 相关接口的原始类型。
    - 对比 `/graph_view` 输出一致性，避免 graph 拓扑展示异常。

- **建议二：在 `ResponseForGrg` 相关响应出口链路中重点关注候选队列和临时扩展字段，但不建议直接修改 RPC 契约类型**
  - 适用文件：
    - `processor/video_launch/response_for_grg.cpp`
  - 适用场景：
    - effect queue 遍历
    - response item 临时列表
    - ext 字段组装
    - 实验分支下的截断结果队列，例如 `set2set_truncate_in_response_for_grg`
  - 建议原因：
    - `ResponseForGrgFunction` 是 GRC 从 graph 内部结果转换到 RPC response 的最后出口。
    - 此处如果存在大量临时容器、字符串拼接、map 查找，优化收益会直接体现在响应组装耗时和尾延迟上。
  - 建议方式：
    - 不替换 protobuf / RPC response 中的字段类型。
    - 不改变 `ResponseForGrg` graph data 名称。
    - 只在函数内部的局部临时结构、去重集合、字段聚合结构上做替换或封装。
    - 替换后必须对比：
      - 返回候选数量
      - ext 字段完整性
      - 截断前后队列大小
      - `GenericGRCService::query()` 最终响应状态

- **建议三：`feeda-mv-grc` 中已有目标库使用的 processor/operator 可作为主路径验证参考**
  - 可参考文件：
    - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
    - `processor/new_adjust/precise_score_init_first_refresh.cpp`
    - `processor/multi_factor/subcate_future_factor_gen.cpp`
    - `processor/new_adjust/precise_score_init.cpp`
  - 建议原因：
    - 这些文件位于 `operator`、`processor` 路径下，通常更接近 GRC 图执行主路径。
    - 如果目标技术已经在这些文件中稳定运行，说明其与现有编译环境、processor 生命周期、业务对象有一定兼容基础。
  - 建议方式：
    - 先抽取这些文件中的使用模式作为团队内参考模板。
    - 统一 include、别名、命名空间、错误处理方式。
    - 后续迁移 `ResponseForGrgFunction` 或其他 processor 时，避免每个文件各自引入不同风格。

- **建议四：`feeda-mv-grg` 中应先围绕规则和模型内部实现迁移，不宜直接改动模型接口**
  - 可参考文件：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
    - `model/model.h`
    - `model/paddle_model.h`
  - 建议原因：
    - `model/model.h` 中的接口使用：
      ```cpp
      virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
      ```
    - 这是虚函数接口，影响所有派生模型类。
    - 直接替换 `std::vector` 会引发较大范围的接口变更和 ABI 风险。
  - 建议方式：
    - 保持 `predict(std::vector<RidTmpInfoPtr>&, uint32_t)` 对外接口不变。
    - 在 `paddle_model.h` 或具体模型实现内部优化临时特征数组、候选索引集合、字符串 key 映射等局部结构。
    - 将 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为已有目标库使用样例，先复用其写法和依赖配置。

- **建议五：对 `std::unordered_map` 使用密集的文件优先做哈希表访问性能评估**
  - 适用范围：
    - `feeda-mv-grc`：`std::unordered_map` 2860 次 / 646 个文件
    - `feeda-mv-grg`：`std::unordered_map` 734 次 / 205 个文件
  - 建议原因：
    - 相比 `std::vector`，哈希表在业务代码中更容易出现性能波动：
      - rehash
      - 小对象频繁分配
      - 字符串 key 拷贝
      - bucket 局部性差
    - GRC 的 graph data、实验参数、特征 key、策略标签、ext 字段都可能存在大量 map 查询。
  - 建议方式：
    - 优先选择局部构建、局部消费、无跨接口暴露的 map。
    - 替换前后对比：
      - 请求耗时 P99
      - CPU cycles
      - malloc/free 次数
      - rehash 次数
      - 峰值内存
    - 避免一次性替换全仓 `std::unordered_map`。

---

### 4. ⚠️ 引入风险与限制

- **风险一：不要直接替换跨模块接口中的标准库类型**
  - 例如：
    - `model/model.h`
    - `model/paddle_model.h`
  - 这些文件中的 `std::vector<RidTmpInfoPtr>&` 是模型预测接口的一部分。
  - 如果直接改成目标容器类型，会影响：
    - 所有派生类签名
    - 调用方编译
    - 虚函数覆盖关系
    - 可能的 ABI 兼容性
  - 建议只在函数内部使用目标技术，不改变 public/protected 接口。

- **风险二：`ResponseForGrg` 是响应契约边界，优化不能改变返回语义**
  - `processor/video_launch/response_for_grg.cpp` 不只是普通计算逻辑，而是最终 RPC response 的组装点。
  - 迁移时需要确保：
    - 候选顺序不变
    - 截断逻辑不变
    - ext 字段不丢失
    - debug 字段不漂移
    - graph data 名称 `ResponseForGrg` 不变
  - 尤其要关注 `set2set_truncate_in_response_for_grg` 分支，避免容器替换后导致截断数量或顺序变化。

- **风险三：目标技术已有使用较少，仍需建立统一规范**
  - `feeda-mv-grg` 仅发现 1 个目标库使用文件。
  - `feeda-mv-grc` 虽有 10 个文件使用，但相对全仓标准库使用规模仍然很小。
  - 如果没有统一规范，容易出现：
    - include 风格不一致
    - 类型别名混乱
    - 部分文件使用目标库，部分文件继续使用 std，增加维护成本
    - Code Review 难以判断替换是否必要
  - 建议先沉淀一份团队级迁移规范，再扩大范围。

- **风险四：性能收益需要压测验证，不能仅凭替换类型判断**
  - `std::vector`、`std::string`、`std::unordered_map` 的使用数量很大，但并不代表全部都有迁移价值。
  - 例如 `service/grc_http_service.cpp` 中的静态颜色表：
    ```cpp
    static std::vector<std::string> colors{...};
    ```
    这类结构不是核心性能瓶颈，迁移收益有限。
  - 真正应该优先关注：
    - 请求主路径
    - 高频 processor
    - 候选队列处理
    - response 组装
    - 大量字符串 key map
    - 临时容器频繁创建销毁场景
  - 每次迁移应配套基准测试或线上灰度指标。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
