---
title: 正排（GCMS/IFCS）在 feeda-mv-grc 中的作用：召回结果补元信息、过滤与特征构造
generated_at: 2026-05-21T20:00:47+08:00
代码库路径: /home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc
对照代码库: /home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg（仅作背景，不把结论直接套用）
内网文档检索关键词:
  - GCMS
  - 正排
  - feeda-mv-grc
  - 召回 正排
  - ContentFeature
  - doc feature
  - IFCS 正排缓存
代码检索关键词:
  - gcms_common_pb_plugin
  - ifcs_sdk
  - GcmsComponent
  - query_common
  - FillMetaPipelineFunction
  - GcmsData
  - MicroVideoInfo
  - ContentFeature
  - DocFeatureWithCache
  - _video_info / gcms_data
置信度: 中高；核心链路由本地代码直接命中。内网文档查询因认证/开放应用错误失败，仅保留本地配置中引用的 KU 文档 URL。
---

# 正排（GCMS/IFCS）在 feeda-mv-grc 中的作用：召回结果补元信息、过滤与特征构造

## 0. 运行日志摘要

本次业务主题按要求采用“内网知识检索 → 入口识别 → 关键词扩展 → 链路追踪”的顺序。最终结论收敛为：`feeda-mv-grc` 中的正排主链路不是直接散落在各个召回里，而是集中在 **FillMeta / FillMetaPipeline** 阶段，通过 `GcmsComponent` 调 IFCS 正排缓存服务，得到 `MicroVideoInfo/NewsInfo` 后挂到 `RidTmpInfo`，下游过滤、排序、特征服务再消费这些字段。

## 1. 内网知识检索阶段

### 1.1 检索动作与结果

可用工具：`ku-doc-manage` CLI，路径 `/root/.hermes/skills/ku-doc-manage/bin/ku`。

执行动作：根据本地配置文件发现的 KU 文档链接查询内容：

- 本地来源：`conf/common_component/gcms_common_pb_plugin.conf:1`
- 链接：`https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/pmquNWcpSA/_zXrQ8a8f1/rzlycnK9rMzhXQ`
- 本地配置中的标题/摘要：`mv-grc已接入ifcs正排缓存服务，正排字段开发详见...`

查询失败原因：CLI 自动安装 `get-ugate-token` 后，调用返回 `开放应用不存在，检查下ak/sk或x-ac-Authorization值是否准确`，状态/returnCode 为 `60103`。因此本次**未能读取 KU 正文**，不能引用其详细业务结论。

### 1.2 基于本地证据可确认的内网结论

虽然 KU 正文不可读，但 `conf/common_component/gcms_common_pb_plugin.conf:1` 明确给出两个信息：

1. `mv-grc` 已接入 `ifcs` 正排缓存服务；
2. 正排字段开发以该 KU 文档为准。

后续代码链路与这一点一致：`GcmsComponent` 初始化 `feed::ifcs::IfcsSdk<BaseDocInfo>`，查询时调用 `_ifcs_sdk->get(...)`。

## 2. 代码入口识别：feeda-mv-grc 的服务角色

`README.md:1-4` 写明这是“小视频业务 grc 层模块”，并给出图引擎静态/动态图可视化入口。

服务入口 `src/main.cpp` 体现了典型 graph-engine + brpc 服务形态：

- `src/main.cpp:37-45` 定义服务端口、worker 线程数、dapper 等 gflags；
- `src/main.cpp:90-95` 加载 `conf/gflags.conf` 并执行 `GlobalInitializer::init()` 与 `ExpManager::init()`；
- `src/main.cpp:104-121` 创建 `baidu::rpc::Server`，注册 `GenericGRCService` 和 `GrcHttpServiceImpl`；
- `src/main.cpp:126-130` 启动 Dapper collector、`server.Start(FLAGS_port, &options)` 并运行。

图配置根文件 `conf/plugins/graph/global.conf`：

- `:1-4` 定义 graph pool/name；
- `:6-39` 注入 `log_id/uid/cuid/ua/ReqInfo/ExpInfo` 等全局依赖；
- `:41-83` include `graph.conf`、`request_handle.conf`、`readlist_meta.conf`、`recall_graph.conf`、`queue_vertex.conf`、`response.conf` 等子图。

服务层 `GenericGRCService` 在 `src/service/grc_service.cpp:86-148` 把 request 与公共字段 emit 到 graph data：`Request`、`log_id`、`uid`、`cuid`、`UA/ua`、`flow_loc`、`IsGrgRequest`、`ExpInfo` 等。

## 3. 关键词扩展检索结论

直接搜 `gcms|GCMS` 在工具默认搜索下未命中，说明不能只依赖大小写/文件名；扩展到 `ifcs`、`GcmsComponent`、`query_common`、`MicroVideoInfo`、`ContentFeature` 后命中核心链路：

- `src/plugin/gcms_component.cpp`：IFCS SDK 初始化与查询封装；
- `src/plugin/ifcs_component.cpp`：IFCS 返回 protobuf 到 `MicroVideoInfo/NewsInfo` 的 parser；
- `src/processor/fill_meta.cpp` 与 `src/processor/video_launch/fill_meta_pipeline.cpp`：取正排并挂载到 `RidTmpInfo`；
- `src/processor/doc_feature_with_cache_yitu.cpp`：把 `MicroVideoInfo` 转成 `ContentFeature/RecommendFeature`；
- 大量下游 processor 读取 `rid_info->gcms_data->_video_info` 做过滤、排序、特征。

## 4. 端到端代码链路图

```text
Request → GenericGRCService::emit_common_data()
  → Graph(default/global.conf)
  → DsToRidInfoPipelineVertex：召回结果转 RidTmpInfo / QueueRecallResult
  → FillMetaPipelineFunction：收集 merge_rids
      → GcmsComponent::query_common()
          → feed::ifcs::IfcsSdk<BaseDocInfo>::get(ifcs_ctx, nids, result)
          → MvRecallDocParser::parse()/merge() 解析 MicroVideoItemGcmsInfo
      → 将 MicroVideoInfo 挂到 RidTmpInfo::_video_info / gcms_data
  → 下游：RecallFunnel / GetVidClk / filter / rank / adjust / response
  → 可选特征构造：DocFeatureWithCache / YituDocFeatureWithCache
      → ContentFeature / RecommendFeature
  → UserIntentPredict / Set2setPredict / 响应构造
```

