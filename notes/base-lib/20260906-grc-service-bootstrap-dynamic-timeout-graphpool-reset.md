# 2026-09-06 周日基础库理解：GRC 服务入口、动态超时注入与 GraphPool reset 生命周期

> 日期：2026-09-06  
> 主题来源：当前可用 daily-plan 仍停留在旧的周计划，没有 2026-09-06 当日计划；按历史候选回退到 `DynamicTimeOutPlugin 动态超时注入与场景映射` + `GraphEngine GraphPool 对象池复用与 reset 生命周期`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/service/grc_service.cpp`、`src/service/grg_service.cpp`，聚焦入口校验、GraphEngine 取图、动态超时写入、图上下文填充与 `reset()` 归还。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">入口层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/service/grc_service.cpp:151-224` / `src/service/grg_service.cpp:35-94`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先做 controller 校验、session 初始化和 graph_name 决策，再进入图执行。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">运行层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`GraphEngine::try_get()` + `set_request_timeout()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">从对象池拿图，写入动态超时控制器，再把请求级上下文塞入 Graph。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">回收层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`graph->run()` 后 `graph->reset()` / `pooled_graph->reset()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">响应复制完成后归还对象池，避免下一次请求继承脏状态。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">RpcController</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">GraphPool</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">Graph Data</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">Response CopyBack</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRC service bootstrap and GraphPool reset boundary
participant "RpcController" as CNTL
participant "GenericGRCService::query" as Q
participant "GraphEngine" as GE
participant "GraphPool" as POOL
participant "DynamicTimeOutPlugin" as DT
participant "Graph" as G
participant "ReusableRPCProtocol::Closure" as C
participant "GRCResponse" as RESP
CNTL -> Q : validate cntl / init session context
Q -> GE : get graph_engine
Q -> POOL : try_get(graph_name)
POOL --> Q : pooled_graph
Q -> DT : get_dt_controller()
Q -> DT : set_request_timeout(scene, timeout)
Q -> G : emit_common_data()
Q -> G : preset ResponseForGrg / ResData
Q -> G : run(end node)
G --> Q : closure result
Q -> RESP : CopyFrom(response_p)
Q -> C : send_response()
Q -> G : reset()
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title GRC 入口链上的 6 个关键点
  desc 这些点决定了图实例是否能正确复用、超时是否能下传，以及响应是否能安全回收
  items
    - label controller 校验
      desc `src/service/grc_service.cpp:156-170` 先检查 `cntl->Failed()` 和 `sctx.init()`
      icon mdi-shield-check
    - label graph_name 选择
      desc `src/service/grc_service.cpp:184-198` 根据 UA 选择 `news_updates_dibar` / `interest_card` / `default`
      icon mdi-sitemap
    - label 图池取图
      desc `src/service/grc_service.cpp:199-203` 从 `GraphEngine::try_get()` 取出 `pooled_graph`
      icon mdi-database-arrow-down
    - label 动态超时
      desc `src/service/grc_service.cpp:233-266` 读取上游超时并写入 `DTController`
      icon mdi-timer-outline
    - label 上下文填充
      desc `src/service/grc_service.cpp:267-291` 注入 logid、product、ua、response 占位数据
      icon mdi-table-arrow-right
    - label 图回收
      desc `src/service/grc_service.cpp:320-345` 复制响应后发送并等待 closure，随后由 `graph->reset()` 归还
      icon mdi-recycle
