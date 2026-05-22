# 下周代码理解候选选题（2026-05-22）

> 生成时间：2026-05-22 16:00:50 CST  
> 检索代码库：`/home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc`、`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg`  
> 内网文档检索状态：失败（`ku-doc-manage` CLI 可执行，但调用时提示缺少 `get-ugate-token` skill，无法完成知识库检索；本次业务主题文档证据均标记为“未检索到，需人工补充”）  
> 用户动作：请从“基础库方向”和“业务方向”中分别选择 7 个，作为下周每日推荐内容。

## 一、选择说明

- 建议选择规则：优先选 P0；同一周内建议覆盖“服务入口/图执行/召回/正排/过滤/调权/监控”多个链路，避免 7 个主题都集中在同一类算子。
- 主题优先级含义：P0 = 下周最建议优先深挖，和 GRC/GRG 主链路或排障高频问题强相关；P1 = 很适合形成专题，但可排在主链路之后；P2 = 有价值但证据链或业务背景仍需补充。
- 难度含义：低 = 主要读单模块/单配置；中 = 需要跨 2-4 个模块追数据；高 = 需要结合图配置、插件、RPC/缓存或外部协议一起追。

## 二、基础库方向候选（15 个）

| # | 主题 | 建议窄 scope | 为什么值得讲 | 代码证据 | 产出形式 | 优先级 | 难度 |
|---|------|---------------|--------------|----------|----------|--------|------|
| 1 | brpc `ClosureGuard` 与 reusable RPC 生命周期 | 只讲 GRC/GRG service 方法里 `done` 的 guard、`ReusableRPCProtocol::Closure` 的 `send_response/release/wait` 顺序 | Feed 服务入口最容易出现“响应已发但图还在跑/对象释放时机错误”的理解断点；读懂它是进入图执行链路的第一步 | GRC：`src/service/grc_service.cpp:151-156` Controller/Closure 入参、`:341-343` `send_response()` 后 `closure.wait()`、`:449` `release()`；GRG：`src/service/grg_service.cpp:206-246` 同类流程 | 深挖文档 + 时序小图 | P0 | 高 |
| 2 | graph-engine `Closure::get()` / `wait()` 语义 | 只讲 `graph->run(end)` 返回的 graph closure 如何同步结果、如何和 brpc closure 组合 | 排查请求卡住、部分 vertex 未完成、响应延迟时必须理解 graph closure 与 RPC closure 的边界 | GRC：`src/service/grc_service.cpp:293-313` 多种 `graph->run(...)` 与 `closure.get()`；GRG：`src/service/grg_service.cpp:218-219` `graph->run(end_node, err_node)` | 排障 checklist + 时序图 | P0 | 高 |
| 3 | gflags flagfile 到运行时开关 | 只讲 `main.cpp` 中 `SetCommandLineOption("flagfile", "./conf/gflags.conf")` 与 `FLAGS_*` 如何影响 server 线程、超时、dapper | 配置开关是线上行为差异的常见来源；同一二进制在不同 conf 下表现可能完全不同 | GRC：`src/main.cpp:37-45` flag 定义、`:90` flagfile、`:108-112` server options；GRG：`src/main.cpp:18-24`、`:41`、`:70-74` | 配置到代码映射表 | P0 | 低 |
| 4 | bvar 指标注册、expose 与排障入口 | 只讲 request/response size、graph arena、plugin latency、dict diff 等指标如何注册和写入 | 适合把“看代码”连接到“看监控”；服务延迟/容量/字典异常都依赖 bvar | GRC：`src/service/grc_service.h:50-54` bvar 成员、`src/service/grc_service.cpp:51-54` expose、`:364` request size；GRG：`src/plugin/predictor.cpp:21,38` latency；共同：`src/bvar_grc.cc:19-21` dict diff | 排障 checklist + 指标表 | P0 | 中 |
| 5 | graph-engine config 的 `processor/depend/@emit` 数据契约 | 只讲 graph conf 中一个节点如何声明输入输出，不展开所有 processor 实现 | 业务链路追踪的基础；没有 depend/emit 视角很难理解 GRC/GRG 的数据流 | GRC：`conf/plugins/graph/graph.conf:1-5` `SmallflowProcessor` emit `SidInfo/SidSet`；GRG：`conf/plugins/graph/short_micro_video/graph.conf:1-5` `SmallflowFunction` emit `SidInfo/SidSet` | 数据流小图 + 读图方法 | P0 | 中 |
| 6 | `ExpressionProcessor` 与 graph parser 的表达式节点 | 只讲 parser 如何把表达式/组合配置接入 graph builder，以及表达式依赖失败如何影响下游 | 很多“没有写 C++ 但图里有数据”的节点来自表达式；排查 GraphData missing 必须会看 | GRG：`src/plugin/graph_parser.h:17,25` 引入 `BthreadGraphExecutor` 与 `ExpressionProcessor`；`src/plugin/graph_parser.cpp:34` combo 实验参数需额外读取 | 深挖文档 + 诊断 checklist | P1 | 高 |
| 7 | `babylon::Any` / SidMap 存放实验参数 | 只讲 SidPlugin 中 `SidMap = unordered_map<uint32_t, shared_ptr<unordered_map<uint32_t, Any>>>` 的数据形态 | SID/实验参数是推荐策略开关的核心；Any 类型隐藏了具体值，读代码时容易断链 | GRG：`src/plugin/sid.h:22-30` SidMap/SidMapPtr/get_sid_map；GRC/GRG conf：`conf/sid.conf`、`conf/sid_new.conf` | 数据结构拆解文档 | P1 | 中 |
| 8 | bthread Mutex 与 thread_local 锁池 | 只讲 HTTP 管理接口中的 `thread_local shared_ptr<bthread::Mutex>` 与全局 vector 锁池 | 这是 bthread 环境下混用线程局部状态与互斥锁的具体样本，可延伸到协程/线程语义差异 | GRC：`src/service/grc_http_service.cpp:194` thread_local mutex；`src/service/grc_http_service.h:64-83` 获取/加锁、`:138-161` 锁池和保护锁 | 小实验 + 风险 checklist | P1 | 中 |
| 9 | protobuf `CopyFrom/Swap/ParsePartialFromString` 的所有权与性能边界 | 只讲服务响应 CopyFrom、PCS response Swap 清空、GCMS pb parse 三类用法 | Feed 服务大量 protobuf 读写；Copy/Swap/Parse 的语义与性能影响线上延迟和 core dump 判断 | GRC：`src/service/grc_service.cpp:323-329` response `CopyFrom`；PCS：`src/user_data/pcs_precise_parallel_commented.cpp:123-127,823-826` `Swap`；GRC GCMS：`src/plugin/ifcs_component.cpp:91-96` `ParsePartialFromString` | 深挖文档 + 注意事项表 | P0 | 高 |
| 10 | jemalloc 引入与服务内存观测入口 | 只讲 GRC main 中 jemalloc include、MallocLog 缓冲，以及可如何连接到 malloc profiling/延迟毛刺排查 | 内存分配器会影响 P99；虽然代码证据较窄，但适合做“从服务入口识别 allocator”专题 | GRC：`src/main.cpp:24` `<jemalloc/jemalloc.h>`、`:66-70` `MallocLog`；GRG 未见同等 include（本次限定检索内） | 排障 checklist / 小实验 | P2 | 中 |
| 11 | dapper collector worker 宏与采样日志 | 只讲 `START_COLLECTOR_WORKER`、`SamplingDataCollectorManager::init` 与 dapper flags | 推荐服务链路追踪和漏斗分析依赖 dapper；读服务入口可知道采样日志是否启动 | GRC：`src/main.cpp:41-43` dapper flags、`:52-56` funnel init、`:126` start worker；GRG：`src/main.cpp:20-22`、`:26-30`、`:82` | 启动链路图 + 配置说明 | P1 | 中 |
| 12 | brpc Channel/Stub 同步调用与动态超时控制 | 只讲 predictor/model/grc plugin 中 `baidu::rpc::Controller cntl`、stub 调用、DTController 参数 | 多数外部模型/召回服务走 brpc；理解 controller 是排查超时/错误码的基础 | GRG：`src/plugin/predictor.cpp:14-18,30-34` channel/stub/predict；`src/plugin/model_service.cpp:47-89` model RPC；GRG service：`src/service/grg_service.h:12` DTController | 排障 checklist | P0 | 中 |
| 13 | dict/common_dict 热更新读取模式 | 只讲 `GET_COMMON_DICT`、dict manager、VIP/target cuid 读取 | 字典是灰度、VIP、配置化策略常用入口；读懂宏和插件可解释“为什么某些用户走特殊逻辑” | GRG：`src/service/grg_http_service.cpp:23` dict manager；`src/service/grg_service.cpp:14,363-369` common_dict/register + GET_COMMON_DICT；`src/plugin/vip_cuid.cpp:17-23` VIP 命中 | 深挖文档 + 数据流小图 | P1 | 中 |
| 14 | cache plugin 的 shard/ttl/multi_get 基础设施 | 只讲 `CachePlugin` 的 shard、TTL、`multi_get` 接口，不展开业务特征 | DocFeature/Feature cache 是延迟和命中率关键点；适合沉淀可迁移缓存组件读法 | GRG：`src/plugin/cache_plugin.h:41-52` cache 成员；`src/plugin/cache_plugin.cpp:37-63` run/set/multi_get/dump_stat | 深挖文档 + 小实验 | P1 | 中 |
| 15 | GraphFunction 注册宏与组件注入 | 只讲 `REGISTER_GRC_FUNCTION`、`BABYLON_REGISTER_*`、`BABYLON_MEMBER` 如何把类接入配置 | 很多 processor/plugin 不在 main 显式 new；读懂注册宏才能从配置定位代码 | GRC PCS：`src/user_data/pcs_precise_parallel_commented.cpp:847-898` `BABYLON_MEMBER`/Graph data、`:908` `REGISTER_GRC_FUNCTION`；GRG：`src/plugin/predictor.cpp:72-76` custom component；`src/plugin/gcms_news_parser.cpp:55` factory component | 读代码方法论 + 索引表 | P0 | 中 |