## 5. Producer → Transform → Consumer 三段追踪

### 5.1 Producer：IFCS 正排缓存服务

`GcmsComponent` 是正排读取入口：

- `src/plugin/gcms_component.cpp:18-30` new `feed::ifcs::IfcsSdk<BaseDocInfo>()`，通过 `ApplicationContext` 初始化 `ifcs_sdk.conf`；
- `src/initializer/global.h:255-258` 在全局初始化阶段调用 `GcmsComponent::get_instance().init()`，失败则 FATAL/exit；
- `src/plugin/gcms_component.h:19-24` 定义场景：`GCMS_SECNE_RECALL/NEWS/HISTORY/SEARCH`；
- `src/plugin/gcms_component.cpp:40-79` 的 `query_common()` 构造 `feed::ifcs::Context`，填入 `log_id/scene/sids/server_cache_only`，然后 `_ifcs_sdk->get(ifcs_ctx, nids, result)`；
- `src/plugin/gcms_component.cpp:80-101` 的 `query_news()` 使用 `GCMS_SECNE_NEWS` 查询新闻类正排。

`query_common()` 还包含场景切换/降级逻辑：`src/plugin/gcms_component.cpp:48-59` 默认 `server_cache_only=true`，当 UA 属于 `{69,77,86,123,155,156}` 或特定 `ua=85/flow_loc` 条件时切到 `GCMS_SECNE_SEARCH` 并关闭 `server_cache_only`。

### 5.2 Transform：IFCS item 解析为业务结构

`src/plugin/ifcs_component.cpp` 中 parser 把 `IfcsItem.value()` 解析为 `MicroVideoItemGcmsInfo`：

- 新闻 parser：`src/plugin/ifcs_component.cpp:60-90` 定义 `MvNewsDocParser`；`:90-185` 将 PB 字段复制到 `NewsInfo`，包括 `del_tag/news_category/title/mthid/public_time/check_content_quality` 等；
- 小视频召回 parser：`src/plugin/ifcs_component.cpp:196-228` 定义 `MvRecallDocParser`；`:228-260` 开始解析 `MicroVideoInfo`，`:233-243` 先处理 small-flow 字段 `smfw`；
- small-flow merge：`src/plugin/ifcs_component.cpp:198-226` 会根据命中 sid 将 `smfw_fields` merge 回主 `MicroVideoInfo`，日志关键词为 `ifcs_gcms_smfw_merge`。

正排数据在本地通过 `GcmsData` 包装：`src/plugin/gcms.h:33-49` 定义 `GcmsData`，持有 `MicroVideoInfo` 引用/指针；`:53-97` 的 `to_string()` 展示大量可消费字段，如 `del_tag/fc_tag/video_duration/new_cate_v2/new_sub_cate_v2/is_microvideo/vertical_type/public_time/title_len/check_content_quality` 等。

### 5.3 Transform：FillMeta 挂载到 RidTmpInfo

普通队列 pipeline 路径：

- `conf/plugins/graph/queue_vertex.conf:24-57` 定义 `FillMetaPipelineFunction`，依赖 `QueueRecallResult/SidInfo/Followed*Set/IsDibarSearchLanding`，emit `FillMetaPipelineResult/CardFillMetaResult/UpToFillMetaCost`；
- `src/processor/video_launch/fill_meta_pipeline.cpp:108-123` 构造 `gcms_context` 并调用 `GcmsComponent::query_common(merge_rids, meta_map, ...)`；
- `src/processor/video_launch/fill_meta_pipeline.cpp:155-174` 遍历 `rid_vec`，按 `rid` 查 `meta_map`，命中后设置 `queue_iter->_video_info = gcms_pb_info.get()` 并 `queue_iter->gcms_data = boost::make_shared<const GcmsData>(queue_iter->_video_info)`；
- `src/processor/video_launch/fill_meta_pipeline.cpp:176-185` 在 `hit_collection_ai_zpsmfw` 时复制并改写合集信息，然后重新包装 `GcmsData`；
- `src/processor/video_launch/fill_meta_pipeline.cpp:194-310` 在 `hit_sc_ipname_smfw` 时复制正排，基于 `vertical_type/is_microvideo/content_type/new_cate_v2/vote_strategy_ip/ip_strategy_ip/search_movie_ip_name` 推导 IP 字段并重新挂载。

非 pipeline/base processor 路径也存在：

- `src/processor/fill_meta.cpp:237-252` 调 `GcmsComponent::query_common(..., GCMS_SECNE_RECALL)`；
- `src/processor/fill_meta.cpp:296-321` 对 `RecallResult` 中每个 `RidTmpInfoPtr` 挂 `MicroVideoInfo/GcmsData`；
- `src/processor/fill_meta.cpp:324-333` 与 `:342-440` 后续也会按实验改写正排副本。

数据结构承载点：`src/data/rid_tmp_info.h:2075-2076` 定义 `gcms_data/const_gcms_data`，`:2105-2106` 定义 `_video_info/_video_info_copy`。

### 5.4 Consumer：过滤、排序、特征构造

下游大量 processor 消费 `gcms_data->_video_info`。代表性证据：

- `src/processor/sketchy_score_init.cpp:130-140` 使用 `siteAccountId/mthid` 判断关注关系相关统计；`:147-165` 还用 bthread 按 batch 并发；
- `src/processor/ctr_rank.cpp:188-189` 使用 `vertical_type/sv_duration`；`:662-678` 使用 `idl_cate_micro/video_type/is_microvideo`；
- `src/processor/compute_instant_model_weight.cpp:243-254` 使用 `vertical_type/is_microvideo/video_duration/new_cate_v2/new_sub_cate_v2`；
- `src/processor/merge_recall.cpp:570`、`:1192`、`:1470` 等多处以 `gcms_data == nullptr` 作为资源是否可继续参与处理的前置检查；
- `src/processor/response.cpp:1655` 在响应构造阶段仍检查 `rid_info->gcms_data`。

