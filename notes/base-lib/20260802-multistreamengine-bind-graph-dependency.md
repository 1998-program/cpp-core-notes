# 2026-08-02 周日代码理解：MultiStreamEngine bind_graph_dependency 绑定外层 GraphDependency
<!-- topic-nav:start -->
> 主题：`base/multistream-binding` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260529-exec-engine-multistreamengine-graphdependency-deep-dive.md)
<!-- topic-nav:end -->


> 日期：2026-08-02  
> 主题来源：2026-08-02 daily-plan 缺失，按历史未覆盖主题 fallback 到 `MultiStreamEngine` 依赖绑定链路  
> 范围：GRG 侧 `MultiStreamEnginePlugin` / `bind_graph_dependency()` 如何把外层 `GraphDependency`、`GraphVertex` 和引擎对象串起来；本文只讨论这条绑定链路，不扩展到全部业务策略。  
> 内网文档：当前环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:18px;margin:16px 0;color:#1f2937"><style scoped>.arch-wrap{display:grid;grid-template-columns:1.1fr 1.4fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.arch-col h3{margin:0 0 8px 0;font-size:14px;color:#102a43}.arch-box{border:1px solid #bcccdc;border-radius:8px;padding:10px;margin:8px 0;background:#fdfefe}.arch-box strong{display:block;margin-bottom:4px;color:#102a43}.arch-mid{display:grid;grid-template-rows:auto auto;gap:12px}.arch-flow{border:1px dashed #bcccdc;border-radius:10px;padding:12px;background:#fbfdff}</style><div class="arch-wrap"><div class="arch-col"><h3>输入层</h3><div class="arch-box"><strong>GraphDependency</strong>外层依赖对象，承载顶点、参数、插件配置</div><div class="arch-box"><strong>GraphVertex</strong>顶点级绑定点，提供图节点的运行上下文</div><div class="arch-box"><strong>MultiStreamEnginePlugin</strong>插件入口，负责把图配置装配成运行对象</div></div><div class="arch-mid"><div class="arch-flow"><div class="arch-box"><strong>绑定主链路</strong><br/>`bind_graph_dependency()` 把外层依赖挂到执行引擎侧，再通过顶点对象把图节点、依赖输入、运行时参数连成一条闭环。</div><div class="arch-box"><strong>运行时闭环</strong><br/>插件构图后，`ExecEngine` 按图内队列、函数和依赖关系执行；依赖缺失时，后续节点只能看到部分输入。</div></div><div class="arch-flow"><div class="arch-box"><strong>结果出口</strong><br/>图执行完成后，输出回写到引擎上下文，再交给上层合并或响应函数。</div></div></div><div class="arch-col"><h3>输出层</h3><div class="arch-box"><strong>GraphData / Runtime State</strong>绑定后的运行态数据，供后续节点直接读取</div><div class="arch-box"><strong>ExecEngine Graph</strong>完整执行图，包含 dependency / vertex / function 三层关系</div><div class="arch-box"><strong>Response Boundary</strong>上层仅消费执行结果，不再关心底层绑定细节</div></div></div></div>

## 1. 核心调用链

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "GRG Service" as S
participant "MultiStreamEnginePlugin" as P
participant "bind_graph_dependency()" as B
participant "GraphDependency" as D
participant "GraphVertex" as V
participant "ExecEngine" as E
participant "Graph Runtime" as R
S -> P : 读取 graph config / scene
P -> B : 传入外层依赖与顶点映射
B -> D : 绑定依赖输入
B -> V : 注入 vertex context
B -> E : 装配执行引擎对象
E -> R : 构建可执行图
R -> E : 运行节点并写回 state
E -> S : 返回结果与中间态
@enduml
```

## 2. 结构信息图

```infographic
infographic list-grid-badge-card
data
  title MultiStreamEngine 绑定链路拆解
  desc 这条链路的核心不是“执行”，而是把执行所需的上下文提前挂好。
  items
    - label GraphDependency
      desc 外层依赖容器，负责把图级输入集中交给引擎。
      value 1
    - label GraphVertex
      desc 节点级上下文，承接图内每个算子的运行状态。
      value 2
    - label bind_graph_dependency()
      desc 把外部依赖、顶点和引擎对象连成一条可执行路径。
      value 3
    - label ExecEngine
      desc 读取绑定结果，完成真正的调度和运行。
      value 4
    - label Response boundary
      desc 只看输出，不再回溯依赖装配过程。
      value 5
```

## 3. 关键理解

`bind_graph_dependency()` 的价值不在于“拷贝字段”，而在于把原本分散在配置、顶点和运行对象之间的信息，统一收束到一个可执行图里。这样做的直接好处是后续节点不需要重新解析外部配置，也不需要反向追溯调用方上下文。

另一个容易忽略的点是：`GraphVertex` 不是纯数据对象，它承担了“图节点身份 + 运行上下文”的双重角色。只要绑定阶段漏掉了这层上下文，后面看起来像是算子没生效，实际上是依赖图没有闭合。

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:14px;margin:16px 0;color:#7c2d12"><div style="font-size:15px;font-weight:800;margin-bottom:6px">常见坑</div><ul style="margin:0;padding-left:18px"><li>`bind_graph_dependency()` 只绑了顶点，没把外层依赖一并挂上，后续节点会出现“能跑但缺输入”的假正常。</li><li>图配置更新后没同步顶点上下文，表现通常是旧参数仍在生效。</li><li>把依赖装配和执行调度混在一起改，容易把问题定位拖成“引擎坏了”，实际只是绑定层缺字段。</li></ul></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 调试 checklist
  items
    - label 检查图配置是否真正进入 plugin
      done true
    - label 检查 GraphDependency 是否完整挂载
      done true
    - label 检查 GraphVertex 是否拿到运行上下文
      done true
    - label 检查 ExecEngine 是否看到绑定后的 state
      done true
    - label 检查输出是否回写到上层边界
      done true
```

## 6. 证据来源

- `notes/base-lib/20260709-multistream-bind-graph-dependency.md`
- `notes/business-lib/20260601-multistreamengine-bind-graph-dependency.md`
- `notes/business/20260529-grg-diversity-soft-rules-in-feeda-mv-grg.md`
- `conf/plugins/exec_engine/multi_stream_diversity.conf`
- `src/plugins/exec_engine/multi_stream_engine_plugin.cpp`
- `src/plugins/exec_engine/multi_stream_engine.cpp`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260802-multistreamengine-bind-graph-dependency.md)。
