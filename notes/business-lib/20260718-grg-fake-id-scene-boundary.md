# 2026-07-18 周六代码理解：GRG fake_id 与个性化关闭边界

> 本文基于本地 `feed-gr` 代码阅读生成；KU 正文未能逐篇读取，场景背景与业务口径需人工补充。

## 1. 架构全景图：请求字段、scene 映射与图执行边界

<style>.arch-grg{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:14px;padding:18px;margin:16px 0;color:#243b53}.arch-grg .title{font-size:22px;font-weight:850;color:#102a43;margin-bottom:12px}.arch-grg .grid{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px}.arch-grg .lane{background:#fff;border:1px solid #d9e2ec;border-radius:12px;padding:12px}.arch-grg .lane h3{margin:0 0 10px;font-size:14px;color:#102a43}.arch-grg .box{background:#f8fbff;border:1px solid #cbd5e1;border-radius:10px;padding:10px 12px;margin-bottom:8px}.arch-grg .box strong{display:block;font-size:13px;margin-bottom:4px}.arch-grg .note{font-size:12px;line-height:1.55;color:#486581}.arch-grg .tag{font-size:11px;font-weight:700;letter-spacing:0;text-transform:uppercase;color:#627d98;margin-bottom:6px}.arch-grg .focus{border-top:4px solid #3d5a80}.arch-grg .accent{border-top:4px solid #2d6a4f}.arch-grg .soft{border-top:4px solid #94a3b8}.arch-grg .rail{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:10px}.arch-grg .chip{background:#eef4fb;border:1px solid #d9e2ec;border-radius:999px;padding:6px 10px;font-size:12px;text-align:center;color:#334e68}</style><div class="arch-grg"><div class="title">GRG 的业务边界是把请求字段翻译成 scene，再把 scene 翻译成图执行配置</div><div class="grid"><div class="lane"><div class="tag">请求层</div><div class="box focus"><strong>GRCRequest / gr_common.proto</strong><div class="note">`is_close_individual` 和 `fake_id` 是请求侧开关/透传字段，分别决定是否关闭个性化推荐、以及是否带入虚假 userid。</div></div><div class="box"><strong>request 解析</strong><div class="note">业务入口先完成参数解析与默认值补齐，再把字段交给 GRG service 进行 scene 选择。</div></div></div><div class="lane"><div class="tag">服务层</div><div class="box accent"><strong>GenericGRGService</strong><div class="note">`get_graph_name()` 先按 UA 选 `short_micro_video` 或 `news_updates_dibar`，随后在运行时把 `news_updates_dibar` 重映射到 `short_micro_video`。</div></div><div class="box"><strong>Graph / DTController</strong><div class="note">GRG 侧同时依赖 GraphEngine 与 DynamicTimeOutPlugin，说明业务策略与超时控制是同一条执行链上的两个维度。</div></div></div><div class="lane"><div class="tag">执行层</div><div class="box soft"><strong>Graph data binding</strong><div class="note">`fill_basic_data_for_graph()` 把 request 透传到 graph data，后续 operator 只读这些输入，不再回头查 protobuf 原始对象。</div></div><div class="box"><strong>场景语义</strong><div class="note">`news_updates_dibar` 不是终点，实际执行要落到 `short_micro_video` 场景，避免配置和策略表错位。</div></div></div></div><div class="rail"><div class="chip">fake_id 透传</div><div class="chip">is_close_individual</div><div class="chip">news_updates_dibar</div><div class="chip">short_micro_video</div></div></div>

## 2. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client as client
participant "GRCRequest" as req
participant "GenericGRGService" as svc
participant "GraphEngine" as ge
participant "DTController" as dt
participant "Operator / merge pipeline" as op
client -> req : send request
req -> svc : parse fields
svc -> svc : get_graph_name(ua)
svc -> svc : map news_updates_dibar -> short_micro_video
svc -> ge : build graph + bind request data
ge -> dt : attach timeout controller
ge -> op : execute graph operators
op -> req : consume is_close_individual / fake_id
op --> client : ranked response
@enduml
```

## 3. 配置/结构信息图

```infographic
compare-binary-horizontal-underline-text-vs
data
  title Request field boundary
  items
    - label Request schema
      desc gr_common.proto / generator.proto define fields
      children
        - label is_close_individual
          desc non-personalized switch
        - label fake_id
          desc virtual user id for routing/privacy boundary
    - label Runtime scene
      desc grg_service.cpp remaps graph_name at execution time
      children
        - label short_micro_video
          desc actual runtime scene
        - label news_updates_dibar
          desc source scene label for mapping
```

## 4. Pitfalls 卡片

<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:16px 0;"><div style="background:#fff;border:1px solid #d9e2ec;border-top:4px solid #b08968;border-radius:12px;padding:12px;"><div style="font-weight:800;margin-bottom:6px;color:#102a43">把 proto 当运行时真相</div><div style="font-size:13px;line-height:1.55;color:#334e68">`fake_id` 和 `is_close_individual` 只说明字段存在，真正生效点在 request 解析和 graph 绑定阶段。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-top:4px solid #3d5a80;border-radius:12px;padding:12px;"><div style="font-weight:800;margin-bottom:6px;color:#102a43">scene 名混用</div><div style="font-size:13px;line-height:1.55;color:#334e68">`news_updates_dibar` 会被服务层重映射到 `short_micro_video`，排查策略配置时必须看运行态名字。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-top:4px solid #2d6a4f;border-radius:12px;padding:12px;"><div style="font-weight:800;margin-bottom:6px;color:#102a43">遗漏超时控制</div><div style="font-size:13px;line-height:1.55;color:#334e68">GRG 不是纯业务链路，GraphEngine 之外还依赖 DTController，漏看这一层会把延迟问题误判成策略问题。</div></div></div>

## 5. 调试 Checklist

```infographic
list-column-done-list
data
  title Debug checklist
  items
    - label Confirm request fields
      desc inspect gr_common.proto / generator.proto
      done true
    - label Verify graph name mapping
      desc ua 102 and default scene routing
      done true
    - label Trace request binding
      desc ensure graph data holds the parsed request
      done true
    - label Check timeout plugin attach
      desc DTController present in graph execution
      done true
    - label Compare runtime scene name
      desc news_updates_dibar -> short_micro_video
      done true
```

## 6. 证据来源

- `feed-gr/gr-proto_20260203152918/proto/gr_common.proto:237-241`
- `feed-gr/gr-proto_20260203152918/proto/gr_common.proto:298-303`
- `feed-gr/gr-proto_20260203152918/proto/generator.proto:298-300`
- `feed-gr/feeda-mv-grg/src/service/grg_service.h:10-18`
- `feed-gr/feeda-mv-grg/src/service/grg_service.cpp:113-128`
- `feed-gr/feeda-mv-grg/src/main.cpp:1-17`

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-20T19:38:08.856703
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 这次要适配的能力本质上是 **GRG 的请求边界控制**：`is_close_individual` 用于关闭个性化链路，`fake_id` 用于虚拟用户透传与路由隔离。结合技术笔记来看，它的关键不在 proto 定义，而在 **请求解析、scene 映射、graph 绑定、operator 执行** 这条链路上是否真正生效。
- 从扫描结果看，这类能力在两个业务库里的落点并不平均：`feeda-mv-grg` 目前只发现 **1 个文件**有目标相关使用，适合做轻量接入；`feeda-mv-grc` 已经分布到 **9 个文件**，覆盖 filter、adjuster、HTTP 服务等多个层次，说明它更适合做统一收口，但也意味着改造面更大。

- 从容器使用规模看，两库都大量依赖 `std::vector / std::string / std::unordered_map`，说明代码风格偏“数据流+分支处理”，对新增布尔开关、虚拟 id 透传这类能力是友好的；真正的迁移成本不在数据结构，而在 **业务分支一致性** 和 **上下游语义统一**。

---

## 2. 代码库详情

### `feeda-mv-grg` 扫描发现

- 发现目标库使用：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 这说明 GRG 侧与“关闭个性化 / 多样性规则收敛”最接近的落点，已经出现在 **diversity rule** 这一层，适合作为 `is_close_individual` 的优先接入点。
- 结合已有架构笔记，GRG 侧还有一个关键事实：
  - `grg_service.cpp` 中存在 **scene 重映射**：`news_updates_dibar -> short_micro_video`
  - 这意味着任何新开关都必须在 **运行态 graph_name** 上生效，而不是只看配置名。

- 现有 `std` 等价物使用统计：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 结论：
  - GRG 代码形态比较稳定，容器使用广泛但分散。
  - 新能力更适合先落在 **service 层 + 单个 rule**，避免横切修改过多 operator。

- 可参考的相关代码：
  - `model/model.h`
  - `model/paddle_model.h`
  - `service/grg_service.h`
  - `service/grg_service.cpp`

---

### `feeda-mv-grc` 扫描发现

- 发现目标库使用：
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - 以及另外 4 个同类文件未在摘要中展开
- 这说明 GRC 侧的相关逻辑已经分布到：
  - **过滤器**
  - **调权/排序 adjuster**
  - **首轮初始化**
  - **HTTP 服务入口**
- 这类分布式落点意味着：
  - 如果要适配 `is_close_individual`，最好做成 **统一上下文字段**，避免每个 operator 自己重复判定。
  - 如果要适配 `fake_id`，建议只在 **入口层透传**，不要让业务逻辑到处拼接虚拟 uid。

- 现有 `std` 等价物使用统计：
  - `std::vector`：8442 次，1279 个文件
  - `std::string`：7170 次，1234 个文件
  - `std::unordered_map`：2834 次，639 个文件
- 结论：
  - GRC 是明显更大的业务库，且已经大量使用容器与映射结构。
  - 适配收益高，但推荐走 **渐进式接入**，先从 `grc_http_service.cpp` 和关键 filter/adjuster 开始。

- 可参考的相关代码：
  - `service/grc_http_service.cpp`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`

---

## 3. 💡 适用性评估与建议

- **建议 1：先在 `feeda-mv-grg/src/service/grg_service.cpp` 完成请求字段落地，再传入 graph data**
  - 把 `is_close_individual`、`fake_id` 的解析放在 service 入口层，而不是下沉到 operator 内部临时读取 protobuf。
  - 重点检查 `fill_basic_data_for_graph()` 之前是否已把字段完整绑定到 graph data。
  - 适用场景：
    - `news_updates_dibar -> short_micro_video` 的运行态 scene 路由
    - 需要统一关闭个性化的请求链路
  - 好处：
    - 避免 proto 只是“字段存在”，但执行时不生效的问题。

- **建议 2：在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 增加 `is_close_individual` 的早退出分支**
  - 如果请求明确关闭个性化，则该 rule 不应再做依赖用户历史或兴趣的多样性调整。
  - 建议逻辑：
    - `is_close_individual=true` 时，走静态或轻量默认策略
    - `fake_id` 仅用于路径隔离，不参与个性化特征计算
  - 这是 GRG 侧最接近“业务边界”的文件，改动收益最高、风险也相对可控。

- **建议 3：在 `feeda-mv-grc/service/grc_http_service.cpp` 做统一参数解析与上下文透传**
  - 这个文件已经是 HTTP 入口层，适合集中接收 `fake_id`、`is_close_individual`。
  - 建议把这些字段统一写入请求上下文，再由后续 pipeline 读取。
  - 适用场景：
    - 召回汇聚链路中的全局开关
    - 多个 filter / adjuster 需要共享同一语义
  - 这样可以避免 `processor/filter/*.cc` 和 `operator/adjuster/*.cpp` 重复解析。

- **建议 4：在 `feeda-mv-grc/processor/filter/user_explore_interest_ugc_filter_operator.cc`、`low_agile_goodrate_filter_operator.cc` 增加“关闭个性化”保护分支**
  - 这两个文件属于典型个性化过滤点，应该优先兼容 `is_close_individual`。
  - 推荐策略：
    - 关闭个性化时跳过依赖用户兴趣画像的过滤条件
    - 保留与内容安全、基础质量相关的硬规则
  - 这样可以避免“关闭个性化”后仍被兴趣相关规则误杀。

- **建议 5：在 `feeda-mv-grc/operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`、`youzhi_queue_adjust.cpp`、`processor/new_adjust/precise_score_init_first_refresh.cpp` 做降级路径**
  - 这些位置更像是排序、权重初始化、调度逻辑，容易隐含用户特征依赖。
  - 建议在读取上下文时统一判断：
    - 若 `is_close_individual=true`，则只保留稳定、可解释、非用户相关的权重项
    - `fake_id` 不参与画像回写和长期统计
  - 适用场景：
    - 首刷初始化
    - 队列排序
    - LTV/质量因子调节
  - 这样能减少“关闭个性化后排序异常抖动”的问题。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：scene 名称混用导致配置错位**
  - 技术笔记已经说明 `news_updates_dibar` 会在运行时重映射到 `short_micro_video`。
  - 如果按 proto 名、配置名、运行态名混着看，很容易把开关挂错地方。
  - 建议所有排查和埋点都以 **运行态 graph_name** 为准。

- **风险 2：`fake_id` 可能污染画像、缓存或日志语义**
  - `fake_id` 应只用于路由/边界隔离，不应回写到长期用户特征。
  - 如果某些 adjuster 或 filter 把它当成真实 uid 使用，可能引入：
    - 统计偏差
    - 缓存污染
    - 调试误判

- **风险 3：关闭个性化后，原有规则可能缺少兜底**
  - 有些 operator 可能默认依赖用户兴趣、历史行为、LTV 等信息。
  - 一旦 `is_close_individual=true`，如果没有默认 fallback，容易出现：
    - 召回量下降
    - 排序波动变大
    - 结果质量不稳定

- **风险 4：改动面分散，回归成本高**
  - GRC 侧已经分布到多个 filter/adjuster 文件，若没有统一上下文，会出现“部分链路生效、部分链路漏改”的情况。
  - 建议先定义统一字段和检查点，再逐层迁移，而不是直接在每个 operator 里硬编码判断。

---

如果你愿意，我可以继续把这份内容整理成你技术笔记风格统一的 **“业务代码库适配分析”正文模板**，直接可粘贴进文档。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
