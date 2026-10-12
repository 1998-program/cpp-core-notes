# MV-GRC RPC 入口与 GraphEngine 边界

> 运行说明：2026-08-23 未发现 daily-plan 文件，按附近未覆盖主题 fallback 生成。当前 cron 环境未验证 KU 可用性，KU/业务背景需人工补充；本文结论基于 `feeda-mv-grc` 代码与配置证据。

<div class="arch-wrapper" style="display:flex;gap:12px;align-items:stretch;background:#f8fafc;border:1px solid #d8e2ef;border-radius:8px;padding:14px;color:#1f2937;font-family:Inter,Arial,sans-serif;">
  <div class="arch-main" style="flex:1;min-width:0;">
    <div style="font-size:20px;font-weight:800;margin:0 0 10px 0;">入口到图引擎的分层边界</div>
    <div class="arch-layer application" style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;margin-bottom:10px;">
      <div style="font-weight:700;margin-bottom:6px;">RPC / HTTP Entry</div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;">
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`src/main.cpp:73-130`<br/>注册插件、协议、服务并启动常驻进程</div>
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`src/service/grc_service.cpp:88-140`<br/>请求公共字段装配到 `Graph`</div>
      </div>
    </div>
    <div class="arch-layer ai" style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;margin-bottom:10px;">
      <div style="font-weight:700;margin-bottom:6px;">Graph Runtime</div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;">
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`GraphEngine` 从 `ApplicationContext` 获取</div>
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`graph_engine->try_get(graph_name)` 获取可复用图实例</div>
      </div>
    </div>
    <div class="arch-layer data" style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;">
      <div style="font-weight:700;margin-bottom:6px;">Graph Introspection</div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;">
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`src/service/grc_http_service.cpp:34-70`<br/>HTTP `/graph_view` 输出 vertex 列表</div>
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`conf/plugins/graph/vertex.conf:1-38`<br/>顶点类型与 emit 数据槽定义</div>
      </div>
    </div>
  </div>
  <div class="arch-sidebar" style="width:260px;flex:0 0 260px;">
    <div style="background:#0f172a;color:#e2e8f0;border-radius:8px;padding:10px;margin-bottom:10px;">
      <div style="font-weight:800;margin-bottom:6px;">边界结论</div>
      <div style="font-size:13px;line-height:1.55;">`main.cpp` 负责进程和服务挂载，`grc_service.cpp` 负责把请求状态投喂给 `Graph`，`grc_http_service.cpp` 只做图结构观测，不参与业务计算。</div>
    </div>
    <div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;">
      <div style="font-weight:800;margin-bottom:6px;">配置锚点</div>
      <div style="font-size:13px;line-height:1.6;">`conf/plugins/scenes.conf:1-27`<br/>`conf/plugins/graph/video_launch/grg_response.conf:1-40`<br/>`src/service/grc_service.h:16-25`</div>
    </div>
  </div>
</div>

## 核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "main.cpp" as Main
participant "GenericGRCService" as Grc
participant "GraphEngine" as Engine
participant "Graph" as Graph
participant "GrcHttpServiceImpl" as Http
Client -> Main : 启动进程
Main -> Grc : AddService / register_protocol / init_plugin
Client -> Grc : RPC 请求
Grc -> Graph : emit_common_data()
Grc -> Engine : get graph_engine
Engine -> Graph : try_get(graph_name)
Client -> Http : GET /graph_view
Http -> Engine : get<GraphEngine>("graph_engine")
Http -> Graph : get_vertexs_message(graph_name)
@enduml
```

```infographic
sequence-timeline-simple
data
  title MV-GRC 入口与观测链路
  desc 从进程启动到 GraphEngine 图实例复用，再到 HTTP 图查看接口
  items
    - time 1
      label main.cpp
      desc 注册协议、插件和服务，最后 RunUntilAskedToQuit()
    - time 2
      label GenericGRCService
      desc 把 request、uid、cuid、product、graph_begin_us 等写入 Graph
    - time 3
      label GraphEngine
      desc 通过 ApplicationContext 获取并按 graph_name 复用图实例
    - time 4
      label GrcHttpServiceImpl
      desc 读取 vertex/graph 元信息并序列化为 JSON
