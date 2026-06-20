# GRG 多样性软规则与 MultiStreamEngine 配置链路

> 日期：2026-05-29  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 `fri.business` 计划项  
> 服务：`feeda-mv-grg`  
> 内网文档：今日计划未提供 `business_doc_urls`，未尝试 KU 读取。

## 1. Role and Purpose

GRG 的多样性合并链路负责把 GRC 返回或 GRG 中间阶段形成的 loads/rule/function/effect 多类候选队列，按槽位、硬规则、软规则、PK 算子和业务上下文合成为最终推荐序列。服务入口 `GenericGRGService::query()` 会根据 UA 选择 `short_micro_video` / `news_updates_dibar` / `default` 图，并运行到 `GRGResponse` 结束节点；其中短视频图的 `DiversityMergeFunction` 是多样性合并的核心 engine vertex（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:64-91`, `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:203-219`, `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:798-934`）。

与普通 GraphProcessor 不同，`DiversityMergeFunction` 内部还会启动 ExecEngine 的 `MultiStreamEngine`：Graph vertex 负责收集 GraphData / Channel 输入，ExecEngine 配置负责定义 loads/rule/function/effect/final_select/effect_pk/merge_pk 等 stream 的选择与 PK 规则（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:65-90`, `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:8-66`）。

## 2. Data Flow

```text
Request
  -> GenericGRGService::query()
  -> get_graph_name(ua): 85/87/123 => short_micro_video, 102 => news_updates_dibar
  -> fill_basic_data_for_graph(): Request/log_id/uid/cuid/ua/tab/ExpInfo/timeout
  -> short_micro_video/global.conf includes vertex/pipeline/expression/div_prepare
  -> GrcRecallFunction emits LoadsQueue/RuleQueue/NewsRuleQueue/FunctionQueue/EffectQueue and Reqnum
  -> MergeEffectQueueFunction merges video/news effect queues
  -> DNN/Set2Set processors produce EffectQueueDnnPredictedResult or EffectQueueSet2SetPredictedResult
  -> DiversityMergeFunction
       -> read_queue(loads/rule/news_rule/function/effect)
       -> data_prepare_for_engine(): DynamicStruct + merge_pos slotting
       -> ExecContext condition values: ua/is_open_dnn_soft_rule/searchc/score_pk/general_adjust/...
       -> optional general_adjust soft-rule pre-pass
       -> MultiStreamEngine.run(input_container_map, output)
       -> push_item_to_result(): dedup + queue_type/resource stats
  -> ResponseFunction consumes DiversityMergeResult and builds GRGResponse
  -> Service swaps GRGResponse into RPC response
```

`short_micro_video/global.conf` 是该图的根配置：它声明 `Request/log_id/uid/cuid/baiduid/ua/tab/tabfrom/product/flow_loc/graph_begin_us/is_debug/is_vip_cuid/ExpInfo` 为 global depend，并 include `vertex.conf`、`graph.conf`、`pipeline.conf`、`expression.conf`、`div_prepare.conf` 等（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/global.conf:1-49`）。

## 3. Key Modules

| 模块 | 文件 | 作用 |
|---|---|---|
| 服务入口 | `src/main.cpp:39-89` | 初始化日志、ReusableRPCProtocol、GlobalInitializer、ExpManager、brpc Server，并注册 `GenericGRGService`。 |
| RPC Graph 调度 | `src/service/grg_service.cpp:35-91` | 处理 `query()`，取 Graph、注入数据、运行图、reset 图实例。 |
| Global Graph 配置 | `conf/plugins/graph/short_micro_video/global.conf:1-49` | 声明全局依赖并 include 各阶段配置。 |
| Diversity engine vertex | `conf/plugins/graph/short_micro_video/vertex.conf:798-934` | 声明 `DiversityMergeFunction` 的输入队列、输出数据、condition、plugin/conf。 |
| Effect 队列合并 | `src/process/merge_effect_queue_function.cpp:66-129` | 合并图文/视频效果队列，并发布给 DNN/Set2Set/GenRLHF 等后续节点。 |
| MultiStreamEngine 绑定 | `src/process/diversity_merge.cpp:65-90` | 从配置取 engine pool，绑定 GraphDependency，注入 custom_context。 |
| 多样性运行主体 | `src/process/diversity_merge.cpp:251-350`, `src/process/diversity_merge.cpp:725-851` | 读取队列、准备输入、运行 engine、生成结果。 |
| ExecEngine 多流配置 | `conf/plugins/exec_engine/multi_stream_diversity.conf:1-767` | 定义 stream、executor、rule/pk operator 及 condition。 |
| 回包函数 | `src/process/response_function.cpp:27-178`, `src/process/response_function.cpp:180-220` | 初始化 q 值监控项并开始构建最终响应。 |

## 4. Entry Point 与 Graph 入口证据

`main.cpp` 注册 `ReusableRPCProtocol`，调用 `GlobalInitializer::instance().init()` 与 `ExpManager::init()`，然后把 `GenericGRGService` 加到 brpc server，服务协议限定为 `baidu_std_reuse`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/main.cpp:39-76`）。这说明 GRG 是 brpc + graph-engine 形态服务，而不是离线任务或 collector pipeline。

