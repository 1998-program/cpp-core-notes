# 2026-09-18 基础库理解：fork/framework 的 Flow / Node / Context 运行时合同
<!-- topic-nav:start -->
> 主题：`base/fork-context` · 主文 · [主题目录](README.md)
<!-- topic-nav:end -->


> 日期：2026-09-18  
> 主题选择：未发现本日有效 daily-plan；最近笔记已覆盖 PipelineGraphFunction、GraphPool、ApplicationContext、ExpParam 等主题，因此按邻近未覆盖的回退规则挑选 **`fork/framework` 的配置驱动 Flow 与 FlowContext/RuntimeContext 生命周期合同**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：`main.cpp` 启动链、`Flow::init/handle`、`FlowContext` 的 reset/done/pack_response、`Node::get_handler` 的 lib/jit/so 三路加载、`FlowContext::compute_non_io_cost_time` 的 DAG 非 IO 关键路径。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">配置入口</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">main.cpp + fork.conf</div><div style="margin-top:8px;font-size:12px;color:#52606d;">ParseCommandLineFlags → FperfManager → Framework::init/run；Framework 是单例。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">Flow 合同</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">init_context → init_nodes → handle</div><div style="margin-top:8px;font-size:12px;color:#52606d;">Flow 把配置转成 StgContextCreator 和 NodeMap；handle 把 FlowCntxPtr 沿着 start→next→end 串起来。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">Context 合同</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">FlowContext + RuntimeContext</div><div style="margin-top:8px;font-size:12px;color:#52606d;">FlowContext 装响应/请求/状态计时；RuntimeContext 装 flow_name、smfw 和 LogItem，二者通过 Thread::current_env 串联。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 42px 1fr 42px 1fr 42px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">fork.conf</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#065f46;font-size:13px;">Flow::init</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef3c7;border:1px solid #fde68a;border-radius:6px;padding:10px;text-align:center;color:#92400e;font-size:13px;">FlowContext + NodeMap</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ede9fe;border:1px solid #ddd6fe;border-radius:6px;padding:10px;text-align:center;color:#5b21b6;font-size:13px;">Flow::handle</div></div></div>

**一句话结论：** 这个基础库合同的关键不在“配置解析”，而在 `FlowContext` 必须按请求生命周期 init/reset/done，三路 handler（lib / jit / so）必须至少有一路能拿到 `_creator`，否则整个 flow 在 init 阶段就会 FATAL。