theme
  palette #3d5a80 #2d6a4f #64748b
```

## 关键实现

`src/main.cpp:73-130` 做了四件事：加载日志、注册 reusable protocol、初始化插件、注册 `GenericGRCService` 与 `GrcHttpServiceImpl`，最后进入 `RunUntilAskedToQuit()`。这说明它是一个长驻 RPC/HTTP 混合服务，不是一次性任务入口。

`src/service/grc_service.cpp:88-140` 负责把请求上下文中的公共字段装配到 `Graph`。这里最重要的是 `Request`、`log_id`、`uid`、`cuid`、`tabfrom`、`product`、`graph_begin_us` 等数据都以 graph data 的方式透传，后续 processor 不再直接依赖 RPC 对象。

`src/service/grc_service.cpp:178-200` 处通过 `ApplicationContext::instance().get<GraphEngine>("graph_engine")` 获取引擎，并用 `try_get(graph_name)` 取可复用图实例。这个模式说明图引擎是容器托管对象，服务层只负责选择图并传参。

`src/service/grc_http_service.cpp:34-70` 提供 `graph_view`，解析请求后查询 `GraphEngine` 的 vertex 消息并输出 JSON。它是诊断接口，不介入召回/排序，只暴露运行时结构。

`conf/plugins/graph/vertex.conf:1-38` 和 `conf/plugins/scenes.conf:1-27` 说明图的 emit 数据槽、scene/strategy 分派条件是配置驱动的；`conf/plugins/graph/video_launch/grg_response.conf:1-40` 则把 `ResponseForGrgFunction` 挂到具体 vertex 上。

<div style="border:1px solid #dbe3ef;border-radius:8px;padding:12px;background:#fff;">
  <div style="font-weight:800;margin-bottom:6px;">Pitfalls</div>
  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;font-size:13px;line-height:1.55;">
    <div style="border-left:4px solid #94a3b8;padding-left:10px;">不要把 `graph_view` 当业务链路入口，它只读 `GraphEngine` 元数据。</div>
    <div style="border-left:4px solid #94a3b8;padding-left:10px;">不要把 `Request` 当 protobuf 业务响应对象，服务层只负责 emit 到 graph data。</div>
    <div style="border-left:4px solid #94a3b8;padding-left:10px;">`try_get(graph_name)` 返回的是图实例，不是 processor 列表。</div>
    <div style="border-left:4px solid #94a3b8;padding-left:10px;">`scenes.conf` 的条件才决定 scene，代码里不要硬编码业务分流。</div>
  </div>
</div>

```infographic
list-grid-badge-card
data
  title 调试 checklist
  desc 优先验证入口、图引擎和配置三件事
  items
    - label main.cpp 是否注册服务
      desc 检查 `AddService`、`register_protocol`、`RunUntilAskedToQuit`
      done true
    - label GraphEngine 是否来自 ApplicationContext
      desc 确认 `get<GraphEngine>("graph_engine")` 成功
      done true
    - label graph_name 是否能取到实例
      desc 观察 `try_get(graph_name)` 的空值处理
      done true
    - label vertex/scene 配置是否匹配
      desc 对照 `vertex.conf` 与 `scenes.conf`
      done true
    - label HTTP graph_view 是否返回 JSON
      desc 确认 `graph_view` 不参与业务计算
      done true
theme
  palette #3d5a80 #64748b #2d6a4f
```

## 证据来源

- `src/main.cpp:73-130`
- `src/service/grc_service.cpp:88-140`
- `src/service/grc_service.cpp:178-200`
- `src/service/grc_http_service.cpp:34-70`
- `src/service/grc_service.h:16-25`
- `conf/plugins/graph/vertex.conf:1-38`
- `conf/plugins/scenes.conf:1-27`
- `conf/plugins/graph/video_launch/grg_response.conf:1-40`
