# 下周代码理解候选选题（2026-05-28）

> 生成时间：2026-05-28 20:02:58  
> 检索代码库：`/home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc`；`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg`  
> 内网文档检索状态：失败（已验证 ku CLI 可用但仅支持 query-content/query-repo 等已知 ID/已知 repo 操作；本任务未提供推荐架构 repo/doc-id，且代码库中未发现 ku.baidu-int.com URL，无法按关键词全站搜索。已按要求在业务主题中标注“未检索到，需人工补充”。）  
> 用户动作：请从“基础库方向”和“业务方向”中分别选择 7 个，作为下周每日推荐内容。

## 一、选择说明
- 建议选择规则：优先选 P0；基础库方向建议覆盖 Graph/bthread/protobuf/bvar/gflags/dict；业务方向建议覆盖历史、PCS、GCMS、召回、去重、quota、响应。
- 主题优先级含义：P0=下周最值得讲且证据较强；P1=值得讲但可排后；P2=有用户指定或潜在价值但本次证据不足。
- 难度含义：低=单文件或少量配置可讲清；中=需要跨 2-4 个模块；高=需要跨服务/异步/配置与数据结构联合追踪。

## 二、基础库方向候选（15 个）
| # | 主题 | 建议窄 scope | 为什么值得讲 | 代码证据 | 产出形式 | 优先级 | 难度 |
|---|------|---------------|--------------|----------|----------|--------|------|
| 1 | GraphPool/ObjectPool 图实例池生命周期 | 只讲 GRC/GRG service 从 GraphEngine::try_get 到 graph->reset 的池化对象生命周期 | Graph 是每请求核心资源；理解池化边界能解释串数据、内存占用、并发容量与 reset 时机 | GRC src/service/grc_service.cpp:177-211,220；GRC src/service/grc_service.cpp:42；GRG src/service/grg_service.cpp:48-91 | 数据流小图 + 排障 checklist | P0 | 中 |
| 2 | Graph Data emit/depend 配置解析 | 只讲 graph_parser 如何把 conf 的 emit/depend/condition 翻译为 named_emit/named_depend | 所有业务链路都通过 GraphData 连接；能帮助从配置反推 Producer/Consumer | GRC src/plugin/graph_parser.cpp:35-120；GRC conf/plugins/graph/global.conf:41-83；GRG src/plugin/graph_parser.cpp:53-82 | 深挖文档 + 配置追踪模板 | P0 | 中 |
| 3 | ExpressionProcessor 条件数据选择 | 只讲 expression.conf 中三目表达式如何决定数据分支，例如 CtrInput/PcsRkcjRelevantResult | 很多链路不是 C++ if，而是配置表达式；定位“为什么某数据没跑”必看 | GRC conf/plugins/graph/expression.conf:1-20,72-95；GRG src/plugin/graph_parser.h:25；GRG conf/plugins/graph/short_micro_video/expression.conf | 数据流小图 + 排障 checklist | P0 | 中 |
| 4 | brpc Controller/Closure 与响应发送 | 只讲 Controller 失败处理、Closure/ReusableRPCProtocol::Closure 的 send_response 边界 | RPC 生命周期错误会导致超时、重复回包、错误码不可见；是服务入口必修 | GRC src/service/grc_service.cpp:151-170,342-343；GRG src/service/grg_service.cpp:35-41,95-111,214-245；HTTP ClosureGuard: GRC src/service/grc_http_service.cpp:34-40 | 排障 checklist | P0 | 中 |
| 5 | 动态 timeout controller 传入 Graph 上下文 | 只讲 cntl->timeout_ms/request dynamic_timeout 如何进入 FullLinkDynamicTimeOutController | 推荐链路大量下游 RPC；理解 timeout 预算能解释召回/正排/模型服务截断 | GRC src/service/grc_service.cpp:233-270；GRG src/service/grg_service.cpp:64-89；GRG src/service/grg_service.h:12 | 深挖文档 | P0 | 中 |
| 6 | protobuf CopyFrom/Swap 响应拷贝语义 | 只讲 GRC CopyFrom 与 GRG Swap 的差异和生命周期风险 | 响应对象由 brpc 管理；拷贝/交换策略影响性能和悬挂引用风险 | GRC src/service/grc_service.cpp:324-330；GRG src/service/grg_service.cpp:241-245；GRC src/util/log.h:74 MergeFrom trace log | 小实验 + 排障 checklist | P0 | 高 |
| 7 | bthread_async fan-out 与 Future join | 只讲 UserIntentPredictFunction 批量并发请求的 future 列表、结果容器预分配、join 语义 | 模型/特征 fan-out 是延迟大头；容器引用失效或未 join 会造成疑难问题 | GRC src/processor/video_launch/user_intent_predict.cpp:17-23,91-103,111；GRG src/plugin/model_service.h:42 | 深挖文档 + 小实验 | P0 | 高 |
| 8 | bthread::Mutex 与 HTTP 控制面并发 | 只讲 HttpCntlData 的 thread_local mutex 向量与 offset buffer 保护 | 控制面 graph_view/service_info 看似旁路，也会影响排障和线程安全 | GRC src/service/grc_http_service.cpp:194；GRC src/service/grc_http_service.h:64-94,138-163 | 排障 checklist | P1 | 中 |
| 9 | bvar LatencyRecorder/Adder 指标注册 | 只讲服务级 request/response/graph arena 指标和插件 latency 指标如何暴露 | 线上排障先看指标；理解 bvar 命名与上报位置能缩短定位时间 | GRC src/service/grc_service.cpp:50-55,365,526-533；GRC src/bvar_grc.cc:19-20；GRG src/plugin/predictor.cpp:21-38 | 排障 checklist | P0 | 低 |
| 10 | gflags 配置开关到运行时行为 | 只讲 main/plugin 中 DEFINE_* 如何控制端口、线程、dapper、GCMS pool、quota uplift | 很多行为由启动参数决定；代码阅读必须能从 flag 回推部署差异 | GRC src/main.cpp:37-45；GRG src/main.cpp:18-24；GRC src/plugin/gcms.cpp:26-27；GRC src/plugin/uplift_control.cpp:13-23 | 深挖文档 | P1 | 低 |
| 11 | common dict / GET_COMMON_DICT 热配置读取 | 只讲 GET_COMMON_DICT 读取词典、命中失败处理和业务字段落点 | 推荐策略大量依赖词典；热配置错配会表现为策略“代码没变但效果变” | GRC src/processor/fill_meta.cpp:33-80；GRC src/processor/compute_instant_model_weight.cpp:89；GRG src/service/grg_service.cpp:363 | 排障 checklist | P0 | 中 |
| 12 | sid.conf/实验参数到 GraphData | 只讲 sid plugin/SmallFlowUnitParser 如何 emit sid_xxx 与 exp_param_xxx | 实验开关决定分支；读懂 sid GraphData 是读懂线上行为的前提 | GRG src/plugin/sid.cpp:13-14,204-260；GRG src/plugin/graph_parser.cpp:23-59；GRC conf/plugins/graph/expression.conf:10-20 | 数据流小图 | P0 | 中 |
| 13 | babylon::Any 作为动态类型容器 | 只讲 Context::custom_context、SidPlugin Any、conf.hpp ReusableUnorderedMap 的类型保存边界 | Graph processor 间状态常通过 Any 传递；类型错配难从编译期发现 | GRC src/processor/grc.h:48-52；GRG src/plugin/sid.h:20-42；GRC src/plugin/conf.hpp:45 | 深挖文档 + 小实验 | P1 | 中 |
| 14 | GCMS ObjectPool/StaticMemoryPool 元数据对象复用 | 只讲 GcmsData/MicroVideoInfo 池化对象如何降低正排填充分配成本 | 正排是高 QPS 热路径；对象池设计直接关联延迟和内存毛刺 | GRC src/plugin/gcms.cpp:31-34；GRC src/plugin/ifcs_component.cpp:91,236；GRC src/processor/grc.h:44-45 | 深挖文档 | P1 | 高 |
| 15 | TraceLog/VIP/hash-log 采样门控 | 只讲 is_debug/is_vip_cuid/is_hash_log 到 print_trace_data 的门控，不扩展全日志系统 | 排障时常问“为什么没 trace”；需要区分收集、采样、打印三层 | GRC src/service/grc_service.cpp:213-218；GRC src/plugin/vip_cuid.cpp；GRG src/plugin/vip_cuid.cpp | 排障 checklist | P1 | 中 |

