# History Service 双通道状态 IO：Redis 与 SNDB 的配置化执行骨架

> 运行说明：2026-08-05 未发现 daily-plan 文件，本篇按历史未覆盖主题 fallback 生成。KU/业务背景检索不可用，需人工补充；当前结论基于 History Service 代码与配置证据。

<style>.arch-hs-base{font-family:Inter,Arial,sans-serif;background:#f8fafc;border:1px solid #dbe4ef;border-radius:8px;padding:16px;color:#1f2937}.arch-hs-base .title{font-size:20px;font-weight:800;margin-bottom:12px}.arch-hs-base .band{border:1px solid #cbd5e1;border-radius:8px;background:#fff;padding:10px;margin-bottom:10px}.arch-hs-base h3{font-size:14px;margin:0 0 8px;color:#334155}.arch-hs-base .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.arch-hs-base .box{border-radius:6px;padding:9px;border:1px solid #cbd5e1;background:#f8fafc;font-size:12px;line-height:1.45}.arch-hs-base .box strong{display:block;font-size:13px;color:#0f172a;margin-bottom:3px}.arch-hs-base .accent{border-color:#3d5a80;background:#edf4fb}.arch-hs-base .io{border-color:#2d6a4f;background:#eef7f2}.arch-hs-base .warn{border-color:#b7791f;background:#fff7e6}</style><div class="arch-hs-base"><div class="title">架构全景：RPC 入口、Dispatcher 与配置化 IO 树</div><div class="band"><h3>入口层</h3><div class="grid"><div class="box accent"><strong>main.cc</strong>注册 ReusableRPCProtocol、初始化线程池与全局组件</div><div class="box accent"><strong>HistoryService</strong>作为 brpc 服务挂载到 Server</div><div class="box"><strong>gflags</strong>端口、idl 超时、并发、水位与 gr_state 开关</div><div class="box"><strong>MBvar</strong>记录请求类型、错误码和流控观测</div></div></div><div class="band"><h3>调度层</h3><div class="grid"><div class="box accent"><strong>history_service.cc</strong>解析 request_type、source、logid</div><div class="box accent"><strong>Dispatcher</strong>按 command 选择处理函数</div><div class="box"><strong>FHSSessionContext</strong>承载 request、response、controller</div><div class="box"><strong>DefaultProcFunction</strong>兜底不支持命令并打错误码</div></div></div><div class="band"><h3>执行层</h3><div class="grid"><div class="box io"><strong>GetDataTree</strong>StateMergeNode + GrStateSndbGetIONode + RedisGetIONode</div><div class="box io"><strong>SetDataTree</strong>RedisSetIONode + VideoRedisSetIONode + GrStateSndbSetIONode</div><div class="box io"><strong>channels.conf</strong>Redis 读写通道协议、超时、BNS、valid/invalid key</div><div class="box io"><strong>sndb_gr_state_client.conf</strong>SNDB get/put 超时与 backup_request</div></div></div><div class="band"><h3>关键边界</h3><div class="grid"><div class="box warn"><strong>配置决定拓扑</strong>IO 节点通过 channel/command 绑定后端</div><div class="box warn"><strong>Redis 与 SNDB 分流</strong>普通历史与 gr_state 状态链路分开建模</div><div class="box warn"><strong>状态合并前置</strong>读链路先 StateMerge，再合并历史结果</div><div class="box warn"><strong>线上派生</strong>本地 conf 可能由 template 派生，需核对发布配置</div></div></div></div>

## 核心结论

History Service 不是把 Redis/SNDB 调用散落在业务函数里，而是通过 `main.cc` 完成服务和全局组件启动，通过 `history_service.cc` 进入统一 RPC 处理，再把具体读写路径交给配置树和 handler。`get_data_tree.conf:1-34` 显示读取路径包含 `StateMergeNode`、`GrStateSndbGetIONode`、`GetHistoryMergeNode`、`RedisGetIONode`；`set_data_tree.conf:1-48` 显示写路径同时覆盖 Redis、VideoRedis 和 `GrStateSndbSetIONode`。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "main.cc\nServer" as Main
participant "HistoryService" as Svc
participant "Dispatcher" as Disp
participant "Config Tree" as Tree
participant "Redis Channel" as Redis
participant "SNDB gr_state" as Sndb
Caller -> Main: RPC request
Main -> Svc: HistoryService::Process
Svc -> Svc: parse request_type/source/logid
Svc -> Disp: dispatch(session_context)
Disp -> Tree: select get/set tree
Tree -> Redis: normal history read/write
Tree -> Sndb: gr_state read/write
Redis --> Tree: redis result
Sndb --> Tree: state result
Tree --> Disp: merged context
Disp --> Svc: fill response/error
Svc --> Caller: RPC response
@enduml
```

```infographic
infographic sequence-timeline-simple
data
  title 配置驱动执行链
  desc 从入口初始化到双通道 IO 的关键装配点
  items
    - time 入口
      label main.cc
      desc register_protocol、ThreadPool、GlobalInitializer、Server AddService
      icon mdi/server
    - time RPC
      label history_service.cc
      desc 解析请求类型与来源，维护流控和错误响应
      icon mdi/call-made
    - time 调度
      label dispatcher.cc
      desc command 命中处理函数，兜底返回参数错误
      icon mdi/source-branch
    - time 读树
      label get_data_tree.conf
      desc StateMergeNode 串起 gr_state SNDB 与历史 Redis 读取
      icon mdi/file-tree
    - time 写树
      label set_data_tree.conf
      desc Redis、VideoRedis、GrStateSndb 三类写节点并列
      icon mdi/database-arrow-up
    - time 通道
      label channels.conf
      desc Redis channel 定义协议、超时、BNS、key 白黑名单
      icon mdi/tune
```

## 代码证据解读

`main.cc:52-69` 先注册 Babylon Reusable RPC protocol，再初始化线程池和全局组件，随后构造 `baidu::rpc::Server` 与 `HistoryService`。这说明服务入口更偏运行时框架装配，不承担业务状态细节。

`main.cc:32-39` 暴露 `is_open_gr_state`、`write_layout_sndb`、`write_widget_sndb` 等状态相关开关。状态链路是否启用、写入哪个 SNDB 结构，依赖启动参数与发布配置，而不是单个函数里的硬编码判断。

`dispatcher.cc:22-34` 的默认处理函数在 command 不支持时直接写 `E_PARAM_ERROR`、设置 controller failed 并记录 MBvar。对调试来说，先确认 request_type 是否进入预期 dispatcher，比直接排查 Redis/SNDB 更靠前。

`channels.conf:1-29` 定义 `GrStateRedisReadChannel` 与 `GrStateRedisWriteChannel`，协议为 `redis`，本地 BNS 指向 `127.0.0.1:6379`，并配置 retry、timeout 和 key 过滤。`channels.conf:120-141` 展示 video/delete 相关 Redis channel 也复用同一类通道结构。

`sndb_gr_state_client.conf:6-20` 将 SNDB gr_state 的 put/get 超时和 backup request 分开配置：put 侧 `timeout_ms: 2000`、get 侧 `timeout_ms: 100`。这意味着读路径对延迟更敏感，写路径可接受更长尾部等待。

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title Redis 通道 vs SNDB gr_state 客户端
  desc 两类存储后端在 History Service 中承担不同状态边界
  items
    - label Redis Channel
      desc channels.conf 中声明 protocol redis、BNS、valid/invalid keys；覆盖普通历史与部分视频历史读写
      icon mdi/database
    - label SNDB gr_state
      desc common_component/dalton_client/sndb_gr_state_client.conf 中声明 put/get 超时和 backup_request；专门承载 gr_state 状态链路
      icon mdi/database-cog
```

<style>.card-hs-base{font-family:Inter,Arial,sans-serif;background:#fff;border:1px solid #d8dee8;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:14px 0;color:#1f2937}.card-hs-base .meta{font-size:12px;font-weight:700;color:#3d5a80;text-transform:uppercase;letter-spacing:.04em}.card-hs-base .title{font-size:18px;font-weight:800;margin:4px 0 8px}.card-hs-base .body{font-size:14px;line-height:1.65}.card-hs-base code{background:#f1f5f9;padding:1px 4px;border-radius:4px}</style><div class="card-hs-base"><div class="meta">Pitfall</div><div class="title">不要把 gr_state 当成普通历史字段</div><div class="body">`is_open_gr_state` 与 `write_layout_sndb` 这类开关在入口层暴露，但真实执行落在配置树与 SNDB/Redis 通道组合上。排查状态问题时，要同时看启动参数、`get_data_tree.conf`、`set_data_tree.conf`、`channels.conf` 和 SNDB client 配置，单看 `HistoryService` 入口只会得到半截答案。</div></div>

```infographic
infographic list-column-done-list
data
  title 调试 checklist
  desc 双通道状态 IO 问题建议从框架装配向后端逐层收敛
  items
    - label 确认入口开关
      desc 查看 is_open_gr_state、write_layout_sndb、write_widget_sndb 是否符合预期
      done true
    - label 确认 dispatcher 命中
      desc request_type/command 未命中会走 DefaultProcFunction 并返回参数错误
      done true
    - label 确认读树节点
      desc get_data_tree.conf 是否包含 StateMergeNode 与 GrStateSndbGetIONode
      done true
    - label 确认写树节点
      desc set_data_tree.conf 是否包含 GrStateSndbSetIONode 以及对应 handler
      done true
    - label 确认通道超时
      desc Redis channel timeout 与 SNDB get/put timeout 是否符合线上延迟预算
      done true
```

## 证据来源

- `src/main.cc:32-39`：状态相关 gflags，包括 `is_open_gr_state`、`write_layout_sndb`。
- `src/main.cc:52-69`：RPC protocol、线程池、全局组件、Server 与 HistoryService 装配。
- `src/service/history_service.cc:35-48`：提取 propagated tag 中的 `source` 并进入统一错误处理结构。
- `src/service/dispatcher.cc:22-34`：默认 command 处理与 MBvar 错误记录。
- `conf/channels.conf:1-29`：gr_state Redis 读写 channel 基本配置。
- `conf/channels.conf:120-141`：Video Redis 删除 channel 的同类通道配置。
- `conf/get_data_tree.conf:1-34`：读链路 StateMerge、GrStateSndbGet、RedisGet 节点。
- `conf/set_data_tree.conf:1-48`：写链路 Redis、VideoRedis、GrStateSndb 节点。
- `conf/common_component/dalton_client/sndb_gr_state_client.conf:6-20`：SNDB gr_state put/get 客户端超时与 backup 配置。
