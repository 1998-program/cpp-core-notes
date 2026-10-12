# 2026-08-31 周一基础库理解：GRG 启动装配、ReusableRPCProtocol 与 exec_engine 配置边界

> 日期：2026-08-31  
> 主题来源：当前没有可用的当日 daily-plan，回退到历史候选中的服务入口 / exec_engine 装配主题；KU 正文未读取，业务背景需人工补充。  
> 范围：`src/main.cpp`、`conf/gflags.conf`、`conf/plugins/exec_engine/multi_stream_diversity.conf`、`conf/plugins/graph/news_updates_dibar/vertex.conf`，聚焦启动链、RPC 协议、实验参数、collector worker 和多流执行器配置。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">进程入口</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/main.cpp:37-130`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先解析 gflags，再加载 comlog、注册 ReusableRPCProtocol、初始化插件、实验参数和 dapper funnel。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">服务装配面</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`GenericGRGService` + `GrgHttpServiceImpl`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">brpc server 同时承接 RPC 和 HTTP graph_view，启动前完成版本、协议和线程数配置。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">执行器层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`multi_stream_diversity.conf`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">loads / rule / function / effect 四条 stream 由 select / pk / effect executor 组合成调度图。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">main 初始化</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">服务注册</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">collector worker</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">exec_engine stream</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRG bootstrap and exec_engine assembly
participant "main.cpp" as MAIN
participant "ReusableRPCProtocol" as RPC
participant "GlobalInitializer" as GI
participant "ExpManager" as EXP
participant "SamplingDataCollectorManager" as DAPPER
participant "brpc::Server" as SERVER
participant "GenericGRGService" as SVC
participant "GrgHttpServiceImpl" as HTTP
participant "multi_stream_diversity.conf" as CONF
MAIN -> MAIN : ParseCommandLineFlags
MAIN -> RPC : register_protocol()
MAIN -> GI : instance().init()
MAIN -> EXP : init(./conf, exp_params.conf)
MAIN -> DAPPER : init(./conf, funnel.conf)
MAIN -> SERVER : set_version(v2.0)
MAIN -> SERVER : AddService(grg_service)
MAIN -> SERVER : AddService(graph_view HTTP)
MAIN -> CONF : read exec_engine streams
MAIN -> SERVER : START_COLLECTOR_WORKER()
MAIN -> SERVER : Start(FLAGS_port, options)
SERVER --> SVC : accept RPC / graph request
SERVER --> HTTP : accept graph_view request
SERVER -> SERVER : RunUntilAskedToQuit()
MAIN -> GI : stop()
MAIN -> SERVER : STOP_COLLECTOR_WORKER
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title GRG 启动链的 6 个关键落点
  desc 这些落点决定了服务是否能稳定启动、是否能正确接入 exec_engine 和埋点采集
  items
    - label gflags / comlog
      desc `src/main.cpp:39-46` 先读命令行和日志，再进入后续初始化
      icon mdi/console
    - label reusable rpc protocol
      desc `src/main.cpp:47-51` 注册 `ReusableRPCProtocol`，失败直接退出
      icon mdi/transit-connection-horizontal
    - label global initializer
      desc `src/main.cpp:53-57` 先完成业务全局初始化，再装配服务
      icon mdi/power-on
    - label exp params
      desc `src/main.cpp:53-57` 通过 `ExpManager` 打印 meta，作为运行态开关来源
      icon mdi/test-tube
    - label dapper funnel
      desc `src/main.cpp:59-60` 与 `START_COLLECTOR_WORKER` 一起形成日志和采样链
      icon mdi/chart-box-outline
    - label stream config
      desc `conf/plugins/exec_engine/multi_stream_diversity.conf:1-64` 把 loads / rule / function / effect 组合成执行图
      icon mdi/arrange-bring-forward