### 5.5 Consumer：ContentFeature / doc feature 构造

`YituDocFeatureWithCacheFunction` 是从正排到模型样本特征的清晰例子：

- 图配置：`conf/plugins/graph/vertex.conf:2570-2587` 注册 `YituDocFeatureWithCacheFunction`，仅 `condition: UA == 85`，依赖 `MergeResultForAdjust/SidInfo/CommonInfo`，emit `YituDocFeatureWithCacheResult`；`:2590-2616` 下游 `UserIntentPredictFunction` 依赖这个 doc sample；
- 代码：`src/processor/doc_feature_with_cache_yitu.cpp:21-41` 在命中实验 `hit_user_intent_yanxu` 且 `ua==85` 时处理 `_effect_queue`；
- `src/processor/doc_feature_with_cache_yitu.cpp:43-50` 为每个 `RidTmpInfo` 构造 `SampleContext`，写 `content_feature` 和 `recommend_feature`；
- `src/processor/doc_feature_with_cache_yitu.cpp:60-64` 只在 `_video_info != nullptr` 时构造视频内容特征；
- `src/processor/doc_feature_with_cache_yitu.cpp:77-160` 从 `MicroVideoInfo` 填充 `ContentFeature`：manual tags、uploader、二级类目、mthid、duration、is_microvideo、vertical_type、public_time、picture_num、短剧结构标签、合集/短剧类型等；
- `src/processor/doc_feature_with_cache_yitu.cpp:187-218` 从 `RidTmpInfo` 的多路分数填充 `RecommendFeature`。

GRG response 子图里还有另一个 `DocFeatureWithCacheFunction`：`conf/plugins/graph/video_launch/grg_response.conf:543-557` emit `DocFeatureWithCacheResult`，`:559-586` 下游 `Set2setPredictFunction` 消费它。这是 `feeda-mv-grc` 的本地配置，不是把 `feeda-mv-grg` 结论套用过来。

## 6. 关键模块表

| 模块 | 文件/行号 | 作用 | 证据 |
|---|---|---|---|
| 服务入口 | `src/main.cpp:73-130` | 初始化日志、协议、GlobalInitializer、实验、注册 GRC 服务并启动 brpc | 入口链路 |
| 图根配置 | `conf/plugins/graph/global.conf:1-83` | 注入全局依赖并 include 子图 | graph-engine 结构 |
| 请求注入 | `src/service/grc_service.cpp:86-148` | 把 Request、UA、flow_loc、ExpInfo 等 emit 到 GraphData | 服务 → graph |
| IFCS SDK 初始化 | `src/plugin/gcms_component.cpp:18-30`、`src/initializer/global.h:255-258` | 创建并初始化正排缓存 SDK | Producer |
| IFCS 查询 | `src/plugin/gcms_component.cpp:40-79` | 构造 IFCS context，按 nids 查询 MicroVideoInfo | Producer |
| Parser | `src/plugin/ifcs_component.cpp:196-260` | `IfcsItem` → `MicroVideoInfo`，含 small-flow 字段 | Transform |
| 正排包装 | `src/plugin/gcms.h:33-49` | `GcmsData` 包装 `MicroVideoInfo` | 数据承载 |
| FillMeta pipeline | `src/processor/video_launch/fill_meta_pipeline.cpp:108-174` | 查询正排并挂到 `RidTmpInfo` | Transform |
| Graph vertex | `conf/plugins/graph/queue_vertex.conf:24-57` | `FillMetaPipelineFunction` 的 depend/emit | DAG 证据 |
| RidTmpInfo 字段 | `src/data/rid_tmp_info.h:2075-2106` | 保存 `gcms_data/_video_info/_video_info_copy` | 数据结构 |
| ContentFeature 构造 | `src/processor/doc_feature_with_cache_yitu.cpp:43-160` | 正排 → `ContentFeature` | Consumer |
| UserIntent 消费 | `conf/plugins/graph/vertex.conf:2570-2616` | doc sample 被 `UserIntentPredictFunction` 消费 | Consumer |

## 7. 数据结构与字段

核心数据结构：

- `MicroVideoInfo`：正排主结构，本地字段很多，完整定义在 `src/data/video_info.h`；本次重点证据来自 parser 与 `GcmsData::to_string()`。
- `GcmsData`：`src/plugin/gcms.h:33-49` 包装 `MicroVideoInfo`，在 `RidTmpInfo` 中以 `boost::shared_ptr<const GcmsData>` 持有。
- `RidTmpInfo`：召回候选 item，`src/data/rid_tmp_info.h:2075-2106` 增加正排字段承载。
- `ContentFeature`：模型/特征服务用 PB，`doc_feature_with_cache_yitu.cpp` 从 `MicroVideoInfo` 映射出类目、时长、作者、短剧标签等。

常见字段类别：

- 内容质量/过滤：`del_tag/fc_tag/fc_tag_source/fc_tag_reason/check_content_quality`；
- 类目与垂类：`new_cate_v2/new_sub_cate_v2/idl_cate_micro/vertical_type/is_microvideo/video_type/content_type`；
- 时效与基础信息：`public_time/expire_time/title/title_len/video_duration`；
- 作者/IP/合集：`siteAccountId/mthid/uploader/sc_ip/vote_strategy_ip/ip_strategy_ip/search_movie_ip_name/article_collections_info/video_collections_ai`；
- 模型特征：manual tags、短剧结构标签、duration、picture_num、playlet set type。

## 8. 配置、实验开关与降级逻辑

