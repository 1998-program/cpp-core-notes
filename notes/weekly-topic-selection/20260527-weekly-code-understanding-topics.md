# 下周代码理解候选选题（2026-05-27）

> 生成时间：2026-05-27 23:00:01 CST  
> 检索代码库：`/home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc`、`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg`  
> 内网文档检索状态：部分成功（CLI 可用；未发现全站 search 子命令，本次基于代码配置中出现的 KU URL 成功读取《正排字段添加指南-IFCS版》）  
> 用户动作：请从“基础库方向”和“业务方向”中分别选择 7 个，作为下周每日推荐内容。

## 一、选择说明
- 建议选择规则：优先选 P0；尽量覆盖“入口/图执行/召回/正排/历史/去重/排序调权/监控排障”几类链路；高难度主题建议安排在工作日中段。
- 主题优先级含义：P0 = 本周最值得深挖、能直接提升排障或代码阅读效率；P1 = 很适合补齐架构视角；P2 = 可作为备选或后续扩展。
- 难度含义：低 = 单文件/单机制可讲清；中 = 需要串 2-4 个模块；高 = 需要跨服务、配置、插件或内网文档一起验证。

## 二、基础库方向候选（15 个）
| # | 主题 | 建议窄 scope | 为什么值得讲 | 代码证据 | 产出形式 | 优先级 | 难度 |
|---|------|---------------|--------------|----------|----------|--------|------|
| 1 | GraphPool / ObjectPool 的请求级图实例复用 | 只讲 `GraphEngine::try_get()` 返回 `ObjectPool<Graph>::PooledObject` 到 `reset()` 的生命周期 | GRC/GRG 都是图执行服务；理解图对象复用是排查串请求污染、内存峰值和 reset 时机的基础 | GRC：`src/service/grc_service.cpp:41-42` typedef GraphPool，`emit_common_data()` 写入 GraphData；GRG：`src/service/grg_service.cpp:21-23,64-108` 获取 `pooled_graph` 并 `reset()` | 深挖文档 + 生命周期小图 | P0 | 中 |
| 2 | brpc Controller / Closure 的服务入口错误处理 | 只讲 `baidu::rpc::Controller`、`Closure::Run()`、`SetFailed()` 在 GRC/GRG query 中的约定 | RPC 入口错误处理影响客户端重试、日志与超时；也是读服务主链路的第一站 | GRC：`src/service/grc_service.cpp:62-83` `set_error()`；GRG：`src/service/grg_service.cpp:35-43,112-128` | 排障 checklist | P0 | 低 |
| 3 | Protobuf CopyFrom / repeated field 的热路径成本 | 只讲请求复制、历史字段过滤中的 `CopyFrom()`，不泛讲 protobuf | Feed 热路径里大量 proto 复制可能导致 P80/P99 抖动；也关联 protobuf 并发 mutation 风险 | GRG：`src/service/grg_service.cpp:87-89` `grg_request.CopyFrom(*request)`；GRC：`src/processor/history/filter_history_info.cpp:63,96,100` 对 `IssuedItem` 和 `history_info` CopyFrom | 小实验 + 优化 checklist | P0 | 中 |
| 4 | Graph Function 的 emit/depend 数据契约 | 只讲 `GRAPH_FUNCTION_EMIT_DATA` / `GRAPH_FUNCTION_DEPEND_DATA` 如何形成数据边 | 读 GRC/GRG 图链路时，emit/depend 比函数内部细节更关键；可复用到所有 processor | GRC：`src/processor/video_launch/vfs_filter_function.cpp:97-102`；配置：`conf/plugins/graph/graph.conf:1-5` smallflow emit SidInfo/SidSet | 数据流小图 | P0 | 中 |
| 5 | PipelineGraphFunction 的批处理与 Channel 语义 | 只讲 GRG `FilterPipelineFunction` 的 `pre_process`、`input_data_construct`、`process(QueueContext&)` | GRG 许多节点是队列/批处理形态；理解批次边界有助于定位过滤数和耗时归因 | GRG：`src/process/filter_pipeline_function.cpp:11-28,32-67`；`_filter_pipeline_result.set_graph_vertex()` dapper 绑定 | 深挖文档 | P0 | 高 |
| 6 | bvar / SIA 指标打点与 Graph 监控绑定 | 只讲服务入口 bvar expose + processor 内 SIA_ADD/SIA_END 的两类指标 | 排障时要能从监控名反查代码位置；GRC/GRG 都混用 bvar 和 SIA | GRC：`src/service/grc_service.cpp:50-55` expose request/response/arena；GRC VFS：`src/processor/video_launch/vfs_filter_function.cpp:27,44,66,82`；GRG Filter：`src/process/filter_pipeline_function.cpp:43,64-66` | 排障 checklist | P0 | 中 |
| 7 | gflags 配置开关到运行时行为 | 只讲服务入口 `DEFINE_*` 如何影响日志、debug、dapper 或平台开关 | gflags 是线上行为差异的常见来源；适合做“如何从 flag 追行为”的模板 | GRC：`src/service/grc_service.cpp:46-47` `is_print_dapper_log/is_all_ua_dapper`；GRG：`src/service/grg_service.cpp:28` `is_debug_switch` | 深挖文档 | P1 | 低 |
| 8 | 动态超时控制器池化 | 只讲 `DynamicTimeOutPlugin::get_dt_controller()` 和请求级 controller 传递 | 动态超时会影响下游 RPC 放弃、尾延迟和降级判断，是服务稳定性关键基础设施 | GRG：`src/service/grg_service.cpp:30-33,64-77,102-106`；GRC：`src/service/grc_service.cpp:56-59` 获取 DynamicTimeOutPlugin | 数据流小图 | P1 | 中 |
| 9 | dict / common_dict 热更新查询模式 | 只讲 `GET_COMMON_DICT` 和 sid/实验字典在过滤中的用法 | Feed 策略大量依赖字典；读懂字典命中分支才能解释线上“某类资源突然过滤” | GRG：`src/service/grg_service.cpp:14` `common_dict/register.h`；`src/process/filter_pipeline_function.cpp:77-84` 查询 `d_gsvalue_author_dict` | 排障 checklist | P1 | 中 |
| 10 | sid.conf / smallflow 配置的实验流量入口 | 只讲 graph.conf 中 smallflow processor 产出 SidInfo/SidSet | 业务行为经常由 sid 控制；先理解 smallflow 数据如何注入图 | GRC：`conf/plugins/graph/graph.conf:1-7`；GRG：`conf/plugins/graph/short_micro_video/graph.conf:1-7` | 数据流小图 | P1 | 低 |
| 11 | IFCS / GCMS 插件配置的字段选择与缓存参数 | 只讲 `gcms_common_pb_plugin.conf` 中 req_batch_size、ttl、pb_field 列表如何影响正排返回 | 正排字段缺失/新增字段成本/缓存穿透都从这里查起 | GRC：`conf/common_component/gcms_common_pb_plugin.conf:1` 指向 IFCS 指南；GRG：`conf/common_component/gcms_common_pb_plugin.conf:4-18,24-45` 服务、batch、ttl、pb_field | 深挖文档 + 配置解读 | P0 | 中 |
| 12 | Protobuf 反射/序列化在 IFCS 中的中间态 | 只讲 `ContentFeature -> MicroVideoItemGcmsInfo -> string -> 业务结构体` | 这是新增正排字段和排查字段不生效的核心基础库/序列化机制 | KU 文档《正排字段添加指南-IFCS版》明确写：`ContentFeature->...->序列化为string->业务正排结构体`；本地配置见 GRC/GRG `conf/common_component/gcms_common_pb_plugin.conf` | 深挖文档 | P0 | 高 |
| 13 | absl 字符串工具在服务入口日志中的轻量使用 | 只讲 `absl/strings/str_cat.h` 引入与日志/拼接边界，不扩展到 Abseil 全家桶 | 小但可迁移：避免临时 string 过多、统一日志拼接风格 | GRC：`src/service/grc_service.cpp:36` 引入 `absl/strings/str_cat.h`；需继续 grep 使用点 | 小实验 | P2 | 低 |
| 14 | `ref/cref/emit` 的 GraphData 零拷贝语义 | 只讲服务入口把 request、uid、cuid、ExpInfo 以引用方式注入 GraphData | 这直接决定下游是否能安全持有数据，以及请求对象生命周期边界 | GRC：`src/service/grc_service.cpp:91-148` 多处 `.ref()` / `.cref()`；GRG：`src/service/grg_service.cpp:144-157` `.ref(*request)` | 生命周期小图 | P0 | 中 |
| 15 | 过滤循环中的容器 erase / swap 成本 | 只讲 `vector` 迭代 erase 与 `output_vec.swap(input_vec)` 的成本和替代写法 | 热路径过滤中 erase 可能退化；这类基础容器模式很适合结合业务过滤做性能复盘 | GRG：`src/process/filter_pipeline_function.cpp:49-66`；GRC：`src/processor/video_launch/vfs_filter_function.cpp:42-65` reserve + emplace | 小实验 + checklist | P1 | 中 |

