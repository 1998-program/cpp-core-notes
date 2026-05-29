# 20260529 下周代码理解候选选题与每日计划

- week_of: 2026-06-01
- 代码库: feeda-mv-grc, feeda-mv-grg
- 生成时间: 2026-05-29T20:35:50+08:00

## 扫描摘要
- GRC 最近一周变更多，热点集中在性能优化、PCS/Set2Set、video_launch、看剧/合集/LCN、互动、AIGC、fake_id、二跳替换。
- GRG 最近一周变更少，主要是 grg_service.cpp fakeid/个性化关闭兼容，以及 util 相关小改。
- 内网搜索：OKR/会议指定关键词未命中；周报、KU、内搜均命中，已据实作为优先级支撑。

## 15 个基础库/基础框架候选

| Rank | 选题 | 范围 | 优先级 | 难度 | 代码证据 | 内网/文档支撑 |
|---:|---|---|---|---|---|---|
| 1 | GraphEngine GraphPool 对象池复用与 reset 生命周期 | GRC/GRG 服务入口 GraphPool::try_get、run、reset 全链路 | P0 | 中 | `GRC src/service/grc_service.cpp:199；GRG src/service/grg_service.cpp:65` | 周报：feeda-mv-grc 成本优化；KU graph-engine 命中 |
| 2 | DynamicTimeOutPlugin 动态超时注入与场景映射 | GRC/GRG dt controller 获取、scene 选择、timeout 写入 FrameworkContext | P0 | 中 | `GRC src/service/grc_service.cpp:255；GRG src/service/grg_service.cpp:190` | 代码热点支撑 |
| 3 | bthread_async 批量并行模式与捕获安全 | GRC parallel_predictor/doc_feature/user_intent 与 GRG diversity/user_predict 的 Future 模式 | P0 | 高 | `GRC src/plugin/parallel_predictor.cpp:26；GRG src/process/diversity_merge.cpp:752` | 内搜 bthread 并行消费命中 bthread/ParkingLot/Ranker并行组件 |
| 4 | PipelineGraphFunction parallel_consume 批消费框架 | GRC/GRG pipeline_function 的 Channel 消费、mutable input、publisher 语义 | P0 | 中 | `GRC src/processor/base/pipeline_function.h:51；GRG src/process/base/pipeline_function.h:58` | 内搜 bthread并行消费命中 ChannelGraphProcessor/P2PChannel 摘要 |
| 5 | Protobuf SerializeToString 热点与 FlatBuffers 替代方向 | GRC set2set/response/cache_queue 与 GRG response/extmsg 序列化点 | P0 | 高 | `GRC src/processor/set2set_predict_function.cpp:589；GRG src/process/response_function.cpp:4219` | 周报：gr->grg flat_buf 可有 3ms；内搜 protobuf性能优化命中 |
| 6 | ObjectPool 热路径对象复用模式 | GraphPool、DupClient pool、IFCS item pool、MultiStreamEngine pool | P1 | 中 | `GRC src/plugin/dup_service.cpp:26；GRG src/process/diversity_merge.cpp:1777` | 代码热点支撑 |
| 7 | MultiStreamEngine bind_graph_dependency 桥接外层 GraphData | GRC video_launch 和 GRG diversity_merge 的 engine_vertex 绑定方式 | P1 | 高 | `GRC src/processor/video_launch/diversity_merge.cpp:57；GRG src/process/diversity_merge.cpp:89` | KU graph engine 命中 |
| 8 | Expression/条件图路由与 SelectProcessor 排查 | GRC expression.conf 中条件表达式如何影响主链路/二跳链路 | P1 | 中 | `GRC conf/plugins/graph/expression.conf:543；GRC conf/plugins/graph/common_vertex.conf:487` | KU condition/graph engine 命中 |
| 9 | TraceLog/VIP/HashLog 采样与业务日志分层 | is_vip_cuid 注入、print_trace_data、调整因子日志 | P1 | 中 | `GRC src/service/grc_service.cpp:132；GRG src/service/grg_service.cpp:153` | 代码热点支撑 |
| 10 | ReusableRPCProtocol 与响应 Closure 生命周期 | service run 后 Closure、response 填充、attachment 写入时序 | P1 | 中 | `GRC src/service/grc_service.cpp:211；GRG src/service/grg_service.cpp:214` | 代码热点支撑 |
| 11 | 配置驱动组件注册与 ApplicationContext 获取模式 | Set2setPredictorPlugin、DynamicTimeOutPlugin、GraphEngine 组件获取 | P1 | 低 | `GRC src/processor/set2set_predict_function.cpp:34；GRC src/service/grc_service.cpp:177` | 代码热点支撑 |
| 12 | 高频字典/实验参数访问反模式扫描 | adjuster 内 GET_COMMON_DICT / EXPERIMENT_GET_PARAM 在循环中的位置 | P1 | 中 | `GRC src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:70；GRC src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:44` | 近一周 adjuster 高频变更 |
| 13 | SIA/BVAR 热点监控打点与性能归因 | SIA_START/SIA_END、service latency bvar 与模块耗时定位 | P2 | 低 | `GRC src/processor/video_launch/data_merge.cpp:49；GRC src/service/grc_service.cpp:50` | 周报性能优化支撑 |
| 14 | Proto ParseFromString 与缓存代理解析成本 | GRG cache_proxy/scatter_context parse 点与异常处理 | P2 | 中 | `GRG src/operator/diversity/scatter_context.cpp:2174；GRG src/strategy/cache_proxy/newhot_cache_proxy.cpp:128` | 内搜 protobuf性能优化命中 |
| 15 | Graph reset 前后数据引用生命周期 | GraphData ref/cref、pooled_graph reset 后悬挂引用风险 | P2 | 高 | `GRC src/service/grc_service.cpp:220；GRG src/service/grg_service.cpp:91` | KU graph-engine 解读命中 |

