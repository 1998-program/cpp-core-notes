# 2026-08-21 周五代码理解：MultiStreamEngine bind_graph_dependency 桥接外层 GraphData
<!-- topic-nav:start -->
> 主题：`base/multistream-binding` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260529-exec-engine-multistreamengine-graphdependency-deep-dive.md)
<!-- topic-nav:end -->


> 日期：2026-08-21  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的高优先级候选项回退；本次 cron 未发现可直接执行的当日 daily-plan，KU/业务背景需人工补充。  
> 范围：只分析 `src/processor/video_launch/diversity_merge.cpp` 与 `src/process/diversity_merge.cpp` 中的 `bind_graph_dependency`、外层 `GraphData` 绑定、`engine_vertex` 取数、以及依赖注入后的执行闭环；不扩展到完整 diversity 规则。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.1fr 1fr;gap:12px}.arch-col{border:1px solid #d9e2ec;border-radius:10px;padding:12px;background:#fff}.arch-h{font-size:18px;font-weight:800;margin:0 0 8px}.arch-s{font-size:12px;color:#64748b;margin:0 0 10px}.arch-box{border:1px solid #cbd5e1;border-radius:10px;padding:10px 12px;margin:8px 0;background:#f8fafc}.arch-box strong{display:block;font-size:13px;margin-bottom:4px}.arch-arrow{font-size:12px;color:#475569;margin:4px 0;text-align:center}.arch-tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:#dbeafe;color:#1d4ed8;margin-bottom:8px}.arch-note{font-size:12px;color:#475569;line-height:1.45}</style><div class="arch-wrap"><div class="arch-col"><div class="arch-h">外层数据桥接</div><div class="arch-s">把服务级 GraphData 变成 engine 可消费的输入绑定</div><div class="arch-box"><span class="arch-tag">Request Layer</span><strong>video_launch / diversity_merge 请求上下文</strong><span class="arch-note">携带场景、召回结果、配置开关与后续执行所需的图依赖。</span></div><div class="arch-arrow">↓ bind_graph_dependency</div><div class="arch-box"><span class="arch-tag">Bridge Layer</span><strong>MultiStreamEngine 依赖绑定</strong><span class="arch-note">把外层 GraphData、节点输入、图内依赖关系写入 engine_vertex 可见的状态。</span></div><div class="arch-arrow">↓</div><div class="arch-box"><span class="arch-tag">Execution Layer</span><strong>engine_vertex / function chain</strong><span class="arch-note">基于已绑定依赖执行各个 function，决定 merge、filter、output 的顺序。</span></div></div><div class="arch-col"><div class="arch-h">边界与约束</div><div class="arch-s">依赖绑定做的是“接线”，不是业务决策</div><div class="arch-box"><strong>输入</strong><span class="arch-note">GraphData、scene、上下文、上游回填结果。</span></div><div class="arch-box"><strong>输出</strong><span class="arch-note">绑定后的 engine 输入、可运行的 vertex 依赖、后续 stage 可读的状态。</span></div><div class="arch-box"><strong>风险</strong><span class="arch-note">依赖缺失、绑定顺序错位、复用对象残留旧状态、上层把业务判断塞进桥接层。</span></div></div></div></div>

## 1. 入口链路
1. 业务请求进入 `video_launch` 或 GRG 的 `diversity_merge` 处理链。
2. 上层先构造或拿到 `GraphData`，再进入 `bind_graph_dependency`。
3. `bind_graph_dependency` 将外层图数据注入 engine 侧，建立 `engine_vertex` 能消费的依赖关系。
4. engine 执行阶段读取这些绑定，驱动后续 merge / response / cleanup。
5. 执行完成后，桥接层不保留业务状态，只保留本次运行所需的最小上下文。

## 2. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
start
:接收 video_launch / diversity_merge 请求;
:准备外层 GraphData;
:调用 bind_graph_dependency;
:将 GraphData 绑定到 engine_vertex;
if (依赖完整?) then (yes)
  :进入 engine 执行;
  :读取 vertex 输入;
  :执行 merge / filter / output;
  :清理本轮绑定状态;
  stop
else (no)
  :记录依赖缺口;
  :返回空执行或降级结果;
  stop
endif
@enduml
```

## 3. 依赖结构信息图
infographic list-grid-badge-card
data
  title bind_graph_dependency 的关键信息
  desc 这个阶段只负责把外层数据桥接成 engine 可读状态，不负责复杂策略判断。
  items
    - label 外层输入
      desc GraphData、scene、上下文、召回结果
      value 1
      icon mdi/inbox-arrow-down
    - label 绑定对象
      desc engine_vertex、node dependency、stage input
      value 2
      icon mdi/link-variant
    - label 结果形态
      desc 可执行的 engine 图与本轮状态快照
      value 3
      icon mdi/play-circle
    - label 典型风险
      desc 旧状态残留、依赖缺口、顺序错配
      value 4
      icon mdi/alert-circle

theme
  palette #2563eb #0f766e #f59e0b

## 4. 关键观察
<div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;margin:14px 0;">
<div style="border:1px solid #dbe4ee;border-radius:12px;padding:14px;background:#fff;">
<strong>边界结论</strong>
<p style="margin:8px 0 0;color:#334155;line-height:1.6">`bind_graph_dependency` 的价值在于把外层调用上下文和执行引擎解耦。它应该稳定、窄而清晰，所有业务分歧都留在上游策略或下游 stage。</p>
</div>
<div style="border:1px solid #dbe4ee;border-radius:12px;padding:14px;background:#fff;">
<strong>实现提醒</strong>
<p style="margin:8px 0 0;color:#334155;line-height:1.6">如果这个层做了太多条件判断，后续的 engine 复用、排查和回收都会被污染，尤其是对象池复用场景。</p>
</div>
</div>

## 5. Pitfalls
<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0;">
<div style="border:1px solid #d9e2ec;border-left:4px solid #2563eb;border-radius:10px;padding:12px;background:#fff;"><strong>状态污染</strong><div style="margin-top:6px;color:#475569;line-height:1.55">复用 `GraphData` 或 vertex 时没有清掉旧依赖，下一轮会读到错误输入。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #0f766e;border-radius:10px;padding:12px;background:#fff;"><strong>绑定顺序错位</strong><div style="margin-top:6px;color:#475569;line-height:1.55">先执行后绑定，或只绑定部分节点，都会让执行图出现隐性空值。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #f59e0b;border-radius:10px;padding:12px;background:#fff;"><strong>职责外溢</strong><div style="margin-top:6px;color:#475569;line-height:1.55">把 scene 选择、业务兜底、实验逻辑塞进桥接层，会破坏可维护性。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #dc2626;border-radius:10px;padding:12px;background:#fff;"><strong>空依赖降级缺失</strong><div style="margin-top:6px;color:#475569;line-height:1.55">缺少完整校验时，执行层可能继续跑到一个无意义的半成品图。</div></div>
</div>

## 6. 调试 checklist
infographic list-column-done-list
data
  title 调试 checklist
  items
    - label 确认 GraphData 在绑定前已完整填充
      done true
    - label 确认 engine_vertex 能读到绑定结果
      done true
    - label 确认复用对象在本轮结束后完成 reset
      done true
    - label 确认缺依赖时走了预期降级路径
      done true
    - label 确认没有把业务分支塞进桥接层
      done true

theme
  palette #2563eb #0f766e #f59e0b

## 7. 结论
`bind_graph_dependency` 是 MultiStreamEngine 的接线层。它的正确目标是稳定传递依赖、隔离执行引擎、并保持每轮运行的状态可回收。本文的业务背景仍需 KU/人工补充，但代码边界已经足够清楚：桥接层只做绑定，不做决策。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260821-multistreamengine-bind-graph-dependency-bridge.md)。