## 三、业务方向候选（15 个）
| # | 主题 | 建议窄 scope | 业务问题 | 代码切入点 | 文档证据 | 数据链路 | 优先级 | 难度 |
|---|------|---------------|----------|------------|----------|----------|--------|------|
| 1 | 下发历史过滤 | 只讲 GRC 如何从 request.history_info 过滤未真实下发/需 fetch back 的 issued_items | 避免把未曝光或需回捞内容误当历史，影响去重和召回 | `src/processor/history/filter_history_info.cpp:21-64,89-105` | 未检索到专门历史文档，需人工补充；代码证据充分 | `GRCRequest.history_info -> FilterHistoryInfoFunction -> filtered history_info -> 下游去重/过滤（待验证消费者）` | P0 | 中 |
| 2 | 卡片下发历史（slidecard/batch loading） | 只讲 `micro_slidecard_batch_loading` 分支如何改变历史处理 | 卡片流量与普通 feed 曝光语义不同，历史过滤规则容易影响重复/召回量 | `src/processor/history/filter_history_info.cpp:120` 出现 `ua==85 && flow_loc==88` 卡片批量加载判断 | 未检索到专门文档，需人工补充 | `Request.common_info(ua/flow_loc) -> history_info -> FilterHistoryInfoFunction -> 卡片历史分支（待继续读 121-145）` | P0 | 中 |
| 3 | PCS 结果接入与融合 | 只讲 GRC/GRG 中 PCS 相关 function 如何产出调权/召回配额信号 | PCS 是用户/内容特征和模型结果的重要来源，影响召回 quota、精排前调权 | GRC 多文件：`src/processor/video_launch/common_pcs.cpp`、`pcs_precise_parallel.cpp`、`pcs_after_merge.cpp`、`src/processor/common_pcs_rpc.cpp`；GRG：`src/process/grg_pcs_function.cpp`、`set2set_pcs_function.cpp` | 未检索到 PCS 专门文档；IFCS 指南提到 feeda-mv-pcs 接入 IFCS | `Request/候选 -> PCS RPC/function -> pcs_* result -> after_merge/adjuster/recall_quota` | P0 | 高 |
| 4 | IDM / 个性化关闭与 fake_id | 只讲 GRG 入口 `is_close_individual` 时替换 uid/cuid/baiduid 的链路 | 解决用户关闭个性化时仍需服务可用、但不能使用真实个性化 ID 的问题 | `src/service/grg_service.cpp:85-99` `is_close_individual`、`Util::caculate_fake_id()` | 未检索到 IDM 文档；“IDM”本地文件名未命中，需人工补充业务定义 | `GRCRequest.common_info -> is_close_individual -> fake_id -> fill_basic_data_for_graph(uid/cuid/baiduid)` | P0 | 中 |
| 5 | 置顶容灾 / recovery 类策略 | 只讲服务中 rank/adjust/filter 失败时的置顶或恢复指标候选（先做代码定位） | 推荐链路异常时要保证首刷体验和核心资源露出；适合排障型文档 | 本次未精确命中“置顶容灾”；可从 `src/processor/video_launch/rank_index_calc.cpp`、`src/operator/adjuster/*top*`、监控 recovery 指标继续查 | 未检索到，需人工补充 | `异常/降级信号 -> recovery/top adjuster -> Response`（待验证） | P1 | 高 |
| 6 | 个性化召回 / GRC Recall | 只讲 GRG 调 GRC 召回并拆分 loads/rule/news/effect queue 的一段 | GRG 作为汇聚层，理解从 GRC 拉候选是业务主链路核心 | GRG：`src/process/grc_recall_function.cpp:14-18,39-73`；GRC：`src/parser/recall_parser.cpp`、`src/processor/merge_recall.cpp` | 未检索到专门文档 | `GRG Request -> GrcRecallFunction -> GrcPlugin RPC -> recall_nid_vec/map & queues -> fill meta/filter/rank` | P0 | 高 |
| 7 | combo 测试过滤/调权 | 只讲 combo_test_filter 与 combo adjuster 的实验控制点 | combo 常用于多策略组合实验，适合讲“实验策略如何插入过滤/调权链路” | GRC：`src/processor/filter/combo_test_filter.cc`、`src/operator/adjuster/sketchy/combo_test_adjuster.cpp`；GRG：`src/operator/diversity/first_refresh_push_cate_combo_soft_rule.cpp` | 未检索到 combo 文档，需人工补充 | `Sid/实验 -> combo filter/adjuster -> 候选删除或分数调整 -> Response` | P1 | 中 |
| 8 | 正排 GCMS / IFCS 字段链路 | 只讲新增/读取一个正排字段从 `gcms_common_pb_plugin.conf` 到 `VideoInfo` 的链路 | 正排字段是过滤、排序、调权的基础；字段缺失是高频问题 | GRC：`conf/common_component/gcms_common_pb_plugin.conf:1`、`src/processor/fill_meta.cpp`、`src/processor/video_launch/fill_meta_pipeline.cpp`；GRG：`conf/common_component/gcms_common_pb_plugin.conf:4-18,24-45`、`src/process/feature_service/doc_feature_with_cache_pipeline.cpp` | 成功读取 KU：《正排字段添加指南-IFCS版》，摘要：IFCS 在业务模块和正排服务之间增加缓存，核心链路为 `ContentFeature -> MicroVideoItemGcmsInfo/MvPcsItemGcmsInfo -> 序列化 string -> 业务正排结构体` | `候选 nid -> GCMS/IFCS plugin -> ContentFeature/IfcsItem -> 业务 VideoInfo/NewsInfo -> filter/rank/adjust` | P0 | 高 |
| 9 | Dup Service / VFS 去重 | 只讲 `VfsFilterFunction` 如何用历史和 nid 相似关系过滤候选 | 去重直接影响重复曝光与召回损耗，是 Feed 体验关键 | GRC：`src/processor/video_launch/vfs_filter_function.cpp:31-66,93-102`；`plugin/dup_service.h` | 未检索到专门文档；技能记忆中有 Dup Service split dedup 模式，需本地继续补链路 | `候选 RidTmpInfo -> DupClient.is_replicated_with_history / is_replicated_with_nid -> VFS output_vec` | P0 | 中 |
| 10 | 召回汇聚 / merge_recall | 只讲 GRC 多路召回结果合并为统一候选的接口与数据结构 | 召回后合并决定候选规模、队列来源和后续配额 | GRC：`src/processor/merge_recall.cpp`、`src/processor/video_launch/card_recall_merge.cpp`、`src/parser/recall_parser_new.cpp`；GRG：`src/process/recall_history_nid_merge_function.cpp` | 未检索到专门文档 | `recall parser -> queue/type RidTmpInfo -> merge_recall/card_recall_merge -> downstream filter` | P0 | 中 |
| 11 | quota distribute / 召回配额分发 | 只讲短视频队列的 `quota_distribute_for_grg` 与 PCS recall quota | 控制各召回队列露出份额，直接影响多样性和业务目标 | GRC：`src/strategy/short_micro/quota_distribute_for_grg.cpp`、`src/processor/recall_personalized_quota.cpp`、`src/processor/video_launch/pcs_gr_recall_quota_function.cpp`；GRG：`src/process/deepes_quota_pcs_function.cpp` | 未检索到文档 | `recall queues + PCS/quota conf -> quota distribute -> selected candidates` | P1 | 中 |
| 12 | rank strategy / CTR rank | 只讲 GRC 中 `ctr_rank_function` 与 `rank_strategy` 的输入输出 | 排序是从候选到最终列表的核心转折点，便于理解分数如何产生 | GRC：`src/processor/video_launch/ctr_rank_function.cpp`、`src/strategy/short_micro/rank_strategy.cpp`、`src/processor/parallel_ctr_rank.cpp` | 未检索到文档 | `候选 + features -> ctr_rank/rank_strategy -> score/order -> adjust/response` | P1 | 高 |
| 13 | filter operator 体系 | 只讲 GRC filter operator 与 GRG filter pipeline 的差异 | 过滤是候选损耗最大的一层；跨服务对比有助于定位“在哪层被杀” | GRC：`src/processor/filter/grc_filter_base.h` 与大量 `*_filter_operator.cc`；GRG：`src/process/filter_pipeline_function.cpp:11-67` | 未检索到文档 | `候选 + VideoInfo + dict/sid -> filter operators/pipeline -> filtered candidates` | P0 | 中 |
| 14 | response_for_grg / GRC 到 GRG 响应适配 | 只讲 GRC video_launch 如何生成供 GRG 消费的响应 | 跨服务响应适配是 GRC/GRG 分层边界，适合讲服务间契约 | GRC：`src/processor/video_launch/response_for_grg.cpp`、`src/processor/response.cpp`；GRG：`src/process/response_function.cpp` | 未检索到文档 | `GRC internal candidates -> response_for_grg -> GRCResponse -> GRG consuming/response_function` | P0 | 中 |
| 15 | 用户意图 / sketchy / precise adjust 链路 | 只讲 `user_intent_predict` 产出的意图分如何进入 sketchy/precise adjust | 意图信号影响调权，是业务策略最典型的一段“模型结果 -> 调权”链路 | GRC：`src/processor/video_launch/user_intent_predict.cpp`、`src/operator/adjuster/sketchy/user_intent_adjuster.cpp`、`user_intent_score_adjuster.cpp`、`src/operator/adjuster/precise/user_intent_pre_adjuster.cpp`；GRG：`src/process/grc_recall_function.cpp:66` `intent_score` emit | 未检索到文档 | `Request/user features -> user_intent_predict -> intent_score -> sketchy/precise adjuster -> score/order` | P1 | 高 |