- 端口与图配置：`conf/gflags.conf:1` 端口 `8946`；`:38-40` 指定 `graph_conf_path=./conf/plugins`、`graph_conf_file=multi_graph.conf`、`plugins_gcms_file=gcms_global.conf`。
- 正排类型：`conf/gflags.conf:42-43` 注释“0 加载所有正排 1:全民二跳正排 2:星火正排”，当前 `-gcms_type=0`。
- PB 正排：`conf/gflags.conf:82-83` `-is_gcms_pb_flag=true`。
- IFCS 文档入口：`conf/common_component/gcms_common_pb_plugin.conf:1` 指向“mv-grc已接入ifcs正排缓存服务”的 KU 文档。
- 查询场景切换：`src/plugin/gcms_component.cpp:48-59` 根据 UA/flow_loc/sid 切换 SEARCH 场景和 `server_cache_only`。
- 查询失败：`src/processor/fill_meta.cpp:244-249` 若 `query_common` 返回非 0，记录 `gcms request failed` 并返回 -1；pipeline 版本 `src/processor/video_launch/fill_meta_pipeline.cpp:140-144` 记录 warning 后返回 `ERR_OK`，体现不同图路径降级策略不同。

## 9. 风险与排查方法

1. **正排缺失**：看 `fill_meta_gcms_pb_rpc` / `fill_meta_pipeline_rpc` SIA，日志 `gcms request failed`、`no nids`。证据：`src/processor/fill_meta.cpp:253-265`、`src/processor/video_launch/fill_meta_pipeline.cpp:125-137`。
2. **字段为空或 small-flow 未生效**：查 `ifcs_gcms_smfw_merge` 日志与 sid 是否传入 `ifcs_ctx.sids`。证据：`src/plugin/ifcs_component.cpp:198-226`、`src/plugin/gcms_component.cpp:65-70`。
3. **下游过滤异常**：grep 目标字段的 getter，例如 `get_fc_tag|get_vertical_type|get_new_cate_v2`，确认是否在 FillMeta 后消费。
4. **ContentFeature 缺字段**：查 `doc_feature_with_cache_yitu.cpp:77-160` 映射表；如果是新闻资源，注意 `contruct_news_feature` 目前被注释，`src/processor/doc_feature_with_cache_yitu.cpp:163-186` 明确是 TODO/注释状态。
5. **不可把 GRG 当 GRC 事实**：`feeda-mv-grg` 可作为 GCMS 模式对照，但本报告的核心 Producer/Transform/Consumer 均来自 `feeda-mv-grc` 本地代码。

## 10. 未确认问题与下一步检索计划

- **KU 文档正文未读取**：认证失败（returnCode 60103）。下一步需修复 ugate/open app 配置后读取 `https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/pmquNWcpSA/_zXrQ8a8f1/rzlycnK9rMzhXQ`，补充“字段开发规范/业务口径”。
- **IFCS SDK 配置未完全展开**：本地命中 `ifcs_sdk.conf` 名称，但未在当前片段中展开 SDK 内部路由/BNS/缓存层级。下一步搜索 `conf/common_component` 和外部 IFCS SDK 包。
- **`MicroVideoInfo` 字段全量映射未枚举**：本次只列关键字段。下一步可基于 `src/data/video_info.h` 和 `ifcs/mv_grc_gcms.pb.h` 生成字段差异表。
- **老 GCMS SDK 与 IFCS 的关系**：`src/plugin/gcms.h:18` 仍 include `gcms_sdk.h`，并有 `MyClosure`；但主查询链路已命中 IFCS `GcmsComponent`。下一步应确认旧 SDK 是否仍在某些非主路径使用。

---

## 本次增量分析（2026-05-22）

### 背景

今日继续深入追踪 GCMS 在 `feeda-mv-grc` 中的作用链，新增以下维度：

1. 明确区分 `FillMetaBaseProcessor` 与 `FillMetaPipelineFunction` 两条并行 FillMeta 路径及其在 DAG 中的定位。
2. 追踪 RecallResultWithMeta → DataWithMeta → OutputMap → 下游 consumer 的完整 Graph Data 流。
3. 补充 `recall_fill_filter.conf` 模板实例化模式，展示 20+ 个召回渠道的正排填充复用结构。
4. 深入 `GcmsData` 的 ObjectPool 内存管理机制。
5. 追踪 `QueueContext::gcms_common_pb_meta_map` 在 Pipeline 中的传递。

> ⚠️ 本次分析**不**把 `feeda-mv-grg` 的 GCMS 结论直接套用到 `feeda-mv-grc`；所有证据均来自 `feeda-mv-grc` 本地代码（`/home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc`）。

### 内网检索结果

同昨日：认证失败（returnCode 60103，`开放应用不存在`）。无法读取 KU 正文。替代方案：基于本地代码 + 本地配置文件中引用的 KU 文档标题（`mv-grc已接入ifcs正排缓存服务`）确认业务语义。

---

### 1. 两条 FillMeta 路径的区分与 DAG 定位

#### 1.1 FillMetaPipelineFunction（主路径，queue_vertex）

配置：`conf/plugins/graph/queue_vertex.conf:24-57`

```ini
[@vertex]
function: FillMetaPipelineFunction
[.@depend]
name: _queue_recall_result  # DsToRidInfoPipelineVertex 输出的 RecallResult
data: QueueRecallResult
[.@depend]
name: _sid_info
data: SidInfo
[.@depend]
name: _followed_tb_fid_set
data: SketchyRankFollowedTbFidSet
[.@depend]
name: _followed_author_set
data: SketchyRankFollowedAuthorSet
[.@depend]
name: _followed_mthid_set
data: SketchyRankFollowedMthidSet
[.@depend]
name: _is_dibar_search_landing
data: IsDibarSearchLanding
[.@depend]
name: _is_lite_search_landing
data: IsLiteSearchLanding
[.@emit]
name: _fill_meta_pipeline_result
data: FillMetaPipelineResult
[.@emit]
name: _card_fill_meta_result
data: CardFillMetaResult
[.@emit]
name: _up_to_fill_meta_cost
data: UpToFillMetaCost
[.option]
is_queue: 1
```

关键特征：`is_queue: 1` → 这个 vertex 是队列模式，意味着 `PipelineGraphFunction::processor()` 被调用，执行 `parallel_consume()` 并发分片。

