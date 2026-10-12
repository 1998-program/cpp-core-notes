# 2026-09-03 周四基础库理解：MultiStreamEngine bind_graph_dependency 桥接外层 GraphData

> 日期：2026-09-03  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 仍停留在旧的周计划，没有 2026-09-03 当日计划；按历史候选回退到 `MultiStreamEngine bind_graph_dependency 桥接外层 GraphData`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/processor/video_launch/diversity_merge.cpp`、`src/process/diversity_merge.cpp`、`src/service/grc_service.cpp`、`src/service/grg_service.cpp`，聚焦图依赖绑定、GraphPool 获取、场景选择、以及执行前的上下文注入边界。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">请求层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/service/grc_service.cpp:151-220` / `src/service/grg_service.cpp:35-91`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">服务入口先解析 UA、scene、timeout 和 request 基本信息，再决定要拿哪一个 graph。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">桥接层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`bind_graph_dependency(this->vertex(), _diversity_engine)`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">把外层 GraphData / vertex 依赖接到 MultiStreamEngine 可见的执行图上，完成“接线”而不是“决策”。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">执行层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/processor/video_launch/diversity_merge.cpp:32-83` / `src/process/diversity_merge.cpp:58-260`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">DiversityMerge 进入图函数后，消费已绑定的依赖、构建上下文、执行 merge / rule / output。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">请求解析</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">依赖绑定</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">执行图构建</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">结果回收</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title MultiStreamEngine bind_graph_dependency bridge
participant "GenericGRCService" as GRC
participant "GenericGRGService" as GRG
participant "GraphEngine" as ENG
participant "GraphPool" as POOL
participant "MultiStreamEnginePlugin" as PLG
participant "DiversityMergeFunction" as DM
participant "BaseStreamContext" as CTX
GRC -> ENG : get<GraphEngine>("graph_engine")
GRC -> POOL : try_get(graph_name)
GRC -> PLG : get ExecEnginePlugin
GRC -> PLG : bind_graph_dependency(this->vertex(), _diversity_engine)
PLG --> GRC : dependency wired
GRC -> DM : run(graph, graph_name, sctx, done, cntl, res_cnt)
DM -> CTX : prepare exec_context / custom_context
DM -> DM : read bound inputs / condition values
DM --> GRC : emit merged result
GRG -> ENG : get<GraphEngine>("graph_engine")
GRG -> POOL : try_get(graph_name)
GRG -> PLG : bind_graph_dependency(this->vertex(), _diversity_engine)
GRG -> DM : run(graph_name, pooled_graph.get(), ...)
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title bind_graph_dependency 的 6 个关键落点
  desc 这个桥接层只负责把请求侧状态接到执行图，尽量不掺杂业务决策。
  items
    - label GraphEngine 获取
      desc `src/service/grc_service.cpp:177-179` 与 `src/service/grg_service.cpp:49-51` 从 ApplicationContext 取引擎实例
      icon mdi-engine-outline
    - label GraphPool 复用
      desc `src/service/grc_service.cpp:180-200` 和 `src/service/grg_service.cpp:65-79` 从对象池拿当前场景图
      icon mdi-database-sync-outline
    - label 依赖绑定
      desc `src/processor/video_launch/diversity_merge.cpp:54-60` 用 `bind_graph_dependency` 接通 vertex 与 engine
      icon mdi-link-variant
    - label custom_context
      desc `src/process/diversity_merge.cpp:47-53` 把业务上下文挂进 `BaseStreamContext`
      icon mdi-clipboard-text-outline
    - label 场景选择
      desc `src/service/grc_service.cpp:184-198` 与 `src/service/grg_service.cpp:113-119` 通过 UA 选择 graph_name
      icon mdi-map-marker-path
    - label reset 边界
      desc `src/service/grc_service.cpp:217-220`、`src/service/grg_service.cpp:88-91` 在执行后 reset 图对象
      icon mdi-restore
```