## 四、推荐的 7+7 初选组合（供用户改）
### 基础库 7 个
1. GraphPool / ObjectPool 的请求级图实例复用
2. brpc Controller / Closure 的服务入口错误处理
3. Protobuf CopyFrom / repeated field 的热路径成本
4. Graph Function 的 emit/depend 数据契约
5. PipelineGraphFunction 的批处理与 Channel 语义
6. bvar / SIA 指标打点与 Graph 监控绑定
7. IFCS / GCMS 插件配置的字段选择与缓存参数

### 业务 7 个
1. 正排 GCMS / IFCS 字段链路
2. 下发历史过滤
3. PCS 结果接入与融合
4. 个性化召回 / GRC Recall
5. Dup Service / VFS 去重
6. filter operator 体系
7. response_for_grg / GRC 到 GRG 响应适配

## 五、检索日志摘要
### 代码检索
- 已执行 `git pull origin main`，仓库已是最新。
- 限定检索范围：
  - `/home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc`
  - `/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg`
- 入口文件已读：
  - GRC：`src/service/grc_service.cpp`，确认 GraphEngine/ObjectPool、GraphData emit、bvar expose、brpc Controller/Closure。
  - GRG：`src/service/grg_service.cpp`，确认 graph_name 选择、DynamicTimeOutPlugin、`is_close_individual` fake_id、Graph run/reset。