## 1. 启动与请求生命周期

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title fork/framework 启动到 Flow::handle
participant "main.cpp" as MAIN
participant "Framework" as FW
participant "Flow" as FLOW
participant "FlowContext" as FC
participant "RuntimeContext" as RC
participant "Node" as NODE
MAIN -> MAIN : ParseCommandLineFlags + FperfManager
MAIN -> FW : Framework::instance()
MAIN -> FW : init(config_path, config_file)
MAIN -> FW : run() + run_until_asked_to_quit()
FW -> FLOW : parse flow conf unit
FLOW -> FLOW : init_context(prefix, context_so)
FLOW -> FLOW : init_nodes(start/end/cpu/tree/chain)
FLOW -> FLOW : init_flow_info(next_item DAG)
FW -> FC : borrow FlowCntxPtr per request
FC -> FC : reset() -> _is_done=false, brpc_color_ctx 复位
FC -> RC : rtcntx->_flow_name + _smfw + _log_item 初始化
FW -> FLOW : handle(fcntx)
FLOW -> NODE : start->handle() in sync/bthread
NODE -> NODE : find_next_node(ret) -> end/next
NODE -> NODE : bthread_async(pthread_workers_type)
FLOW -> FC : pack_response + done()
@enduml
```

## 2. 关键合同面信息图

```infographic
infographic list-grid-badge-card
data
  title fork/framework 启动到请求期合同面
  desc 合同面同时覆盖配置解析、Context 生命周期、handler 加载和关键路径归因
  items
    - label main.cpp
      desc ParseCommandLineFlags + FperfManager + Framework::init/run；Framework::instance 是单例，run_until_asked_to_quit 控制退出
      icon mdi:rocket-launch-outline
    - label Flow::init
      desc init_context → init_nodes → init_flow_info 三段必须全部返回 E_OK，否则 FORK_LOG(FATAL) 直接终止
      icon mdi:cog-transfer-outline
    - label 三路 handler
      desc Node::get_handler 先尝试 lib factory、再尝试 jit、最后 dlopen so；任一成功就 break out
      icon mdi:puzzle-outline
    - label context 加载
      desc Flow::init_context 先尝试 StgContextFactory，再尝试 jit，最后 dlopen context_so 并查找 <prefix>_creator 符号
      icon mdi:package-variant-closed
    - label FlowContext 复位
      desc reset 清 _is_done、_brpc_color_ctx、_flow_env；handle 之前必须借出 FlowCntxPtr
      icon mdi:restart
    - label Response 序列化
      desc set_response 根据 cntl 协议区分 HTTP(JSON) 与非 HTTP(SerializeToString)；失败返回 false 不抛错
      icon mdi:package-up
    - label DAG 非 IO 关键路径
      desc compute_non_io_cost_time 基于拓扑和 Dijkstra 找最长非 IO 链，并把结果写入 rtcntx->_log_item
      icon mdi:chart-line-variant
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 启动链从 `Framework::instance()` 开始，初始化失败立即 FATAL
`main.cpp:74-125` 的 `run()` 先注册 `SIGUSR2(gprof)` 与 `SIGINT(sigint_handler)`，再调用 `Framework::instance()->init(FLAGS_config_path, FLAGS_config_file)`。`init` 失败时 `FORK_LOG(FATAL) << "init framework failed"` 并 `framework->destroy()` 后返回错误码；`run` 失败时同样走 `destroy`。`run_until_asked_to_quit` 之后还有 `framework->stop()` → `framework->join()` → `framework->destroy()` 的清理链。当前代码中 `while (!g_signal_quit)` 段落被注释，进程退出仅由 `run_until_asked_to_quit` 决定。

### 3.2 `FlowContext` 通过 `reset()` 让请求可以重复借出
`src/common/flow_context.h:60-66` 的 `reset()` 复位 `_is_done=false`、`_brpc_color_ctx.reset()`、`_flow_env.reset()`。这是请求借出前必须执行的前提；否则上次请求的 `_done` 闭包、染色上下文和环境指针会污染下一次请求。

### 3.3 `set_response` 按 brpc 协议区分 JSON 与 protobuf
`src/common/flow_context.cpp:35-58` 先判断 `_cntl->request_protocol() == PROTOCOL_HTTP`，命中就走 `ProtoMessageToJson`（`bytes_to_base64=true`）写到 `_bcntx->mutable_resp_str()`；否则走 `SerializeToString`。两条路径任一失败都只 `return false` 并 `FORK_LOG(WARNING)`，不会触发 FATAL。因此上游业务要主动检查返回值，否则响应会回传空 body 但日志只是 WARNING 级别。

### 3.4 `GeneralFlowContext::pack_response` 复用 `RuntimeCntxPtr` 写响应附件
`src/common/general_flow_context.h:30-60` 在 `pack_response()` 中通过 `Thread::current_env()->get_env(TL_NAME_RTCNTX, rtcntx)` 拿到 runtime context，把 node stat 序列化后写到 `cntl->response_attachment()`。`done()` 重复执行有幂等保护：`if (!_is_done) { _done->Run(); _is_done = true; }`，避免重复 release closure。

### 3.5 `Flow::handle` 把请求从 start 串到 end，pthread 节点跨线程切换
`src/strategy/flow.cpp:280-357` 的 `handle` 先做队列等待超时（`_queue_timeout`）检查，然后从 `_node_start` 开始循环 `find_next_node(node_ret)`。当节点类型是 `PThreadWorkers` 且同步线程是 `WORKERS_BTHREAD`，会通过 `Framework::instance()->engine()->schedule(...)` 切到 pthread 并 `future_handle->get()` 等待。循环结束会跑一次 end 节点，并在 `_proctime` 上设置 errno 与耗时。

