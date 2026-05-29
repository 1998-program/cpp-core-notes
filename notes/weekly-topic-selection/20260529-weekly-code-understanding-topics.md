# 20260529 下周代码理解候选选题（feeda-mv-grc / feeda-mv-grg）

- 生成时间：2026-05-29T16:00:38+08:00
- 下周周期：2026-06-01 ~ 2026-06-07
- 扫描代码库：
  - GRC：`/home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc`
  - GRG：`/home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg`
- 产物：本文件 + `notes/weekly-topic-selection/daily-plan-20260529.json`

## 一、代码热点概览

### GRC 最近一周提交热点

近一周 GRC 提交集中在性能优化、互动/看剧/LCN/Set2Set、二跳 nid 替换、fake_id 关闭等方向：

- `7161cad82 feed-arch-36986 【架构】grc性能优化`
- `14f9a369d feed-arch-37113 [Story] 【架构】grc 关闭自身 fake_id 及请求下游的 fake_id`
- `42dadbf6a mvrec-23952 看剧资源扶持`
- `0ae2b7212 mvrec-23941 修改调参方式+lcn迭代`
- `87e94c3bd / 6d94c9147 / 37d135955 / fcd1d2b96 A-celuejiagou-12804 cj_grc_0526 互动`
- `a819a2ed5 A-celuejiagou-12757 [Feature] grc_interest_quota_0522`
- `a5b0021a0 hpb-120219 [Feature] 二跳nid替换`
- `e10816aa3 aigc低清降权`

按近一周提交改动文件频次，GRC 高频文件包括：

| 频次 | 文件 | 主题信号 |
|---:|---|---|
| 9 | `conf/sid_new.conf` | 小流量/实验参数活跃 |
| 8 | `conf/exp_params.conf` | 实验参数密集迭代 |
| 8 | `conf/plugins/graph/micro_frame/first_refresh_precise_adjust.conf` | 首刷精调链路 |
| 8 | `conf/plugins/graph/micro_frame/precise_adjust_part_all.conf` | 调权配置主链路 |
| 5 | `src/parser/queue_parser.cpp` | 队列解析/召回结果组织 |
| 3 | `src/processor/set2set_predict_function.cpp` | Set2Set 预测链路 |
| 3 | `src/operator/adjuster/sketchy/duanju_adjuster.cpp` | 短剧/看剧调权 |
| 2 | `src/processor/compute_lcn_info.cpp` | LCN 历史兴趣统计 |
| 2 | `src/processor/filter/lcn_filter_operator.cc` | LCN 过滤 |
| 2 | `src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp` | 互动分位数调权 |

### GRG 最近一周提交热点

GRG 最近一周变更较少但集中在服务入口、关闭个性化推荐 fakeid、exec_engine 软规则、多样性/历史兴趣：

- `8ed39c022 feed-arch-37111 [Story] 【架构】grg 兼容关闭个性化推荐关闭开关_fakeid 代替 cuid`
- `2d46f08e6 A-celuejiagou-12765`
- `fcd8c110f A-celuejiagou-12747`

热点文件：

| 频次 | 文件 | 主题信号 |
|---:|---|---|
| 1 | `src/service/grg_service.cpp` | 图入口、GraphPool、动态超时、fakeid 兼容 |
| 1 | `src/util/util.cpp` / `src/util/util.h` | 公共工具与画像映射 |
| 1 | `conf/plugins/exec_engine/multi_stream_select_context_soft_rule.conf` | 多流 soft rule 配置 |
| 1 | `src/operator/diversity/mv_fancate_down_rank_soft_rule.cpp` | 多样性软规则 |
| 1 | `src/process/history_interest_info_function.cpp` | 历史兴趣特征 |

## 二、服务入口与主链路证据

- GRC `GenericGRCService::query()` 在 `src/service/grc_service.cpp:151-211` 初始化 `GRCSessionContext`，按 UA 选择图名（如 `video_immersion`、`searchc_related`），从 `GraphEngine` 获取 `GraphPool` 实例并调用 `run()`。
- GRC `emit_common_data()` 在 `src/service/grc_service.cpp:87-149` 向图注入 `Request/log_id/uid/cuid/UA/flow_loc/ExpInfo/IsGrgRequest` 等全局数据。
- GRG `GenericGRGService::query()` 在 `src/service/grg_service.cpp:35-91` 获取 `GraphEngine`、`DynamicTimeOutPlugin`、图实例，执行 `fill_basic_data_for_graph()` 后运行图。
- GRG `get_graph_name()` 在 `src/service/grg_service.cpp:113-119` 按 UA 选择 `short_micro_video` / `news_updates_dibar` / `default`。
- GRG `fill_basic_data_for_graph()` 在 `src/service/grg_service.cpp:121-200` 注入请求、用户标识、UA、实验、动态超时控制器。