- 关键业务文件/配置已抽样读取：
  - GRC：`src/processor/video_launch/vfs_filter_function.cpp`、`src/processor/history/filter_history_info.cpp`、`conf/plugins/graph/graph.conf`、`conf/common_component/gcms_common_pb_plugin.conf`。
  - GRG：`src/process/filter_pipeline_function.cpp`、`src/process/grc_recall_function.cpp`、`conf/plugins/graph/short_micro_video/graph.conf`、`conf/common_component/gcms_common_pb_plugin.conf`。
- 文件名检索发现：PCS、recall、filter、rank、adjust、feature、history、combo 等候选点均有本地代码切入点；IDM、置顶容灾暂未精确命中文件名。

### 内网文档检索
- 已按要求设置 `BAIDU_CC_USERNAME=hujiyang`。
- `ku` 不在 PATH；改用 `~/.hermes/skills/ku-doc-manage/bin/ku` 成功执行 `query-user-info`。
- 当前 ku-doc-manage skill 文档提示没有全站 `search` 子命令，因此未臆造关键词全站搜索。
- 从 GRC 配置 `conf/common_component/gcms_common_pb_plugin.conf` 中发现 KU URL，并成功读取文档：
  - 标题：《正排字段添加指南-IFCS版》
  - URL：`https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/SCIE54A-T7/vwbx5ZVfxu/rzlycnK9rMzhXQ`
  - 摘要：IFCS 是业务模块和正排服务之间的缓存服务；feeda-mv-grc、feeda-mv-pcs、parameter-calculation-service 已接入；关键数据流为 `ContentFeature -> MicroVideoItemGcmsInfo/MvPcsItemGcmsInfo -> 序列化 string -> 业务正排结构体`。

### 未确认问题
- “推荐架构/GRC/GRG/下发历史/PCS/IDM/置顶容灾/combo”等关键词没有全站 KU 搜索能力，本次只引用了代码内可发现 URL 对应文档；其余文档证据标注为“未检索到，需人工补充”。
- “置顶容灾”需要进一步用监控指标、日志名或策略配置定位，当前只给出候选方向，不作为强证据结论。
- “IDM”本地未命中文件名；当前以 `is_close_individual` fake_id 作为个性化关闭/隐私降级候选，需用户确认是否等同于目标 IDM 方向。
