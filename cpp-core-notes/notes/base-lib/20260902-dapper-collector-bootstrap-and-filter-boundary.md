# 2026-09-02 周三基础库理解：dapper-collector 启动初始化、请求过滤与双入口边界

> 日期：2026-09-02  
> 主题来源：当前可用 daily-plan 未找到 2026-09-02 当日文件，按历史候选回退到 `dapper-collector` 的启动/初始化边界。KU 正文未读取，业务背景需人工补充。  
> 范围：`main.cc`、`src/initializer/global_initializer.cc`、`include/common/request_filter.h`、`include/common/channels.h`、`src/manager/sid_sender_receiver_whitelist_mgr.cc`，聚焦启动顺序、配置加载、pd_ch 白名单与 v1/v2 服务挂载。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.08fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">进程入口</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`main.cc:62-146`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">解析 gflags、注册 reusable rpc 协议、加载日志和白名单，再把两个 service 绑定到 brpc server。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">初始化编排</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`GlobalInitializer::init()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">按 UploadDB → DownloadDB → TaskManager → UpdateDB → SndbComponent → CacheComponent 的顺序串联依赖。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求准入</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`RequestFilter` + `SidSenderReceiverWhitelistMgr` + `BnsPermissionController`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">对 pd_ch、sender/receiver、module_name、BNS 写权限做前置过滤。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">gflags / conf</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">全局初始化</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">服务挂载</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">白名单与过滤</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title dapper-collector bootstrap and filter boundary
participant "main.cc" as MAIN
participant "ReusableRPCProtocol" as PROTO
participant "GlobalInitializer" as GI
participant "UploadDB / DownloadDB" as DB
participant "TaskManager" as TM
participant "RequestFilter" as RF
participant "SidSenderReceiverWhitelistMgr" as SW
participant "BnsPermissionController" as BNS
participant "CollectorServiceImpl" as V1
participant "CltServiceV2Impl" as V2
participant "brpc::Server" as SRV
MAIN -> PROTO : register_protocol()
MAIN -> GI : init()
GI -> DB : initialize()
GI -> TM : initialize()
GI -> DB : UpdateDatabaseManager::initialize()
GI -> SW : load whitelist state
MAIN -> RF : load(request_filter.conf)
MAIN -> SRV : AddService(v1 upload)
MAIN -> V2 : init()
MAIN -> SRV : AddService(v2 upload / collector / trigger_task)
MAIN -> SRV : Start(port, options)
V1 -> BNS : write permission check happens in v2 path
V2 -> BNS : is_allow_write(request_ip)
V2 -> RF : request_filter.conf already loaded by process init
V2 -> SW : sender-receiver whitelist loaded at startup
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title 启动链上的 6 个关键动作
  desc 这些动作决定服务能否启动、能否接收请求、以及请求是否会在入口被过滤
  items
    - label protocol 注册
      desc `main.cc:63-68` 调用 `ReusableRPCProtocol::register_protocol()`，失败直接退出
      icon mdi-shuffle-variant
    - label 日志初始化
      desc `main.cc:71-79` 载入 comlog 并初始化 BabylonLogger
      icon mdi-text-box-check-outline
    - label 全局依赖
      desc `src/initializer/global_initializer.cc:19-49` 串起 DB、TaskManager、SndbComponent、CacheComponent
      icon mdi-cog-transfer
    - label pd_ch 过滤
      desc `include/common/request_filter.h:35-137` 解析 `request.item`，构建 sender/receiver/module 白名单
      icon mdi-filter-cog
    - label sender-receiver 白名单
      desc `src/manager/sid_sender_receiver_whitelist_mgr.cc:15-41` 从文本文件装载允许集合
      icon mdi-account-switch-outline
    - label BNS 写权限
      desc `src/service/collector_v2.cc:120-187` 对请求 IP 做准入控制，失败时直接返回拒绝
      icon mdi-shield-account-outline