## 三、内网检索状态

### OKR 搜索

| 查询 | 状态 | 摘要 |
|---|---|---|
| `GRC召回` + 2026 Q2 | 成功但 0 命中 | API 返回 `code=0`，`data=[]` |
| `正排IFCS` + 2026 Q2 | 成功但 0 命中 | API 返回 `code=0`，`data=[]` |
| `去重dup` + 2026 Q2 | 成功但 0 命中 | API 返回 `code=0`，`data=[]` |

### 会议搜索

| 查询 | 状态 | 摘要 |
|---|---|---|
| `GRC性能优化`，`start-time=last_week` | 成功但 0 命中 | API 返回 `code=0`，`data=[]` |
| `召回quota`，`start-time=last_week` | 成功但 0 命中 | API 返回 `code=0`，`data=[]` |

### 周报搜索与详情

| 查询 | 状态 | 摘要 |
|---|---|---|
| `feeda-mv-grc` | 成功，命中 20 条 | 近周命中许云鹏、董铎、吴昊宇等周报 |
| `weekly_report_fetch xuyunpeng 2026-05-25` | 成功 | 提到 `feeda-mv-grc` 优化上线，整体优化 CPU 3%-5%；继续摸底回包截断提前，线下压测 CPU 降 1%-4% 但耗时上涨 20ms；PcsGrcExportFunction 截断上线后耗时涨 16ms；调权/过滤热点上线后 CPU 下降 2.62% |
| `weekly_report_fetch wuhaoyu 2026-05-25` | 成功 | 提到新增正排字段 `high_interact_groupkey`，分析 `replace_nid_info.cpp` 替换 nid 时同步替换 `mark/type/type_vec/rec_type=1111` 的影响，推进二跳 nid 替换 |

### 知识库搜索与读取

| 查询 | 状态 | 摘要 |
|---|---|---|
| `dup service` | 成功，命中 10 条 | 读取 `_nIzcXSRlfKMKd`《dup-server标题lcs去重透传grg》：DupService 协议、HistorySimilar/HistoryScore、标题 LCS 与历史资源 pair/score 透传 |
| `IFCS正排缓存` | 成功，命中 10 条 | 读取 `rzlycnK9rMzhXQ`《正排字段添加指南-IFCS版》：IFCS 位于业务模块与正排之间，GRC/PCS 接入；新增字段需改 IFCS proto/解析与业务结构体；上线关注 CPU/MEM |
| `graph engine` | 成功，命中 10 条 | 读取 `muKpLwUg8HWtrc`《graph-engine解读》：GraphData/Commiter/ref/cref、GraphPool 多实例、GraphData reset 不清理 Any 引用、ExpressionProcessor/SelectProcessor/condition |

### 内搜搜索

| 查询 | 状态 | 摘要 |
|---|---|---|
| `bthread并行消费` | 成功，命中 10 条 | 多个 bthread/并行消费/Channel/P2PChannel 结果，适合支撑 bthread_async、PipelineGraphFunction、Channel 批消费专题 |
| `protobuf性能优化` | 成功，命中 10 条 | 命中《protobuf性能优化》《火焰图+耗时优化》《C++ 性能优化 SKiLL》等，适合支撑序列化/ParseFromString/Arena/字段引用缓存专题 |

> 结论：OKR 与会议未命中；周报、知识库、内搜均可为下周选题提供支撑。优先级提升的方向是：GRC 性能优化、IFCS/正排字段、Graph Engine 运行语义、二跳 nid 替换、bthread/Channel 批处理、dup/LCS 去重。

## 四、基础库候选选题（15 个）