`GenericGRGService::query()` 中，服务通过 `ApplicationContext::instance().get<GraphEngine>("graph_engine")` 取图引擎，根据 `request->device_info().ua()` 选择图名，并调用 `graph_engine->try_get(graph_name)` 取池化图实例（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:48-65`）。图运行后，无论成功与否都会走到 `pooled_graph->reset()` 复用图实例（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:85-91`）。

`fill_basic_data_for_graph()` 把 RPC 请求转换为 Graph 全局数据：`Request` 直接 ref 输入 request，`log_id` 写入 controller log id，`uid/cuid/baiduid/tab/tabfrom` 使用字符串 ref，`ua/product/flow_loc/is_debug/is_vip_cuid` 写入标量，并加载 `ExpInfo`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:121-176`）。动态超时也在这里注入 `MutableFrameworkContext::timeout_cntl`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:177-200`）。

## 5. DiversityMergeFunction 的 Graph 依赖

在 `vertex.conf` 中，`DiversityMergeFunction` 是 `[@engine_vertex]`，输出 `DiversityMergeResult`、`SidRollbackCnt`、`DivSuccSize`、`RedPointTopN`、`PkGenerateRLHFInvalidPreviousNum`、`CriticResV5`、`ClientRerankTopnVec`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:798-820`）。

输入分为三类：

1. **队列类输入**：`_loads_queue`、`_rule_queue`、`_news_rule_queue`、`_function_queue` 依赖 PKGenerate 分支表达式选择 sample/example/candidate channel；`_effect_rid_queue` 依赖 `is_open_set2set ? EffectQueueSet2SetPredictedResult : EffectQueueDnnPredictedResult`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:830-845`）。
2. **业务上下文输入**：`Request`、`UaStrContentCFVec`、`Reqnum`、`SidInfo`、`UmsFeature`、`UmsFeatureStatus`、`MsvReadInfo`、`HistoryInterestInfoData` 等（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:860-929`）。
3. **条件输入**：如 `is_open_set2set`、`is_open_pk_generate_v5`、`is_open_pk_generate_longterm_v1`、`is_open_pk_generate_rlhf` 等控制是否依赖特定模型结果（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:849-919`）。

`DiversityMergeFunction` 的 C++ 宏接口与配置一致：它通过 `GRAPH_FUNCTION_DEPEND_MUTABLE_CHANNEL` 声明 loads/rule/function/effect/news_rule 五类 channel，通过 `GRAPH_FUNCTION_EMIT_DATA` 声明 diversity result 与统计输出，通过 `GRAPH_FUNCTION_DEPEND_DATA` 声明 request、reqnum、sid、UMS、readlist、LTV 等依赖（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:1848-1890`）。

## 6. MultiStreamEngine 如何解释“软规则”

`multi_stream_diversity.conf` 中的 stream 决定业务队列如何被选择：loads/rule/function/effect 四类输入先各自进入 select，再由 `merge_pk` 汇合（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:8-66`）。其中 effect 队列除了硬规则，还会在条件满足时运行软规则配置：

