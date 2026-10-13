# 2026-09-06 周日基础库理解：GRC 服务入口、动态超时注入与 GraphPool reset 生命周期

> 日期：2026-09-06  
> 主题来源：当前可用 daily-plan 仍停留在旧的周计划，没有 2026-09-06 当日计划；按历史候选回退到 `DynamicTimeOutPlugin 动态超时注入与场景映射` + `GraphEngine GraphPool 对象池复用与 reset 生命周期`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/service/grc_service.cpp`、`src/service/grg_service.cpp`，聚焦入口校验、GraphEngine 取图、动态超时写入、图上下文填充与 `reset()` 归还。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">入口层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/service/grc_service.cpp:151-224` / `src/service/grg_service.cpp:35-94`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先做 controller 校验、session 初始化和 graph_name 决策，再进入图执行。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">运行层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`GraphEngine::try_get()` + `set_request_timeout()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">从对象池拿图，写入动态超时控制器，再把请求级上下文塞入 Graph。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">回收层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`graph->run()` 后 `graph->reset()` / `pooled_graph->reset()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">响应复制完成后归还对象池，避免下一次请求继承脏状态。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">RpcController</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">GraphPool</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">Graph Data</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">Response CopyBack</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRC service bootstrap and GraphPool reset boundary
participant "RpcController" as CNTL
participant "GenericGRCService::query" as Q
participant "GraphEngine" as GE
participant "GraphPool" as POOL
participant "DynamicTimeOutPlugin" as DT
participant "Graph" as G
participant "ReusableRPCProtocol::Closure" as C
participant "GRCResponse" as RESP
CNTL -> Q : validate cntl / init session context
Q -> GE : get graph_engine
Q -> POOL : try_get(graph_name)
POOL --> Q : pooled_graph
Q -> DT : get_dt_controller()
Q -> DT : set_request_timeout(scene, timeout)
Q -> G : emit_common_data()
Q -> G : preset ResponseForGrg / ResData
Q -> G : run(end node)
G --> Q : closure result
Q -> RESP : CopyFrom(response_p)
Q -> C : send_response()
Q -> G : reset()
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title GRC 入口链上的 6 个关键点
  desc 这些点决定了图实例是否能正确复用、超时是否能下传，以及响应是否能安全回收
  items
    - label controller 校验
      desc `src/service/grc_service.cpp:156-170` 先检查 `cntl->Failed()` 和 `sctx.init()`
      icon mdi-shield-check
    - label graph_name 选择
      desc `src/service/grc_service.cpp:184-198` 根据 UA 选择 `news_updates_dibar` / `interest_card` / `default`
      icon mdi-sitemap
    - label 图池取图
      desc `src/service/grc_service.cpp:199-203` 从 `GraphEngine::try_get()` 取出 `pooled_graph`
      icon mdi-database-arrow-down
    - label 动态超时
      desc `src/service/grc_service.cpp:233-266` 读取上游超时并写入 `DTController`
      icon mdi-timer-outline
    - label 上下文填充
      desc `src/service/grc_service.cpp:267-291` 注入 logid、product、ua、response 占位数据
      icon mdi-table-arrow-right
    - label 图回收
      desc `src/service/grc_service.cpp:320-345` 复制响应后发送并等待 closure，随后由 `graph->reset()` 归还
      icon mdi-recycle
```

## 3. 代码链路拆解
### 3.1 入口层的职责很窄，但每一步都决定后续是否能继续跑
- `src/service/grc_service.cpp:156-170`：先检查 `cntl` 和 `GRCSessionContext`，失败就立即 `set_error()`，这让错误路径早返回，避免把坏请求带进图执行。
- `src/service/grc_service.cpp:172-179`：`ReqExtractPlugin` 先抽取 `ua`、`product`、`channel_id`，说明图外配置先于图内逻辑准备好。
- `src/service/grc_service.cpp:184-203`：UA 决定 `graph_name`，随后通过 `GraphEngine::try_get(graph_name)` 获取对象池图实例。这里的模式是“按场景复用图模板”，不是每次现建。

### 3.2 动态超时是图执行前的硬边界，不是附属日志
- `src/service/grc_service.cpp:233-266`：`get_dt_controller()` 失败就直接返回，随后读取 `cntl->timeout_ms()`、请求里的 `dynamic_timeout`，最后兜底到 700ms。这个优先级说明超时来源是分层的：上游显式值优先，图默认值兜底。
- `src/service/grg_service.cpp:178-195`：GRG 侧同样写入 `DTController`，并把 `news_updates_dibar` 映射为 `short_micro_video`。这表明场景名是逻辑标签，不一定等于图名。
- `src/service/grc_service.cpp:267-271` 与 `src/service/grg_service.cpp:196-200`：把 `timeout_cntl` 挂进 `MutableFrameworkContext`，说明后续 vertex 运行拿到的是统一的框架上下文，而不是每个节点单独处理控制器。

### 3.3 reset 是对象池契约的一部分
- `src/service/grc_service.cpp:328-345`：`run()` 完成后把图里的 response 拷回 session response，再通过 `ReusableRPCProtocol::Closure::send_response()` 发出。`closure.wait()` 之后才进入日志和收尾，这个顺序确保响应已经出栈。
- `src/service/grc_service.cpp:220-221`：`graph->reset()` 明确出现在 `query()` 尾部，说明图对象会被回收到池中再次使用。
- `src/service/grg_service.cpp:89-91`：GRG 侧也在 `run()` 结束后 `pooled_graph->reset()`，两边实现一致，契约就是“run 后必须清理再归还”。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #3d5a80;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#3d5a80;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">图池复用最怕“拿到了图，却把脏状态一起带回下一次请求”</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`try_get()` 成功不代表可直接使用。必须确认 `dynamic_timeout_cntl`、`REQ_INFO`、`response` 占位都已填好，否则图里某个节点会拿到空上下文或旧上下文。</div><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:8px;padding:12px;font-size:14px;">`reset()` 的位置不能前移。响应复制和 `send_response()` 还没结束时就回收图，会把后续异步链路一起打断。</div></div><div style="margin-top:10px;font-weight:900;color:#3d5a80;">∎ 排查顺序：cntl / session init → graph_name → try_get → dt timeout → run → CopyFrom → send_response → reset</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GRC 入口链排查清单
  desc 适用于图池为空、超时未下传、响应没回、reset 后状态污染和场景映射错误
  items
    - label 检查 controller
      desc 确认 `cntl->Failed()` 和 `sctx.init()` 没有提前返回
      done true
    - label 检查图名
      desc UA 到 `graph_name` 的映射要和图配置一致
      done true
    - label 检查图池
      desc `GraphEngine::try_get()` 必须拿到非空 `pooled_graph`
      done true
    - label 检查超时
      desc `timeout_ms()`、请求字段和默认值的优先级要一致
      done true
    - label 检查上下文
      desc `MutableFrameworkContext::timeout_cntl` 要在 run 前写入
      done true
    - label 检查回收顺序
      desc 先 `CopyFrom` 和 `send_response()`，再 `reset()` 归还图对象
      done true
```

## 6. 证据来源
- `src/service/grc_service.cpp:151-224`
- `src/service/grc_service.cpp:226-345`
- `src/service/grg_service.cpp:35-94`
- `src/service/grg_service.cpp:121-200`
- `src/service/grg_service.cpp:203-219`

## 7. 说明
当前运行环境没有 2026-09-06 的 daily-plan 文件；本笔记基于历史候选与本地代码回退生成，KU 正文未读取，业务背景需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260906-grc-service-bootstrap-dynamic-timeout-graphpool-reset.md)。