| Rank | 主题 | 范围 | 优先级 | 难度 | 代码证据 | 内网支撑 |
|---:|---|---|---|---|---|---|
| 1 | GraphPool 与 GraphData 生命周期：`ref/cref/reset` 的性能与坑 | GRC/GRG 图实例获取、数据注入、reset 后复用 | P0 | 高 | GRC `src/service/grc_service.cpp:177-220`；GRG `src/service/grg_service.cpp:48-91`；GRC `src/service/grc_service.cpp:87-149` | KU《graph-engine解读》提到 GraphData reset 不清理 Any 引用、GraphPool 多实例 |
| 2 | ExpressionProcessor / condition 如何影响图执行路径 | GRC `vertex.conf` 中大量 condition 与表达式依赖 | P0 | 中 | GRC `conf/plugins/graph/vertex.conf:63-80`；GRC `src/service/grc_service.cpp:184-199` | KU《graph-engine解读》解释 ExpressionProcessor/SelectProcessor/condition |
| 3 | DynamicTimeOutPlugin 动态超时在 GRG 图中的注入与传播 | GRG 入口 dynamic_timeout、FrameworkContext | P0 | 中 | GRG `src/service/grg_service.cpp:48-67`、`src/service/grg_service.cpp:177-200` | 代码热点包含 `grg_service.cpp`；无 OKR/会议命中 |
| 4 | bthread_async 批量并发：Future 捕获安全与结果合并 | GRC UserIntentPredictFunction 批量请求模式 | P0 | 高 | GRC `src/processor/video_launch/user_intent_predict.cpp:99-137`、`143-167` | 内搜 `bthread并行消费` 命中 bthread 调度/并行消费资料 |
| 5 | PipelineGraphFunction 与 Channel mini-batch 消费 | GRG Set2setPredictFunction、GRC fill_meta/pipeline 类处理 | P0 | 高 | GRG `src/process/set2set_predict_function.cpp:45-88`；GRC `src/processor/fill_meta.cpp:126-158` | KU《graph-engine解读》说明 Channel 可用 mini batch 平衡延迟和成本 |
| 6 | IFCS SDK get 调用封装与 server_cache_only 场景切换 | GRC `GcmsComponent::query_common/query_news` | P0 | 中 | GRC `src/plugin/gcms_component.cpp:40-79`、`80-101` | KU《正排字段添加指南-IFCS版》解释 IFCS 缓存层与字段接入 |
| 7 | ReusableUnorderedMap / 对象复用在 Merge 热路径中的作用 | GRC MergeProcessor 大量 emit/clear/reuse | P1 | 中 | GRC `src/processor/merge_recall.cpp:24-60`、`65-99` | 周报提到 GRC CPU 水位高、调权/过滤热点优化 |
| 8 | brpc ReusableRPCProtocol 与异步 Closure 回包链路 | GRC/GRG 服务入口 run 后回包 | P1 | 中 | GRC `src/service/grc_service.cpp:151-220`；GRG `src/service/grg_service.cpp:203-220` | 图服务入口主链路证据；无内网文档详情 |
| 9 | GraphData emit/clear 的热路径成本：MergeProcessor 案例 | 大量 GraphData emit 后 clear、map/vector 初始化 | P1 | 中 | GRC `src/processor/merge_recall.cpp:24-60` | 周报 GRC 性能优化支撑 |
| 10 | Protobuf Parse/Serialize 优化在 GRC/GRG 回包链路中的排查方法 | GRC/GRG ProtoMessageToJson、ContentItem 拷贝、潜在 ParseFromString | P1 | 高 | GRC `src/service/grc_service.cpp:69-78`；GRG `src/service/grg_service.cpp:59-61`；GRG `src/service/grg_service.cpp:102-110` | 内搜 `protobuf性能优化` 命中 protobuf/Arena/序列化优化资料 |
| 11 | ExecEngine MultiStreamEngine 绑定 GraphDependency 的机制 | GRG diversity merge 与 exec_engine plugin | P1 | 高 | GRG `src/process/diversity_merge.cpp:73-90`、`102-105` | GRG 变更涉及 exec_engine soft rule 配置 |
| 12 | TraceLog / SIA / BVAR 指标如何定位热点算子 | GRC/GRG SIA_START/SIA_END、trace 打印 | P1 | 中 | GRC `src/service/grc_service.cpp:213-219`；GRG `src/process/diversity_merge.cpp:107-120` | 周报性能优化、CPU 降耗支撑 |
| 13 | ExpInfo / SidInfo 在图入口与算子中的传递模型 | 小流量参数如何从入口进入 adjuster | P1 | 中 | GRC `src/service/grc_service.cpp:147-149`；GRG `src/service/grg_service.cpp:165-170`；GRC `conf/sid_new.conf:1-20` | 代码热点中 `sid_new.conf` 与 `exp_params.conf` 频繁变更 |
| 14 | Any::cref 与避免额外对象构造：GRC/GRG 实战模式 | GraphData 引用注入 Request/ExpInfo/Response | P2 | 中 | GRC `src/service/grc_service.cpp:91-148`；GRG `src/service/grg_service.cpp:127-176` | KU《graph-engine解读》详细说明 cref/get 注意点 |
| 15 | GraphEngine 条件图的 typo 与类型安全风险 | magic string、GraphData 名称、类型匹配 | P2 | 中 | GRC `conf/plugins/graph/vertex.conf:1-80`；GRC `src/service/grc_service.cpp:205-206` | KU《graph-engine解读》Q&A 提到 find_data typo/core 风险 |