- `EffectSoftRuleOperator` 使用 `multi_stream_select_soft_rule.conf`，condition 是 `is_open_dnn_soft_rule = 1 && is_searchc_immersive_request != 1 && ua != 87 && enable_general_adjust = 0`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:602-632`）。
- `EffectContextSoftRuleOperator` 使用 `multi_stream_select_context_soft_rule.conf`，condition 是 `is_open_dnn_soft_rule = 1 && is_searchc_immersive_request != 1 && ua != 87`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:633-662`）。
- Dibar 场景 `ua = 87` 使用 `dibar_multi_stream_select_soft_rule.conf`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:664-674`）。
- 搜 C 场景 `is_searchc_immersive_request = 1` 使用 `searchc_multi_stream_select_soft_rule.conf`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:675-685`）。

这些 condition 的值来自 `DiversityMergeFunction::process()` 手动写入的 ExecContext：代码将 `is_open_dnn_soft_rule`、`is_searchc_immersive_request`、`is_hit_searchc_mmr_refresh_exp`、`is_hit_diversity_score_pk_exp`、`enable_general_adjust`、`high_quality_user`、`ua`、`is_set2set_data_valid` 等值写入 `exec_context->add_condition_value()`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:178-208`）。因此，排查“某条软规则为什么没跑”时不能只看软规则配置本身，必须同时看 Graph 表达式、DiversityMerge 写入的 condition value，以及 ExecEngine operator condition。

## 7. soft-rule 前置预处理：general_adjust

业务上还有一类“软规则”不是由 `MultiStreamEngine::run()` 内部直接触发，而是在 engine 前显式并发执行。当 `is_open_dnn_soft_rule && !is_searchc_immersive_request && ua != 87 && enable_general_adjust` 成立时，`DiversityMergeFunction` 从 `_general_adjust_rule_conf` 中筛选可运行 operator，8 并发切分 effect queue，逐段调用 `operator_ptr->run_soft_rule(exec_context, &scatter_context, item_sublist)`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:730-774`）。

这段逻辑结束后，effect 队列每个 item 的 `general_adjust_score` 被设置为 `div_res_score`，function/loads/rule 队列的 `general_adjust_score` 被设置为 `offer_score`，并把 `exec_context->_ori_score_key` 切换为 `_general_adjust_score_key`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:776-794`）。所以，`enable_general_adjust` 不只是一个配置开关，它会改变后续 PK 看到的“原始分”字段。

## 8. MultiStreamEngine 执行与结果回流

输入准备后，代码构造 `SelectStreamContainerMap input_container_map`，把 `_input_map` 中每个槽位的 `RidTmpInfo*` vector reinterpret 为 `DynamicStruct*` vector，并为每个 vector 建立 `SelectStreamContainer`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:696-706`）。

正式执行分三步：

1. `_diversity_engine->run_prepare(input_container_map)` 做 engine 预处理（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:725-729`）。
2. `_diversity_engine->set_loop_num(result_num)` 设置循环槽位数，然后 `_diversity_engine->run(input_container_map, &output)` 产出结果（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:796-806`）。
3. `output.next_all()` 取回结果，逐条 `push_item_to_result()` 写入 GraphData `DiversityMergeResult`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:808-851`）。

`push_item_to_result()` 有三个业务副作用：按 nid 去重、按 `div_reason` 标记来源队列、统计输出资源类型。具体地，`div_reason` 包含 `loads` 归类为 `QueueType::LOADS`，`rule_select` 或 `ignore_rule_insert` 归类为 `RULE`，`function_select` 归类为 `FUNCTION`，其他归类为 `EFFECT`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:1352-1378`）。

## 9. ResponseFunction 如何消费结果