### 3.6 `Node::get_handler` 三路加载 lib → jit → so
`src/strategy/node.cpp:196-212` 优先 `_get_handler_from_lib`（`StgHandlerFactory`），其次 jit（`BAIDU_FORK_ENABLE_LLVM_JIT` 开关控制），最后 `_get_handler_from_so`。so 路径需要找到 `<reg_name>_creator` 符号并 `creator->create()`。`src/strategy/node.cpp:113-124` 的 `stg_handler` 版本走 `SharedManager`，二者不要混淆。

### 3.7 `FlowContext::compute_non_io_cost_time` 基于 DAG Dijkstra 找最长非 IO 路径
`src/common/flow_context.cpp:109-227` 把 `_node_cntx_map` 复制出 `tmp_node_map`，再用 `dag_engine->get_dag_info()` 里的 `adjacent`、`topological`、`start_node`、`end_node` 做 Dijkstra。最后把 `distTo[end_node_name]` 包装成 `CommandStatInfo("T_NON_IO_TOTAL")` 写入 `rtcntx->_log_item`，并把最长路径字符串写到 `_non_io_path`。debug 级别（`g_comlog_min_level >= 16`）会额外打印节点上下文与 topological。

## 4. 上下文与生命周期对账

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title FlowContext vs Context vs RuntimeContext 的边界对账
  desc 三种 Context 各管一段生命周期，错误指派会让请求期状态丢失
  items
    - label Context base
      desc src/common/context.h:24-43 定义 init/reset/destroy/dump/load 五接口，FlowContext/RuntimeContext 都继承它
      value base
      icon mdi:vector-circle
      children
        - label E_NOT_IMPL
          desc dump/load 默认返回 FError::E_NOT_IMPL，不实现序列化迁移
        - label E_OK
          desc init/reset/destroy 默认返回 E_OK，子类按需重写
    - label FlowContext
      desc src/common/flow_context.h:43-189 装响应、请求、计数、node map、event engine；请求期被 thread local TL_NAME_FCNTX 持有
      value fcntx
      icon mdi:file-document-multiple-outline
      children
        - label request lifetime
          desc reset 清 _is_done、_brpc_color_ctx、_flow_env
        - label pack_response
          desc 由子类实现，GeneralFlowContext 把 node stat 写到 response_attachment
        - label compute_non_io_cost_time
          desc Dijkstra 写 T_NON_IO_TOTAL 到 rtcntx->_log_item
    - label RuntimeContext
      desc src/common/runtime_context.h:26-57 装 flow_name、smfw、LogItem 和 _tid；Thread::current_env 持有 TL_NAME_RTCNTX
      value rtcntx
      icon mdi:chart-timeline-variant
      children
        - label reset
          desc 清 _flow_name 和 _log_item
        - label smfw
          desc Flow::handle 用 fcntx->_req_smfw 初始化 rtcntx->_smfw
        - label log_item
          desc Flow::handle 把 parse_time、queue_time、proctime 加入 rtcntx->_log_item
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d7dee8;border-left:6px solid #3d5a80;border-radius:8px;padding:18px;margin:18px 0;color:#1f2937;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800;">Pitfalls · fork framework</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">配置通过 ≠ Context 合同就绪</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:6px;padding:12px;font-size:14px;"><b>四个常见误判：</b><br>① 把 init 成功当作所有节点都可用；② 忽略 FlowContext::reset 必须每次请求前调用；③ 三路 handler 加载顺序被读成“必须三路都成功”；④ 直接吃 set_response 的 false 返回值而不打 ERROR。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>Framework init 返回值→Flow::init_context→init_nodes→init_flow_info→handle 借 fcntx→reset→set response→done→pack_response→compute_non_io_cost_time。</div></div><div style="margin-top:12px;font-weight:900;color:#3d5a80;">∎ fork 的合同边界在 init/reset/handle 一行，在 done 一行，而不是 main.cpp 的 AddService 一行。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title fork/framework 启动与请求期排查清单
  desc 适用于 fork 服务 init 失败、Context 状态异常、响应空或非 IO 关键路径归因错位
  items
    - label 确认 main.cpp 加载顺序
      desc FperfManager init 必须在 Framework::init 之前完成，bthread 栈大小被设为 10M
      done true
    - label 确认 Framework init 返回 0
      desc 非 0 时 FORK_LOG(FATAL) 出现后 framework->destroy 必须被调用
      done true
    - label 确认 Flow init 三段
      desc init_context、init_nodes、init_flow_info 顺序返回 E_OK
      done true
    - label 检查 context 加载来源
      desc StgContextFactory 失败时再走 jit，最后 dlopen context_so 并查找 <prefix>_creator
      done true
    - label 检查 Node handler 三路
      desc lib factory → jit → dlopen so，找 <reg_name>_creator 符号
      done true
    - label 检查 FlowContext reset
      desc 每次请求前 reset()，避免 _is_done 与 _flow_env 污染
      done true
    - label 检查 set_response 返回值
      desc HTTP 走 ProtoMessageToJson，非 HTTP 走 SerializeToString；失败仅 WARNING，需要业务层兜底
      done true
    - label 检查 compute_non_io_cost_time
      desc DAG 类型必须是 ENGINE_DAG，否则提前 return；非 IO 路径会被写入 _non_io_path
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/main.cpp:39-91`：gflags、config_path、config_file、stack_size_normal 默认值。
- `src/main.cpp:74-125`：`run()` 的信号处理、FperfManager、Framework::init/run/run_until_asked_to_quit/stop/join/destroy 调用顺序。
- `src/main.cpp:149-165`：`main()` 通过 `setenv("ALREADY_SET_ENV")` + `execv` 自重启来设置 OMP/MALLOC 环境变量。
- `src/common/context.h:24-43`：`Context` 接口 init/reset/destroy/dump/load。
- `src/common/runtime_context.h:26-57`：`RuntimeContext` 的 reset、`_flow_name`、`_smfw`、`_log_item`、`_tid`。
- `src/common/flow_context.h:43-189`：FlowContext 全部字段、`_done`、`_bcntx`、`_node_cntx_map`、`future_tomb`、`_event_engine`、`_eventbus`。
- `src/common/flow_context.cpp:35-58`：`set_response` HTTP/非 HTTP 分支。
- `src/common/flow_context.cpp:82-97`：`get_flow_info` 线程安全单次构造。
- `src/common/flow_context.cpp:99-107`：`add_node_cntx` 加锁防重名。
- `src/common/flow_context.cpp:109-227`：`compute_non_io_cost_time` 的 DAG Dijkstra 与 CommandStatInfo 写入。
- `src/common/general_flow_context.h:30-67`：`pack_response` 通过 `TL_NAME_RTCNTX` 写 response_attachment，`done()` 幂等。
- `src/strategy/flow.cpp:47-78`：`Flow::init` 的 env/context/nodes/flow_info 顺序。
- `src/strategy/flow.cpp:80-150`：`init_context` 三路 creator 加载。
- `src/strategy/flow.cpp:152-274`：`init_nodes` 支持 start/end/cpu/tree/chain 五类节点。
- `src/strategy/flow.cpp:280-357`：`Flow::handle` 队列超时、pthread 切换、proctime 写入。
- `src/strategy/flow.cpp:371-472`：`init_flow_info` 用 `next_item` JSON 构建 `next_nodes` 与 `NodeInfo`。
- `src/strategy/node.cpp:32-67`：`Node::init/next_item` 解析与失败日志。
- `src/strategy/node.cpp:113-212`：`get_handler` 三路 lib/jit/so 加载。

> KU 正文未读取，需人工补充 fork/framework 在生产中真实启用的 flow 列表、注册 context 命名空间、handler 三路注册的优先级策略以及 `compute_non_io_cost_time` 在监控报警中的具体口径。