## 五、业务候选选题（15 个）

| Rank | 主题 | 范围 | 优先级 | 难度 | 代码证据 | 文档/周报/检索支撑 |
|---:|---|---|---|---|---|---|
| 1 | GRC 性能优化主线：调权/过滤热点、回包截断与耗时反涨 | GRC 性能热点、截断提前、PcsGrcExportFunction 影响 | P0 | 高 | GRC `src/processor/merge_recall.cpp:16-60`；GRC `src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:43-93` | 许云鹏周报：CPU 3%-5%、回包截断 CPU 降 1%-4% 但耗时涨 20ms、调权过滤热点 CPU 降 2.62% |
| 2 | IFCS 正排字段新增到 GRC 业务结构体的端到端链路 | IFCS proto/解析 -> `VideoInfo/NewsInfo` -> fill_meta -> 调权/过滤 | P0 | 高 | GRC `src/plugin/gcms_component.cpp:40-79`；GRC `src/processor/fill_meta.cpp:126-170`；GRC `src/data/video_info.h` | KU《正排字段添加指南-IFCS版》；吴昊宇周报新增 `high_interact_groupkey` |
| 3 | 二跳 nid 替换与 groupkey 互动实验 | `replace_nid_info.cpp` 替换 nid/gcms_data/mark/type/rec_type | P0 | 中 | GRC `src/processor/replace_nid_info.cpp:27-40`、`51-105` | 吴昊宇周报：分析替换 nid 时同步替换 `mark/type/type_vec/rec_type=1111` 的影响 |
| 4 | 看剧/合集 LCN 调权链路 | 看剧资源扶持、合集/剧集 LCN、session LCN | P0 | 中 | GRC `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:43-83`、`99-120` | 代码提交 `mvrec-23952 看剧资源扶持`、`mvrec-23941 lcn迭代` |
| 5 | 用户 LCN 历史特征计算与过滤 | `ComputeLcnInfoProcessor`、`lcn_filter_operator`、历史 read/show list | P0 | 高 | GRC `src/processor/compute_lcn_info.cpp:11-37`、`41-70` | 代码热点：`compute_lcn_info.cpp`、`lcn_filter_operator.cc` |
| 6 | 互动 V5 精调：收藏/点赞/评论/分享分位数如何影响 factor | `MicrovideoHudongV5PreciseAdjuster` 列表分位数和单 item 调权 | P0 | 中 | GRC `src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:35-93`、`101-120` | 近期多次 `cj_grc_0526 互动` 提交 |
| 7 | Set2Set 集合级预测在 GRG/GRC 的接入与降级条件 | GRG `Set2setPredictFunction`、GRC 同名处理器热点 | P0 | 高 | GRG `src/process/set2set_predict_function.cpp:45-88`、`99-121`；GRC `src/processor/set2set_predict_function.cpp` | GRC 热点文件 `set2set_predict_function.cpp` 3 次变更 |
| 8 | GRG 关闭个性化推荐：fakeid 代替 cuid 的入口兼容 | GRG `grg_service.cpp`、用户标识注入、fakeid 兼容 | P1 | 中 | GRG `src/service/grg_service.cpp:121-160`；GRG `src/service/grg_service.cpp:35-91` | 提交 `feed-arch-37111 grg 兼容关闭个性化推荐关闭开关_fakeid 代替 cuid` |
| 9 | GRC 关闭自身 fake_id 及请求下游 fake_id | GRC 入口身份注入与下游请求构造 | P1 | 中 | GRC `src/service/grc_service.cpp:98-105`、`151-211` | 提交 `feed-arch-37113 grc 关闭自身 fake_id 及请求下游的 fake_id` |
| 10 | Dup Service 标题 LCS 透传与 GRG 消费机会 | dup-server LCS score、HistorySimilar/HistoryScore、GRG 去重/多样性 | P1 | 高 | GRG `src/process/diversity_merge.cpp:58-90`；GRC `src/processor/replace_nid_info.cpp:1-3` 引入 dup service | KU《dup-server标题lcs去重透传grg》 |
| 11 | GRG 多样性软规则与 MultiStreamEngine 配置 | `multi_stream_select_soft_rule.conf` + `DiversityMergeFunction` | P1 | 高 | GRG `conf/plugins/exec_engine/multi_stream_select_soft_rule.conf:1-27`；GRG `src/process/diversity_merge.cpp:73-90` | 近期 GRG 变更涉及 exec_engine soft rule 配置 |
| 12 | GRG ResponseFunction 回包 q 值与资源维度监控 | 回包阶段 q 值统计、资源类型分组 | P1 | 中 | GRG `src/process/response_function.cpp:27-48`、`95-153` | 周报中曾提到 gr->grg 使用 flat_buf 有耗时优化空间（历史命中） |
| 13 | AIGC 低清降权链路 | `microvideo_aigc_clarity_adjuster`、低清字段/调权参数 | P1 | 中 | GRC `src/operator/adjuster/precise/microvideo_aigc_clarity_adjuster.cpp`；GRC `conf/plugins/graph/micro_frame/precise_adjust_part_all.conf` | 提交 `aigc低清降权` |
| 14 | 短剧/短视频队列解析与 duanju adjuster | `queue_parser.cpp`、`duanju_adjuster.cpp`、召回队列结构 | P2 | 中 | GRC `src/parser/queue_parser.cpp`；GRC `src/operator/adjuster/sketchy/duanju_adjuster.cpp` | 热点文件：`queue_parser.cpp` 5 次、`duanju_adjuster.cpp` 3 次 |
| 15 | 新作者/90 天过滤与生态策略联动 | 新作者过滤、小流量号切换、正排字段与过滤策略 | P2 | 中 | GRC `src/processor/filter/light_quality_recall_filter_operator.cc`；GRC `src/processor/replace_nid_info.cpp:27-40` | 吴昊宇周报提到 90 天新作者过滤小流量号切换测试 |