## 三、业务方向候选（15 个）
| # | 主题 | 建议窄 scope | 业务问题 | 代码切入点 | 文档证据 | 数据链路 | 优先级 | 难度 |
|---|------|---------------|----------|------------|----------|----------|--------|------|
| 1 | 下发历史 / readlist 解析链路 | 只讲 GRC/GRG readlist 如何解析出用户历史并参与新老用户、兴趣、过滤判断 | 解决“已看/近期行为如何影响推荐”的问题 | GRC src/plugin/ums_parser.cpp:112-114；GRC conf/plugins/graph/readlist_meta.conf；GRG conf/plugins/graph/short_micro_video/readlist.conf | 未检索到，需人工补充 | Request/UMS -> ums_parser/readlist conf -> MsvReadInfo/History fields -> expression/filter/rank（待逐节点验证） | P0 | 高 |
| 2 | PCS 用户画像与历史兴趣 | 只讲 GRG GetHistoryInterestInfoFunction 从 PCS Result 取 HistoryInterestInfo 的字段映射 | 解决“长期兴趣画像如何进入召回/排序”的问题 | GRG src/process/history_interest_info_function.cpp:1-20,39-80；GRG src/data/base.h:880；GRC src/plugin/grc_pcs_att_plugin.h | 未检索到，需人工补充 | PCS Result -> HistoryInterestInfoFunction -> microvideo_pcs_history_interest_info -> recall/diversity/rank consumers（待验证） | P0 | 中 |
| 3 | IDM/实验参数候选链路 | 只讲代码中 IDM 相关命中稀少时，如何从 exp_params_graph/sid/GraphData 反查实验参数 | 解决“实验参数如何控制推荐分支”的问题；当前本地代码未形成明确 IDM 命名证据 | GRC/GRG 关键词 IDM/idm 命中弱；GRG src/plugin/graph_parser.cpp:34-47 combo 实验参数；GRC conf/plugins/graph/expression.conf | 未检索到，需人工补充 | exp_params_graph.conf -> graph_parser emit exp_param_xxx -> expression/processor depend（待补 IDM 文档） | P2 | 高 |
| 4 | 卡片下发历史 / interest_card 图 | 只讲 UA=110 如何切到 interest_card 图，以及 InterestCardData 作为 end data | 解决“卡片场景是否走普通 GRC 链路”的问题 | GRC src/service/grc_service.cpp:193-195,304；GRC conf/plugins/graph/interest_card/interest_card.conf；关键词 Card/InterestCardData | 未检索到，需人工补充 | Request UA=110 -> graph_name=interest_card -> InterestCard processors -> InterestCardData response（待展开） | P0 | 中 |
| 5 | 置顶/话题/合集正排字段链路 | 只讲 GCMS parser 中 topic/top/heji 字段如何写入 VideoInfo/ArticleCollectionsInfo | 解决“置顶/合集/话题内容如何被识别和过滤/排序”的问题 | GRC src/plugin/ifcs_component.cpp:334,362,375,502-508；GRG src/plugin/gcms_parser.cpp:516-590 | 未检索到，需人工补充 | GCMS ContentFeature -> gcms_parser/ifcs_component -> ArticleCollectionsInfo/topic fields -> diversity/response | P1 | 中 |
| 6 | 个性化召回：GRG 调 GRC 通用召回 | 只讲 GRG GrcRecallFunction 如何组装并调用 GRC 插件获取候选 | 解决“GRG 汇聚层候选从哪里来”的问题 | GRG src/process/grc_recall_function.cpp:14-18,39-74；GRG src/plugin/grc.cpp:30-57；GRG conf/plugins/graph/conf_template/recall_pipeline.conf | 未检索到，需人工补充 | GRG Request -> GrcRecallFunction -> GrcPlugin RPC -> recall_nid_vec/map -> downstream merge | P0 | 中 |
| 7 | combo 实验参数进图机制 | 只讲 graph_parser 对 exp_params_graph.conf 的 combo 参数重读和 GraphData emit | 解决“combo 实验为什么能改变图分支/策略参数”的问题 | GRG src/plugin/graph_parser.cpp:34-47；GRC src/plugin/graph_parser.cpp:322-330 附近；表达式依赖 sid/exp_param | 未检索到，需人工补充 | exp_params_graph.conf -> SmallFlowUnitParser/GraphParser -> exp_param GraphData -> expression/processor condition | P1 | 中 |
| 8 | 正排 GCMS/IFCS 填充链路 | 只讲 GRC IFCS/GcmsComponent 查询与 MvRecallDocParser 字段映射 | 解决“召回只有 nid 后如何补齐内容特征”的问题 | GRC src/processor/fill_meta.cpp:16-23；GRC src/plugin/ifcs_component.cpp:203-236,812-825；GRG src/plugin/gcms_parser.cpp:25,845-866 | 未检索到，需人工补充 | rid list -> FillMeta/GcmsComponent -> IFCS/GCMS ContentFeature -> MicroVideoInfo/VideoInfo -> filter/rank | P0 | 高 |
| 9 | 召回汇聚与队列类型 | 只讲 loads/rule/function/effect 等队列在 response/merge 前如何计数和输出 | 解决“多路召回候选如何汇聚成最终队列”的问题 | GRG src/process/response_function.cpp:33-59；GRC src/processor/merge_recall.cpp；GRC src/parser/queue_parser.cpp | 未检索到，需人工补充 | Recall queues -> merge/diversity/rank -> ResponseFunction queue counters -> GRCResponse | P0 | 高 |
| 10 | Dup Service 去重链路 | 只讲 GRC DupServicePlugin 构造 GET_DUP 请求与 client pool | 解决“历史去重/跨层去重如何接入”的问题 | GRC src/plugin/dup_service.cpp:13-27,42-57；GRC src/plugin/dup_service.h；GRG src/data/rid_tmp_info.h:115 is_title_dup | 未检索到，需人工补充 | CommonInfo -> DupRequest MODULE_GRC/PRODUCT_MICRO_VIDEO -> DupClient -> dup result -> filter（consumer 待补） | P0 | 中 |
| 11 | set2set 预测链路 | 只讲 Set2setPredictorPlugin/PredictorPlugin 到 response_with_set2set 的请求响应 | 解决“集合到集合模型如何影响最终候选”的问题 | GRC src/plugin/set2set_predictor.cpp:26-43,71-81；GRC src/processor/set2set_predict_function.cpp；GRG src/process/set2set_predict_function.cpp | 未检索到，需人工补充 | Candidate set -> set2set_predict_function -> PredictorPlugin -> set2set scores/result -> response/merge | P1 | 高 |
| 12 | Quota distribute for GRG | 只讲 GRC QuotaDistributeForGrgExecutor 如何按资源类型/PCS quota 分配候选 | 解决“短视频/小视频/短剧/合集比例如何控制”的问题 | GRC src/strategy/short_micro/quota_distribute_for_grg.cpp:15-20,45-59,77-100；GRC src/plugin/uplift_control.cpp:13-37 | 未检索到，需人工补充 | Ranked candidates + SidInfo + PcsQuota400/NewQuota -> quota_distribution -> rid_output/resource_quota | P0 | 高 |
| 13 | filter operator 主链路 | 只讲 GRC filter operators 如何承接正排字段和历史字段做过滤 | 解决“候选为什么被过滤”的问题 | GRC src/processor/filter/*_filter_operator.cc；GRC conf/plugins/graph/micro_frame/filter_main.conf；GRG src/operator/diversity/scatter_context.h:834 | 未检索到，需人工补充 | Candidate + GCMS/History/Sid -> filter_main/filter operators -> filtered candidates -> rank/merge | P1 | 中 |
| 14 | response_for_grg / GRGResponse 输出结构 | 只讲 GRC 给 GRG 的 ResponseForGrg 与 GRG 最终 GRGResponse 的边界 | 解决“GRC 与 GRG 层响应字段如何衔接”的问题 | GRC src/service/grc_service.cpp:283-289；GRC src/processor/video_launch/response_for_grg.cpp；GRG src/service/grg_service.cpp:211-245 | 未检索到，需人工补充 | GRC graph -> ResponseForGrg/GrcResponse -> GRG GrcPlugin/GrcRecallFunction -> GRGResponse Swap -> client | P0 | 中 |
| 15 | user intent predictor / 意图分支 | 只讲 video_launch UserIntentPredictFunction 对 effect_queue 分 batch 调模型并回填 intent_score | 解决“用户意图如何影响排序/调整”的问题 | GRC src/processor/video_launch/user_intent_predict.cpp:30-63,91-103；GRG src/data/rid_tmp_info.h:106 intent_score_qsc | 未检索到，需人工补充 | Effect queue + doc_sample + user/request response -> bthread batch predictor -> user_intent_result_map -> adjust/rank | P1 | 高 |

## 四、推荐的 7+7 初选组合（供用户改）
### 基础库 7 个
- GraphPool/ObjectPool 图实例池生命周期
- Graph Data emit/depend 配置解析
- ExpressionProcessor 条件数据选择
- brpc Controller/Closure 与响应发送
- 动态 timeout controller 传入 Graph 上下文
- protobuf CopyFrom/Swap 响应拷贝语义
- bthread_async fan-out 与 Future join
### 业务 7 个
- 下发历史 / readlist 解析链路
- PCS 用户画像与历史兴趣
- 卡片下发历史 / interest_card 图
- 个性化召回：GRG 调 GRC 通用召回
- 正排 GCMS/IFCS 填充链路
- Dup Service 去重链路
- Quota distribute for GRG

## 五、检索日志摘要
### 代码检索
- 已执行 `git pull origin main`，仓库已是最新。
- 限定检索 GRC：`/home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc`；GRG：`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg`。未对 `/home1/code_read` 做无界全量递归。
- 基础库关键词：bthread、Closure、Controller、MergeFrom/CopyFrom、Arena、bvar、DEFINE_、BABYLON_REGISTER、named_emit/named_depend、ObjectPool、Any、ExpressionProcessor、GET_COMMON_DICT、sid。
- 业务关键词：History/readlist、PCS、IDM、card、top、combo、GCMS/Gcms、recall、Dup、set2set、quota、filter、response、doc_feature、video_launch、intent、sketchy、adjust。
- 检索摘要：GRC 中 adjust/sketchy/video_launch/filter/rank/recall/pcs/gcms/readlist 命中较多；GRG 中 set2set/response/readlist/history/pcs/gcms/showlist/recall/quota/rank 命中较多。
### 内网文档检索
- 已按要求先设置 `BAIDU_CC_USERNAME=hujiyang`。
- 已验证 `/root/.hermes/skills/ku-doc-manage/bin/ku --help`：当前 CLI 无全站 `search` 子命令，只能 query-content/query-repo 等。
- 已执行 `query-user-info --username hujiyang` 成功，确认认证可用；但没有推荐架构 repo/doc-id，无法用关键词检索。
- 已在 GRC/GRG 代码中搜索 `ku.baidu-int.com` / `knowledge/`，未发现可直接 query-content 的 KU URL。
### 未确认问题
- IDM 方向本次代码证据不足，建议用户补充 IDM 内网文档 URL 或明确 IDM 在代码中的字段名。
- “置顶容灾”未找到明确 recovery/disaster 链路，仅发现 topic/top/合集正排字段与少量 recovery channel 命中，需后续补文档或限定具体业务词。
- 业务主题文档证据均未检索到；若用户提供 KU repo/doc-id，下次可补充文档标题/URL/摘要。
