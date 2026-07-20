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
---

## 七、业务代码库适配分析
> **分析时间**：2026-07-20T19:19:49.745297
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，目标技术/目标库在 **feeda-mv-grg** 与 **feeda-mv-grc** 中已经有少量落地使用，主要集中在多样性规则、scatter/rollback、特征处理等性能敏感模块。例如 `operator/diversity/sor_factor_rule.cpp`、`operator/diversity/diehard_topic_soft_rule.cpp`、`operator/diversity/rollback_rule.cpp`、`operator/diversity/scatter_context.cpp` 等文件可作为后续迁移时的参考样例。

- 两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 的使用规模很大：  
  - **feeda-mv-grg**：`std::vector` 1969 次、`std::string` 2443 次、`std::unordered_map` 734 次。  
  - **feeda-mv-grc**：`std::vector` 8442 次、`std::string` 7170 次、`std::unordered_map` 2834 次。  
  这说明如果目标技术是面向容器、字符串、哈希表或小对象优化的替代方案，在业务代码中具备较高迁移潜力。但考虑到 GRG/GRC 属于在线推荐链路，建议优先选择 **DiversityMergeFunction、MultiStreamEngine 输入准备、规则算子、召回汇聚热路径** 等高频模块做增量适配，而不是全仓库机械替换。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标库使用：**10 个文件**，主要集中在多样性规则和结果处理相关逻辑中：
  - `operator/diversity/sor_factor_rule.cpp`
  - `operator/diversity/diehard_topic_soft_rule.cpp`
  - `process/rkcj_v3_get_result_function.cpp`
  - `operator/diversity/diversity_sim_rule.cpp`
  - `operator/diversity/slow_in_rule.cpp`

- 现有 `std` 等价物使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 从业务链路看，feeda-mv-grg 的热点主要在：
  - `src/process/diversity_merge.cpp`
    - 负责读取 loads/rule/news_rule/function/effect 多类队列。
    - 构造 `SelectStreamContainerMap input_container_map`。
    - 将候选按 `merge_pos` 分槽。
    - 调用 `MultiStreamEngine::run()`。
    - 将输出结果写回 `DiversityMergeResult`。
  - `src/process/response_function.cpp`
    - 消费 diversity 输出。
    - 维护大量 q 值监控字段与响应构造逻辑。
  - `operator/diversity/*.cpp`
    - 实现软规则、硬规则、相似性规则、慢插规则等。
    - 对候选 item 做大量遍历、打分、过滤和上下文判断。

- 当前典型代码仍大量使用标准容器，例如：
  - `model/model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
  - `model/paddle_model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```

- 结论：  
  feeda-mv-grg 是更适合优先适配的代码库。尤其是 `DiversityMergeFunction` 处在短视频图的核心链路中，且内部存在大量候选集合遍历、哈希去重、字符串 condition、规则配置匹配和多流容器构造，具备明确的性能优化空间。

#### feeda-mv-grc：召回汇聚服务

- 已发现目标库使用：**6 个文件**，主要集中在召回策略、多样性规则、特征处理和上下文模块：
  - `strategy/virtual_mark_select.cpp`
  - `operator/diversity/rollback_rule.cpp`
  - `processor/vids_gcf_embeddings.cpp`
  - `operator/diversity/author_vec_diversity_rule.cpp`
  - `operator/diversity/scatter_context.cpp`

- 现有 `std` 等价物使用规模：
  - `std::vector`：8442 次，分布在 1279 个文件
  - `std::string`：7170 次，分布在 1234 个文件
  - `std::unordered_map`：2834 次，分布在 639 个文件

- 从扫描示例看，GRC 中 `std` 容器不仅出现在召回主流程，也出现在服务可视化、HTTP debug、图依赖分析等非核心链路中，例如：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    ```
  - `service/grc_http_service.cpp`
    ```cpp
    static std::vector<std::string> colors{...};
    ```
  - `service/grc_http_service.cpp`
    ```cpp
    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    ```