#### 1.2 FillMetaBaseProcessor（非 pipeline 路径）

用于各个独立召回的正排填充，通过模板实例化多个 vertex。

配置：`conf/plugins/graph/conf_template/recall_fill_filter.conf:29-45`

```ini
[@vertex]
processor: FillMetaBaseProcessor
name: FillMeta_<$service_name:$>_<$fork_type:$>
[..@emit]
name: RecallResultWithMeta
data: <$data_prefix:$>ResultWithMeta
[..@depend]
data: <$data_prefix:$>RecallResult   # 匿名 anonymous depend，多个 RecallResult
is_anonymous: 1
[..@depend]
data: SidInfo
[..@depend]
data: SketchyRankFollowedAuthorSet
[..@depend]
data: SketchyRankFollowedMthidSet
[..@option]
handle_name: <$service_name:$>_<$fork_type:$>
```

关键特征：非 queue 模式（无 `is_queue`），`is_anonymous: 1` 的匿名 depend 表示接受多个匿名 RecallResult，`setup()` 中通过 `vertex.anonymous_dependency_size()` 遍历（`src/processor/fill_meta.cpp:137-145`）。

#### 1.3 两条路径的代码差异

| 维度 | FillMetaPipelineFunction | FillMetaBaseProcessor |
|---|---|---|
| 配置 | `queue_vertex.conf` | `recall_fill_filter.conf` 模板实例化 |
| 并发 | `parallel_consume()` 分片并发 | 单线程遍历 anonymous depend |
| GCMS 查询 | `QueueContext::gcms_common_pb_meta_map`，每 batch 独立 | `FillMetaBaseContext::gcms_common_pb_meta_map`，全局共享 |
| 数据结构 | `FillMetaPipelineResult`，含 `_video_info` | `RecallResultWithMeta`，emit 后被后续 vertex 依赖 |
| 适用场景 | 主队列正排填充（视频/新闻/电商） | 20+ 独立召回渠道的正排填充（CF/UCF/UGC/Direct/HotIntervene 等） |

代码证据（FillMetaPipelineFunction）：
- `src/processor/video_launch/fill_meta_pipeline.cpp:118-123`：在每个 `process(queue_context)` 内调 `GcmsComponent::query_common(merge_rids, meta_map, gcms_context, log_id, _sid_info, &context)`，其中 `meta_map` 来自 `QueueContext::gcms_common_pb_meta_map`（`src/processor/grc.h:42` 定义）。
- `src/processor/video_launch/fill_meta_pipeline.cpp:155-174`：分片内遍历 `rid_vec` 填充 `_video_info` / `gcms_data`。

代码证据（FillMetaBaseProcessor）：
- `src/processor/fill_meta.cpp:182-198`：遍历 `anonymous_dependency_size()` 收集所有 `RecallResult` 中的 rid 到 `merge_rids`。
- `src/processor/fill_meta.cpp:244-249`：一次性对 `merge_rids` 调用 `GcmsComponent::query_common()`。

---

### 2. RecallResultWithMeta → OutputMap → 下游 consumer 链路

#### 2.1 RecallResultWithMeta 的 emit 模式

`RecallResultWithMeta` 由以下 vertex emit（按 `conf/plugins/graph/` 中的配置）：

| vertex / function | 来源 conf | 场景 |
|---|---|---|
| `FillMeta_<$service$_$fork$>` | `conf_template/recall_fill_filter.conf` 展开 | 20+ 独立召回渠道（CF/UCF/Direct/NewHot/直播等） |
| `ShowlistFillMetaBaseProcessor` | `request_handle.conf:41-56` | 历史/历史列表正排 |
| `UaStrFillMetaBaseProcessor` | `vertex.conf:1964-1980` | UA 分桶召回正排 |
| `ManjuAppFollowUpdateFillMetaBaseProcessor` | `manju_app_follow_update.conf:18-36` | 追更更新正排 |

这些 `RecallResultWithMeta` 被下游 vertex 依赖，形成数据流：

#### 2.2 OutputMap 转换：FillMetaToMapProcessor

`src/processor/fill_meta_to_map.cpp` 中的 `FillMetaToMapProcessor` 把 `MultiQueueResult`（即 `RecallResultWithMeta`）展开为 `unordered_map<rid, RidTmpInfoPtr>`：

- `src/processor/fill_meta_to_map.cpp:43-55`：遍历 `fill_meta_data` 的每个队列，把 `RidTmpInfoPtr` 按 rid emplace 到 `output_map`。
- 注册：`src/processor/fill_meta_to_map.cpp:64` → `BABYLON_REGISTER_COMPONENT_WITH_TYPE_NAME(FillMetaToMapProcessor, GraphProcessor, FillMetaToMapProcessor)`。

配置：`conf/plugins/graph/request_handle.conf:57-69`

```ini
[@vertex]
processor: FillMetaToMapProcessor
name: ShowlistFillMetaToMapProcessor
[.@emit]
name: OutputMap
data: GrShowListlResultMetaMap
[.@depend]
name: DataWithMeta
data: GrShowListlResultWithMeta
```

#### 2.3 下游 consumer 的数据依赖

以 `fill_meta_to_map_for_msv_duration.cpp` 中的 `FillMetaToMapForMsvDurationProcessor` 为例，说明下游如何消费 OutputMap：

- `src/processor/fill_meta_to_map_for_msv_duration.cpp:97-112`：从 `output_map` 中按 rid 查找 `RidTmpInfoPtr`。
- `src/processor/fill_meta_to_map_for_msv_duration.cpp:215-229`：如果 `ridinfo != nullptr && ridinfo->gcms_data != nullptr`，则读取 `ridinfo->gcms_data->_video_info.get_new_sub_cate_v2()` 和 `get_new_cate_v2()`，用于类目维度的配额分析。
- 这说明正排的二级类目字段在下游配额计算中被消费。

#### 2.4 RecallResultWithMeta → downstream pipeline

在 `queue_vertex.conf:59-74`：

