# 2026-09-11 基础库理解：GRF rframe 启动、IOC 注册与动态 SO 生命周期

> 日期：2026-09-11  
> 主题选择：未发现当日 weekly/daily plan；最近笔记已覆盖 ApplicationContext、PipelineGraphFunction、MultiStreamEngine、GraphPool 与 dapper-collector。本次按邻近未覆盖主题回退到 `gr-front` 的 rframe 启动、IOC 注册、channel 初始化与动态 SO handler 绑定。KU 正文未读取，业务上下文需人工补充；以下结论只依据代码证据。  
> 范围：`main.cc`、`global.conf`、执行树配置、`ConfManager`、`Channels` 与 `init_so`/`init_rframe`。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:10px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;">进程入口</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">main.cc</div><div style="margin-top:8px;font-size:12px;color:#52606d;">注册日志 appender，初始化 imitation、brpc Server、HTTP 路由和 nshead_service。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;">rframe 容器</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">IOC + DictManager + ThreadPool</div><div style="margin-top:8px;font-size:12px;color:#52606d;">CPUNode、IONode、NsheadIO、RedisIO、HttpIO 等基础类型在启动期注册。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;">动态扩展</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">libgrf.so handlers</div><div style="margin-top:8px;font-size:12px;color:#52606d;">执行树节点通过 pre/after handler 把业务逻辑挂到 rframe 节点生命周期。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 52px 1fr 52px 1fr 52px 1fr;gap:8px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">gflags / comlog</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;font-size:13px;">GlobalInitializer</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;font-size:13px;">ConfManager / Channels</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">rframe tree + SO</div></div></div>

## 1. 启动调用链

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRF 启动：从 brpc 到 rframe 执行树
participant "main.cc" as MAIN
participant "brpc Server" as SERVER
participant "GlobalInitializer" as GLOBAL
participant "ConfManager" as CONF
participant "Channels" as CH
participant "rframe IOC" as IOC
participant "SharedManager" as SO
MAIN -> MAIN : ParseCommandLineFlags + flagfile
MAIN -> MAIN : init_log + imitation::init
MAIN -> SERVER : set nshead_service + HTTP services
MAIN -> GLOBAL : init global configure
MAIN -> CONF : init ConfContext and register_dict("conf_ctx")
MAIN -> CH : init channels from configure
MAIN -> IOC : register CPUNode / IONode / IO implementations
MAIN -> SO : load pre/after handlers from config
MAIN -> SERVER : Start(port, options)
SERVER --> MAIN : RunUntilAskedToQuit
note over IOC,SO
IOC 注册基础节点类型；SharedManager 装载业务 SO handler。
执行树配置把 node、io、channel、handler 组合成运行时拓扑。
end note
@enduml
```

## 2. 启动阶段信息图

```infographic
infographic sequence-ascending-steps
data
  title GRF 基础设施启动顺序
  desc 每一步都为后续请求期执行树解析提供前置对象
  items
    - label 日志与仿真框架
      desc comlog、AsyncFileAppender、imitframe.conf 先于服务启动完成
      icon mdi:file-document-cog
    - label brpc 服务壳
      desc ServerOptions 绑定 nshead_service，并暴露 /degrade 与 /discard HTTP 路由
      icon mdi:server-network
    - label 全局配置
      desc GlobalInitializer 读取 configure，ConfManager 注册 conf_ctx 字典
      icon mdi:cog-sync
    - label 下游通道
      desc Channels 从 channels 配置创建 brpc Channel、重试策略和 ns plugin
      icon mdi:transit-connection-variant
    - label rframe 运行时
      desc IOC 注册节点和 IO 类型，SharedManager 加载 libgrf.so handler
      icon mdi:graph-outline
    - label 对外监听
      desc server.Start 后进入 RunUntilAskedToQuit，退出时停止本地时间管理器
      icon mdi:play-network
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 brpc 服务壳先于 rframe 动态拓扑完成

`main.cc:151-169` 创建 `baidu::rpc::Server`，设置 `options.nshead_service = new grf::NsheadJsonService`，并把 `/degrade => Degrade`、`/discard => Discard` 两个 HTTP 控制面挂到同一个 server。真正的 rframe 初始化发生在后面：`main.cc:171-188` 依次初始化 `SharedManager`、`GlobalInitializer`、`ConfManager`、`Channels`、`TopDisasterComponent`、`init_rframe` 和 `init_so`。

这个顺序说明 GRF 不是先构建完整图再注册服务，而是先把 brpc 接入点准备好，再把配置、通道、节点类型和 SO handler 补齐。排障时如果 server 已创建但启动失败，问题更可能落在配置、通道或 handler 装载阶段。

### 3.2 rframe 基础类型通过 IOC 注册，业务逻辑通过 SO handler 接入

`main.cc:108-123` 的 `init_rframe` 注册 `CPUNode`、`GRCPUNode`、`IONode`、`NsheadIO`、`RedisIO`、`HttpIO`、`FeatureIO`、`HistoryRedisIO`、`UnsendSndbIO`，随后初始化全局线程池。`main.cc:44-105` 的 `init_so` 则遍历配置中的 group，读取 `pre_so_path`、`pre_so_name`、`pre_handler`、`after_so_path`、`after_so_name`、`after_handler`，并调用 `SharedManager::load`。

