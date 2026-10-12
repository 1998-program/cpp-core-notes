# 2026-09-10 基础库理解：ApplicationContext 组件注册与依赖获取生命周期

> 日期：2026-09-10  
> 主题选择：未发现当日有效 daily-plan；仓库中的旧计划日期较早，且 GraphPool、MultiStreamEngine、PipelineGraphFunction、Expression 等邻近主题已有覆盖。本次按“邻近未覆盖主题”回退到 `ApplicationContext` 组件注册、初始化、wireup 与运行时依赖获取。KU 正文未读取，企业业务上下文需人工补充；以下结论以代码证据为准。  
> 范围：GRC/GRG 服务入口、Babylon 组件工厂、插件 `initialize`/`wireup`、GraphEngine 与 predictor 依赖获取。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">启动层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">main.cpp → protocol → GlobalInitializer</div><div style="margin-top:8px;font-size:12px;color:#52606d;">进程先注册可复用 RPC 协议，再初始化全局组件、实验管理器和日志漏斗。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">组件容器</div><div style="margin-top:8px;font-size:14px;color:#102a43;">ApplicationContext + factory registration</div><div style="margin-top:8px;font-size:12px;color:#52606d;">普通注册提供默认构造路径，custom registration 用配置文件或 channel 名创建变体。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">服务运行层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">Service ctor/init → get&lt;T&gt; → graph query</div><div style="margin-top:8px;font-size:12px;color:#52606d;">服务构造阶段获取稳定依赖，函数 init 阶段按配置获取命名 predictor，查询阶段获取 graph_engine。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 54px 1fr 54px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">注册名 / 类型</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">初始化与 wireup</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">运行时依赖读取</div></div></div>

## 1. 核心调用链

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title ApplicationContext：从进程启动到 GraphEngine 依赖
participant "main.cpp" as MAIN
participant "GlobalInitializer" as INIT
participant "Component factory" as FACTORY
participant "ApplicationContext" as CTX
participant "GRC/GRG service" as SVC
participant "GraphEngine" as ENGINE
MAIN -> MAIN : register_protocol()
MAIN -> INIT : instance().init()
INIT -> FACTORY : create registered components
FACTORY -> CTX : store by type/name
MAIN -> MAIN : ExpManager::init()
SVC -> CTX : get<DynamicTimeOutPlugin>()
SVC -> CTX : get<ReqExtractPlugin>()
SVC -> CTX : get<GraphEngine>("graph_engine")
CTX --> SVC : dependency pointer
SVC -> ENGINE : try_get(graph_name)
SVC -> ENGINE : run graph
SVC -> ENGINE : reset pooled graph
note over FACTORY,CTX
普通组件由 BABYLON_REGISTER_COMPONENT 注册；
带场景的变体由 custom factory 按配置构造。
end note
@enduml
```

## 2. 生命周期信息图

```infographic
infographic sequence-snake-steps-simple
data
  title 组件从注册到消费的五个阶段
  desc 类型注册和命名注册共同构成配置驱动的依赖容器
  items
    - label 编译期注册
      desc BABYLON_REGISTER_COMPONENT 或 WITH_NAME 将类型接入 factory
      icon mdi:factory
    - label 进程初始化
      desc GlobalInitializer 创建并初始化组件，失败通过返回值阻断启动
      icon mdi:power
    - label 配置实例化
      desc custom factory 为 cache_q、dr_data 或 channel 变体选择配置文件
      icon mdi:file-cog-outline
    - label 容器取依赖
      desc ApplicationContext::get<T>() 按类型或名称返回已注册对象
      icon mdi:database-search
    - label 服务运行
      desc RPC 查询选择 graph，调用 GraphEngine，并在请求结束 reset 对象
      icon mdi:run-fast
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键契约

### 3.1 启动顺序决定组件可见性

GRC `main.cpp:73-100` 先注册 `ReusableRPCProtocol`，再执行 `GlobalInitializer::instance().init()`、`ExpManager::init()` 和 log funnel 初始化，最后创建 brpc service 并启动 server。GRG `main.cpp:39-59` 采用同样的骨架。因而服务构造函数中的 `ApplicationContext::get<T>()` 隐含前提是全局初始化已经成功。

### 3.2 普通注册与 custom 注册表达不同实例策略

`plugin/predictor.cpp:22-39,68-69` 展示普通组件注册：`PredictorPlugin::initialize()` 读取默认插件配置，然后 `BABYLON_REGISTER_COMPONENT(PredictorPlugin)` 接入容器。`plugin/dalton_client.cpp:102-126` 则额外注册 `CacheQDaltonClientPlugin` 和 `DaltonDrDataClientPlugin`，factory lambda 使用不同配置文件创建对象并在返回前调用 `initialize()`。

这两个注册入口不能简单互换：普通注册适用于单一默认实例，custom 注册将“实例命名”和“配置选择”绑定到创建函数。新增场景时应先确认消费方使用的是 `get<T>()` 还是 `get<T>(name)`。

### 3.3 wireup 是依赖连接边界，不等于 initialize

`plugin/dalton_client.h:19-41` 将 `initialize()`、`wireup(ApplicationContext&)` 和数据读写接口分开；当前 `dalton_client.cpp:16-47` 中 `initialize()` 负责创建并校验 SNDB client，而 `wireup` 返回 0。其他插件仍实现非空 wireup，例如 `plugin/feature_service.cpp`、`plugin/router.cpp` 等。评审时要分别验证“自身资源已初始化”和“外部组件已连接”，不能因为 initialize 成功就推断依赖链完整。