```ini
[@vertex]
function: RecallFunnelFunction
[.@depend]
name: _fill_meta_pipeline_result
data: FillMetaPipelineResult
[.@depend]
name: _log_funnel
data: FunnelSampleObj
[.@emit]
name: _upload_recall_funnel_done
data: UploadRecallFunnelDone
```

然后 `GetVidClkPipelineFunction` → `FilterPipelineFunction` → `PreciseScoreInit` → `MultiRank` → `Adjust` → `Response`。

---

### 3. recall_fill_filter.conf 模板实例化：20+ 召回渠道的正排复用

`conf/plugins/graph/conf_value/recall_fill_filter.conf` 实例化了 20+ 个独立召回渠道的正排填充 vertex，每个实例遵循 `FillMeta_<$service$_$fork$>` 命名模式：

| service_name | fork_type | data_prefix |
|---|---|---|
| `video_micro_rec_new` | `video_micro_rec_new` | `VideoMicroRecNewRecall` |
| `video_micro_cf_new` | `video_micro_um_new` | `VideoMicroUmNewRecall` |
| `video_micro_cf_new` | `video_micro_ucf_new` | `VideoMicroUcfNewRecall` |
| `video_micro_cf_new` | `video_micro_cf_new` | `VideoMicroCfNewRecall` |
| `video_micro_cf_new` | `video_micro_mb_new` | `VideoMicroMbNewRecall` |
| `grc_haokan_session` | `haokan_session_for_micro_short` | `HaokanSessionNewRecall` |
| `grc_haokan_session` | `haokan_session_related_for_micro_short` | `HaokanSessionRelatedNewRecall` |
| `grc_haokan_session` | `haokan_session_related` | `HaokanSessionRelatedSearch` |
| `grc_haokan_content` | `haokan_cb_for_micro_short` | `HaokanCbNewRecall` |
| `grc_haokan_content` | `haokan_cb_related_for_micro_short` | `HaokanCbRelatedNewRecall` |
| `grc_direct` | `hk_direct_for_micro_short` | `HaokanDirectNewRecall` |
| `video_direct` | `video_direct_no_dnn_for_micro_short` | `VideoDirectNoDnnForHaokanNewRecall` |
| `shoubai_live_video` | `live_mvideo` | `LiveVideoNew` |
| `shoubai_live_ecom` | `live_mvideo` | `LiveVideoEcom` |
| `shoubai_live_ecom` | `shoubai_video_live` | `VideoLiveEcom` |

完整定义见 `conf/plugins/graph/conf_value/recall_fill_filter.conf:1-121`。

这说明 `FillMetaBaseProcessor` 通过模板化设计，复用了同一套正排填充逻辑，但每个 vertex 独立收集自己依赖的匿名 RecallResult。多个匿名 RecallResult 合并去重后，一次性查询 GCMS。

---

### 4. GcmsData 的 ObjectPool 内存管理

`src/plugin/gcms.h:33-111` 中的 `GcmsData` 使用了 ObjectPool 优化：

```cpp
// src/plugin/gcms.h:37-44
inline static void set_pool(size_t num) {
    _pool.reset(new StaticMemoryPool());
    _s_pool.reset(new MicroVideoInfoPool(num, 0, 0));
    _s_pool->expose("gcms");
}

// src/plugin/gcms.h:45
inline GcmsData() noexcept : _pooled_video_info(_s_pool->get()), _video_info(*_pooled_video_info), _video_info_ptr(&_video_info) {}
```

- `StaticMemoryPool`：在静态内存中连续分配，减少碎片。
- `MicroVideoInfoPool`：基于 `ObjectPool` 的模板特化。
- 构造函数中通过 pool 分配 `MicroVideoInfo`，并用引用 `_video_info` 持有。
- 在 `RidTmpInfo` 中，`gcms_data` 以 `boost::shared_ptr<const GcmsData>` 保存：`src/processor/video_launch/fill_meta_pipeline.cpp:166` → `boost::make_shared<const microvideo::grc::GcmsData>(queue_iter->_video_info)`。

这说明正排数据的生命周期由 `RidTmpInfo` 的生命周期管理，`GcmsData` 本身不直接持有堆分配的数据，而是持有从 ObjectPool 分配的对象。

---

### 5. GCMS 查询上下文构造细节

`src/processor/fill_meta.cpp:203-236` 构造 `gcms_context`：

```cpp
// src/plugin/gcms_component.h:30-37
int32_t query_common(
    const std::unordered_set<uint64_t>& nids,
    ::feed::ifcs::DocInfoMap<MicroVideoInfo>& result,
    const ::baidu::feed::gr::component::Context& gcms_context,
    uint64_t logid,
    const SidInfo* sid_info,
    const Context* context,
    GcmsScene scene = GCMS_SECNE_RECALL);
```

`gcms_context` 的关键字段（`src/processor/fill_meta.cpp:204-236`）：

1. `gcms_context.set_logid(log_id)`：用于 tracing。
2. `gcms_smfw_columns_str_vec`：正排小流量字段列表，当前硬编码为 `autor_level_sm`（17 个 sid，如 `128644_2` 等）。这个列表决定 IFCS 返回哪些额外字段。
3. `sids_str_vec`：根据实验命中的小流量 sid 列表，用于 small-flow merge 逻辑。
4. `add_condition_value("ua", ua)` / `add_condition_value("flow_loc", flow_loc)`：ua/flow_loc 条件用于 IFCS 路由和缓存 key。

GCMS scene 枚举（`src/plugin/gcms_component.h:19-24`）：
- `GCMS_SECNE_RECALL = 0`：主召回场景，用于 FillMeta。
- `GCMS_SECNE_NEWS = 1`：新闻场景。
- `GCMS_SECNE_HISTORY = 2`：历史场景。
- `GCMS_SECNE_SEARCH = 3`：搜索场景，在 `query_common` 中当 UA ∈ {69,77,86,123,155,156} 时切换。

---

### 6. 关键证据来源