```

## 3. 代码链路拆解
### 3.1 入口层的职责很窄，但每一步都决定后续是否能继续跑
- `src/service/grc_service.cpp:156-170`：先检查 `cntl` 和 `GRCSessionContext`，失败就立即 `set_error()`，这让错误路径早返回，避免把坏请求带进图执行。
- `src/service/grc_service.cpp:172-179`：`ReqExtractPlugin` 先抽取 `ua`、`product`、`channel_id`，说明图外配置先于图内逻辑准备好。
- `src/service/grc_service.cpp:184-203`：UA 决定 `graph_name`，随后通过 `GraphEngine::try_get(graph_name)` 获取对象池图实例。这里的模式是“按场景复用图模板”，不是每次现建。

### 3.2 动态超时是图执行前的硬边界，不是附属日志
- `src/service/grc_service.cpp:233-266`：`get_dt_controller()` 失败就直接返回，随后读取 `cntl->timeout_ms()`、请求里的 `dynamic_timeout`，最后兜底到 700ms。这个优先级说明超时来源是分层的：上游显式值优先，图默认值兜底。
- `src/service/grg_service.cpp:178-195`：GRG 侧同样写入 `DTController`，并把 `news_updates_dibar` 映射为 `short_micro_video`。这表明场景名是逻辑标签，不一定等于图名。
- `src/service/grc_service.cpp:267-271` 与 `src/service/grg_service.cpp:196-200`：把 `timeout_cntl` 挂进 `MutableFrameworkContext`，说明后续 vertex 运行拿到的是统一的框架上下文，而不是每个节点单独处理控制器。

### 3.3 reset 是对象池契约的一部分
- `src/service/grc_service.cpp:328-345`：`run()` 完成后把图里的 response 拷回 session response，再通过 `ReusableRPCProtocol::Closure::send_response()` 发出。`closure.wait()` 之后才进入日志和收尾，这个顺序确保响应已经出栈。
- `src/service/grc_service.cpp:220-221`：`graph->reset()` 明确出现在 `query()` 尾部，说明图对象会被回收到池中再次使用。
- `src/service/grg_service.cpp:89-91`：GRG 侧也在 `run()` 结束后 `pooled_graph->reset()`，两边实现一致，契约就是“run 后必须清理再归还”。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">图池复用最怕“拿到了图，却把脏状态一起带回下一次请求”</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`try_get()` 成功不代表可直接使用。必须确认 `dynamic_timeout_cntl`、`REQ_INFO`、`response` 占位都已填好，否则图里某个节点会拿到空上下文或旧上下文。</div><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`reset()` 的位置不能前移。响应复制和 `send_response()` 还没结束时就回收图，会把后续异步链路一起打断。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：cntl / session init → graph_name → try_get → dt timeout → run → CopyFrom → send_response → reset</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GRC 入口链排查清单
  desc 适用于图池为空、超时未下传、响应没回、reset 后状态污染和场景映射错误
  items
    - label 检查 controller
      desc 确认 `cntl->Failed()` 和 `sctx.init()` 没有提前返回
      done true
    - label 检查图名
      desc UA 到 `graph_name` 的映射要和图配置一致
      done true
    - label 检查图池
      desc `GraphEngine::try_get()` 必须拿到非空 `pooled_graph`
      done true
    - label 检查超时
      desc `timeout_ms()`、请求字段和默认值的优先级要一致
      done true
    - label 检查上下文
      desc `MutableFrameworkContext::timeout_cntl` 要在 run 前写入
      done true
    - label 检查回收顺序
      desc 先 `CopyFrom` 和 `send_response()`，再 `reset()` 归还图对象
      done true
```

## 6. 证据来源
- `src/service/grc_service.cpp:151-224`
- `src/service/grc_service.cpp:226-345`
- `src/service/grg_service.cpp:35-94`
- `src/service/grg_service.cpp:121-200`
- `src/service/grg_service.cpp:203-219`

## 7. 说明
当前运行环境没有 2026-09-06 的 daily-plan 文件；本笔记基于历史候选与本地代码回退生成，KU 正文未读取，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-07T19:02:06.846983
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 本次技术点的核心是两件事：**动态超时注入** 和 **GraphPool / 可复用对象的 `reset()` 生命周期管理**。它更适合出现在**服务入口层**和**高复用执行引擎**中，用来统一控制请求超时、上下文注入、以及对象池归还后的脏状态清理。
- 从扫描结果看，`feeda-mv-grc` 与 `feeda-mv-grg` 都是以**高频容器操作、字符串处理、Map 聚合**为主的业务代码库，`std::vector` / `std::string` / `std::unordered_map` 使用量很大，说明代码路径中存在大量请求级数据组装和中间态管理场景，具备引入“**统一上下文 + 可复用对象 + 明确 reset 边界**”的迁移潜力。
- 其中，`feeda-mv-grc` 的适配潜力更高：已存在 `service/grc_http_service.cpp` 这类入口型代码，且扫描到了多个 processor/operator 场景文件，适合逐步把“超时下传、场景映射、状态回收”做成统一模式。`feeda-mv-grg` 当前只发现 1 个目标文件，适合先从单点试点，再决定是否扩面。

---

## 2. 代码库详情

### `feeda-mv-grg`：序列生成服务

- 已发现目标库使用文件：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 现状特征：
  - `std::vector` 使用 1969 次，分布在 356 个文件
  - `std::string` 使用 2443 次，分布在 425 个文件
  - `std::unordered_map` 使用 734 次，分布在 205 个文件
- 典型代码参考：
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
- 适配判断：
  - 当前扫描到的直接命中较少，说明该技术在此仓库里**尚未形成广泛落地**。
  - 但该仓库中已经存在大量 `vector/string/map` 组织型逻辑，若涉及在线生成、候选排序、规则过滤等高频路径，仍适合引入**请求级上下文注入**和**清理式复用**模式。

### `feeda-mv-grc`：召回汇聚服务

- 已发现目标库使用文件：
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
- 现状特征：
  - `std::vector` 使用 8520 次，分布在 1290 个文件
  - `std::string` 使用 7267 次，分布在 1247 个文件
  - `std::unordered_map` 使用 2860 次，分布在 646 个文件
