# ExecEngine MultiStreamEngine 绑定 GraphDependency 深入理解（GRG DiversityMerge）

> 日期：2026-05-29  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 `fri.base_lib` 计划项  
> 范围：只分析 GRG 中 `DiversityMergeFunction` 如何把图引擎 GraphData / Channel 依赖绑定到 ExecEngine `MultiStreamEngine`，不泛化到所有 brpc / graph-engine 用法。

---

## 0. 架构全景图

<div style="position: relative; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f8fafc; border-radius: 12px; padding: 24px; margin: 16px 0;">
<style scoped>
.arch-wrapper { display: flex; gap: 16px; }
.arch-main { flex: 1; }
.arch-layer { border-radius: 8px; padding: 16px; margin-bottom: 12px; }
.arch-layer.user { background: #dbeafe; border-left: 4px solid #3b82f6; }
.arch-layer.application { background: #dcfce7; border-left: 4px solid #22c55e; }
.arch-layer.ai { background: #fef3c7; border-left: 4px solid #f59e0b; }
.arch-layer.data { background: #fce7f3; border-left: 4px solid #ec4899; }
.arch-layer.infra { background: #f3e8ff; border-left: 4px solid #a855f7; }
.arch-box { background: rgba(255,255,255,0.8); border-radius: 6px; padding: 8px 12px; margin: 4px; font-size: 12px; display: inline-block; }
.arch-box.highlight { background: #fff; border: 2px solid #f59e0b; font-weight: 600; }
.arch-grid { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.arch-layer-title { font-size: 14px; font-weight: 700; margin-bottom: 8px; color: #1e293b; }
</style>
<div class="arch-wrapper">
<div class="arch-main">
<div class="arch-layer user">
<div class="arch-layer-title">👤 用户层 (RPC 入口)</div>
<div class="arch-grid">
<div class="arch-box">GenericGRGService::query()</div>
<div class="arch-box">GraphEngine::try_get()</div>
<div class="arch-box">fill_basic_data_for_graph()</div>
</div>
</div>
<div class="arch-layer application">
<div class="arch-layer-title">⚙️ 图引擎层 (Graph Engine)</div>
<div class="arch-grid">
<div class="arch-box">GrcRecallFunction</div>
<div class="arch-box">MergeEffectQueueFunction</div>
<div class="arch-box">DNN/Set2Set Predict</div>
<div class="arch-box highlight">[@engine_vertex] DiversityMergeFunction</div>
</div>
</div>
<div class="arch-layer ai">
<div class="arch-layer-title">🧠 ExecEngine 层 (MultiStreamEngine)</div>
<div class="arch-grid">
<div class="arch-box">loads_select</div>
<div class="arch-box">rule_select</div>
<div class="arch-box">function_select</div>
<div class="arch-box">final_select</div>
<div class="arch-box">effect_pk</div>
<div class="arch-box highlight">merge_pk</div>
</div>
</div>
<div class="arch-layer data">
<div class="arch-layer-title">💾 数据流层 (Channels)</div>
<div class="arch-grid">
<div class="arch-box">LoadsQueue</div>
<div class="arch-box">RuleQueue</div>
<div class="arch-box">FunctionQueue</div>
<div class="arch-box">EffectQueue</div>
<div class="arch-box">DiversityMergeResult</div>
</div>
</div>
<div class="arch-layer infra">
<div class="arch-layer-title">🔧 基础设施层</div>
<div class="arch-grid">
<div class="arch-box">GraphPool</div>
<div class="arch-box">EngineMgr</div>
<div class="arch-box">MutableChannel</div>
<div class="arch-box">bind_graph_dependency()</div>
</div>
</div>
</div>
</div>
</div>

---

## 1. API 模型：Graph Engine 外层 vertex + ExecEngine 内层 multi-stream

GRG 服务不是直接在 RPC 方法里做排序合并，而是在入口取到 Graph 实例后运行图：`GenericGRGService::query()` 通过 `ApplicationContext` 获取 `graph_engine`，按 UA 选择 `graph_name`，`try_get(graph_name)` 得到池化图实例，然后 `fill_basic_data_for_graph()` 注入全局数据，最后 `run()` 调用 `graph->run(end_node, err_node)`；运行结束后调用 `pooled_graph->reset()` 复用图实例（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:48-91`, `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:203-219`）。

在这个外层 Graph 中，`DiversityMergeFunction` 被声明为 `[@engine_vertex]`，它一方面像普通 Graph vertex 一样声明 `emit/depend` 数据，另一方面通过 `[.option] plugin: multi_stream_engine_plugin` 与 `conf: multi_stream_diversity.conf` 进入 ExecEngine 的多流选择配置（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:798-934`）。

`multi_stream_engine_plugin` 可用的 engine 配置来自 `exec_engine.conf`：同一插件配置了 `multi_stream_diversity.conf`、`multi_stream_diversity_new.conf`、`news_diversity.conf` 三个 engine 文件（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/exec_engine.conf:1-3`）。本文只分析当前主链路 `multi_stream_diversity.conf`。

### 关键 API / 宏

| API / 宏 | 文件与行号 | 作用 |
|---|---|---|
| `GraphEngine::try_get(graph_name)` | `src/service/grg_service.cpp:48-65` | 从 GraphPool 取出请求对应的图实例。 |
| `graph->find_data(...)->emit<T>()` | `src/service/grg_service.cpp:127-176` | 在 Graph 全局依赖中注入 `Request/log_id/uid/cuid/ua/ExpInfo` 等数据。 |
| `[@engine_vertex] function: DiversityMergeFunction` | `conf/plugins/graph/short_micro_video/vertex.conf:798-934` | 声明多样性合并 vertex 及其 GraphData/Channel 依赖。 |
| `EngineMgr::get_multi_stream_engine_pool(conf)` | `src/process/diversity_merge.cpp:68-76` | 按 `conf` 取出 MultiStreamEngine 池并拿到 engine 实例。 |
| `exec_context->custom_context.ref(...)` | `src/process/diversity_merge.cpp:80-85` | 将业务自定义 `DiversityScatterContext` 暴露给 ExecEngine 算子。 |
| `bind_graph_dependency(this->vertex(), _diversity_engine)` | `src/process/diversity_merge.cpp:86-90` | 把内层 ExecEngine 依赖绑定到外层 Graph vertex。 |
| `GRAPH_FUNCTION_DEPEND_MUTABLE_CHANNEL` | `src/process/diversity_merge.cpp:1848-1856` | 声明输入队列是 Graph mutable channel，而不是普通 data。 |
| `BABYLON_REGISTER_FACTORY_COMPONENT_WITH_TYPE_NAME` | `src/common/common.h:186-187` | `REGISTER_GRG_FUNCTION` 的底层注册宏，把函数注册为 `GraphProcessor`。 |

---

## 2. 数据流图

```plantuml
@startuml
skinparam backgroundColor #f8fafc
skinparam handwritten false

title DiversityMerge 数据流图

rectangle "RPC 入口" as rpc #e3f2fd {
  card "GenericGRGService::query()" as query
}

rectangle "Graph Engine" as graph #e8f5e9 {
  card "GraphEngine::try_get()" as tryget
  card "fill_basic_data_for_graph()" as fill
  card "GrcRecallFunction" as recall
  card "MergeEffectQueueFunction" as merge
  card "DNN/Set2Set Predict" as predict
  card "[@engine_vertex]\nDiversityMergeFunction" as diversity #fff3e0
}

rectangle "ExecEngine" as exec #fff8e1 {
  card "loads_select" as loads
  card "rule_select" as rule
  card "function_select" as func
  card "final_select" as final
  card "effect_pk" as pk
  card "merge_pk" as mergepk #ffecb3
}

rectangle "输出" as output #fce4ec {
  card "DiversityMergeResult" as result
  card "ResponseFunction" as resp
}

query --> tryget : 1. 获取图实例
tryget --> fill : 2. 注入全局数据
fill --> recall : 3. 产出原始队列
recall --> merge : 4. 合并效果队列
merge --> predict : 5. DNN/Set2Set 预测
predict --> diversity : 6. 进入多样性合并

diversity --> loads : loads channel
diversity --> rule : rule channel
diversity --> func : function channel
diversity --> final : effect channel
loads --> mergepk : 输出候选
rule --> mergepk : 输出候选
func --> mergepk : 输出候选
pk --> mergepk : PK 后候选
final --> pk : 效果队列 PK

mergepk --> result : 7. 最终选择
result --> resp : 8. 构建响应

note right of diversity
  data_prepare_for_engine()
  转 DynamicStruct
  按 merge_pos 放入 input_map
end note

note right of mergepk
  去重、标记 queue_type
  统计输出类型
end note

@enduml
```

该链路中，外层 Graph 的全局依赖来自 `short_micro_video/global.conf`，其中声明了 `Request/log_id/uid/cuid/ua/tab/product/flow_loc/graph_begin_us/is_debug/is_vip_cuid/ExpInfo`，并 include 了 `vertex.conf`、`pipeline.conf`、`expression.conf`、`div_prepare.conf` 等子配置（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/global.conf:1-49`）。

---

## 3. MultiStreamEngine 配置语义

### Stream 结构一览

```infographic
infographic list-grid-badge-card
data
  title MultiStreamEngine 6 大 Stream
  desc multi_stream_diversity.conf 核心配置
  items
    - label loads_select
      desc loads 兜底/插入队列 | executor: loads_select_executor
    - label rule_select
      desc 规则队列 | executor: select_executor
    - label function_select
      desc 功能队列 | executor: select_executor
    - label final_select
      desc 效果队列 | executor: effect_select_executor
    - label effect_pk
      desc 效果队列提前 PK | executor: effect_pk_executor
    - label merge_pk
      desc 最终槽位选择 | 合并所有 stream 输出
```

`multi_stream_diversity.conf` 首先声明 engine 的 context schema：`dependency_schema: ContextInfo` 与 `mutable_context_schema: ContextInfo`，说明 ExecEngine 算子看到的依赖上下文不是任意 GraphData，而是由插件绑定和 ContextInfo schema 映射后的依赖集合（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:1-7`）。

多流结构由五个 stream 组成：

| stream | 配置行 | 输入 pip | executor | 业务含义 |
|---|---|---:|---|---|
| `loads_select` | `multi_stream_diversity.conf:10-16` | `loads` | `loads_select_executor` | loads 兜底/插入队列。 |
| `rule_select` | `multi_stream_diversity.conf:17-23` | `rule` | `select_executor` | 规则队列。 |
| `function_select` | `multi_stream_diversity.conf:24-30` | `function` | `select_executor` | 功能队列。 |
| `final_select` | `multi_stream_diversity.conf:31-37` | `effect` | `effect_select_executor` | 效果队列，执行硬/软规则与打分。 |
| `effect_pk` | `multi_stream_diversity.conf:38-44` | `final_select` | `effect_pk_executor` | 对效果队列提前 PK，缩小候选集。 |
| `merge_pk` | `multi_stream_diversity.conf:45-64` | `loads_select/rule_select/function_select/effect_pk` | score/index/effect pk executor | 将各 stream 输出做最终槽位选择。 |

`select_executor` 使用 `exec_type: 1` 且 `stages: rule`，同时配置 `CostGenerator` 和多个 rule operator，例如 `DiversityRuleOperator` 在 `ua = 85` 时生效，`RuleSelectOperator` 使用 `multi_stream_select_hard_rule.conf` 且可配置 `is_terminal_select`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:68-124`）。`effect_select_executor` 使用 `exec_type: 3`，这与普通 rule/function 队列不同，说明效果队列的规则执行可以采取不同并发策略（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:388-401`）。

软规则的触发条件主要由 ExecContext 中的 condition value 决定：例如 `EffectSoftRuleOperator` 只在 `is_open_dnn_soft_rule = 1 && is_searchc_immersive_request != 1 && ua != 87 && enable_general_adjust = 0` 时启用，配置文件引用 `multi_stream_select_soft_rule.conf`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:602-632`）。`effect_pk_executor` 根据 `is_hit_diversity_score_pk_exp`、`is_open_dnn_soft_rule`、`ua`、`is_hit_searchc_mmr_refresh_exp` 选择不同 PK 算子（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:725-767`）。

---

## 4. 绑定 GraphDependency 的真实代码路径

```plantuml
@startuml
skinparam backgroundColor #f8fafc

title DiversityMergeFunction::init() 绑定流程

participant "DiversityMergeFunction" as dm
participant "EngineMgr" as em
participant "MultiStreamEnginePool" as pool
participant "MultiStreamEngine" as engine
participant "ExecContext" as ctx
participant "multi_stream_engine_plugin" as plugin

activate dm
dm -> em : get_multi_stream_engine_pool(conf)
activate em
em -> pool : 获取 engine pool
activate pool
pool --> em : pool instance
em --> dm : _diversity_engine

dm -> engine : pool->get()
activate engine
engine --> dm : engine instance

dm -> ctx : engine->get_context()
activate ctx
ctx --> dm : exec_context

dm -> ctx : custom_context.ref(_diversity_context)
note right
  将 DiversityScatterContext
  以 BaseStreamContext& 形式挂载
  供 ExecEngine 内部算子访问
end note

dm -> plugin : bind_graph_dependency(vertex, engine)
activate plugin
plugin --> dm : 绑定成功 / FATAL

deactivate plugin
deactivate ctx
deactivate engine
deactivate pool
deactivate em
deactivate dm

@enduml
```

`DiversityMergeFunction::init()` 是绑定发生的地方：它从 vertex option 读取 `plugin` 与 `conf`，用 `EngineMgr::instance().get_multi_stream_engine_pool(conf)` 获取 engine pool，再从 pool 中取出 `_diversity_engine`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:65-76`）。随后它取出 engine context，把 `_diversity_context` 以 `BaseStreamContext&` 形式挂到 `exec_context->custom_context`，这意味着 ExecEngine 内部算子可以通过统一的 BaseStreamContext 接口访问 GRG 业务上下文（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:80-85`）。

真正的 GraphDependency 绑定是 `multi_stream_engine_plugin->bind_graph_dependency(this->vertex(), _diversity_engine)`；失败时直接 `LOG(FATAL)` 并返回错误（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:86-90`）。这一步是关键：ExecEngine 配置里大量 `[..@depend] name: ...` 依赖需要从 Graph vertex 的依赖上下文中解析，如果没有绑定，内层 rule/operator 的 `Request/SidInfo/MsvReadInfo/ShowItemListWithMeta/...` 等依赖无法连到外层 GraphData。

---

## 5. 调度与阻塞语义

```plantuml
@startuml
skinparam backgroundColor #f8fafc

title DiversityMergeFunction::process() 执行时序

participant "Graph Vertex" as gv
participant "DiversityMergeFunction" as dm
participant "read_queue()" as rq
participant "data_prepare_for_engine()" as prep
participant "MultiStreamEngine" as engine
participant "push_item_to_result()" as push
participant "GraphData" as gd

activate gv
gv -> dm : process()
activate dm

dm -> rq : 消费 channels
activate rq
rq -> rq : loads / rule / function / effect
rq -> rq : 批量 consume(context.batch_size())
rq --> dm : _input_map filled

dm -> prep : 转 DynamicStruct
activate prep
prep -> prep : 按 merge_pos 放入槽位
prep -> prep : merge_pos 越界则跳过
prep --> dm : input_container_map

dm -> dm : general_adjust 预打分 (可选)
note right
  8 并发 soft rule
  写入 general_adjust_score
end note

dm -> engine : run_prepare(input_container_map)
activate engine
dm -> engine : run(input_container_map, &output)
engine --> dm : SelectStreamContainer output

dm -> push : output.next_all()
activate push
push -> push : 去重 (_div_nid_set)
push -> push : 标记 queue_type
push -> push : 统计输出类型
push --> gd : DiversityMergeResult

deactivate push
deactivate engine
deactivate prep
deactivate rq
deactivate dm
deactivate gv

@enduml
```

`DiversityMergeFunction::process()` 的执行是同步等待内层 ExecEngine 完成：先 `run_prepare(input_container_map)`，再调用 `_diversity_engine->run(input_container_map, &output)`，并用 `SIA_START/SIA_END` 包裹 `diversity_engine_rpc` 监控耗时（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:725-806`）。因此，对外层 Graph 来说，`DiversityMergeFunction` 是一个 vertex；对内层 ExecEngine 来说，它内部还会按配置执行 stream/executor/operator。

在调用 engine 前，函数会通过 `read_queue()` 消费多个 `MutableChannelConsumer<RidTmpInfoPtr>`：loads、rule、news_rule、function、effect 被读取到 `_input_map`，其中 rule/function 在 `rec_num <= FLAGS_recnum_threshold_for_insert` 时会被跳过（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:251-273`, `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:343-350`）。`read_queue()` 内部按 `context.batch_size()` 反复 `consumer.consume()`，每批调用 `data_prepare_for_engine()`，并累加 `_input_item_size`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:1257-1274`）。

`data_prepare_for_engine()` 会把 `RidTmpInfoPtr` 转成 `DynamicStruct`，按 `item->_content_item.ext().merge_pos()` 写入对应槽位；如果 `merge_pos` 越界则跳过该 item；同时 effect/news_effect 进入 `_effect_input`，其他队列进入 `_no_effect_input`，并清理 `div_status/div_del_tag/div_log` 等运行态字段（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:1276-1345`）。

---

## 6. 真实服务使用示例

### 6.1 Effect 队列如何进入 DiversityMerge

`MergeEffectQueueFunction` 将图文效果队列和视频效果队列合并：它从 `_news_doc_feature_info` 与 `_effect_doc_feature_info` channel 读取数据，调用 `effect_merge()` 合并，然后向多个 channel 发布同一批结果，例如 `EffectMergeQueueDocSampleInfo`、`EffectMergeSet2SetDocSampleInfo`、`EffectMergeSeq2SeqDocSampleInfo`、`GenRLHFDocSampleInfo` 等（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/merge_effect_queue_function.cpp:66-129`）。配置上，`pipeline.conf` 中该 vertex 的输入是 `NewsQueueDocSampleInfo` / `EffectQueueDocSampleInfo` 或 PKGenerate 候选队列，输出是后续 DNN/Set2Set 依赖的多个 channel（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/pipeline.conf:1-39`）。

随后 `ContextDnnCommonNetPredictFunction` 输出 `EffectQueueDnnPredictedResult`，`Set2setPredictFunction` 输出 `EffectQueueSet2SetPredictedResult`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/pipeline.conf:174-220`）。`DiversityMergeFunction` 的 `_effect_rid_queue` 依赖选择表达式是 `is_open_set2set ? EffectQueueSet2SetPredictedResult : EffectQueueDnnPredictedResult`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:842-845`）。

### 6.2 General adjust 预打分如何嵌入 engine 前

在正式 `MultiStreamEngine::run()` 之前，如果 `is_open_dnn_soft_rule && !is_searchc_immersive_request && ua != 87 && enable_general_adjust`，代码会从 `_general_adjust_rule_conf` 中筛出可运行的 soft rule operator，8 并发切分 `effect_input`，调用 `operator_ptr->run_soft_rule(exec_context, &scatter_context, item_sublist)`，等待所有 future 完成后把 `div_res_score` 写入 `general_adjust_score` 并将 `exec_context->_ori_score_key` 切换到 `_general_adjust_score_key`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:730-794`）。这说明"软规则"并不全在 `MultiStreamEngine::run()` 内部执行，GRG 在 engine 前还有一段显式预处理。

### 6.3 输出如何回到 GraphData

`MultiStreamEngine::run()` 的输出是 `SelectStreamContainer output`，代码通过 `output.next_all()` 拿到 `DynamicStruct*` 列表，再转换回 `RidTmpInfo*` 并调用 `push_item_to_result()` 写入 `div_result`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:796-851`）。`push_item_to_result()` 会按 `div_reason` 判断来源队列并设置 `QueueType::LOADS/RULE/FUNCTION/EFFECT`，同时统计视频/图文/动态/短剧/合集等输出数（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:1352-1397`）。

---

## 7. Pitfalls

<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #fef2f2; border-radius: 12px; padding: 24px; margin: 16px 0; border-left: 4px solid #ef4444;">
<style scoped>
.pitfall-card { background: #fff; border-radius: 8px; padding: 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.pitfall-title { font-size: 14px; font-weight: 700; color: #dc2626; margin-bottom: 8px; }
.pitfall-desc { font-size: 13px; color: #374151; line-height: 1.6; }
.pitfall-ref { font-size: 11px; color: #6b7280; margin-top: 6px; font-family: monospace; }
</style>

<div class="pitfall-card">
<div class="pitfall-title">⚠️ 1. 不要只看 multi_stream_select_soft_rule.conf</div>
<div class="pitfall-desc">它只是软规则列表；真正的 stream/executor 结构在 multi_stream_diversity.conf，而 GraphData 入口在 vertex.conf 的 [@engine_vertex]</div>
<div class="pitfall-ref">vertex.conf:798-934 | multi_stream_diversity.conf:8-66</div>
</div>

<div class="pitfall-card">
<div class="pitfall-title">⚠️ 2. Graph condition 与 ExecEngine condition 是两层条件</div>
<div class="pitfall-desc">Graph vertex 依赖中有 condition: is_open_set2set 等条件，ExecEngine operator 也有 condition: ua = 85 等。排查未执行时要分层确认。</div>
<div class="pitfall-ref">vertex.conf:849-858 | multi_stream_diversity.conf:85-88, 602-607</div>
</div>

<div class="pitfall-card">
<div class="pitfall-title">⚠️ 3. Channel 消费是批量 drain</div>
<div class="pitfall-desc">read_queue() 会一直 consume(context.batch_size()) 到空，不能假设只消费一批。</div>
<div class="pitfall-ref">diversity_merge.cpp:1257-1274</div>
</div>

<div class="pitfall-card">
<div class="pitfall-title">⚠️ 4. merge_pos 越界会静默跳过候选</div>
<div class="pitfall-desc">data_prepare_for_engine() 在 slot_index >= input_vec.size() 时 continue，这会导致输入候选未进入 engine。</div>
<div class="pitfall-ref">diversity_merge.cpp:1295-1298</div>
</div>

<div class="pitfall-card">
<div class="pitfall-title">⚠️ 5. custom_context 是共享上下文入口</div>
<div class="pitfall-desc">exec_context->custom_context.ref(...) 把业务上下文暴露给 operator，修改其字段会影响后续 operator 与统计。</div>
<div class="pitfall-ref">diversity_merge.cpp:80-85</div>
</div>

</div>

---

## 8. 调试 checklist

```infographic
infographic list-column-done-list
data
  title DiversityMerge 调试 Checklist
  items
    - label 入口是否选中了预期图
      desc 检查 get_graph_name() 中 UA 映射
      done false
    - label Graph 全局依赖是否注入
      desc 检查 Request/log_id/uid/cuid/ua/ExpInfo
      done false
    - label vertex 是否启用
      desc 检查 [@engine_vertex] 依赖、condition、plugin/conf
      done false
    - label ExecEngine 配置是否存在
      desc 检查 exec_engine.conf 与 multi_stream_diversity.conf
      done false
    - label 输入 channel 是否为空
      desc 检查 read_queue() 的 input_size 和 [fsq_test] 日志
      done false
    - label 软规则是否被 condition 跳过
      desc 检查 exec_context condition values 和 operator condition
      done false
    - label 输出是否被去重或类型过滤
      desc 检查 push_item_to_result() 的 _div_nid_set 和 div_reason
      done false
```

---

## 9. 证据 / 来源

- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/main.cpp:39-89`：服务启动、protocol、Graph 初始化、service 注册。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:48-91`：GraphPool 获取、动态超时、run/reset。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/global.conf:1-49`：Graph 全局依赖与 include。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:798-934`：DiversityMergeFunction engine vertex。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:65-90`：MultiStreamEngine pool 获取、custom_context、bind_graph_dependency。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:1-767`：MultiStreamEngine streams、executors、operators、conditions。

---

## 10. 未确认问题

1. `multi_stream_engine_plugin` 的实现未在当前 GRG 仓库中直接找到；已确认调用点为 `src/process/diversity_merge.cpp:86-90`，配置入口为 `vertex.conf:930-932`，下一步应在外部依赖 `baidu/feed/general/common-processor/plugin/exec_engine.h` 或 `baidu/feed/gr/exec_engine` 库中继续追踪 `bind_graph_dependency()`。
2. `ExecType` 数值（如 `exec_type: 1/3`）的枚举定义未在当前代码库中直接找到；已确认配置使用位置为 `multi_stream_diversity.conf:70-75` 与 `multi_stream_diversity.conf:388-393`，下一步应搜索 exec_engine 基础库枚举。
3. `DiversityRuleOperator`、`RuleSelectOperator`、`DiversityScorePkOperatorV2` 的具体算法实现位于外部 exec_engine/operator 依赖或本仓库其他目录，本文仅基于绑定链路与配置语义分析。