- 结论：  
  feeda-mv-grc 的 `std` 容器使用规模显著大于 GRG，但其中相当一部分可能位于管理、HTTP、调试、配置解析等非请求核心路径。适配时应优先关注：
  - `operator/diversity/rollback_rule.cpp`
  - `operator/diversity/author_vec_diversity_rule.cpp`
  - `operator/diversity/scatter_context.cpp`
  - `processor/vids_gcf_embeddings.cpp`
  - `strategy/virtual_mark_select.cpp`

---

### 3. 💡 适用性评估与建议

- **建议 1：优先在 `src/process/diversity_merge.cpp` 的候选槽位与输入容器构造阶段做小范围替换**
  - 适用场景：
    - `DiversityMergeFunction::process()` 中读取五类 channel。
    - `data_prepare_for_engine()` 中按 `merge_pos` 将 `RidTmpInfo*` 放入不同槽位。
    - 构造 `SelectStreamContainerMap input_container_map` 并传给 `MultiStreamEngine`。
  - 优化方向：
    - 对固定或上界明确的候选列表，评估使用目标库中的小 vector / inline vector / flat vector 类容器，减少小规模 vector 的堆分配。
    - 对 `_input_map`、`input_container_map` 这类短生命周期哈希结构，评估使用目标库中的 flat hash map / dense hash map 类结构，降低哈希桶分配和指针跳转成本。
  - 注意点：
    - 当前代码存在将 `RidTmpInfo*` vector reinterpret 为 `DynamicStruct*` vector 的逻辑，这类地方对内存布局和指针类型非常敏感，不能直接机械替换底层容器。
    - 建议先封装局部临时容器，保持传给 ExecEngine 的接口不变。

- **建议 2：在 `operator/diversity/*.cpp` 中复用已有目标库使用经验，优先迁移规则内部临时集合**
  - 可参考文件：
    - `operator/diversity/sor_factor_rule.cpp`
    - `operator/diversity/diehard_topic_soft_rule.cpp`
    - `operator/diversity/diversity_sim_rule.cpp`
    - `operator/diversity/slow_in_rule.cpp`
  - 适用场景：
    - 软规则中对候选 item 的临时打分集合。
    - 相似性规则中的 nid、author、topic、category 去重集合。
    - PK 或 rule 过程中构造的局部 map/set。
  - 优化方向：
    - 将局部 `std::unordered_map` / `std::unordered_set` 替换为目标库的高性能哈希容器。
    - 将生命周期仅限单次规则执行的 `std::vector` 替换为目标库的小对象优化容器。
  - 收益判断：
    - 这些规则在 `MultiStreamEngine` 内部可能按槽位多轮执行，单次优化会被请求量和候选规模放大。
    - 已有目标库使用文件可作为编码规范和 ABI 兼容参考，迁移风险低于全新模块。

- **建议 3：`src/process/response_function.cpp` 适合做字符串与监控字段的只读化/预分配优化**
  - 适用场景：
    - 初始化阶段注册大量 q 值监控项。
    - `ResponseFunction::process()` 构造最终响应和 pass-through content key。
  - 优化方向：
    - 对固定监控 key、固定 content key，避免每次请求重复构造 `std::string`。
    - 可评估使用 string view / constexpr 字符串 / 静态常量，降低请求路径上的临时字符串分配。
    - 对响应 item vector，如果有明确 `reqnum` 或 topN 上限，可提前 `reserve()`，或迁移为目标库提供的预分配容器。
  - 具体文件：
    - `src/process/response_function.cpp`
    - `data/base.h`
    - 与响应 item 结构定义相关的数据头文件。
  - 预期收益：
    - 单点 CPU 收益可能不如 diversity 规则明显，但响应阶段稳定出现在每次请求中，适合做低风险优化。

- **建议 4：feeda-mv-grc 中优先迁移召回/多样性热路径，暂不优先处理 `service/grc_http_service.cpp`**
  - 优先文件：
    - `operator/diversity/rollback_rule.cpp`
    - `operator/diversity/author_vec_diversity_rule.cpp`
    - `operator/diversity/scatter_context.cpp`
    - `processor/vids_gcf_embeddings.cpp`
    - `strategy/virtual_mark_select.cpp`
  - 暂缓文件：
    - `service/grc_http_service.cpp`
  - 原因：
    - `service/grc_http_service.cpp` 中虽然有 `std::unordered_map<std::string, std::vector<int>>`、`std::vector<std::string>` 等结构，但更像 graph view / HTTP debug / 可视化链路，不一定在主请求路径高频执行。
    - 优先迁移在线召回、embedding、scatter、rollback 相关逻辑，收益更确定。
  - 优化方向：
    - 对 embedding id 列表、author/category 去重集合、rollback 候选集合进行容器替换。
    - 对只读字符串 key 或配置 key 引入轻量字符串视图，减少拷贝。