## 3. 代码链路拆解
### 3.1 入口层先做的是场景分流，不是执行决策
- `src/service/grc_service.cpp:172-200`：`req_extract_plugin` 先把 `ua`、`product`、`channel_id` 写入动态结构，再用 `GraphEngine` 和 `GraphPool` 取出对应场景图。
- `src/service/grc_service.cpp:184-198`：`ua` 决定 `dibar_reddot`、`video_immersion`、`searchc_related`、`news_updates_dibar` 等 graph_name，说明 service 层已经把业务场景映射成图名。
- `src/service/grg_service.cpp:56-89`：GRG 侧先解析 UA、拿动态超时控制器，再走 `fill_basic_data_for_graph()` 和 `run()`，同样是先接线、后执行。

### 3.2 bind_graph_dependency 是把外层 vertex 与 engine 侧状态连起来
- `src/processor/video_launch/diversity_merge.cpp:54-60`：`ExecEnginePlugin` 被动态转成 `MultiStreamEnginePlugin` 后调用 `bind_graph_dependency(this->vertex(), _diversity_engine)`。
- `src/process/diversity_merge.cpp:42-60`：同样的模式在 GRG 的 diversity merge 里出现，说明这不是单点 hack，而是跨服务的统一接线方式。
- `src/processor/video_launch/diversity_merge.cpp:47-53`：`exec_context->custom_context.ref(static_cast<BaseStreamContext&>(diversity_context))` 说明绑定不仅连依赖，也连上下文类型。

