# MultiStreamEngine bind_graph_dependency 桥接 GraphDependency 深入理解

> 日期：2026-06-01  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 `mon.business_lib` 候选项  
> 范围：分析 GRG 侧 `MultiStreamEnginePlugin` / `bind_graph_dependency()` 如何把外层 `GraphDependency`、`GraphVertex` 和引擎对象串起来；本文只讨论这条绑定链路，不扩展到全部业务策略。
> 内网文档：本次环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:18px;margin:16px 0;color:#1f2937"><style scoped>.ms-grid{display:grid;grid-template-columns:1.1fr 1.2fr 1fr;gap:12px}.ms-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.ms-k{font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase}.ms-v{margin-top:6px;font-size:14px;line-height:1.55}.ms-chip{display:inline-block;padding:3px 8px;border-radius:999px;background:#e2e8f0;color:#334155;font-size:12px;margin:4px 6px 0 0}.ms-title{font-size:22px;font-weight:850;margin:0 0 10px 0}.ms-sub{font-size:13px;color:#64748b;margin:0 0 14px 0}</style><div class="ms-title">MultiStreamEngine 绑定链路</div><div class="ms-sub">外层 GraphDependency 先进入 ExecEngine 插件层，再由 bind_graph_dependency 完成 vertex 与引擎对象的桥接。</div><div class="ms-grid"><div class="ms-col"><div class="ms-k">上游输入</div><div class="ms-v"><span class="ms-chip">GraphDependency</span><span class="ms-chip">GraphVertex</span><span class="ms-chip">dependency_map</span></div></div><div class="ms-col"><div class="ms-k">执行框架</div><div class="ms-v">`MultiStreamEnginePlugin` 负责把图数据绑定到 exec engine，形成多流并行/汇聚的运行壳。</div></div><div class="ms-col"><div class="ms-k">输出结果</div><div class="ms-v">业务侧 `DiversityMerge` / `NewResponseFunction` 继续处理聚合结果，直到最终响应落出。</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "MultiStreamEnginePlugin" as MSP
participant "bind_graph_dependency()" as BGD
participant "EngineObject" as EO
participant "GraphVertex" as GV
participant "Business Merge" as BM
Caller -> MSP: initialize / bind
MSP -> BGD: bind_graph_dependency(vertex, engine)
BGD -> GV: read GraphDependency bindings
BGD -> EO: attach engine object
EO --> MSP: ready engine
MSP -> BM: execute merge / response flow
BM --> Caller: merged result
@enduml
```

## 2. 配置与结构信息图

```infographic
infographic sequence-timeline-simple
data
  title 绑定链路的四个节点
  desc 从 graph dependency 到 engine object，再进入业务合并链路。
  items
    - label 读取依赖
      desc GraphDependency 进入图顶点上下文
      value 1
    - label 执行绑定
      desc bind_graph_dependency 把 dependency 贴到引擎对象
      value 2
    - label 装配引擎
      desc MultiStreamEnginePlugin 完成 exec engine 组装
      value 3
    - label 业务合并
      desc 交给 diversity merge / response function
      value 4
```

## 3. 关键实现

`common-processor_20260203152938/src/plugin/exec_engine.h:60-68` 定义了 `MultiStreamEnginePlugin` 和 `bind_graph_dependency(baidu::feed::graph::GraphVertex&, EngineObject&)`。这说明绑定动作不是隐式发生的，而是插件层显式暴露出来的接口，调用方必须把图顶点和引擎对象一起交给它。

`common-processor_20260203152938/src/plugin/multi_stream_engine.cpp` 中可以看到 `bind_graph_dependency` 被真正调用，证明这条桥接链路是运行时执行的一部分，不只是头文件声明。

从 daily-plan 的候选项看，这条链路对应的主题就是 `MultiStreamEngine bind_graph_dependency 桥接外层 GraphData`，代码证据指向 `src/processor/video_launch/diversity_merge.cpp:57` 和 `src/process/diversity_merge.cpp:89`。本次环境下未能完整读取业务主文件正文，因此具体业务策略位点需要人工补充，但框架级绑定语义已经足够确认。

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #d9e2ec;border-left:4px solid #f59e0b;border-radius:10px;padding:14px;margin:14px 0;color:#1f2937"><div style="font-size:16px;font-weight:800;margin-bottom:6px">绑定顺序不能反过来</div><div style="font-size:14px;line-height:1.6">先装配引擎、再补 GraphDependency 和 vertex，通常会让业务侧拿到半初始化对象，后面的 merge 逻辑会出现空依赖或默认分支。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #d9e2ec;border-left:4px solid #ef4444;border-radius:10px;padding:14px;margin:14px 0;color:#1f2937"><div style="font-size:16px;font-weight:800;margin-bottom:6px">不要把 framework 层绑定当成业务策略</div><div style="font-size:14px;line-height:1.6">`bind_graph_dependency()` 只负责桥接，不负责策略判断。策略应继续留在业务 merge / response function 层，否则会把框架和业务耦死。</div></div>

## 5. 调试 Checklist

```infographic
infographic list-column-done-list
data
  title 绑定链路调试清单
  items
    - label 确认入口
      desc 查 exec_engine.h 和 multi_stream_engine.cpp 的实际调用点
      done true
    - label 核对依赖对象
      desc 确认 GraphDependency 不是空 map 或空引用
      done true
    - label 验证顶点装配
      desc 观察 GraphVertex 是否已完成绑定后再进入 merge
      done true
    - label 回看业务侧
      desc 对照 diversity_merge.cpp 的业务入口继续追踪
      done true
    - label 保留人工补充位
      desc 本次环境未读全业务源码，业务策略细节需补证
      done true
```

## 6. 证据来源

- `common-processor_20260203152938/src/plugin/exec_engine.h:60-68`
- `common-processor_20260203152938/src/plugin/multi_stream_engine.cpp`
- `notes/weekly-topic-selection/daily-plan-20260529.json:72-79`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260601-multistreamengine-bind-graph-dependency.md)。