| 文件 | 行号 | 内容 |
|---|---|---|
| `conf/plugins/graph/queue_vertex.conf` | 24-57 | FillMetaPipelineFunction DAG 定义 |
| `conf/plugins/graph/conf_template/recall_fill_filter.conf` | 29-45 | FillMetaBaseProcessor 模板定义 |
| `conf/plugins/graph/conf_value/recall_fill_filter.conf` | 1-121 | 20+ 召回渠道实例化 |
| `conf/plugins/graph/request_handle.conf` | 39-69 | ShowlistFillMeta / FillMetaToMap DAG |
| `src/processor/video_launch/fill_meta_pipeline.cpp` | 118-174 | Pipeline 路径 GCMS 查询与挂载 |
| `src/processor/fill_meta.cpp` | 203-236 | GCMS context 构造（小流量/sid/ua） |
| `src/processor/fill_meta.cpp` | 244-249 | 非 pipeline 路径 GCMS 调用 |
| `src/processor/fill_meta_to_map.cpp` | 43-55 | MultiQueueResult → OutputMap |
| `src/processor/fill_meta_to_map_for_msv_duration.cpp` | 97-112, 215-229 | 下游消费 gcms_data 二级类目 |
| `src/processor/grc.h` | 34-45 | QueueContext 定义，含 gcms_common_pb_meta_map |
| `src/plugin/gcms.h` | 33-111 | GcmsData ObjectPool 内存管理 |
| `src/plugin/gcms_component.h` | 19-24 | GCMS scene 枚举 |
| `src/plugin/gcms_component.h` | 30-37 | query_common 接口签名 |

---

### 7. 本次未确认问题与下一步

1. **IFCS SDK 内部实现未展开**：本地命中 `feed::ifcs::IfcsSdk`，但未展开 SDK 内部 BNS 路由/缓存层级。下一步在 `/home1/code_read` 下搜索 `ifcs_sdk.conf` 或 `ifcs_component.cpp` 的完整内容。
2. **FillMetaPipelineFunction 的 concurrencts/batch_size 配置来源**：虽然 `parallel_consume()` 调用了 `context.concurrents()` 和 `context.batch_size()`，但 gflags/graph option 中这些值的赋值链路未追踪到。下一步追 `BaseGraphFunction::setup()` 如何读取这些 option。
3. **`autor_level_sm` 小流量 sid 列表的动态更新机制**：当前代码中硬编码为 17 个 sid，`src/processor/fill_meta.cpp:208-220`。下一步确认是否有动态配置或实验参数化的方式。
4. **FillMetaBaseProcessor 的 GCMS 查询结果如何与 anonymous depend 匹配**：代码显示对 `merge_rids` 一次性查询，但 `RecallResult` 中每个匿名 depend 的类型/来源处理逻辑（`src/processor/fill_meta.cpp:285-321`）仍有未完全解析的分支，如 `_hk_handle_names` / `_channel_handle_names` 对正排字段的特殊处理。

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-20T19:14:29.786272
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析：GCMS / IFCS 正排能力

## 1. 分析摘要

- 这套技术的本质是**召回结果的正排补元**：先通过 `GCMS/IFCS` 把候选 `rid/nid` 对应的 `MicroVideoInfo / NewsInfo` 拉出来，再挂到候选对象上，供后续的过滤、排序、特征构造使用。
- 在 `feeda-mv-grc` 中，这条链路已经比较完整，核心入口集中在 `FillMeta / FillMetaPipeline`，并进一步被 `ContentFeature`、`RecommendFeature`、各类过滤与排序逻辑消费，说明**适配成熟度高、迁移收益明确**。
- 在 `feeda-mv-grg` 中，本次扫描到的是与补元、多样性、策略相关的周边代码，但**未直接命中 `gcms_component` / `ifcs_sdk` 的核心调用链**，因此更像是“可迁移、但尚未落地”的状态。若业务需要文档级元信息参与序列生成或多样性控制，迁移潜力中等偏高；若当前流程不依赖正排字段，则收益有限。

## 2. 代码库详情

### feeda-mv-grc

- 这是本次分析的**主落地点**，GCMS/IFCS 正排链路已经接入。
- 已确认的关键文件包括：
  - `conf/common_component/gcms_common_pb_plugin.conf`
    - 明确写有“`mv-grc` 已接入 `ifcs` 正排缓存服务”，属于配置级证据。
  - `src/plugin/gcms_component.cpp`
    - 初始化 `feed::ifcs::IfcsSdk<BaseDocInfo>`，并通过 `query_common()` / `query_news()` 发起正排查询。
  - `src/plugin/ifcs_component.cpp`
    - 把 IFCS 返回的 PB 解析成 `MicroVideoInfo` / `NewsInfo`，包含 `smfw` 合并逻辑。
  - `src/processor/fill_meta.cpp`
    - 召回结果进入后补元，并把 `MicroVideoInfo` 挂到 `RidTmpInfo`。
  - `src/processor/video_launch/fill_meta_pipeline.cpp`
    - pipeline 版本的补元主链路，查询后将 `_video_info` 和 `gcms_data` 写回候选。
  - `src/processor/doc_feature_with_cache_yitu.cpp`
    - 将正排内容转成 `ContentFeature / RecommendFeature`，是“正排 → 模型特征”的典型消费点。
  - `src/data/rid_tmp_info.h`
    - 候选对象承载正排字段：`gcms_data / const_gcms_data / _video_info / _video_info_copy`。
- 现有使用特点：
  - 正排不是散点式调用，而是**集中在 FillMeta 阶段**；
  - 下游过滤、排序、响应阶段会反复读取 `rid_info->gcms_data`；
  - 已存在对 `UA / scene / flow_loc` 的查询分流和降级逻辑，说明链路考虑了场景差异。
- 结论：
  - `feeda-mv-grc` 已具备较完整的正排能力，是**优先保留并优化**的对象；
  - 如果后续要扩展字段或调整查询策略，优先改这里，而不是在下游零散补逻辑。

### feeda-mv-grg