```

## 3. 代码链路拆解
### 3.1 启动链不是“顺手初始化”，而是有严格前后关系
- `src/main.cpp:39-51`：先 `ParseCommandLineFlags`，再注册 `BFILE` appender、加载 `comlog.conf`、注册 `ReusableRPCProtocol`。如果协议或日志失败，后续服务根本不会起。
- `src/main.cpp:53-60`：`GlobalInitializer`、`ExpManager`、`SamplingDataCollectorManager` 依次初始化，说明运行态参数、实验开关和埋点采样都属于服务前置条件。
- `src/main.cpp:73-90`：`brpc::Server` 先设版本、协议和线程数，再注册 `GenericGRGService` 和 `GrgHttpServiceImpl`，最后才启动 collector worker 和 server。

### 3.2 exec_engine 配置决定了多流调度如何收敛
- `conf/plugins/exec_engine/multi_stream_diversity.conf:1-64`：`loads_select`、`rule_select`、`function_select`、`final_select` 和 `effect_pk` 是分开的 stream，`merge_pk` 则把多个 stream 的局部选择合并起来。
- `conf/plugins/exec_engine/multi_stream_diversity.conf:68-261`：`select_executor` 和 `loads_select_executor` 通过 `DiversityRuleOperator`、`RuleSelectOperator` 等规则项把 UA、请求字段、历史特征和兴趣图谱串成执行图。
- `conf/plugins/graph/news_updates_dibar/vertex.conf:1-23`：`NewResponseFunction` 通过 `@decorate: common` 接入图 vertex，说明执行图和响应图在配置层已经对齐。

### 3.3 服务启动后的资源收束也在 main 中闭环
- `src/main.cpp:33-37`（同目录的资源释放逻辑）与 `src/main.cpp:128-136`：服务退出时先打印 `Server quit.`，再做资源释放，并在 collector worker 停止后 `_exit(0)`。
- 这意味着 `main.cpp` 不只是 bootstrap，还是整个进程生命周期的收口点。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">启动成功不等于执行图就绪</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`ReusableRPCProtocol`、`GlobalInitializer`、`ExpManager` 和 `SamplingDataCollectorManager` 都在服务开始前执行。任何一个失败都会让 server 看起来像“没起服务”，但根因其实在前置初始化链。</div><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`multi_stream_diversity.conf` 里 stream / executor / rule 的关系是强耦合的。改了 executor 名称或依赖项，不同步改配置会导致图启动后才失败。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：flags → log → protocol → global init → exp/funnel → service add → stream config</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GRG 启动链排查清单
  desc 适用于服务起不来、graph_view 不通、collector worker 没跑、stream 配置不生效
  items
    - label 检查命令行和日志
      desc 确认 gflags 和 comlog 在最早阶段就已加载
      done true
    - label 检查协议注册
      desc `ReusableRPCProtocol::register_protocol()` 返回值必须为 0
      done true
    - label 检查全局初始化
      desc `GlobalInitializer`、`ExpManager` 和 funnel 初始化不能省略
      done true
    - label 检查服务注册
      desc RPC service 和 HTTP graph_view 都要挂到同一个 server 上
      done true
    - label 检查 collector worker
      desc `START_COLLECTOR_WORKER` / `STOP_COLLECTOR_WORKER` 要成对出现
      done true
    - label 检查 exec_engine 配置
      desc `multi_stream_diversity.conf` 里的 stream / executor / rule 名称要一致
      done true
```

## 6. 证据来源
- `src/main.cpp:37-130`
- `conf/gflags.conf:1-75`
- `conf/plugins/exec_engine/multi_stream_diversity.conf:1-261`
- `conf/plugins/graph/news_updates_dibar/vertex.conf:1-23`

## 7. 说明
当前运行环境未发现 2026-08-31 的 daily-plan 文件，也没有读取 KU 正文；本笔记使用本地代码包与历史候选主题回退生成，KU/业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-01T19:04:52.807728
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析

## 1. 分析摘要

- 从扫描结果看，这项技术在两个业务代码库里都已经有一定落点，但分布很不均衡：`feeda-mv-grg` 仅发现 `1` 个相关文件，`feeda-mv-grc` 已覆盖 `10` 个文件，说明 **grc 更接近可批量迁移/扩散的主战场**，而 `grg` 更适合作为最小化试点库。
- 结合技术笔记里的启动链与 `exec_engine` 配置边界，这项技术更像是**需要和启动装配、规则配置、执行图绑定的能力**，不是单文件替换型改造。业务代码中 `std::vector`、`std::string`、`std::unordered_map` 使用量都很高，说明数据处理链路已经很重；如果这项技术能减少手工装配、统一规则接入或降低配置错配风险，**迁移收益主要会出现在 processor/operator/service 的高频路径**，而不是启动入口本身。

## 2. 代码库详情

### 2.1 `feeda-mv-grg`：覆盖少，适合做参考样板

- 当前仅发现 `1` 个目标库使用文件：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 这说明 `grg` 里该技术的应用还很局部，**更像是某个 diversity/rule 分支上的单点接入**，尚未形成全链路扩散。
- 结合现有代码风格，`grg` 里大量使用：
  - `std::vector`：`1969` 次，`356` 个文件
  - `std::string`：`2443` 次，`425` 个文件
  - `std::unordered_map`：`734` 次，`205` 个文件
- 典型数据结构接口集中在模型层，例如：
  - `model/model.h`
  - `model/paddle_model.h`