## 三、业务方向候选（15 个）

| # | 主题 | 建议窄 scope | 业务问题 | 代码切入点 | 文档证据 | 数据链路 | 优先级 | 难度 |
|---|------|---------------|----------|------------|----------|----------|--------|------|
| 1 | 下发历史在 GRC 过滤/补召回中的作用 | 只讲 history 数据如何产生过滤 nid 与 fetch back nid，不泛讲所有历史 | 避免用户重复看到内容，同时在需要时把可豁免内容补回来 | GRC：`src/processor/history/filter_history_info.cpp`、`src/processor/history/history_fetch_back_nids_function.cpp`；GRG：`src/process/recall_history_nid_merge_function.cpp` | 未检索到，需人工补充（ku 缺少 get-ugate-token） | HistoryRead/Filter（待验证） -> filtered/fetch_back nids -> Recall/Merge/Filter consumer | P0 | 高 |
| 2 | 卡片下发历史与卡片召回合并 | 只讲 card recall merge 如何与历史/去重协同，不扩展全部卡片业务 | 卡片类资源的重复展示体验不同于普通视频，需要单独理解 | GRC：`src/processor/video_launch/card_recall_merge.cpp`、`src/data/dup_vfs.cpp`、`src/plugin/dup_service.cpp`；GRG 架构图提到“卡片 mock 兜底”：`gcms-integration-architecture.html:142,266` | 未检索到，需人工补充 | Card Recall -> Card Merge -> Dup/History Filter -> Response | P0 | 高 |
| 3 | PCS 精准参数并行调用链路 | 只讲 `PcsPreciseParallelFunction`：填充请求、调用 PCS、解析参数给下游 adjuster | PCS 决定大量调权参数，是理解精排/调权策略的关键外部系统 | GRC：`src/processor/video_launch/pcs_precise_parallel.cpp`、`src/user_data/pcs_precise_parallel_commented.cpp:99-204`、`src/operator/adjuster/precise/microvideo_adjust_merge_get_result_from_pcs.cpp` | 未检索到，需人工补充 | Request/Feature -> PcsRequest -> PcsCommonPlugin RPC -> PcsResultMap -> Precise Adjuster | P0 | 高 |
| 4 | IDM/意图方向的本地替代切入：user intent predictor | 代码中未直接命中 IDM；建议本周只讲 user intent predictor 如何产出 `intent_q` 并进入 PCS/调权 | 用户意图是召回/排序个性化的核心信号；即使 IDM 文档待补，也能先从意图链路入手 | GRC：`src/processor/video_launch/user_intent_predict.cpp`、`src/processor/video_launch/user_intent_score.cpp`、`src/plugin/user_intent_predictor.cpp`、`src/operator/adjuster/precise/user_intent_pre_adjuster.cpp` | 未检索到，需人工补充；IDM 关键词在限定代码中未命中 | User features -> UserIntentPredictor -> intent_q/score -> PCS fill_request / Adjuster | P1 | 中 |
| 5 | 置顶容灾/恢复指标方向 | 本次未命中明确“置顶容灾”代码；建议先讲 rank recovery/backup 的监控定位方法，后续人工补业务文档 | 容灾是线上推荐稳定性的关键，但需要准确文档避免误读 | 本次限定检索 `置顶/top/recovery/backup` 未命中可确认主链路；可从 `src/strategy/short_micro/rank_strategy.cpp`、`src/processor/video_launch/model_rerank.cpp` 继续人工查 | 未检索到，需人工补充 | 待验证：Rank/Recall failure -> Recovery candidates -> Response top positions | P2 | 高 |
| 6 | 个性化召回 quota 分配 | 只讲 recall_personalized_quota 与 quota_distribute_for_grg 如何影响各召回队列配额 | 召回多路融合中配额决定供给结构，是业务效果和多样性的关键控制点 | GRC：`src/processor/recall_personalized_quota.cpp`、`src/strategy/short_micro/quota_distribute_for_grg.cpp`、`src/processor/video_launch/pcs_gr_recall_quota_function.cpp`；GRG：`src/process/quota_calculate.cpp` | 未检索到，需人工补充 | User/Context -> PCS/Quota function -> Queue quota -> Recall/Merge consumer | P0 | 高 |
| 7 | combo 实验参数如何影响过滤/调权 | 只讲 combo 配置被 graph parser 额外读取后，进入 combo filter/adjuster 的窄链路 | combo 常用于小流量策略组合，容易出现“配置生效但代码里找不到”的问题 | GRG：`src/plugin/graph_parser.cpp:34` combo 实验参数读取；GRC：`src/processor/filter/combo_test_filter.cc`、`src/operator/adjuster/sketchy/combo_test_adjuster.cpp`；GRG：`src/operator/diversity/first_refresh_push_cate_combo_soft_rule.cpp` | 未检索到，需人工补充 | sid/exp params -> graph_parser combo params -> combo filter/adjuster -> candidate score/filter | P0 | 中 |
| 8 | 正排 GCMS 补全与 parser 字段映射 | 只讲 GRG `gcms_parser.cpp` 如何把 `ContentFeature` 映射到 `VideoInfo`，并对比 GRC IFCS/GCMS 组件 | 正排补全是召回结果变成可排序/可过滤 item 的关键步骤 | GRG：`gcms-integration-architecture.html:60-62,106-131,188-192`、`src/plugin/gcms_parser.cpp:25,85-120`；GRC：`src/plugin/ifcs_component.cpp:91-151`、`src/parser/gcms_parser.cpp` | 本地架构 HTML 可用；内网文档未检索到 | Recall rids -> FillMetaPipeline/GCMS Plugin -> ContentFeature -> Parser -> VideoInfo/RidTmpInfo | P0 | 高 |
| 9 | 召回汇聚：GRC merge_recall 到 GRG grc_recall_function 对比 | 只讲 GRC 本地多路召回 merge 与 GRG 调 GRC 的边界，不把两者混为一谈 | 跨 GRC/GRG 服务边界是推荐架构理解主线：谁召回、谁汇聚、谁生成响应 | GRC：`src/processor/merge_recall.cpp`、`src/parser/recall_parser.cpp`、`src/plugin/trigger_rpc.h`；GRG：`src/process/grc_recall_function.cpp`、`src/process/grc_news_recall_function.cpp` | 未检索到，需人工补充 | GRC RecallParser/Trigger RPC -> MergeRecall -> GRG GrcRecallFunction -> downstream filter/rank | P0 | 高 |
| 10 | dup service 去重读写边界 | 只讲 GRC `dup_service` 与 `dup_vfs` 的数据结构/调用点，和 history 去重区别开 | 去重是重复曝光体验的基础；dup 与 history 常被混淆 | GRC：`src/plugin/dup_service.h/cpp`、`src/data/dup_vfs.h/cpp`；History 参考：`src/processor/history/filter_history_info.cpp` | 未检索到，需人工补充 | Response/showlist or request dup data（待验证） -> DupService/DupVfs -> Filter/Merge consumer | P1 | 高 |
| 11 | Set2set 预测与 soft rule | 只讲 set2set predictor/function 产出如何被 diversity soft rule 消费 | Set2set 是集合级重排/多样性的重要机制，适合形成一次端到端代码理解 | GRC：`src/processor/set2set_predict_function.cpp`、`src/plugin/set2set_predictor.cpp`、`src/processor/response_with_set2set.cpp`；GRG：`src/process/set2set_predict_function.cpp`、`src/operator/diversity/set2set_*_soft_rule.cpp` | 未检索到，需人工补充 | Candidate set -> Set2setPredictor -> set2set score/response -> Diversity soft rule/Response | P0 | 高 |
| 12 | filter pipeline 与细粒度 filter operator | 只讲 `filter_pipeline` 如何组织多个 operator，以及 low_quality/time/aigc 等 operator 的统一接口 | 过滤解释了“为什么候选没进响应”，是排障高频链路 | GRC：`src/processor/video_launch/filter_pipeline.cpp`、`src/processor/filter/grc_filter_base.h`、`src/processor/filter/aigc_filter_operator.cc`、`time_filter_operator.cc`；GRG：`src/process/filter_pipeline_function.cpp`、`src/operator/diversity/nid_filter_rule.cpp` | 未检索到，需人工补充 | Recall/Merge candidates -> FilterPipeline -> Operator rules -> filtered candidates -> Rank/Response | P0 | 中 |
| 13 | doc_feature / feature service cache | 只讲带 cache 的 doc feature 获取：multi_get、miss 后 RPC/补全、写回 | 特征服务命中率直接影响延迟；也是正排/模型之间的连接点 | GRC：`src/processor/doc_feature_with_cache.cpp`、`src/processor/doc_feature_with_cache_yitu.cpp`；GRG：`src/process/feature_service/doc_feature_with_cache_pipeline.cpp`、`src/plugin/feature_service.cpp`、`src/plugin/cache_plugin.cpp` | 未检索到，需人工补充 | Candidate rids -> DocFeatureWithCache -> FeatureService/Cache -> feature map -> model/filter/rank | P1 | 高 |
| 14 | video_launch 链路的分裂、合并、response_for_grg | 只讲 GRC `video_launch` 目录下 data split/merge/rank/response_for_grg 的主干 | video_launch 是微视频 GRC 主链路，适合作为业务架构总览前置篇 | GRC：`src/processor/video_launch/data_spit.cpp`、`data_merge.cpp`、`ctr_rank_function.cpp`、`response_for_grg.cpp`、`recall_data_split.cpp` | 未检索到，需人工补充 | Request -> DataSplit -> Recall/PCS/Rank/Filter -> DataMerge -> ResponseForGrg | P0 | 高 |
| 15 | sketchy 粗排/调权链路 | 只讲 `sketchy_score_init`、`sketchy_rpc_pipeline`、一个典型 sketchy adjuster 如何协作 | 粗排/轻量调权影响候选进入精排前的质量和成本，是推荐链路重要阶段 | GRC：`src/processor/sketchy_score_init.cpp`、`src/processor/video_launch/sketchy_rpc_pipeline.cpp`、`src/operator/adjuster/sketchy/user_intent_adjuster.cpp`、`src/operator/adjuster/sketchy/microvideo_adjust_merge_get_result_from_pcs_sketchy.cpp` | 未检索到，需人工补充 | Candidate -> Sketchy score init/RPC -> Sketchy adjuster -> rank/filter downstream | P1 | 中 |

