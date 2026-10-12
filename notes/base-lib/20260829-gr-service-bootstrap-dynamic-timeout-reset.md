# 2026-08-29 周六代码理解：GR 服务入口、动态超时与 Graph reset 生命周期

> 日期：2026-08-29  
> 主题来源：当前没有可用的当日 daily-plan，回退到 `notes/weekly-topic-selection/daily-plan-20260529.json` 中尚可复用的服务入口 / DynamicTimeOut / Graph reset 主题；KU/业务背景需人工补充。  
> 范围：`src/main.cpp`、`src/service/gr_service.cpp` 和 `conf/plugins/dynamic_timeout.conf`，只分析入口装配、请求上下文、动态超时控制器、GraphPool 取图与 reset 顺序。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">进程启动</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/main.cpp:44-83`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">加载 comlog、注册 ReusableRPCProtocol、初始化插件和实验参数，再启动 brpc server。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求执行面</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`GenericGRService::ProcessNsheadRequest()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">从 ApplicationContext 获取 GraphEngine、ReqExtractPlugin、DynamicTimeOutPlugin，按 UA 选择 graph_name。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">生命周期收束</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`graph->run()` → log → `graph->reset()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">响应和日志完成后才 reset；动态超时控制器在请求耗时上报后归还池化生命周期。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">main 初始化</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">GraphPool 取图</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;padding:10px;text-align:center;color:#a16207;">timeout 注入</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">run + reset</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GenericGRService bootstrap and request lifecycle
actor Client
participant "main.cpp" as MAIN
participant "brpc Server" as SERVER
participant "GenericGRService" as SVC
participant "ReqExtractPlugin" as REQ
participant "GraphEngine / GraphPool" as GP
participant "DynamicTimeOutPlugin" as DTO
participant "Graph" as GRAPH
MAIN -> MAIN : load conf / register protocol
MAIN -> SERVER : AddService(GeneralGRService)
MAIN -> SERVER : Start(FLAGS_port, options)
Client -> SVC : ProcessNsheadRequest(nshead)
SVC -> REQ : get_dynamic_struct + set uid/cuid/ua
SVC -> GP : try_get(graph_name)
SVC -> DTO : get_dt_controller()
SVC -> DTO : set_request_timeout(scene_name, cntl.timeout_ms)
SVC -> GRAPH : emit Request / set log info
SVC -> GRAPH : run(end)
GRAPH --> SVC : closure.get ret
SVC -> DTO : report_request_time(cost_ms)
SVC -> SVC : print_log(resp_tmp, err, graph_log)
SVC -> GRAPH : func_each_vertex + reset()
SVC --> Client : Nshead response / error
@enduml
```

## 2. 配置结构信息图
```infographic
infographic list-grid-badge-card
data
  title dynamic_timeout 配置到运行时的 6 个落点
  desc 服务入口把 brpc timeout 映射到场景，再由配置文件拆成阶段预算
  items
    - label controller_pool
      desc `conf/plugins/dynamic_timeout.conf:1-2` 定义控制器池大小，入口每次请求取一个 controller
      icon mdi/database-sync
    - label micro_feed
      desc `conf/plugins/dynamic_timeout.conf:4-37` 覆盖主 feed 场景，包含 ID/UMS/GRC/GRG/fill_meta/HS 阶段
      icon mdi/timer-cog
    - label micro_feed_rec
      desc `conf/plugins/dynamic_timeout.conf:40-68` 是默认召回场景，对 GRC APP 和 fill_meta 给出固定预算
      icon mdi/source-branch
    - label micro_feed_spark
      desc `conf/plugins/dynamic_timeout.conf:70-103` 给 Spark UA 增加 spark_idm 与 spark_hs 分支
      icon mdi/lightning-bolt
    - label scene_name
      desc `src/service/gr_service.cpp:186-193` 根据 UA 从 micro_feed_rec 切到 micro_feed 或 micro_feed_spark
      icon mdi/map-marker-path
    - label request_cost
      desc `src/service/gr_service.cpp:317-318` 在 graph run 后回报真实耗时，供控制器闭环
      icon mdi/chart-timeline-variant
