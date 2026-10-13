# 2026-08-07 周五代码理解：GRC `ResponseForGrg` 响应出口契约

> 日期：2026-08-07  
> 主题来源：2026-08-07 daily-plan 缺失，按相邻未覆盖主题 fallback 到 GRC 查询入口与 `ResponseForGrg` 响应出口契约  
> 服务：`feeda-mv-grc`  
> 范围：分析 brpc 查询入口、GraphEngine 执行边界、`ResponseForGrgFunction` 的响应产出与 graph 配置绑定；本文不展开 History Service `gr_state` 存储与业务枚举。  
> 内网文档：当前 cron 环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.grc-arch{display:grid;grid-template-columns:1fr;gap:12px}.grc-layer{border:1px solid #d5dee8;border-radius:10px;background:#fff;padding:12px}.grc-title{font-size:14px;font-weight:800;color:#334155;margin-bottom:8px}.grc-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.grc-box{border:1px solid #cbd5e1;border-left:4px solid #3d5a80;border-radius:8px;background:#f8fafc;padding:10px;min-height:62px}.grc-box strong{display:block;font-size:13px;color:#1f2937;margin-bottom:4px}.grc-box span{font-size:12px;line-height:1.45;color:#475569}.grc-flow{font-size:12px;color:#64748b;margin-top:8px}.grc-warn{border-left-color:#b45309;background:#fff7ed}.grc-ok{border-left-color:#2d6a4f;background:#f4f8f6}@media(max-width:760px){.grc-grid{grid-template-columns:1fr}}</style><div class="grc-arch"><div class="grc-layer"><div class="grc-title">入口层：brpc 服务注册</div><div class="grc-grid"><div class="grc-box"><strong>GenericGRCService</strong><span>`main.cpp` 创建 GRC service 并注册到 brpc server。</span></div><div class="grc-box"><strong>GrcHttpServiceImpl</strong><span>辅助暴露 `/graph_view`，用于观察 graph 结构。</span></div><div class="grc-box grc-ok"><strong>baidu_std_reuse</strong><span>服务协议复用，减少连接层开销。</span></div><div class="grc-box"><strong>ExpManager</strong><span>启动阶段加载 `conf` 下实验参数。</span></div></div></div><div class="grc-layer"><div class="grc-title">执行层：请求上下文与 GraphEngine</div><div class="grc-grid"><div class="grc-box"><strong>SessionContext</strong><span>承载 request、response、sid、日志和中间状态。</span></div><div class="grc-box"><strong>DynamicTimeOutPlugin</strong><span>构造函数从 ApplicationContext 获取，用于查询预算控制。</span></div><div class="grc-box"><strong>GraphEngine</strong><span>`query()` 进入图执行，按 vertex 依赖推进。</span></div><div class="grc-box grc-warn"><strong>Failure Path</strong><span>异常、超时、graph 失败需要落到统一错误响应。</span></div></div></div><div class="grc-layer"><div class="grc-title">响应层：`ResponseForGrg` 出口</div><div class="grc-grid"><div class="grc-box"><strong>ResponseForGrgFunction</strong><span>video_launch 场景下的最终响应组装函数。</span></div><div class="grc-box"><strong>Effect Queue</strong><span>从候选、融合、截断后的结果队列读取。</span></div><div class="grc-box"><strong>Response Extensions</strong><span>补充 ext、策略标记、调试与透传字段。</span></div><div class="grc-box grc-ok"><strong>GRCResponse</strong><span>作为 RPC 返回体交回上游 GRG 或调用方。</span></div></div><div class="grc-flow">关键边界：入口只负责接收请求和调图，`ResponseForGrg` 是把图内候选结果转成 RPC response 的最后契约点。</div></div></div></div>

---

## 1. 入口链路

`main.cpp` 的启动逻辑先初始化插件、全局组件和实验参数，然后创建 `GenericGRCService` 并注册到 brpc server。这里确定了服务的外部身份：请求不是直接调用某个 processor，而是先进入 RPC service，再由 service 组织上下文和 graph 执行。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller as caller
participant "brpc Server" as server
participant "GenericGRCService" as service
participant "SessionContext" as ctx
participant "GraphEngine" as graph
participant "ResponseForGrgFunction" as resp
caller -> server: query(request)
server -> service: dispatch GenericGRCService::query
service -> ctx: build request-scoped context
service -> graph: run configured graph

graph -> resp: trigger ResponseForGrg vertex
resp -> ctx: fill response fields / ext / queues
ctx --> service: finalized GRCResponse
service --> caller: RPC response
@enduml
```

证据：

- `main.cpp:104-118`：创建 `baidu::rpc::Server`，实例化 `GenericGRCService`，注册 service，同时暴露 `/graph_view`。
- `service/grc_service.cpp:51-59`：`GenericGRCService` 构造期从 `ApplicationContext` 获取 `DynamicTimeOutPlugin`。
- `service/grc_service.cpp:152-205`：`GenericGRCService::query()` 是请求入口，负责 RPC controller、request、response 的主流程。
- `service/grc_service.cpp:284-284`：通过 `graph->find_data("ResponseForGrg")` 找响应出口数据，说明 graph 内部把最终响应边界显式命名为 `ResponseForGrg`。

---

## 2. `ResponseForGrg` 的职责边界

`ResponseForGrgFunction` 注册在 `processor/video_launch/response_for_grg.cpp` 中，类名已经暴露了它的定位：不是召回、不是排序、不是存储，而是 video_launch 链路在 GRC 内把图执行结果整理为返回给 GRG 的 response。

```infographic sequence-timeline-simple
data
  title GRC 查询到响应出口的责任切分
  desc 入口、执行、响应三个阶段各自只承担一类职责
  items
    - time 1
      label Service Entry
      desc brpc dispatch 到 GenericGRCService::query，建立请求上下文
      icon mdi/lan-connect
    - time 2
      label Graph Runtime
      desc GraphEngine 根据 vertex.conf 推进依赖和 processor 执行
      icon mdi/graph-outline
    - time 3
      label Candidate Closure
      desc 上游召回、融合、set2set、截断等节点产出可返回队列
      icon mdi/source-branch
    - time 4
      label ResponseForGrg
      desc 汇总队列、ext、策略透传字段，形成 GRCResponse
      icon mdi/package-variant-closed
```

核心判断：

- `ResponseForGrgFunction` 之前的节点负责“算什么候选、怎样融合、怎样截断”。
- `ResponseForGrgFunction` 负责“哪些结果和字段进入 response”。
- 因此排查“上游 GRG 收不到字段、候选数量和预期不一致、ext 透传缺失”时，不能只看召回或融合节点，必须检查这个响应出口是否执行、是否被配置依赖触发、是否命中了截断和字段填充分支。

---

## 3. 配置与代码绑定

`ResponseForGrg` 的风险点在于它既是代码函数，又是 graph data/vertex 命名约定。代码侧注册函数，配置侧通过 graph 依赖把它放在正确位置；任一侧名字漂移都会变成“服务正常返回但内容不符合预期”的问题。

```infographic compare-binary-horizontal-underline-text-vs
data
  title 代码注册 vs Graph 配置
  desc 响应出口需要两边同时对齐
  leftTitle Code Registration
  rightTitle Graph Binding
  items
    - label 注册点
      desc `REGISTER_GRC_FUNCTION(ResponseForGrgFunction)` 让框架可实例化该函数
    - label 数据点
      desc `ResponseForGrg` 作为 graph data 名称被 service 查找
    - label 依赖点
      desc `vertex.conf` 控制哪些前置结果进入响应出口
    - label 排查点
      desc 函数存在不等于链路触发，必须同时看 vertex 依赖
```

证据：

- `processor/video_launch/response_for_grg.cpp:102-102`：定义 `ResponseForGrgFunction`。
- `processor/video_launch/response_for_grg.cpp:506-515`：命中 `set2set_truncate_in_response_for_grg` 时会在响应出口阶段截断 effect queue，并发布截断后的队列。
- `processor/video_launch/response_for_grg.cpp:8790-8830`：文件末尾注册 `ResponseForGrgFunction`。
- `conf/plugins/graph/vertex.conf:2634-2638`：存在 `_user_response`、`_request_response` 等响应相关 graph data；响应出口依赖这些 data 名称组织最终返回。

---

## 4. 调试 Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fafaf8;border:1px solid #ddd6c8;border-radius:12px;padding:16px;margin:16px 0;color:#333"><style>.pit-grid{display:grid;grid-template-columns:1.3fr 1fr;gap:12px}.pit-main{background:#fff;border-left:5px solid #b45309;border-radius:8px;padding:14px}.pit-side{display:grid;gap:10px}.pit-card{background:#fff;border:1px solid #e5dfd5;border-radius:8px;padding:12px}.pit-meta{font-size:11px;font-weight:800;color:#7c6853;text-transform:uppercase;letter-spacing:.04em}.pit-title{font-size:22px;font-weight:850;color:#1f2937;margin:4px 0 8px}.pit-body{font-size:13px;line-height:1.65;color:#4b5563}.pit-card strong{display:block;font-size:13px;color:#1f2937;margin-bottom:4px}@media(max-width:760px){.pit-grid{grid-template-columns:1fr}}</style><div class="pit-grid"><div class="pit-main"><div class="pit-meta">Pitfall</div><div class="pit-title">把响应问题误判成召回问题</div><div class="pit-body">候选召回成功并不代表最终 response 正确。`ResponseForGrgFunction` 仍可能因为 graph 依赖、实验分支、截断逻辑或 ext 填充分支，把上游结果改写成另一个形态。排查线上字段缺失时，应先确认响应出口是否执行以及输出队列大小。</div></div><div class="pit-side"><div class="pit-card"><strong>名字漂移</strong><div class="pit-body">`ResponseForGrgFunction`、`ResponseForGrg` graph data 和配置中的响应 data 名称需要共同检查。</div></div><div class="pit-card"><strong>截断位置</strong><div class="pit-body">`set2set_truncate_in_response_for_grg` 在响应出口阶段生效，问题表现可能像融合结果减少。</div></div><div class="pit-card"><strong>观测入口</strong><div class="pit-body">`/graph_view` 能帮助确认 graph 拓扑，但仍需结合代码分支和实验参数判断实际执行路径。</div></div></div></div></div>

---

## 5. 调试 Checklist

```infographic list-column-done-list
data
  title ResponseForGrg 排查清单
  desc 从入口、图配置、函数执行、响应字段四层验证
  items
    - label 确认服务入口
      desc 查看 brpc 是否注册 GenericGRCService，query 是否收到请求
      done true
      icon mdi/check-network
    - label 确认 graph 数据名
      desc 检查 service 是否查找 `ResponseForGrg`，配置中响应 data 是否一致
      done true
      icon mdi/graph
    - label 确认函数注册
      desc 检查 `REGISTER_GRC_FUNCTION(ResponseForGrgFunction)` 是否存在且加载到插件体系
      done true
      icon mdi/function-variant
    - label 确认截断实验
      desc 检查 `set2set_truncate_in_response_for_grg` 是否命中，比较截断前后队列大小
      done false
      icon mdi/scissors-cutting
    - label 确认 ext 透传
      desc 对照 response 中缺失字段，定位 fill/ext 分支是否执行
      done false
      icon mdi/file-document-check-outline
```

---

## 6. 本次结论

`ResponseForGrg` 是 GRC 里从 graph 世界回到 RPC response 世界的关键出口。它的价值不只是“最后填 response”，而是把候选队列、截断策略、ext 透传和上游 GRG 协议连在一起。后续如果要做字段扩展或排查返回异常，应把 `GenericGRCService::query()`、`ResponseForGrg` graph data、`ResponseForGrgFunction` 注册点和 `vertex.conf` 响应 data 作为一组契约同时检查。

---

## 证据来源

- `main.cpp:104-118`：brpc server 创建、`GenericGRCService` 注册和 `/graph_view` 暴露。
- `service/grc_service.cpp:51-59`：GRC service 构造期获取动态超时插件。
- `service/grc_service.cpp:152-205`：RPC query 入口主流程。
- `service/grc_service.cpp:284-284`：查找 `ResponseForGrg` graph data。
- `processor/video_launch/response_for_grg.cpp:102-102`：`ResponseForGrgFunction` 定义。
- `processor/video_launch/response_for_grg.cpp:506-515`：响应出口阶段的 set2set 截断分支。
- `processor/video_launch/response_for_grg.cpp:8790-8830`：`ResponseForGrgFunction` 注册。
- `conf/plugins/graph/vertex.conf:2634-2638`：响应相关 graph data 配置。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260807-grc-response-for-grg-contract.md)。