- 典型代码参考：
  - `service/grc_http_service.cpp:62`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
  - `service/grc_http_service.cpp:152`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    ```
- 适配判断：
  - 该仓库更接近“**服务入口 + 图/规则执行 + 响应回填**”的运行形态，和动态超时、对象池 reset 的技术点高度相关。
  - `service/grc_http_service.cpp` 可作为入口层改造参考；`processor/*`、`operator/*` 目录则适合作为下游节点的上下文消费端，统一接收超时控制和请求元数据。

---

## 3. 💡 适用性评估与建议

- **优先改造 `feeda-mv-grc/service/grc_http_service.cpp`**
  - 建议把入口流程拆成固定顺序：`controller 校验 -> session 初始化 -> graph_name 决策 -> 取图 -> 动态超时写入 -> 上下文填充 -> run -> CopyFrom -> send -> reset`。
  - 该文件已经有 `graph_engine->get_vertexs_message(graph_name)`、Query 参数解析等入口逻辑，最适合作为动态超时注入的统一接入点。
  - 重点是把超时来源统一到一个策略层，避免业务节点各自读超时，导致优先级不一致。

- **在 `feeda-mv-grc/processor/new_adjust/precise_score_init_first_refresh.cpp` 和 `processor/multi_factor/ltr_factor_gen_scene.cpp` 中补齐请求级上下文**
  - 如果这些文件里存在请求级中间态对象，建议引入类似 `MutableFrameworkContext` 的统一上下文结构，提前写入 `timeout`、`scene`、`logid`、`ua` 等字段。
  - 这样下游 operator 不需要自己重复解析超时与场景，减少分散式逻辑。
  - 对于多阶段处理链，统一上下文还能帮助定位“是谁覆盖了默认超时”。

- **在 `feeda-mv-grc/operator/adjuster/sketchy/duanju_adjuster.cpp` 和 `processor/filter/user_explore_interest_ugc_filter_operator.cc` 中引入 reset 约束**
  - 如果这些模块有缓存对象、临时容器或复用型成员变量，建议明确规定：**一次请求结束后必须显式清空/重置**。
  - 可参考 GraphPool 的约束思想：`run()` 结束后先完成结果回填，再执行 `reset()`，不要把清理动作前移。
  - 这类文件通常是热路径，最容易因为“复用对象残留状态”引发偶发线上问题。

- **在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 做小范围试点**
  - 这个文件是当前扫描到的唯一目标文件，适合先验证“动态上下文 + 清理归还”模式在序列策略里的收益。
  - 如果该规则依赖候选集、阈值、场景标签，建议把这些输入都改为请求级显式传入，而不是隐式依赖全局状态。
  - 试点目标不是立刻引入完整 GraphPool，而是先验证：**统一参数入口、执行前初始化、执行后清理** 这套约束是否能减少异常分支。

- **在 `feeda-mv-grc/service/grc_http_service.cpp` 里作为参考实现沉淀“超时优先级”**
  - 建议明确超时优先级：`上游显式 timeout` > `请求动态字段` > `默认兜底值`。
  - 这个策略可以直接映射到 `grc_http_service.cpp` 的请求解析逻辑里，避免 processor/operator 自己再定义一套默认值。
  - 如果后续还有图执行或规则链调用，也可以复用同样的优先级约束。

---

## 4. ⚠️ 引入风险与限制

- **`reset()` 顺序错误风险很高**
  - 如果响应复制、异步发送还未完成就回收对象，容易出现“下一次请求拿到脏状态”或“回调链被提前打断”。
  - 这类问题通常只在高并发和低概率路径下暴露，排查成本高。

- **动态超时的优先级容易冲突**
  - 一旦 `cntl->timeout_ms()`、请求字段、默认值同时存在，如果没有统一策略，就可能出现同一请求在不同模块里读到不同超时。
  - 建议只保留一个“最终生效值”的写入点，不要让下游重新计算。

- **对象池复用会放大隐藏状态问题**
  - 复用对象的收益是减少分配，但代价是必须对成员状态、临时缓存、错误码、响应占位符做完整清理。
  - 特别是 `unordered_map`、`vector`、字符串缓存、指针型上下文，任何遗漏都可能导致跨请求污染。

- **在 `feeda-mv-grg` 中直接全量迁移可能收益有限**
  - 当前扫描到的相关文件只有 1 个，说明这套技术在该仓库里还不是普遍形态。
  - 更稳妥的方式是先在单个策略文件或单个入口函数试点，确认收益后再评估是否扩面到整个序列生成链路。

---

## 5. 结论

- 如果目标是把“**动态超时注入 + 复用对象 reset 生命周期**”落到业务代码库里，**`feeda-mv-grc` 是主战场**，特别是 `service/grc_http_service.cpp`、`processor/*`、`operator/*` 这一层。
- `feeda-mv-grg` 更适合做**局部试点**，先验证请求上下文显式化和状态清理是否能降低规则/模型链路的复杂度。
- 总体上，这套技术对两库都**有适配价值**，但在 `grc` 中更容易形成体系化收益，在 `grg` 中更适合以低风险方式逐步推进。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
