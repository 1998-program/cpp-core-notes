# lite-grc Recall GraphSchedule 生命周期：入口、GraphData 与 recall_status

> 2026-08-11 运行说明：未发现当日 daily-plan，按历史未覆盖主题 fallback 到 lite-grc 召回调度链。KU/业务背景需人工补充；本文只使用本地代码与配置证据。

<style>.arch{font-family:Inter,Arial,sans-serif;border:1px solid #d8dee9;border-radius:8px;background:#f8fafc;padding:18px;margin:12px 0}.arch h3{margin:0 0 12px;color:#1f2937}.arch-row{display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:10px}.arch-layer{border:1px solid #cbd5e1;background:#fff;border-radius:6px;padding:10px}.arch-layer b{display:block;color:#334155;margin-bottom:8px}.arch-box{border-left:4px solid #3d5a80;background:#eef4f8;margin:6px 0;padding:8px;border-radius:4px;font-size:13px}.arch-flow{font-size:12px;color:#64748b;margin-top:10px}.infocard{font-family:Inter,Arial,sans-serif;background:#fff;border:1px solid #d6d3d1;border-radius:8px;padding:16px;margin:16px 0;color:#292524}.infocard .k{font-size:12px;font-weight:700;color:#3d5a80;text-transform:uppercase}.infocard h3{margin:6px 0 10px;font-size:20px}.infocard ul{margin:8px 0 0 18px;padding:0}.infocard li{margin:6px 0}</style>
<div class="arch"><h3>架构全景：从进程启动到召回状态闭环</h3><div class="arch-row"><div class="arch-layer"><b>启动层</b><div class="arch-box">main.cc 初始化 GlobalInitializer</div><div class="arch-box">ExecEngineInitializer 装载执行引擎</div><div class="arch-box">RouteEngine 完成路由入口</div></div><div class="arch-layer"><b>GraphSchedule 层</b><div class="arch-box">RecallProcessor 声明 recall_node_status</div><div class="arch-box">RecallDataProcessor 发出 recall_status</div><div class="arch-box">Babylon 注册 GraphProcessor 组件</div></div><div class="arch-layer"><b>new_recall_framework</b><div class="arch-box">fast/pass/slow 图按配置展开</div><div class="arch-box">RecallFinalProcessor 汇总分支结果</div><div class="arch-box">recall_status 作为图级完成信号</div></div></div><div class="arch-flow">数据方向：请求上下文 -> GraphData 依赖声明 -> processor process() -> emit recall_status -> 后续 response/merge 节点</div></div>

## 核心结论

lite-grc 的召回基础库不是单个函数调用链，而是“入口初始化 + Babylon 组件注册 + 配置化 vertex 绑定”的组合。`main.cc:75-80` 负责把全局组件、ExecEngine 和 RouteEngine 拉起来；`recall_processor.cpp:37-64` 与 `recall_data_processor.cpp:37-59` 分别把召回节点状态和召回完成状态映射成 GraphData 输入/输出。

`recall_status` 是这条链最值得跟踪的信号：GraphSchedule 中的 processor 通过 `emit<bool>()` 写出状态，new_recall_framework 的 fast/pass/slow final node 又在配置里统一 `emit: recall_status`，形成“代码声明 + 配置约束”的双重契约。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Request
participant "main.cc" as Main
participant "ExecEngineInitializer" as Exec
participant "GraphSchedule" as Graph
participant "RecallProcessor" as RP
participant "RecallDataProcessor" as RDP
participant "RecallFinalProcessor" as RFP
Request -> Main: service startup
Main -> Exec: init()
Exec -> Graph: load graph configs
Graph -> RP: setup/process
RP -> Graph: recall_node_status
Graph -> RDP: setup/process
RDP -> Graph: emit recall_status
Graph -> RFP: final fast/pass/slow branches
RFP -> Graph: emit recall_status
@enduml
```

## 配置结构

```infographic
infographic hierarchy-structure
data
  title lite-grc 召回图配置骨架
  desc 入口配置负责串联 GraphSchedule 与 new_recall_framework 分支
  items
    - label graph_schedule.conf
      desc 定义 GraphSchedule 顶层 vertex 和 processor 绑定
      icon mdi/source-branch
      children
        - label RecallProcessor
          desc 声明 recall_node_status 输入
        - label RecallDataProcessor
          desc 写出 recall_status
    - label recall_fast_graph.conf
      desc fast 分支 fork/merge/final 节点
      icon mdi/flash
      children
        - label FastForkFinalNode
          desc RecallFinalProcessor + emit recall_status
    - label recall_pass_graph.conf
      desc pass 分支召回结果收敛
      icon mdi/check-network
    - label recall_slow_graph.conf
      desc slow 分支依赖更多外部/历史召回数据
      icon mdi/timer-outline
theme
  palette #3d5a80 #2d6a4f #b7791f
```

## 代码链路拆解

1. 进程入口：`main.cc:75-80` 依次初始化全局组件、ExecEngine 和 RouteEngine。这里决定后续图配置能否被加载，而不是具体执行业务召回。
2. 处理器声明：`recall_processor.cpp:37-64` 在 `setup()` 中声明 GraphData，在 `process()` 中写状态布尔值，最后通过 `BABYLON_REGISTER_COMPONENT_WITH_TYPE_NAME` 注册为 `GraphProcessor`。
3. 状态发射：`recall_data_processor.cpp:37-59` 使用相同模式读取/写入上下文，`emit<bool>()` 让 `recall_status` 成为图调度层的显式信号。
4. 分支收敛：`recall_fast_graph.conf:721-729`、`recall_pass_graph.conf`、`recall_slow_graph.conf` 通过 `RecallFinalProcessor` 将不同召回队列回填到统一完成状态。

<div class="infocard"><div class="k">Pitfalls</div><h3>调试召回链路时不要只看 C++ 类</h3><ul><li>类注册名必须和配置里的 processor 名一致，否则代码存在也不会进入图。</li><li>GraphData 的 `declare` 与配置里的 `@named_depend`/`emit` 要一起看，单看一侧容易误判状态来源。</li><li>fast/pass/slow 分支共享 `recall_status` 名称，排查时要先确认是哪一个 final node 发出的信号。</li></ul></div>

```infographic
infographic list-column-done-list
data
  title 调试 Checklist
  desc 从启动、配置、注册名到状态信号逐层缩小范围
  items
    - label 检查 main.cc 初始化顺序
      desc ExecEngineInitializer 与 RouteEngine 初始化失败会阻断后续图执行
      done true
    - label 核对 processor 注册名
      desc BABYLON_REGISTER_COMPONENT_WITH_TYPE_NAME 名称需匹配配置 processor
      done true
    - label 跟踪 GraphData 名称
      desc declare、@named_depend、emit 必须使用同一个语义字段
      done true
    - label 区分 fast/pass/slow 分支
      desc final node 名称不同但可能 emit 相同 recall_status
      done true
theme
  palette #3d5a80 #2d6a4f #b7791f
```

## 证据来源

- `src/main.cc:75-80`：GlobalInitializer、ExecEngineInitializer、RouteEngine 初始化顺序。
- `src/graph_schedule/recall_processor.cpp:37-64`：RecallProcessor 的 setup/process 与组件注册。
- `src/graph_schedule/recall_data_processor.cpp:37-59`：RecallDataProcessor 写出 `recall_status`。
- `src/recall_framework/processor/recall_final_processor.cpp`：召回分支 final processor。
- `conf/plugins/graph/graph_schedule.conf`：GraphSchedule 顶层 vertex 配置。
- `conf/plugins/graph/new_recall_framework/recall_fast_graph.conf:721-729`：FastForkFinalNode 使用 RecallFinalProcessor 并 `emit: recall_status`。
- `conf/plugins/graph/new_recall_framework/recall_pass_graph.conf`：pass 分支配置。
- `conf/plugins/graph/new_recall_framework/recall_slow_graph.conf`：slow 分支配置。