```

## 3. 代码链路拆解
### 3.1 启动顺序是强依赖链，不是松耦合初始化
- `main.cc:62-79`：先解析命令行，再设置 `flagfile`，然后注册 `ReusableRPCProtocol`，最后加载 comlog 和全局 logger。这个顺序说明协议注册和日志初始化都属于 server 能否正常运行的前置条件。
- `src/initializer/global_initializer.cc:19-49`：`UploadDatabaseManager`、`DownloadDatabaseManager`、`TaskManager`、`UpdateDatabaseManager`、`SndbComponent`、`CacheComponent` 串行初始化。任何一个关键组件失败都会让整体初始化返回 `-1`。
- `main.cc:101-138`：`CollectorServiceImpl` 和 `CltServiceV2Impl` 都在 `server.Start()` 之前挂载，说明这个进程同时承担旧版 v1 协议和新版 v2 协议的接入职责。

### 3.2 请求过滤是配置驱动的前置门禁
- `include/common/request_filter.h:35-61`：`is_valid(pd_ch)`、`is_valid(pd_ch, ProductInfo&)`、`is_valid(pd_ch, module_name)` 三层判断把 `pd_ch`、sender/receiver 和 module 白名单拆开，说明过滤规则不是单一开关，而是多维组合。
- `include/common/request_filter.h:62-137`：`load()` 通过 `ExperimentConf` 读取 `request.item` 数组，再把 `pd_ch`、`sender`、`receiver`、`module`、`prefix_module` 写入 `_valid_pd_ch`。这意味着 filter 内容完全由配置文件决定。
- `src/manager/sid_sender_receiver_whitelist_mgr.cc:15-41`：白名单读取失败会返回 `false` 并保留 `_enabled=false`，但不会抛异常终止。上层需要自己决定“继续跑但放宽”还是“拒绝启动”。

### 3.3 v2 入口把过滤和 bucket 分发连成一条线
- `src/service/collector_v2.cc:110-187`：`upload()` 先做 BNS 写权限校验，再按 `request->dapper_record_size()` 遍历记录，区分 `BAIDU_APP` 和 `NEW_APP` 的 key 高 32 位。
- `src/service/collector_v2.cc:180-198`：当 `channel_id` 不是 1 或 178 时，记录为过滤请求；若 `FLAGS_is_filter_channel` 打开，则直接 `continue`。
- `src/service/collector_v2.cc:205-229`：保留 `path_info` 的请求体大小统计，把数据按 `item.key % FLAGS_buffer_pool1_size` 分桶后交给 `_buffer_pool1[i]->receive_data()`。这一步把前置过滤和后续业务处理连接起来。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">最容易出问题的是“能启动，但入口规则失效”</div><div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`RequestFilter::load()` 依赖 `request.item` 配置项，一旦配置文件内容不完整，就会静默少装载一部分 pd_ch。服务仍然能起来，但请求可能在不该放行的地方被放行，或者相反。</div><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`SidSenderReceiverWhitelistMgr::load()` 失败只返回 `false`。如果上层只看进程是否启动，不看日志和白名单状态，就会把“白名单没加载”误判成“策略生效”。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：protocol 注册 → 全局初始化 → request_filter.conf → sender/receiver 白名单 → BNS 准入 → v2 分桶</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title dapper-collector 启动与过滤排查清单
  desc 适用于启动失败、请求全拒、pd_ch 误判、白名单失效和 v1/v2 服务未挂载
  items
    - label 检查 protocol 注册
      desc `ReusableRPCProtocol::register_protocol()` 是否返回 0
      done true
    - label 检查全局初始化
      desc `GlobalInitializer::init()` 是否在 DB / TaskManager 上返回 0
      done true
    - label 检查 request_filter.conf
      desc `request.item` 是否覆盖目标 pd_ch / sender / receiver / module
      done true
    - label 检查白名单装载
      desc `SidSenderReceiverWhitelistMgr::load()` 是否成功且集合非空
      done true
    - label 检查 BNS 准入
      desc v2 upload 的 remote_side IP 是否命中 `is_allow_write()`
      done true
    - label 检查分桶与过滤开关
      desc `FLAGS_is_filter_channel`、`FLAGS_buffer_pool1_size` 是否符合预期
      done true
```

## 6. 证据来源
- `main.cc:62-146`
- `src/initializer/global_initializer.cc:19-49`
- `include/common/request_filter.h:35-137`
- `src/manager/sid_sender_receiver_whitelist_mgr.cc:15-41`
- `include/common/channels.h:22-38`
- `conf/request_filter.conf:1-180`
- `conf/gflags.conf.template:1-47`
- `src/service/collector_v2.cc:110-229`
- `src/service/collector_service.cc:28-100`

## 7. 说明
当前运行环境未找到 2026-09-02 的 daily-plan 文件；本笔记基于历史候选与本地代码回退生成，KU 正文未读取，业务背景需人工补充。