### 3.4 服务构造阶段与请求阶段的 get 位置不同

GRC `service/grc_service.cpp:51-61` 在构造函数中获取 `DynamicTimeOutPlugin` 与 `ReqExtractPlugin`，而 `:178-200` 在每次请求中按固定名称获取 `GraphEngine` 并按 UA 选择 graph。GRG `service/grg_service.cpp:30-33,48-65` 也在构造阶段获取超时插件、请求阶段获取 graph engine 和 graph pool 对象。

构造阶段的依赖缺失通常是启动配置或初始化顺序问题；请求阶段的 graph 为空则是 graph 名称、配置加载或池容量问题，排查路径不同。

### 3.5 配置命名是运行时 ABI

`processor/set2set_predict_function.cpp:26-47` 从 config 读取六个 channel 名，再用 `ApplicationContext::get<Set2setPredictorPlugin>(_channel_name_...)` 获取六个 predictor。任意一处配置名、注册名或类型不一致都会让 init 失败。该模式说明配置文件中的字符串不是普通参数，而是组件容器的运行时接口。

### 3.6 依赖获取之后仍需校验生命周期

GRC `service/grc_service.cpp:200-221` 获取 pooled graph 后检查空指针，请求执行结束调用 `graph->reset()`；GRG `service/grg_service.cpp:64-91` 同样检查 pooled graph 与动态超时 controller，并在 run 后 reset graph。即使 `get<T>()` 本身成功，也不能跳过 pooled object、controller 和请求级数据的有效性检查。

## 4. 配置与注册信息图

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title 两类组件注册的边界
  desc 选择注册方式时同时看实例数量、命名方式和初始化责任
  items
    - label 普通注册
      desc 类型固定；BABYLON_REGISTER_COMPONENT；initialize 读取默认配置；消费方通常按类型获取
      icon mdi:shape-outline
    - label custom 注册
      desc 实例命名；WITH_NAME + factory lambda；每个变体选择独立配置；消费方按类型和名称获取
      icon mdi:source-branch
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#F5F7FA;border-radius:12px;padding:20px;margin:18px 0;border-left:8px solid #3D5A80;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:800;">Pitfalls / Component Lifecycle</div><h3 style="font-size:24px;line-height:1.2;margin:8px 0 12px;">ApplicationContext 解决的是依赖发现，不会替调用方完成配置、类型和生命周期校验。</h3><div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;"><div><ul style="font-size:14px;line-height:1.65;margin:0 0 0 18px;"><li><b>初始化顺序：</b>服务构造前必须确认 GlobalInitializer 已成功，否则 get&lt;T&gt; 的失败表现可能只是空指针。</li><li><b>名字漂移：</b>配置中的 channel 名、custom 注册名和 get 的字符串必须三处一致。</li><li><b>initialize / wireup 混淆：</b>本地 client 初始化成功，不代表外部组件依赖已经连接。</li><li><b>请求级空对象：</b>GraphEngine、pooled graph、DT controller 都要在消费前检查。</li><li><b>复用对象残留：</b>run 后 reset 是请求边界；提前 reset 或遗漏 reset 都会污染后续请求。</li><li><b>非拥有服务：</b>brpc `SERVER_DOESNT_OWN_SERVICE` 表示 server 不负责 service 对象所有权，退出顺序需由进程管理。</li></ul></div><div style="border-left:3px solid #F59E0B;padding-left:12px;color:#374151;font-size:14px;line-height:1.65;">排查顺序：启动日志 → 注册名 → get 位置 → 空对象检查 → reset 边界 → 线上耗时。</div></div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title ApplicationContext 调试 checklist
  desc 从启动、注册、获取和请求复用四层验证组件链路
  items
    - label 确认入口顺序
      desc protocol、GlobalInitializer、ExpManager 和 server 启动顺序符合预期
      done true
    - label 核对注册形态
      desc 确认消费方需要普通类型注册还是命名 custom 实例
      done true
    - label 核对配置名
      desc 对比 config channel、注册名和 ApplicationContext::get 的字符串
      done true
    - label 验证 initialize
      desc 检查插件配置路径、底层 client 初始化返回值和异常日志
      done true
    - label 验证 wireup
      desc 对每个外部依赖确认 wireup 是否真正完成连接，而非只返回成功
      done true
    - label 检查请求对象
      desc graph、pooled object、动态超时 controller 获取后再消费
      done true
    - label 检查 reset
      desc 请求完成后清理 graph 和请求级状态，确认 closure 生命周期不被提前破坏
      done true
    - label 做双服务回放
      desc 分别覆盖 GRC graph_name 分支与 GRG short_micro_video/news_updates_dibar 分支
      done true
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/main.cpp:73-130`：GRC 启动顺序、协议注册、全局初始化与服务挂载。
- `src/main.cpp:39-89`：GRG 启动顺序与服务挂载。
- `src/plugin/predictor.cpp:22-39,68-69`：普通组件初始化与注册。
- `src/plugin/dalton_client.h:19-41`：initialize、wireup 与 client 成员契约。
- `src/plugin/dalton_client.cpp:16-47,102-126`：底层 client 初始化与 custom factory 注册。
- `src/service/grc_service.cpp:51-61,178-221`：构造阶段依赖获取、命名 graph 获取与 reset。
- `src/service/grg_service.cpp:30-91`：GRG 依赖获取、graph pool 与 reset。
- `src/processor/set2set_predict_function.cpp:26-47`：按配置 channel 获取 predictor 实例。
- `baidu/feed-general/framework/src/graph_function.cpp:7-48`：框架函数 setup、operator 与 hook 生命周期。
