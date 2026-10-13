# 2026-08-16 周日代码理解：feeda-pcs 启动引导与请求分发边界

> 日期：2026-08-16  
> 主题来源：2026-06-01 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 `feeda-pcs` 的启动引导 + 请求分发链路；KU/业务背景需人工补充。  
> 范围：只分析 `main.cpp`、`common_base/service/dispatch.cpp`、`common_base/processor/request_processor.cpp`，关注服务启动、请求解析、图依赖注入与基础特征透传，不展开完整业务策略。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937">
  <div style="font-size:20px;font-weight:800;margin-bottom:10px;">feeda-pcs 启动与分发全景</div>
  <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">1. main.cpp</div>
      <div style="font-size:13px;line-height:1.5;">完成全局初始化、RPC 服务注册、配置装载与进程启动。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">2. Dispatch</div>
      <div style="font-size:13px;line-height:1.5;">解析 `PcsRequest`，补齐 `cuid`、实验参数与 sid 相关上下文。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">3. RequestContext</div>
      <div style="font-size:13px;line-height:1.5;">把 request、common_info、refresh_info、feature 节点挂到 Graph 依赖上。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">4. AOPProcessor</div>
      <div style="font-size:13px;line-height:1.5;">`RequestProcessor` 负责把 protobuf 字段写入 `intelligence_feature_info`。</div>
    </div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:#334155;">
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">RPC 入口</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">sid 缓存</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">GraphDependency</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">特征透传</span>
  </div>
</div>

## 1. 入口链路
`main.cpp` 的角色是把进程级依赖先装好，再把 brpc 服务拉起来。文件头部直接引入 `pcs_service`、`global_initializer`、`reusable_rpc_protocol`、`async_file_appender`、`server`、`gflags` 和 `exp_manager`，说明它不是纯 launcher，而是把日志、实验参数和 RPC 框架一起编排起来。证据见 `main.cpp:1-16`。

`Dispatch::parser_request()` 是请求进入业务图之前的第一道整理层。它先检查 `common_info` 是否存在，然后把 `cuid` 写回上下文，并读取 `need_sample_data`。微视频分支里还会先加载 `exp_manager`，这意味着实验参数比后续 processor 更早进入图上下文。证据见 `common_base/service/dispatch.cpp:36-46`。

## 2. 调用链时序图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "main.cpp" as Main
participant "PcsServiceImpl" as Svc
participant "Dispatch" as D
participant "GraphDependency" as G
participant "RequestProcessor" as RP
Client -> Main : start process
Main -> Svc : register service / init runtime
Client -> Svc : RPC request
Svc -> D : parser_request(request)
D -> D : check common_info
D -> D : set_cuid / need_sample_data
D -> G : bind request / graph data
G -> RP : AOPProcessor::run()
RP -> G : named_emit("IntelligenceFeatureInfo")
RP -> G : write floats from request
@enduml
```

## 3. 请求上下文结构信息图
```infographic
infographic hierarchy-tree-tech-style-capsule-item
data
  title RequestContext and graph dependencies
  desc Dispatch builds a small request context, then AOP processors read/write graph nodes.
  items
    - label request
      desc GraphDependency* input request
      children
        - label common_info
          desc Required for cuid / product / log_id / experiment parsing
        - label is_trigger_all
          desc GraphData used by downstream toggles
        - label refresh_info
          desc GraphData for refresh path
        - label intelligence_feature_info
          desc GraphData carrying float feature vector
    - label parser_request
      desc Validates PcsRequest and prepares runtime state
    - label sample_data flag
      desc Drives sampling behavior early in dispatch
theme
  palette #3b82f6 #64748b #0f766e
```

## 4. 关键实现细节
`Dispatch` 里最值得注意的是 sid 缓存和实验参数加载这两类前置动作。代码显式写了“sid cache实验，缓存sid解析结果”，并用 `SidCacheKey(product, log_id, cuid)` 去命中缓存；这意味着 `cuid`、`log_id` 和 `product` 共同决定了 sid 解析复用。证据见 `common_base/service/dispatch.cpp:64-68`。

`RequestProcessor` 则很薄，它只做一个明确的事情：把 `request_p->intelligence_feature_info()` 的 float 列表写入 `custom_context->intelligence_feature_info`。这条路径说明基础库层更像图节点装配器，而不是策略决策器。证据见 `common_base/processor/request_processor.cpp:21-26`、`common_base/processor/request_processor.cpp:35-61`。

## 5. Pitfalls
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0;">
  <div style="border:1px solid #cbd5e1;border-left:4px solid #f59e0b;border-radius:10px;background:#fff;padding:12px;">
    <div style="font-weight:800;margin-bottom:6px;">上下文缺失会直接短路</div>
    <div style="font-size:13px;line-height:1.6;">`common_info` 为空时 `Dispatch::parser_request()` 直接返回 -1，后续任何图节点装配都不会发生。</div>
  </div>
  <div style="border:1px solid #cbd5e1;border-left:4px solid #ef4444;border-radius:10px;background:#fff;padding:12px;">
    <div style="font-weight:800;margin-bottom:6px;">透传字段不是自动填充</div>
    <div style="font-size:13px;line-height:1.6;">`intelligence_feature_info` 只有在 `RequestProcessor` 显式 `named_emit` 后才可见，调试时不要默认它一定存在。</div>
  </div>
</div>

## 6. 调试 Checklist
```infographic
infographic list-column-done-list
data
  title Debug checklist
  items
    - label Confirm main.cpp initializes RPC, logging, and exp manager
      desc Check startup includes and service bootstrap path
      done true
    - label Verify Dispatch rejects requests without common_info
      desc Inspect parser_request early return branch
      done true
    - label Trace sid cache key composition
      desc product + log_id + cuid decide cache reuse
      done true
    - label Check RequestProcessor writes intelligence_feature_info
      desc Ensure float vector is emitted into graph data
      done true
    - label Keep all evidence paths relative
      desc Use src/... or conf/... paths only
      done true
theme
  palette #16a34a #2563eb #64748b
```

## 7. 证据来源
- `src/main.cpp:1-16`
- `common_base/service/dispatch.cpp:36-46`
- `common_base/service/dispatch.cpp:64-68`
- `common_base/processor/request_processor.cpp:21-26`
- `common_base/processor/request_processor.cpp:35-61`

## 8. 结论
`feeda-pcs` 的基础层边界很清晰：`main.cpp` 管启动，`Dispatch` 管请求上下文整理和 sid/实验前置，`RequestProcessor` 只负责把 protobuf 字段落到图里。基础库真正的职责不是做业务决策，而是把后续 processor 可以依赖的数据形态稳定地准备出来。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260816-feeda-pcs-bootstrap-and-request-dispatch.md)。