## 四、推荐的 7+7 初选组合（供用户改）

### 基础库 7 个

1. brpc `ClosureGuard` 与 reusable RPC 生命周期
2. graph-engine `Closure::get()` / `wait()` 语义
3. graph-engine config 的 `processor/depend/@emit` 数据契约
4. protobuf `CopyFrom/Swap/ParsePartialFromString` 的所有权与性能边界
5. bvar 指标注册、expose 与排障入口
6. brpc Channel/Stub 同步调用与动态超时控制
7. GraphFunction 注册宏与组件注入

### 业务 7 个

1. 下发历史在 GRC 过滤/补召回中的作用
2. PCS 精准参数并行调用链路
3. 个性化召回 quota 分配
4. combo 实验参数如何影响过滤/调权
5. 正排 GCMS 补全与 parser 字段映射
6. 召回汇聚：GRC merge_recall 到 GRG grc_recall_function 对比
7. video_launch 链路的分裂、合并、response_for_grg

## 五、检索日志摘要

### 代码检索

- 已执行 `git pull origin main`，仓库 fast-forward 到 `00b26c4`。
- 限定检索范围：
  - `feeda-mv-grc`: `/home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc`
  - `feeda-mv-grg`: `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg`
- 入口文件已读：
  - GRC `src/main.cpp`：注册 `GenericGRCService` 与 `GrcHttpServiceImpl`，启用 `baidu_std_reuse`，加载 `gflags.conf`，启动 dapper collector。
  - GRG `src/main.cpp`：注册 `GenericGRGService` 与 `GrgHttpServiceImpl`，启用 `baidu_std_reuse`，加载 `gflags.conf`，启动 dapper collector。