```

## 3. 代码链路拆解
### 3.1 进程启动：协议、插件、实验系统先于服务注册
- `src/main.cpp:49-62`：进程先加载日志配置、注册 `ReusableRPCProtocol`、加载 `gflags.conf`、初始化插件与实验参数。这里失败直接退出，说明插件和实验系统是请求入口的硬前置。
- `src/main.cpp:73-83`：`ServerOptions` 同时打开 `nshead` 和 `baidu_std_reuse`，并把 `GenericGRService` 设置为 nshead 默认服务；`GeneralGRService` 作为 brpc service 显式注册。

### 3.2 请求入口：先构造请求动态结构，再从 GraphPool 取图
- `src/service/gr_service.cpp:55-90`：入口先检查 controller、初始化 `GRSessionContext`，再通过 `ReqExtractPlugin` 把 uid、cuid、baiduid、ua、product、sid、flow_loc 等字段写进动态结构。
- `src/service/gr_service.cpp:96-115`：按 UA 选择 `default`、`author_graph` 或 `interest_card`，再从 `GraphPool::try_get(graph_name)` 取可复用图实例；取图失败直接返回错误。
- `src/service/gr_service.cpp:117-121`：`run()` 后先构造 `GraphLog` 并打印，再遍历 vertex，最后 `graph->reset()`。这说明 reset 不是请求中途清理动作，而是所有日志和响应收束后的归还动作。

### 3.3 动态超时：controller 与 graph mutable context 共享同一请求窗口
- `src/service/gr_service.cpp:173-185`：入口拿 `FrameworkContext` 写 logid，拿 `MutableFrameworkContext` 执行 `clear()`，然后从 `DynamicTimeOutPlugin` 拿池化 controller。
- `src/service/gr_service.cpp:186-203`：默认 scene 是 `micro_feed_rec`，Spark UA 切到 `micro_feed_spark`，部分沉浸式/search UA 切到 `micro_feed`；当 brpc timeout 非正时降级为 1250ms。
- `src/service/gr_service.cpp:222-224`：Graph 起始数据是 `Request`，引用 `GRSessionContext` 里的 rapidjson document；这要求 `sctx`、graph 数据引用和 reset 顺序保持一致。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">不要把 Graph reset 和普通对象 clear 混在一起看</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`MutableFrameworkContext::clear()` 发生在请求运行前，用来清空本次图上下文；`graph->reset()` 发生在响应、日志、vertex 遍历之后，用来归还池化图。如果调换顺序，问题会表现为日志缺字段、closure 后读到空数据，或下一次请求污染。</div><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">动态超时的关键不是配置里某个 timeout 数字，而是入口选中的 `scene_name` 是否符合 UA。Spark、沉浸式和默认召回如果落错场景，会让下游 RPC 预算完全不同。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：graph_name → scene_name → Request emit → closure.get → report → reset</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GR 服务入口生命周期排查清单
  desc 适用于请求超时、空响应、日志缺失、GraphPool 复用污染和 UA 场景不一致
  items
    - label 检查 main 初始化
      desc 确认 comlog、ReusableRPCProtocol、plugin、ExpManager 都在服务启动前成功
      done true
    - label 对齐 graph_name
      desc UA 100/101 走 author_graph，UA 110 走 interest_card，其它默认 default
      done true
    - label 对齐 scene_name
      desc Spark UA、micro_feed UA 与默认 micro_feed_rec 的 timeout 配置不同
      done true
    - label 检查 Request 数据引用
      desc `Request` emit 的是 rapidjson document 引用，生命周期依赖 sctx 和 graph reset 顺序
      done true
    - label 检查 closure 和耗时上报
      desc graph run 后必须 closure.get，再 report_request_time，避免控制器统计缺口
      done true
    - label 检查 reset 位置
      desc print_log 和 func_each_vertex 之后再 reset，避免提前清掉可观测状态
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/main.cpp:44-83`
- `src/service/gr_service.cpp:48-121`
- `src/service/gr_service.cpp:161-224`
- `src/service/gr_service.cpp:291-318`
- `conf/plugins/dynamic_timeout.conf:1-103`

## 7. 说明
当前运行环境未发现 2026-08-29 的 daily-plan 文件，也没有读取 KU 正文；本笔记使用本地代码包与历史候选主题回退生成，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-30T19:01:52.149091
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析

## 1. 分析摘要

- 从扫描结果看，这次技术更适合落在**服务入口的请求生命周期管理**上，而不是直接散落到各个业务算法文件里。`feeda-mv-grc` 已经有明显的服务层入口 `service/grc_http_service.cpp`，且本身就在做 `graph_engine`、依赖图、请求参数解析等工作，和笔记里的“取图 → 运行 → 收束 → reset”模式贴合度较高。
- `feeda-mv-grg` 目前只命中 1 个相关文件 `strategy/diversity/rule/low_clarity_diversity_rule.cpp`，更偏策略/规则计算，说明它对“动态超时、Graph reset、请求上下文”这类入口型能力的直接承接较弱。  
  但两个库都大量使用 `std::vector`、`std::string`、`std::unordered_map`，说明如果要引入新的请求上下文封装、超时控制器或图生命周期管理，**接口改造和容器承载成本不高**，迁移潜力主要集中在服务层与公共上下文层。

---

## 2. 代码库详情

### `feeda-mv-grg`

- 扫描仅发现 1 个目标相关文件：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 现有 `std` 等价物使用规模很大：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 这说明 `grg` 的代码形态以**候选集处理、规则计算、策略分支**为主，已经深度依赖标准容器，适合承载：
  - 请求级临时上下文对象
  - 规则计算中的局部状态隔离
  - 轻量的超时/配置参数透传