- 这意味着如果要迁移到新技术或新调度方式，**模型接口层的数据传递方式**会先受影响，尤其是 `std::vector<RidTmpInfoPtr>` 这种高频入参。

### 2.2 `feeda-mv-grc`：覆盖较广，更适合批量落地

- 当前已发现 `10` 个目标库使用文件，覆盖面明显更广：
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/new_adjust/precise_score_init.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - 以及其他相关 processor/operator 文件
- 这说明 `grc` 里该技术已经进入**多因子处理、首刷初始化、调整器、服务展示**等多个阶段，和技术笔记里提到的“启动链 + 配置边界 + stream 组合”更接近。
- `grc` 的 `std` 使用规模更大：
  - `std::vector`：`8520` 次，`1290` 个文件
  - `std::string`：`7267` 次，`1247` 个文件
  - `std::unordered_map`：`2860` 次，`646` 个文件
- 典型代码集中在：
  - `service/grc_http_service.cpp`
- 这类文件本身就承担了图结构展示、依赖关系组织、HTTP 请求解析等职责，**非常适合优先做技术接入与配置验证**。

## 3. 💡 适用性评估与建议

- **先在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 做试点封装**
  - 这是 `grg` 唯一已发现的相关落地点，建议作为参考样板。
  - 如果技术目标涉及规则接入、stream 配置绑定或执行器适配，可以先在这里抽出统一的适配层，避免后续在别的 rule 文件里重复造轮子。

- **在 `feeda-mv-grc/processor/multi_factor/session_ltr_dibar_factor_gen.cpp` 和 `ltr_factor_gen_scene.cpp` 优先接入**
  - 这两个文件离“多流执行图 / 特征组合 / 场景分发”最近，和技术笔记里的 `multi_stream_diversity.conf` 语义最一致。
  - 如果要引入新的调度方式或规则组合，建议先在这两个文件做小流量验证，再扩展到其他 processor。

- **把 `feeda-mv-grc/processor/new_adjust/precise_score_init.cpp`、`precise_score_init_first_refresh.cpp` 作为配置边界检查点**
  - 这类初始化逻辑最适合接入启动前校验、参数合法性检查和配置降级逻辑。
  - 可以重点对齐 `conf/plugins/exec_engine/multi_stream_diversity.conf` 和 `conf/plugins/graph/news_updates_dibar/vertex.conf`，把“配置可用性”前移到初始化阶段。

- **在 `feeda-mv-grc/service/grc_http_service.cpp` 增加联调/诊断能力**
  - 这个文件已经负责图依赖解析和 HTTP 访问入口，非常适合补充执行图可视化、stream 配置校验、依赖缺失提示。
  - 如果新技术会影响图结构或规则路由，这里可以作为统一观测点，减少“启动成功但图没配对”的排障成本。

- **如果目标是性能优化，优先改高频容器使用点，而不是改入口代码**
  - `grc` 里 `std::vector/std::string/std::unordered_map` 使用密度极高，说明真正的收益点在 processor/operator 路径。
  - 建议优先排查：
    - `reserve()` 是否缺失
    - 是否存在不必要的字符串拷贝
    - `unordered_map` 是否需要提前预留 bucket
    - `vector` 是否可以改为引用传递或只读视图

## 4. ⚠️ 引入风险与限制

- **配置强耦合风险**
  - 技术笔记已经说明 `stream / executor / rule` 之间是强绑定关系。
  - 一旦改了执行器或规则名称，必须同步更新配置文件，否则可能出现“服务已启动，但执行图在运行时失败”。

- **启动链顺序不能打乱**
  - `gflags`、日志、协议注册、`GlobalInitializer`、`ExpManager`、`SamplingDataCollectorManager`、collector worker 的顺序是前置条件。
  - 如果新技术插入点放错位置，排障时容易误判成“服务没起”，实际上是前置初始化失败。

- **RPC/HTTP 兼容性风险**
  - `grg` 和 `grc` 都涉及服务装配，改动协议或请求模型时要注意二进制兼容和线上路由兼容。
  - 尤其是 `service/grc_http_service.cpp` 这种同时承接展示和调试入口的代码，容易因为字段变化导致诊断页面失效。

- **容器替换不一定带来净收益**
  - 两个库都大量使用 `std::vector`、`std::string`、`std::unordered_map`，说明现有代码已经深度依赖标准容器。
  - 如果新技术只是“换一种容器/封装”，但没有减少拷贝或简化执行图，实际收益可能不明显，还会增加迁移成本和学习成本。

如果你愿意，我可以继续把这份分析整理成你笔记里可直接粘贴的“章节版式”，例如补成 `### 业务代码库适配分析` 的正式文稿。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