`ResponseFunction` 的初始化中为 loads/rule/function/effect 四类队列注册了 q 值监控项，例如 final_score、offer_score、q_ratio_score、div_res_score、context_dnn_finish_score、playtime_score、set2set_res_score、pk_generate_score_v5、pk_generate_rlhf_score 等（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/response_function.cpp:27-107`）。这说明 diversity 阶段输出的 `queue_type/div_res_score` 等字段会直接影响最终响应阶段的日志与监控维度。

`ResponseFunction::process()` 开始时检查 `_recall_nid_vec/_recall_nid_map/_longterm_interest_sample_luopan/_recomm_sentence/_one_airecomm_sentence/_luopan_switch/_ua_str_contentcf_vec` 等依赖，随后 `_response.emit()` 构建最终响应对象，并向 pass-through response 添加 `SofaMicroVrIONode-microvideo_grc` content key（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/response_function.cpp:180-220`）。服务层最终在 `GenericGRGService::run()` 中把 `GRGResponse` 从 end node swap 到 RPC response（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:211-245`）。

## 10. Configuration and Deployment

服务启动端口默认 8888，gflags 中还包含 dapper 采集配置、worker 线程配置等：`port`、`idle_timeout_s`、`dapper_conf_file`、`dapper_collector_name`、`open_num_threads_worker`、`threads_worker`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/main.cpp:18-24`）。brpc Server 启用协议是 `baidu_std_reuse`，并注册 `GenericGRGService` 与 `/graph_view => graph_view` 的 HTTP graph view 服务（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/main.cpp:65-84`）。

Dapper 采集线程通过 `START_COLLECTOR_WORKER(FLAGS_dapper_conf_file, FLAGS_dapper_collector_name, FLAGS_dapper_process_time_ms)` 启动，服务退出时 `STOP_COLLECTOR_WORKER`（`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/main.cpp:82-89`）。这意味着 diversity 阶段的 `SIA_*` 和日志统计有独立采集链路，排查延迟时应结合 dapper 指标和服务日志。

## 11. Notable Patterns

1. **GraphEngine + ExecEngine 双层 DAG**：外层 Graph 负责 RPC 请求生命周期和 Data/Channel 编排；内层 MultiStreamEngine 负责多流选择、硬规则、软规则和 PK（`conf/plugins/graph/short_micro_video/vertex.conf:798-934`, `conf/plugins/exec_engine/multi_stream_diversity.conf:8-66`）。
2. **业务上下文通过 custom_context 注入**：`_diversity_context` 被 ref 到 `exec_context->custom_context`，同时 process 阶段不断设置 logid、cuid、ua、sid、trace context、condition values（`src/process/diversity_merge.cpp:80-85`, `src/process/diversity_merge.cpp:137-168`）。
3. **Graph condition 与 Exec condition 叠加**：Graph 侧通过 `condition:` 控制依赖是否存在，Exec 侧通过 `condition:` 控制 operator 是否运行（`conf/plugins/graph/short_micro_video/vertex.conf:849-919`, `conf/plugins/exec_engine/multi_stream_diversity.conf:602-685`）。
4. **Channel drain + slotting**：候选队列不是普通 vector，而是 `MutableChannelConsumer`；读取后按 `merge_pos` 放到槽位 vector（`src/process/diversity_merge.cpp:1257-1345`）。
5. **结果统计与响应监控耦合**：`push_item_to_result()` 写 queue_type，`ResponseFunction` 注册 queue/resource q 值监控项（`src/process/diversity_merge.cpp:1352-1378`, `src/process/response_function.cpp:27-153`）。

## 12. Evidence / Sources

- `notes/weekly-topic-selection/daily-plan-20260529.json:305-309`：今日主题为 `ExecEngine MultiStreamEngine 绑定 GraphDependency` 与 `GRG 多样性软规则与 MultiStreamEngine 配置`。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/main.cpp:39-89`：服务初始化与注册。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:48-91`：Graph 获取、运行、reset。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/global.conf:1-49`：Graph 根配置。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:798-934`：DiversityMergeFunction 主配置。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:65-90`：MultiStreamEngine 获取与绑定。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:725-851`：engine 运行与结果生成。
- `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg/conf/plugins/exec_engine/multi_stream_diversity.conf:1-767`：多流配置。

## 13. 未确认问题

1. 未在当前 GRG 仓库中直接找到 `multi_stream_engine_plugin` 的实现文件；已确认配置与调用点为 `vertex.conf:930-932`、`diversity_merge.cpp:86-90`，下一步应在外部依赖 `baidu/feed/general/common-processor/plugin/exec_engine.h` 或 `baidu/feed/gr/exec_engine` 中追踪。
2. 未在当前仓库中直接找到 `ExecType` 数值 1/3 的枚举说明；当前只能确认其配置位置与业务使用，不展开解释其调度细节。
3. 今日计划未提供业务 KU 文档 URL，未做内网文档读取；如需补充业务背景，可后续提供与 GRG 多样性、soft rule 或 MultiStreamEngine 相关 KU URL。