## 六、推荐 7+7 每日组合

| 日期 | 基础库主题 | 业务主题 | 选择理由 |
|---|---|---|---|
| 周一 | GraphPool 与 GraphData 生命周期 | GRC 性能优化主线 | 先打底图复用/ref/reset，再分析 CPU/耗时热点，难度高放工作日 |
| 周二 | IFCS SDK get 调用封装 | IFCS 正排字段新增端到端链路 | 代码与 KU 文档支撑最完整，适合沉淀标准流程 |
| 周三 | bthread_async 批量并发 | 用户 LCN 历史特征计算与过滤 | 并发/批处理与历史特征计算可形成性能专题 |
| 周四 | PipelineGraphFunction 与 Channel mini-batch | Set2Set 集合级预测接入与降级 | 两者同属批处理/集合级链路，跨 GRC/GRG 关联强 |
| 周五 | ExecEngine MultiStreamEngine 绑定 GraphDependency | GRG 多样性软规则与 MultiStreamEngine 配置 | GRG 近期变更热点，适合专门分析 exec_engine |
| 周六 | ExpressionProcessor / condition 执行路径 | 看剧/合集 LCN 调权链路 | 周末中等难度：配置条件 + 具体策略调权 |
| 周日 | Protobuf Parse/Serialize 优化排查方法 | 二跳 nid 替换与 groupkey 互动实验 | 结合周报提到的 replace_nid_info 与 protobuf/结构拷贝风险 |

## 七、待人工补充/后续建议

1. OKR/会议本次未命中，不能编造 OKR/会议证据。后续若需 OKR 支撑，建议提供团队/负责人 uuap 或具体 OKR owner 后再定向 fetch。
2. `fake_id/fakeid` 在本次本地内容搜索未直接命中字符串，可能变更已体现在提交标题对应但代码字段名不同；建议在对应 commit diff 中继续追踪完整字段名。
3. GRC/GRG 大文件（如 `response_function.cpp`、`set2set_predict_function.cpp`、`merge_recall.cpp`）很大，下周执行单日深挖时应按 Data 名称、Graph 配置和 commit diff 缩小范围。