- 但从扫描结果看，`grg` 没有明显的服务入口文件或图生命周期管理样例，和笔记里的 `graph->run()` / `graph->reset()` 模式没有直接同构代码可借鉴。

### `feeda-mv-grc`

- 扫描发现 10 个相关文件，覆盖范围明显更广：
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
- 现有 `std` 等价物使用规模也非常高：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 最关键的参考文件是：
  - `service/grc_http_service.cpp`
- 该文件已经体现出和笔记高度接近的结构：
  - 使用 `std::unordered_map<std::string, std::vector<int>>` 管理依赖关系
  - 通过 `graph_engine->get_vertexs_message(graph_name)` 遍历图节点
  - 通过 HTTP 请求参数构造响应字符串
- 这意味着 `grc` 已经具备**图驱动请求处理**的基础形态，适合进一步引入：
  - 请求级 scene/timeout 选择
  - 图对象池化后的 reset 顺序约束
  - 运行耗时上报与控制器闭环

---

## 3. 💡 适用性评估与建议

- **优先在 `feeda-mv-grc/service/grc_http_service.cpp` 落地“请求生命周期三段式”**
  - 建议把请求流程拆成：
    - `获取 graph / controller`
    - `执行 graph`
    - `打印日志 / 上报耗时 / reset`
  - 这和笔记中的 `run → log → reset` 顺序一致，最容易减少复用污染。
  - 适用场景：图引擎服务、召回聚合服务、需要复用 graph 实例的请求入口。

- **在 `feeda-mv-grc/service/grc_http_service.cpp` 增加 scene 级超时映射**
  - 当前文件已经在解析 HTTP 请求和 graph 依赖，适合补一个“请求场景 → timeout 预算”层。
  - 可以按 UA、请求类型或 query 参数划分 scene，避免把所有请求都落到同一个超时值。
  - 如果短期不能接入独立插件，也可以先在该文件里做配置驱动的分支，作为迁移过渡方案。

- **将 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为“局部状态隔离”改造起点**
  - 这个文件属于策略规则层，通常会处理候选集和临时打分状态。
  - 建议避免把请求上下文、超时信息、图对象引用塞进全局静态状态，改为通过局部 context 传递。
  - 适用场景：规则链、排序链、策略分支中需要临时参数但不希望污染长期状态的逻辑。

- **在 `feeda-mv-grg/model/model.h` 和 `model/paddle_model.h` 预留上下文透传接口**
  - 目前 `predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)` 这类签名偏轻量，适合作为兼容层。
  - 如果后续要把超时、trace id、场景名带进模型推理，建议新增 `Context` 或 `RequestMeta` 参数，而不是直接扩散大量基础类型参数。
  - 这样可以减少对现有 `std::vector`/`std::string` 调用链的破坏。

- **在 `feeda-mv-grc/service/grc_http_service.cpp` 先做“reset 顺序保护”再做功能扩展**
  - 如果后续要接入池化 graph 或可复用上下文，先明确：
    - 响应构造完成后再 reset
    - 日志打印完成后再 reset
    - 任何 closure / 结果回收都在 reset 之前完成
  - 这是最容易出错但最值得先规范的部分。

---

## 4. ⚠️ 引入风险与限制

- **reset 顺序错位会导致复用污染**
  - 如果在 `graph->run()` 结束前过早 `reset()`，可能出现日志字段缺失、closure 读取空数据、下一次请求继承脏状态等问题。
  - 这类问题在 `service/grc_http_service.cpp` 这种图驱动入口里尤其危险。

- **scene / timeout 映射一旦配置漂移，影响会比代码错误更隐蔽**
  - 动态超时的核心不是某个固定数字，而是“请求被分到哪个 scene”。
  - 如果 `grc` 或 `grg` 后续按 UA、业务线、请求类型拆 scene，但映射规则没有统一，很容易出现同一类请求预算不一致。

- **`grg` 的改造更偏接口设计，不适合直接硬塞入口层机制**
  - `feeda-mv-grg` 目前主要是策略与模型层文件，扫描到的相关文件很少。
  - 如果强行引入服务入口式的控制器或图池机制，可能会造成职责混乱，建议通过公共上下文对象承接，而不是改造所有规则文件。

- **请求上下文引用生命周期要特别小心**
  - 笔记里强调了 `Request` / rapidjson document 引用与 `graph reset` 的顺序约束。
  - 在 `grc_http_service.cpp` 或相关 processor/operator 中，如果把引用保存到异步任务、缓存或跨阶段结构里，极易引发悬空引用或跨请求串数据。

---

如果你希望，我可以继续把这份分析整理成你笔记里可直接粘贴的 **“业务代码库适配分析”** 小节模板版，保持和原文风格一致。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
