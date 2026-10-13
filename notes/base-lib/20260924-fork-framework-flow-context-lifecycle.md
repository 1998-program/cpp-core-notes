# 基础库代码理解：`fork::Framework` 初始化与 `FlowContext` 生命周期契约
<!-- topic-nav:start -->
> 主题：`base/fork-context` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260918-fork-flow-context-runtime-contract.md)
<!-- topic-nav:end -->


> 主题：`rec::fork` 框架如何把配置初始化、线程执行环境、RPC 请求上下文、Future 等待和事件引擎回收串成一条生命周期。
> 主题选择说明：仓库内唯一的 daily-plan 是 `daily-plan-20260529.json`，已明显早于本次执行日 2026-09-24；其中 GraphPool、DynamicTimeOut、PipelineGraphFunction、序列化等高优先级候选在近期笔记中已有覆盖。本次按 fallback 规则选择计划候选池外仍未独立覆盖的 `fork/framework` 基础契约。KU/业务上下文未逐篇读取，需人工补充；以下结论以源码证据为主。

## 0. 架构全景图

<style>.fork-arch{font-family:Inter,Arial,sans-serif;border:1px solid #d8dee9;background:#f8fafc;border-radius:8px;padding:18px;margin:18px 0;color:#243041}.fork-arch h3{margin:0 0 12px;font-size:20px}.fork-arch h4{margin:14px 0 8px;font-size:14px;color:#1f3b57}.fork-grid{display:grid;grid-template-columns:2fr 1fr;gap:10px}.fork-box{border:1px solid #c9d3e3;border-left:4px solid #2d6a4f;background:#fff;padding:11px;min-height:72px;border-radius:6px}.fork-box b{display:block;font-size:13px;color:#1a2f44}.fork-box span{display:block;font-size:12px;line-height:1.5;color:#526071;margin-top:4px}.fork-init{border-left-color:#3d5a80}.fork-life{border-left-color:#9a5b28}.fork-risk{border-left-color:#b8432f}.fork-note{font-size:12px;line-height:1.5;color:#526071;margin-top:10px}@media(max-width:760px){.fork-grid{grid-template-columns:1fr}}</style><div class="fork-arch"><h3>fork/framework 运行时分层</h3><h4>进程启动与框架装配</h4><div class="fork-grid"><div class="fork-box fork-init"><b>main.cpp::run</b><span>安装信号处理器、解析 flags、初始化 perf 与 bthread 栈参数，然后取得 Framework 单例。</span></div><div class="fork-box fork-init"><b>Framework::init</b><span>读取配置，依次创建线程组、插件、策略、监控、优先级与 InterfaceClub。</span></div></div><h4>请求级上下文</h4><div class="fork-grid"><div class="fork-box"><b>FlowContext</b><span>集中保存 request/response protobuf、brpc Controller、Closure、FlowEnv、Future tomb 与 EventEngine。</span></div><div class="fork-box fork-life"><b>GeneralFlowContext</b><span>在 pack_response/done 阶段写 attachment 并保证 Closure 只执行一次。</span></div></div><h4>跨请求共享与回收</h4><div class="fork-grid"><div class="fork-box fork-life"><b>FlowEnv / EventEngine</b><span>FlowEnv 与 DAG 同生命周期；EventEngine 由 context 持有，完成后显式 destroy。</span></div><div class="fork-box fork-risk"><b>future_tomb</b><span>异步任务必须登记，wait_all_future() 等待并清空，否则 reset/done 可能早于后台任务。</span></div></div><div class="fork-note">核心边界：Framework 管进程级装配，FlowContext 管请求级资源；二者不能用“全局单例”方式替代彼此的生命周期。</div></div>

## 1. 入口链：`run → init → run_until_asked_to_quit`

`src/main.cpp:74-114` 显示启动顺序：先安装信号处理与 perf，再设置普通 bthread 栈大小，调用 `Framework::init`，成功后进入 `Framework::run`，最后等待退出、stop、join。初始化失败和运行失败都会先 `destroy()` 再返回错误码，因此 `init` 的返回值是进程启动闸门，而不是可忽略的 warning。

`src/framework.h:34-67` 将 Framework 暴露为进程级入口：`instance()` 返回单例，`init()` 负责装配，`handle(FlowCntxPtr)` 负责进入请求处理，`run/stop/join` 管理服务运行窗口；`engine()` 则是旧异步接口到新 Engine 的桥接点。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam sequenceMessageAlign center
title fork/framework 进程启动与请求上下文生命周期
participant "main::run" as Main
participant "Framework" as Fw
participant "ConfigManager" as Cfg
participant "Thread/Plugin/Strategy" as Modules
participant "FlowContext" as Ctx
participant "RPC Closure" as Done
participant "EventEngine" as EE
Main -> Main : install signals + parse flags
Main -> Fw : Framework::instance()
Main -> Fw : init(config_path, config_file)
Fw -> Cfg : init(conf_path, configs)
Fw -> Modules : init_thread_groups()
Fw -> Modules : init_plugin_manager()
Fw -> Modules : init_strategy_manager()
Fw -> Modules : init_monitor / priority / interface
alt any init returns non-zero
  Fw --> Main : error
  Main -> Fw : destroy()
else initialized
  Main -> Fw : run()
  Main -> Ctx : handle(FlowCntxPtr)
  Ctx -> Ctx : init / bind request + response
  Ctx -> EE : collect_event_engine(engine)
  Ctx -> Ctx : collect_timeout_future(future)
  Ctx -> Ctx : wait_all_future()
  Ctx -> EE : destroy_event_engine()
  Ctx -> Done : done() once
  Done --> Main : response completed
end
@enduml
```

## 2. `Framework::init` 的配置装配顺序

`src/framework.cpp:200-404` 是实际初始化骨架。它先加载日志并决定 `non_io_cost_enable`，再初始化 thread-local key 与 `ConfigManager`；之后读取默认 worker 类型，创建线程组，继续装配 dict、plugin、strategy、smfw、commander、monitor、priority、interface club、dataframe device manager、coach 和 meta manager。每个阶段都检查返回码，失败立即返回，避免服务以半初始化状态接受请求。

这里有两个重要契约：

1. `threading.default_workers_type` 只接受 `pthread` 或 `bthread` 对应的枚举分支；未知值走 fatal/error 返回，不会静默落到默认值。
2. 配置项不是平铺读取，而是按 `threading`、`configs`、`plugin`、`strategy`、`monitor` 等 section 分层传入各 manager。排查时应先确认 section 路径，再看具体插件配置。

`src/framework.h:114-127` 进一步说明运行时持有 `_sync_proc_group`、`_cmd_proc_group`、`_engine`、`_binited` 和默认 worker 类型。`_binited` 是框架是否完成初始化的状态位，不能由业务请求自行修改。

```infographic
infographic sequence-ascending-steps
data
  title Framework 初始化流水线
  desc 从进程入口到可接收请求的严格顺序
  items
    - label 1. 解析启动参数
      desc main.cpp 读取 config_path 和 config_file，并设置信号与 bthread 栈参数
      icon mdi/flag-outline
    - label 2. 初始化日志与本地存储
      desc 加载 comlog，初始化 bthread key 和 thread context key
      icon mdi/file-cog-outline
    - label 3. 初始化 ConfigManager
      desc 把 configs section 交给统一配置管理器
      icon mdi/database-cog-outline
    - label 4. 创建线程执行环境
      desc 读取 default_workers_type，创建 sync/cmd thread groups
      icon mdi/lan-connect
    - label 5. 装配扩展组件
      desc dict/plugin/strategy/smfw/monitor/priority/interface 依次初始化
      icon mdi/puzzle-outline
    - label 6. 进入服务循环
      desc Framework::run 成功后才允许 handle FlowContext
      icon mdi/play-circle-outline
    - label 7. 退出时回收
      desc stop 后 join；初始化或运行失败先 destroy
      icon mdi/stop-circle-outline
 theme
  palette #2d6a4f #3d5a80 #9a5b28 #b8432f
```

## 3. `FlowContext`：请求资源的统一容器

`src/common/flow_context.h:43-190` 把一条请求需要的跨层对象集中在同一个 context：

- `_resp`、`_general_resp_pb`、`_general_req_pb` 和 `_resp_pb` 分别指向框架接口及用户服务的 protobuf 请求/响应。
- `_done` 是 Closure 基类指针，`_cntl` 是 brpc Controller；二者生命周期由 RPC 框架管理，FlowContext 只保存借用指针。
- `_flow_env` 与 DAG 同生命周期，注释明确它应在最后一个请求结束后销毁。
- `future_tomb` 保存待等待的异步 Future；`collect_timeout_future()` 加锁登记，`wait_all_future()` 逐个 wait 后清空。
- `_event_engine` 由 `collect_event_engine()` 移入 context，`destroy_event_engine()` 显式调用 destroy 并在失败时 fatal。
- `_node_cntx_map` 受独立 mutex 保护；不要把它与 `_status_mutex` 混为同一状态域。

`reset()` 只重置 flow info、done 标志、染色 context 与 FlowEnv，不会自动替调用方等待 Future，也不会自动销毁 EventEngine。因此复用 context 前必须执行框架规定的等待与回收阶段。

`copy()` 只复制请求/响应指针、Closure、Controller、BaseContext、Document 等浅层句柄，不复制 `future_tomb`、mutex、node map 和 event engine。它适合派生上下文复制请求视图，不适合当作完整深拷贝。

## 4. `GeneralFlowContext` 的响应收尾契约

`src/common/general_flow_context.h:28-68` 给出了通用 RPC context 的两个关键动作：

- `pack_response()` 如果 `_is_done` 已经为真则直接跳过；否则检查 `_cntl`，从当前线程环境取 `RuntimeCntx`，在非 HTTP 协议下把 node statistics 序列化到 response attachment。
- `done()` 只有在 `_is_done == false` 时才调用 `_done->Run()`，然后设置 `_is_done = true`。这是 Closure 单次执行的防重入门闩。

因此业务 processor 如果同时有同步完成、超时 Future 回调和事件引擎后置任务，所有路径都必须汇聚到同一个 `done()` 语义，不能直接重复调用 `_done->Run()`。

## 5. 复用与异步安全边界

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title 请求上下文复用：可复制与不可复制
  desc FlowContext::copy 的实际语义不是完整深拷贝
  items
    - label 可复制的请求视图
      desc protobuf 指针、Controller 指针、Closure 指针、BaseContext 和已解析 Document 句柄
      icon mdi/content-copy
    - label 不应复制的运行时资源
      desc future_tomb、EventEngine、mutex、node context map、done 状态的并发控制
      icon mdi/shield-lock-outline
 theme
  palette #2d6a4f #b8432f
```

实践上，派生 context 可以复用请求指针和统计视图，但必须重新决定异步 Future 的登记归属。一个 Future 如果被两个 context 同时持有并等待，容易出现重复回收或提前 reset；一个 Future 如果未登记，则 `done()` 可能先于后台任务结束。

## 6. Pitfalls

<div class="fork-arch"><h3>fork/framework 排查卡片</h3><div class="fork-grid"><div class="fork-box fork-risk"><b>初始化半成功</b><span>某个 manager 返回错误后仍继续 run，会造成线程组、插件或 strategy 不完整。检查每个 init 阶段的返回值与对应 section。</span></div><div class="fork-box fork-risk"><b>未知 worker 类型</b><span>default_workers_type 只接受已实现分支；拼写或配置值错误应视为启动失败，不要靠默认线程模型掩盖。</span></div><div class="fork-box fork-risk"><b>copy 被误当深拷贝</b><span>copy() 不复制 Future、EventEngine、mutex 和 node context map。派生 context 只获得浅层请求视图。</span></div><div class="fork-box fork-risk"><b>Future 未登记</b><span>所有需要在请求结束前完成的异步任务都应进入 future_tomb，并在收尾路径 wait_all_future()。</span></div><div class="fork-box fork-life"><b>EventEngine 只 move 不 destroy</b><span>collect_event_engine() 只是转移所有权；必须在请求结束阶段调用 destroy_event_engine()。</span></div><div class="fork-box fork-life"><b>Closure 重复执行</b><span>同步完成、超时回调和后置事件都可能触发收尾，必须统一经过 done() 的 _is_done 门闩。</span></div></div></div>

```infographic
infographic list-column-done-list
data
  title fork/framework 调试 Checklist
  desc 从进程启动到请求结束依次核对
  items
    - label 检查 main.cpp 启动顺序
      desc init 成功后才进入 run，失败路径必须 destroy
      done true
      icon mdi/order-numeric-ascending
    - label 检查配置 section
      desc threading/configs/plugin/strategy/monitor 等 section 是否来自同一份配置
      done true
      icon mdi/file-tree
    - label 确认 worker 类型
      desc default_workers_type 必须映射到 PThreadWorkers 或 BThreadWorkers
      done true
      icon mdi/lan
    - label 追踪 Future 登记
      desc 搜索 collect_timeout_future 与 wait_all_future 是否覆盖所有异步分支
      done true
      icon mdi/clock-check-outline
    - label 追踪 EventEngine 所有权
      desc collect 后必须在收尾调用 destroy_event_engine
      done true
      icon mdi/engine
    - label 追踪 Closure 单次完成
      desc 所有完成路径统一调用 GeneralFlowContext::done
      done true
      icon mdi/check-circle-outline
 theme
  palette #2d6a4f #3d5a80 #b8432f
```

## 证据来源

- `baidu/fork/framework/src/main.cpp:74-114`：进程启动、Framework 初始化、运行与退出顺序。
- `baidu/fork/framework/src/framework.h:34-67`：Framework 公共生命周期接口。
- `baidu/fork/framework/src/framework.h:114-145`：线程组、Engine、初始化状态与兼容 Future 类型。
- `baidu/fork/framework/src/framework.cpp:200-404`：配置、线程组、插件、策略、监控、优先级和接口组件初始化顺序。
- `baidu/fork/framework/src/common/flow_context.h:43-190`：FlowContext 字段、reset/copy、Future 与 EventEngine 管理。
- `baidu/fork/framework/src/common/general_flow_context.h:28-68`：响应 attachment、pack_response 与 Closure 单次 done。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260924-fork-framework-flow-context-lifecycle.md)。