### 3.3 reset 放在执行后，说明对象池复用是这条链路的默认前提
- `src/service/grc_service.cpp:211-220`：`run()` 之后做 trace 打点，再 `graph->reset()`，顺序表明输出先可观测，状态后回收。
- `src/service/grg_service.cpp:88-91`：`run()` 之后立即 `pooled_graph->reset()`，没有把业务状态继续留在对象池里。
- `src/process/diversity_merge.cpp:130-170`：执行前把多组 condition value、trace sample、force/ignore 集合准备好，进一步说明 reset 只负责回收，不负责补偿业务逻辑。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #2563eb;border-radius:10px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#2563eb;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">桥接层最容易被写成“半个业务层”</div><div style="display:grid;grid-template-columns:1.15fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #2563eb;border-radius:8px;padding:12px;font-size:14px;">`bind_graph_dependency` 应只做依赖连接和状态透传。如果把 scene 选择、实验判断、fallback 逻辑混进去，后面排查会分不清是图没接上，还是业务分支本身错了。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">对象池复用要求每轮执行后 `reset()`。少这一句，旧的 vertex 状态、trace 配置或输入引用很容易污染下一次请求。</div></div><div style="margin-top:10px;font-weight:900;color:#2563eb;">∎ 排查顺序：graph_name → pool → bind_graph_dependency → custom_context → run → reset</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title Bridge layer 调试清单
  desc 用于确认依赖是否接通、上下文是否完整、对象池是否回收
  items
    - label 检查 graph_name 分流
      desc 确认 UA 场景映射命中了预期的 graph_name
      done true
    - label 检查 GraphPool 获取
      desc `try_get()` 不能返回空图
      done true
    - label 检查 bind_graph_dependency
      desc 依赖绑定后 vertex 应能读到 engine 侧状态
      done true
    - label 检查 custom_context
      desc `BaseStreamContext` 必须在执行前挂上去
      done true
    - label 检查 run 与 reset 顺序
      desc 先执行，再打印 trace，再回收对象池状态
      done true
    - label 检查无业务外溢
      desc 桥接层不要混入过多 scene 规则和 fallback 决策
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/service/grc_service.cpp:172-220`
- `src/service/grg_service.cpp:48-91`
- `src/processor/video_launch/diversity_merge.cpp:34-60`
- `src/processor/video_launch/diversity_merge.cpp:130-260`
- `src/process/diversity_merge.cpp:58-170`

## 7. 说明
当前运行环境没有 2026-09-03 的 daily-plan 文件；本笔记基于历史候选和本地代码回退生成，KU 正文未读取，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-03T19:09:55.360582
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 这条“MultiStreamEngine bind_graph_dependency”能力在两个业务库里都已经有比较清晰的落点：`feeda-mv-grg` 侧的 `src/process/diversity_merge.cpp` 和 `feeda-mv-grc` 侧的 `src/processor/video_launch/diversity_merge.cpp` 都直接调用了 `bind_graph_dependency()`，说明它已经不是概念层，而是可复用的工程模式。
- `feeda-mv-grc` 额外还有 `src/service/grc_service.cpp` 这种服务入口层，已经把 `GraphEngine`、`GraphPool`、场景选择、动态上下文准备串起来，适合把“桥接层”再向上抽象；`feeda-mv-grg` 则更偏单点接入，适合先保持局部稳定。
- 两个库都大量使用 `std::vector`、`std::string`、`std::unordered_map`，说明它们天然适合承载图依赖和中间状态，但也意味着不要把 bind 层继续做厚，否则会把本来清晰的对象池生命周期污染掉。

## 2. 代码库详情

### feeda-mv-grg

- 相关落点较少，但很直接：
  - `src/process/diversity_merge.cpp`
- `std` 容器使用规模很大：
  - `std::vector`：1969 次
  - `std::string`：2443 次
  - `std::unordered_map`：734 次
- 结论：
  - 适合沿着现有 diversity merge 入口保留绑定逻辑，不建议一口气改成更复杂的统一框架。
  - 如果后续要扩展，优先把 `BaseStreamContext`、condition value、trace sample 这些边界明确下来。

### feeda-mv-grc

- 相关落点更多，覆盖服务层和 processor 层：
  - `src/service/grc_service.cpp`
  - `src/processor/video_launch/diversity_merge.cpp`
  - `src/processor/video_launch/data_merge.cpp`
- `src/service/grc_service.cpp:177-220` 已经完成 GraphEngine 获取、GraphPool 取图、场景映射和执行后 reset，是抽象公共桥接入口的好位置。
- `src/processor/video_launch/diversity_merge.cpp:54-60` 说明 processor 侧也需要统一依赖绑定，避免每个 strategy 组件自己造一遍。

## 3. 适用性评估与建议

- **优先在 `feeda-mv-grc/src/service/grc_service.cpp` 抽象统一桥接入口**
  - 这里已经承担了场景选择、图获取、上下文准备和执行回收，最接近一个稳定的接线层。
  - 可以把 `GraphEngine` 取图、`GraphPool` 获取、`bind_graph_dependency`、`reset()` 收进一个更清晰的入口函数。

- **在 `feeda-mv-grg/src/process/diversity_merge.cpp` 保持轻量绑定**
  - GRG 侧更适合保留当前 `bind_graph_dependency(this->vertex(), _diversity_engine)` 的直接形式。
  - 重点是把执行上下文、trace sample、ignore/force 集合准备好，不要把 scene 决策继续往下压。

- **在 `feeda-mv-grc/src/processor/video_launch/data_merge.cpp` 观察后续依赖融合是否需要桥接层**
  - 该文件已经有 `_diversity_dependency_map`、`named_dependency()` 这种依赖聚合形态。
  - 如果未来多个 processor 都需要同类绑定逻辑，可以把依赖 map 的组装收敛到统一适配层。

- **把 reset 作为强约束，而不是可选补丁**
  - 这条链路默认依赖对象池复用。
  - 任何桥接层抽象都应该把“执行后 reset”纳入合同，否则会有状态污染风险。

## 4. ⚠️ 引入风险与限制

- **状态污染风险**
  - 图对象和上下文被复用时，如果忘了 reset，下一次请求可能读到旧依赖和旧 trace。

- **职责外溢风险**
  - `bind_graph_dependency` 本质是接线，不是策略引擎。它不应该承担 scene 选择、实验开关决策和业务 fallback。

- **绑定顺序风险**
  - 先 run 再 bind，或者只绑部分 vertex，会出现半成品图和隐性空值。

- **统一抽象的收益差异**
  - GRG 侧当前落点少，过早统一会增加改造成本；GRC 侧落点更多，更适合优先收口。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*