## 15 个业务链路候选

| Rank | 选题 | 范围 | 优先级 | 难度 | 代码证据 | 内网/文档支撑 |
|---:|---|---|---|---|---|---|
| 1 | GRC 正排 GCMS/IFCS fill_meta 链路 | FillMetaBaseProcessor -> GcmsComponent::query_common -> IFCS parser -> VideoInfo/NewsInfo | P0 | 高 | `GRC src/processor/fill_meta.cpp:244；GRC src/plugin/ifcs_component.cpp:203` | KU：正排字段添加指南-IFCS版、IFCS多级缓存介绍、IFCS-热点缓存 |
| 2 | GRC 成本/CPU 性能优化热点复盘 | 最近提交 feed-arch-36986 + 调权/过滤热点优化周报 + 序列化/PCS 热点 | P0 | 高 | `GRC src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:47；GRC src/processor/set2set_predict_function.cpp:589` | 周报：许云鹏/郑朋 feeda-mv-grc CPU 3%-5%、月化收益 |
| 3 | Dup Service 去重与 title dup 信号跨层流转 | GRC DupClient 初始化、GRG is_title_dup 消费、GR 写缓存/Recall 查重模型 | P0 | 高 | `GRC src/plugin/dup_service.cpp:22；GRG src/operator/diversity/Rkcj_diversity_hard_rule.cpp:43` | KU：dup-server标题lcs去重透传grg、二跳gr梳理 |
| 4 | GRC fake_id 关闭/替换链路 | 最近提交 feed-arch-37113；fake_id 在 feed_ufs/ctr_rank 请求下游处的覆盖 | P0 | 中 | `GRC src/plugin/feed_ufs_plugin.cpp:114；GRC src/processor/ctr_rank.cpp:609` | 代码提交热点支撑 |
| 5 | GRG 个性化推荐关闭开关 fakeid 兼容 | 最近提交 feed-arch-37111；GRG service 请求基础信息、cuid/uid/vip 注入 | P0 | 中 | `GRG src/service/grg_service.cpp:133；GRG src/service/grg_service.cpp:153` | 代码提交热点支撑 |
| 6 | 召回 quota 与 video_launch 数据合并 | DataMergeFunction 合并保量/非保量、_total_quota、mv/sv/dt/dj/heji 计数 | P0 | 中 | `GRC src/processor/video_launch/data_merge.cpp:51；GRC src/processor/video_launch/data_merge.cpp:79` | 会议搜索“召回quota”无结果，代码变更明显 |
| 7 | 看剧/合集/短剧 LCN 与扶持策略 | kanju_lcn、kanju_days_lt、fuchi_boost、heji 分类链路 | P1 | 中 | `GRC src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:44；GRC src/processor/video_launch/data_merge.cpp:93` | 最近提交：合集追打、看剧资源扶持 |
| 8 | 互动调权 hudong_v4/v5 策略迭代 | MicrovideoHudongV5PreciseAdjuster 与 UMS hudong 特征来源 | P1 | 中 | `GRC src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:17；GRC src/plugin/ums_parser.cpp:1825` | 最近提交：cj_grc_0526 互动 |
| 9 | AIGC 低清降权与正排字段解析 | aigc_resource/aigc_from/aigc_generate_type 从 IFCS/GCMS 到 adjuster | P1 | 中 | `GRC src/operator/adjuster/precise/microvideo_aigc_clarity_adjuster.cpp:26；GRC src/plugin/ifcs_component.cpp:339` | 最近提交：aigc低清降权 |
| 10 | Set2Set 预测与二跳 nid 替换 | set2set predictors、sample 序列化、replace_nid_info/二跳替换提交 | P1 | 高 | `GRC src/processor/set2set_predict_function.cpp:34；GRC src/processor/replace_nid_info.cpp:1` | 最近提交：二跳nid替换 |
| 11 | actual_reqnum 回传与 GR/GRC 结果数闭环 | GRC service 读取 reqnum、response 透传 actual_reqnum 给 GR | P1 | 低 | `GRC src/service/grc_service.cpp:182；GRC src/processor/response.cpp:370` | 代码热点支撑 |
| 12 | DocFeatureWithCache/Yitu 用户意图延续 | doc_feature_with_cache_yitu bthread 并行、用户意图实验参数 | P1 | 中 | `GRC src/processor/doc_feature_with_cache_yitu.cpp:15；GRC src/processor/doc_feature_with_cache_yitu.cpp:41` | 近一周 doc_feature_with_cache_yitu.cpp 变更 212 行 |
| 13 | GRG DiversityMerge 多样性与 reqnum 截断 | ScatterContext 读取 Reqnum、DiversityMergeFunction 调用 MultiStreamEngine | P1 | 高 | `GRG src/operator/diversity/scatter_context.cpp:1646；GRG src/process/diversity_merge.cpp:89` | 代码热点支撑 |
| 14 | GRG DeepesQuotaInfo 与多品类配额 | DeepesQuotaInfo sv/mv/dt/dj/heji 比例在融合层的作用 | P2 | 低 | `GRG src/data/base.h:1248；GRG src/operator/diversity/scatter_context.cpp:8412` | 代码热点支撑 |
| 15 | IFCS 热点缓存与本地缓存雪崩风险 | IFCS 多级缓存、热点缓存文档与 GRC fill_meta 本地查询链路 | P2 | 中 | `GRC src/plugin/ifcs_component.cpp:40；GRC src/processor/fill_meta.cpp:244` | KU：IFCS多级缓存介绍、IFCS-热点缓存、2026稳定性问题记录 |