- **建议 5：模型预测接口暂不直接改签名，先从调用点临时容器入手**
  - 涉及文件：
    - `model/model.h`
    - `model/paddle_model.h`
  - 当前接口：
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
  - 建议：
    - 不建议第一阶段直接将接口参数从 `std::vector` 改为目标库容器，否则会影响所有模型实现类和调用方。
    - 可以先在上游构造阶段减少临时 vector 的重复分配，例如复用 buffer、提前 `reserve()`、减少中间拷贝。
    - 如果目标库支持 span / array view 类抽象，可在后续阶段新增重载接口，而不是直接替换原有虚函数签名。
  - 原因：
    - 虚函数接口属于 ABI 和继承体系边界，改动范围大。
    - 模型预测链路可能还和 paddle、cube、general_predict 等外部库交互，容器类型兼容性需要单独验证。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：GraphEngine / ExecEngine 边界存在强类型与内存布局假设**
  - `src/process/diversity_merge.cpp` 中 MultiStreamEngine 输入构造涉及 `RidTmpInfo*`、`DynamicStruct*`、`SelectStreamContainer` 等结构。
  - 如果目标容器的内存连续性、迭代器失效规则、元素地址稳定性与 `std::vector` 不一致，可能破坏现有 reinterpret 或指针传递逻辑。
  - 建议：
    - 不要直接替换跨 ExecEngine 边界传递的容器类型。
    - 先替换函数内部临时 map/vector。
    - 对传入 engine 的容器保持原始接口兼容。

- **风险 2：请求级上下文和 channel 数据生命周期复杂**
  - `DiversityMergeFunction` 依赖 `MutableChannelConsumer` drain 队列，并将 item 指针放入槽位集合。
  - 候选对象生命周期可能由 GraphData、Channel 或上游 Processor 管理。
  - 如果迁移时引入移动语义、容器扩容、对象重排，可能导致悬垂指针或重复释放。
  - 建议：
    - 对存放 `RidTmpInfo*`、`RidTmpInfoPtr` 的容器保持指针语义不变。
    - 优先优化索引、key、临时状态集合，而不是移动候选对象本身。

- **风险 3：`std::unordered_map` 替换需要关注哈希、相等比较和遍历顺序**
  - 多样性规则、PK、soft rule 中可能存在隐含的遍历顺序依赖。
  - 高性能哈希表通常不保证与 `std::unordered_map` 相同的 bucket 行为和迭代顺序。
  - 建议：
    - 对影响最终排序、打分、去重结果的 map/set 替换前后做 diff。
    - 灰度时重点观察：
      - `DiversityMergeResult` topN 变化。
      - `queue_type` 分布变化。
      - `div_res_score`、`offer_score`、`pk_generate_score_v5`、`pk_generate_rlhf_score` 等监控项变化。

- **风险 4：全仓库机械迁移收益不稳定，且可能增加维护成本**
  - feeda-mv-grc 中 `std::vector` 和 `std::string` 使用规模非常大，但并非都在热点路径。
  - 例如 `service/grc_http_service.cpp` 中的 graph view / HTTP 参数解析逻辑，即使迁移也未必能改善主链路延迟。
  - 建议：
    - 按链路优先级推进：
      1. GRG：`src/process/diversity_merge.cpp`
      2. GRG：`operator/diversity/*.cpp`
      3. GRC：`operator/diversity/*.cpp`、`processor/vids_gcf_embeddings.cpp`
      4. 响应与日志：`src/process/response_function.cpp`
      5. 非核心 HTTP/debug/config 模块
    - 每一阶段都用线上 p99、CPU、内存分配次数和结果一致性作为验收指标。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