因此配置中的节点不是直接写死 C++ 类型，而是由三类信息组合：节点类型来自 IOC，远端访问来自 IO/channel，业务处理来自动态库 handler。`list_tree.conf:47-59` 中 `GRIONode` 选择 `NsheadIO`、`GRChannel`、`gr_pre_handler`、`gr_after_handler`，就是这个组合的典型样例。

### 3.3 配置聚合文件定义运行时拓扑入口

`global.conf:1-5` include `sample_server_tree.conf`、`interleaving_tree.conf`、`list_tree.conf`、`set_redis_tree.conf` 和 `channels.conf`。请求期看到的是 `FeedClosure` 里的树名，但启动期必须先通过这些 include 让 ConfContext 拿到树和 channel 的完整定义。

`ConfManager::init` 在 `confmanager/conf_manager.cc:8-21` 创建 `ConfContext`、调用 `init()`、注册到 `DictManager` 的 `conf_ctx`，并执行 `load()`。请求到达时，`NsheadJsonService` 在 `service/nshead_service.cc:49-53` 创建 `GRFSessionContext`，设置全局配置，并通过 `ConfManager::set_conf_ctx` 把 `conf_ctx` 放入 session。

### 3.4 Channel 初始化同时处理协议、重试、命名插件

`channels/channels.cc:26-84` 遍历 `channels.channel`，读取 `name`、`lb`、`max_retry`、`protocol`、`bns`、`connection_type`、`timeout_ms`、`backup_timeout_ms`、`retry_policy`、`ns_plugin_name`、`ns_plugin_conf_file`。`channels/channels.cc:86-150` 根据这些字段构造 `baidu::rpc::ChannelOptions`，必要时从 IOC 创建 `CustomRetryPolicy`，加载 naming service plugin JSON 后初始化 brpc Channel，并存入 `_ch_hash`。

这意味着 GRF 的下游依赖并不只由 BNS 决定；协议、连接类型、超时、backup request、retry policy 和 ns plugin 都是同一 channel contract 的一部分。排查下游异常时要看 channel 配置整体，而不是只看 BNS 名称。

## 4. 证据来源

- `main.cc:130-190`：主启动链、brpc service 注册、全局配置、Channels、rframe 和 SO 初始化顺序。
- `main.cc:44-105`：`init_so` 从配置读取 pre/after handler 并调用 `SharedManager::load`。
- `main.cc:108-123`：`init_rframe` 注册 rframe node/IO 类型并初始化线程池。
- `confmanager/conf_manager.cc:8-29`：`ConfContext` 注册到 `DictManager`，请求期通过 `set_conf_ctx` 注入 session。
- `channels/channels.cc:26-150`：channel 字段读取、retry policy、ns plugin 与 `baidu::rpc::Channel` 初始化。
- `global.conf:1-5`：树配置与 channel 配置的聚合入口。
- `list_tree.conf:1-109`、`interleaving_tree.conf:1-16`、`set_redis_tree.conf:1-19`：执行树、节点、IO、handler、channel 和 command 的配置绑定。

## 5. Pitfalls 卡片

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f7fb;border-left:5px solid #3D5A80;padding:14px 16px;margin:16px 0;color:#243b53;"><div style="font-size:13px;font-weight:800;color:#334e68;text-transform:uppercase;">Pitfall 1</div><div style="font-size:18px;font-weight:800;margin-top:4px;color:#102a43;">不要只看 main.cc 里的 AddService</div><div style="font-size:13px;margin-top:8px;color:#52606d;">GRF 的实际业务拓扑在 rframe tree 配置和 SO handler 里。AddService 只能说明入口形态，不能说明请求会访问哪些下游。</div></div>
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f6fbf8;border-left:5px solid #0F766E;padding:14px 16px;margin:16px 0;color:#243b53;"><div style="font-size:13px;font-weight:800;color:#335c4a;text-transform:uppercase;">Pitfall 2</div><div style="font-size:18px;font-weight:800;margin-top:4px;color:#102a43;">Channel 配置是多字段契约</div><div style="font-size:13px;margin-top:8px;color:#52606d;">BNS、lb、protocol、connection_type、timeout、backup_request、retry_policy、ns_plugin 任一字段变化都可能改变调用行为。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title GRF 启动问题排查清单
  desc 从入口到动态 handler 的最短检查路径
  items
    - label 确认 gflags 与 comlog 加载成功
      desc main.cc 中 flagfile 和 comlog 是后续日志可见性的前提
      done true
    - label 检查 global.conf include 是否完整
      desc 缺 tree 或 channels include 会导致运行期找不到节点或通道
      done true
    - label 核对 ConfManager load 结果
      desc conf_ctx 必须注册到 DictManager，session 才能读取执行树配置
      done true
    - label 核对 channels 字段
      desc BNS、lb、protocol、timeout、retry policy 和 ns plugin 一起检查
      done true
    - label 核对 SO handler 名称
      desc pre_handler / after_handler 与 libgrf.so 导出符号不一致会在加载期失败
      done true
 theme
  palette #3D5A80 #0F766E #F59E0B
```