- 图配置已抽样读取：
  - GRC `conf/plugins/graph/graph.conf`：`SmallflowProcessor` 依赖 `Request`，emit `SidInfo/SidSet`，包含大量 sid 小流量配置。
  - GRG `conf/plugins/graph/short_micro_video/graph.conf`：`SmallflowFunction` 依赖 `Request`，emit `SidInfo/SidSet`。
- 关键词检索覆盖：bthread、Closure、Controller、CopyFrom、Swap、BVAR、gflags、ExpressionProcessor、dict、sid、history、PCS、GCMS、recall、set2set、quota、filter、response、doc_feature、video_launch、intent、sketchy、combo 等。
- 本次未扩展到 `/home1/code_read` 全量递归；没有进行无界全量搜索。

### 内网文档检索

- 已确认 `/root/.hermes/skills/ku-doc-manage/bin/ku` 存在且可执行，`ku -h` 正常显示子命令。
- 尝试关键词：推荐架构、GRC、GRG、feeda-mv-grc、feeda-mv-grg、下发历史、卡片下发历史、PCS、IDM、置顶容灾、个性化召回、combo、正排GCMS、召回汇聚、graph engine。
- 失败原因：CLI 每次调用均提示缺少 `get-ugate-token` skill，检查路径包括 `/root/.openclaw/skills/get-ugate-token/getUgateToken.py` 与 `/root/.hermes/get-ugate-token/getUgateToken.py`；因此无法完成知识库搜索。本周报没有编造内网文档标题/URL。

### 未确认问题

- “IDM”在限定 GRC/GRG 代码中未直接命中；本次用 user intent predictor 作为代码可落地的替代候选，仍需人工补 IDM 架构文档。
- “置顶容灾”在限定检索中未命中明确代码证据；建议需要业务同学提供关键词、sid 或文档后再作为 P0 深挖。
- `search_files` 对部分复杂正则/brace glob 未返回命中，本次补充使用受限 Python 扫描，且排除了 `.git/output/thirdparty`，未做 `/home1/code_read` 无界递归。
