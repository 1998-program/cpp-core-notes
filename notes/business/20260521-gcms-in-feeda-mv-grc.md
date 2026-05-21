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