## 推荐 7+7 每日组合

| 日期 | 基础库主题 | 业务主题 | 安排理由 |
|---|---|---|---|
| 周一 | GraphEngine GraphPool 对象池复用与 reset 生命周期 | GRC 正排 GCMS/IFCS fill_meta 链路 | P0 优先，前半周放高难度，周末放中低风险串联主题 |
| 周二 | Protobuf SerializeToString 热点与 FlatBuffers 替代方向 | GRC 成本/CPU 性能优化热点复盘 | P0 优先，前半周放高难度，周末放中低风险串联主题 |
| 周三 | bthread_async 批量并行模式与捕获安全 | Dup Service 去重与 title dup 信号跨层流转 | P0 优先，前半周放高难度，周末放中低风险串联主题 |
| 周四 | MultiStreamEngine bind_graph_dependency 桥接外层 GraphData | 召回 quota 与 video_launch 数据合并 | P0 优先，前半周放高难度，周末放中低风险串联主题 |
| 周五 | PipelineGraphFunction parallel_consume 批消费框架 | 看剧/合集/短剧 LCN 与扶持策略 | P0 优先，前半周放高难度，周末放中低风险串联主题 |
| 周六 | DynamicTimeOutPlugin 动态超时注入与场景映射 | GRC fake_id 关闭/替换链路 | P0 优先，前半周放高难度，周末放中低风险串联主题 |
| 周日 | TraceLog/VIP/HashLog 采样与业务日志分层 | GRG 个性化推荐关闭开关 fakeid 兼容 | P0 优先，前半周放高难度，周末放中低风险串联主题 |

## 内网检索日志
- OKR 搜索：`GRC召回`、`正排IFCS`、`去重dup`（2026 Q2）均成功返回但 data 为空，命中 0。
- 会议搜索：`GRC性能优化`、`召回quota` start-time=last_week 均成功返回但 data 为空，命中 0。
- 周报搜索：`feeda-mv-grc` 命中 20 条，重点包括郑朋/许云鹏关于 grc 热点优化、CPU 3%-5%、月化收益，陈达关于 flat_buf 3ms 优化。
- KU 搜索：`dup service` 命中 10 条，包含 dup-server 标题 lcs 去重透传 grg、二跳 gr 梳理；`IFCS正排缓存` 命中 10 条，包含正排字段添加指南-IFCS版、IFCS多级缓存介绍、IFCS热点缓存；`graph engine` 命中 10 条，包含 graph-engine 解读/源码学习/condition 用法。未逐篇读取正文，仅使用搜索摘要与 URL 标题，不编造正文结论。
- 内搜：`bthread并行消费` 命中 10 条，包含 bthread、ParkingLot、P2PChannel、Ranker并行组件；`protobuf性能优化` 命中 10 条，包含 combo性能优化、protobuf性能优化、火焰图+耗时优化。

## 代码变更热点（近一周 numstat Top）
- GRC: `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp` 452 行、`src/processor/video_launch/data_merge.cpp` 445 行、`src/processor/set2set_predict_function.cpp` 271 行、`conf/exp_params.conf` 310 行、多个 kanju/hudong/aigc adjuster。
- GRG: `src/service/grg_service.cpp` 25 行，`src/util/util.cpp` 8 行，`src/util/util.h` 2 行。