- 本次扫描到的相关文件主要是“流程/策略周边”，例如：
  - `process/news_fill_meta_pipeline.cpp`
  - `operator/diversity/mount_priority_soft_rule.cpp`
  - `operator/diversity/diversity_rule_rollback.cpp`
  - `strategy/diversity/diversity_util.h`
  - `operator/diversity/last_scene_hard_rule.cpp`
- 但在本次给出的摘要里，**没有看到 `GcmsComponent`、`ifcs_sdk`、`MicroVideoInfo` 这类核心正排调用的直接命中证据**。
- 这意味着：
  - `feeda-mv-grg` 可能已经有补元/策略链路，但**未显式接入 GCMS/IFCS 正排**；
  - 如果要引入该能力，`process/news_fill_meta_pipeline.cpp` 这类入口会是最自然的落点；
  - 多样性规则文件 `operator/diversity/*` 更适合作为“消费正排字段”的位置，而不是首个接入点。
- 代码规模侧面信息：
  - `std::vector` / `std::string` / `std::unordered_map` 使用量都很大，说明该库已有较成熟的业务对象流转与策略计算代码；
  - 但这并不直接等价于正排能力已存在，更像是**适合承接正排字段的复杂业务框架**。
- 结论：
  - `feeda-mv-grg` 对 GCMS/IFCS 的适配属于**待评估迁移型**；
  - 若其业务场景确实需要“召回后补元、基于内容质量/类目/时长做规则过滤”，则引入收益可观；
  - 若只是序列生成且不依赖文档级元信息，迁移收益会明显下降。

## 3. 💡 适用性评估与建议

- **建议 1：在 `feeda-mv-grc/src/processor/video_launch/fill_meta_pipeline.cpp` 保持正排查询集中化，并抽公共函数降低重复代码**
  - 该文件已经是补元主链路，建议把 `GcmsComponent::query_common()` 之后的“查表、挂载、复制、重包装”抽成公共 helper。
  - 适用场景：
    - 召回结果批量补元；
    - 合集/短剧等需要二次改写 `MicroVideoInfo` 的场景。
  - 价值：
    - 降低 `FillMetaPipelineFunction` 内部复杂度；
    - 便于统一处理 cache miss、字段缺失、实验开关。

- **建议 2：在 `feeda-mv-grc/src/processor/fill_meta.cpp` 和 `src/processor/video_launch/fill_meta_pipeline.cpp` 之间统一正排写回语义**
  - 现在看起来存在“普通路径”和“pipeline 路径”两套补元入口。
  - 建议统一 `RidTmpInfo` 中 `_video_info / gcms_data` 的写入规范，避免不同路径下字段状态不一致。
  - 适用场景：
    - `response.cpp`、`merge_recall.cpp`、`ctr_rank.cpp` 等下游多处依赖 `gcms_data != nullptr` 的逻辑。
  - 价值：
    - 降低下游空指针与字段不一致风险；
    - 便于后续做特征缓存和命中率统计。

- **建议 3：在 `feeda-mv-grc/src/processor/doc_feature_with_cache_yitu.cpp` 增加“字段缺失兜底”和“延迟构造”优化**
  - 该文件是典型的“正排 → 特征”消费点。
  - 建议：
    - 仅在 `_video_info != nullptr` 时构造 `ContentFeature`，当前方向是对的；
    - 对 `manual tags / 类目 / 时长 / 垂类` 等高频字段做局部缓存，减少重复读取；
    - 对 `public_time / duration / video_type` 等缺失字段做默认值兜底。
  - 价值：
    - 降低特征构造失败率；
    - 减少无效对象创建开销。

- **建议 4：如果 `feeda-mv-grg` 计划接入 GCMS/IFCS，优先从 `process/news_fill_meta_pipeline.cpp` 切入**
  - 这类文件最适合承接“召回后补元”逻辑。
  - 推荐迁移方式：
    - 先接入只读正排查询；
    - 再把必要字段挂到候选对象；
    - 最后让 `operator/diversity/*` 和 `strategy/diversity/diversity_util.h` 消费这些字段做规则控制。
  - 可直接参考 `feeda-mv-grc/src/plugin/gcms_component.cpp` 的查询封装方式。
  - 价值：
    - 先形成最小闭环，再逐步扩展到排序/多样性。

- **建议 5：在 `feeda-mv-grc/src/plugin/ifcs_component.cpp` 中补齐解析校验与日志维度，便于后续迁移复用**
  - 当前 parser 已承担 `IfcsItem → MicroVideoInfo/NewsInfo` 的转换。
  - 建议把：
    - `smfw` 合并结果；
    - 解析失败原因；
    - 关键字段缺失统计；
    - 场景切换信息
    统一埋点。
  - 价值：
    - 对 `feeda-mv-grg` 后续迁移时，可直接复用成熟解析逻辑；
    - 有助于比对不同场景下的正排命中率。

## 4. ⚠️ 引入风险与限制

- **风险 1：查询延迟与稳定性风险**
  - 正排引入后，召回链路会额外依赖 `IFCS` 缓存服务。
  - 一旦发生超时、降级或缓存击穿，会直接影响补元完整性，并传导到后续过滤/排序。

- **风险 2：字段一致性与版本兼容风险**
  - `MicroVideoInfo / NewsInfo` 的字段很多，且部分字段会被下游硬依赖。
  - 若 `parser`、PB 定义或字段语义变化，容易导致 `ContentFeature` 或策略规则失真。

- **风险 3：对象拷贝开销与内存压力**
  - 从代码看，存在 `queue_iter->_video_info = ...` 以及 `boost::make_shared<const GcmsData>(...)` 这类包装动作。
  - 若在高 QPS 场景下频繁复制正排对象，可能带来额外内存与 CPU 开销。

- **风险 4：场景切换逻辑复杂**
  - `query_common()` 中已有按 `UA / flow_loc / scene` 切换查询模式的逻辑。
  - 迁移到新业务库时，如果没有明确场景边界，容易出现“查得太多”或“查得不够”的问题。

---

如果你愿意，我可以继续把这份内容整理成**更像技术学习笔记正文**的版本，或者进一步补成一份**“适配建议清单 + 迁移优先级表”**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
