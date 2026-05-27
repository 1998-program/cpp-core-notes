---
title: 正排（GCMS/IFCS）在 feeda-mv-grc 中的作用
生成时间: 2026-05-27 20:00:44 CST
代码库路径: /home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc
内网文档:
  - 标题: 正排字段添加指南-IFCS版
    URL: https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/SCIE54A-T7/vwbx5ZVfxu/rzlycnK9rMzhXQ
    说明: 由 conf/common_component/gcms_common_pb_plugin.conf 引用，ku query-content 成功读取。
检索关键词:
  - GCMS, gcms, IFCS, ifcs, 正排, ContentFeature, doc_feature, feature cache
  - FillMetaPipelineFunction, FillMetaBaseProcessor, GcmsComponent, query_common
  - MicroVideoInfo, MvRecallDocParser, MvNewsDocParser, RidTmpInfo, gcms_data
置信度: 高；GCMS/IFCS 业务语义来自内网文档，feeda-mv-grc 链路来自本地代码与配置。部分 Parser 实现位于外部 ifcs/common-component 依赖，当前仓库未直接命中源码。
---

# 正排（GCMS/IFCS）在 feeda-mv-grc 中的作用

## 0. 本次脑暴与检索计划

围绕“正排（GCMS）在 feeda-mv-grc 的作用”，本次检索分两段：

1. **内网知识检索**：先从代码配置里找 ku 文档引用，再用 `ku query-content` 读取。尝试过 `ku search`，当前 CLI 无 `search` 子命令，改为利用 `conf/common_component/gcms_common_pb_plugin.conf` 中的显式文档 URL。
2. **代码端到端 trace**：入口点 `README.md` / `src/main.cpp` / `conf/plugins/graph/global.conf`；再 trace `queue_vertex.conf` 与 `recall_fill_filter.conf`；最后跟进 `FillMetaPipelineFunction` / `FillMetaBaseProcessor` / `GcmsComponent` / 下游 Filter、Rank、DocFeature 消费。

## 1. 内网知识检索阶段

### 1.1 检索方式与结果

执行前已设置：`BAIDU_CC_USERNAME=hujiyang`。

- `ku query-user-info --username hujiyang` 成功，证明知识库认证可用。
- 尝试 `ku search --keyword ...` 失败：当前 `ku` CLI 输出 `unknown subcommand: search`，支持的命令只有 `query-content/query-repo/create-doc/...` 等。因此本次无法做全站关键词搜索。
- 在代码配置中命中文档引用：`conf/common_component/gcms_common_pb_plugin.conf:1` 写明：`mv-grc已接入ifcs正排缓存服务，正排字段开发详见 https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/pmquNWcpSA/_zXrQ8a8f1/rzlycnK9rMzhXQ`。
- 用该 URL 执行 `ku query-content --protocol 2` 成功，返回文档标题 **《正排字段添加指南-IFCS版》**，实际 canonical URL 为 `https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/SCIE54A-T7/vwbx5ZVfxu/rzlycnK9rMzhXQ`。

### 1.2 内网文档关键结论

文档《正排字段添加指南-IFCS版》给出的核心事实：

1. **IFCS 是业务模块与正排服务之间新增的一层缓存服务**：背景是业务模块实例数多，本地缓存成本和 miss 穿透流量会被放大；IFCS 缓存近乎全量数据，业务模块只缓存热点数据。
2. **当前 feeda-mv-grc 已接入 IFCS**：文档明确列出 `feeda-mv-grc、feeda-mv-pcs、parameter-calculation-service` 三个模块接入 IFCS。
3. **数据流转链路**：`ContentFeature -> IFCS 解析为 MicroVideoItemGcmsInfo/MvPcsItemGcmsInfo -> 序列化 string 缓存/传输 -> 业务模块解析为业务正排结构体`。
4. **feeda-mv-grc 的业务正排结构体**：视频是 `src/data/video_info.h` 中的 `MicroVideoInfo`，图文是 `src/data/news_info.h` 中的 `NewsInfo`。
5. **场景与 parser**：feeda-mv-grc 视频/召回使用 `gcms_recall_pb_plugin.conf.template`，图文/历史使用 `gcms_history_pb_plugin.conf.template`；业务侧 ifcs 配置里可见 parser 名称，如召回 `MvRecallDocParser`、图文 `MvNewsDocParser`。

## 2. 代码入口识别：feeda-mv-grc 的服务角色

### 2.1 README

`README.md:1` 只有一句：“小视频业务grc层模块”。`README.md:3-4` 给出图引擎静态/动态图可视化 URL，说明这是图引擎驱动的 GRC 服务。

### 2.2 main.cpp

关键启动链路：

| 文件 | 行号 | 事实 |
|---|---:|---|
| `src/main.cpp` | 37-45 | gflags 定义端口、idle timeout、dapper、worker 线程开关 |
| `src/main.cpp` | 84-90 | 注册 `ReusableRPCProtocol`，使用 `./conf/gflags.conf` |
| `src/main.cpp` | 93 | `GlobalInitializer::instance().init()` 初始化全局组件 |
| `src/main.cpp` | 95-99 | 初始化实验参数 `ExpManager` |
| `src/main.cpp` | 104-113 | 创建 `baidu::rpc::Server`，设置 `baidu_std_reuse` 协议 |
| `src/main.cpp` | 106-114 | 注册 `GenericGRCService` |
| `src/main.cpp` | 115-121 | 注册 HTTP 图查看/配置服务 `GrcHttpServiceImpl` |
| `src/main.cpp` | 126 | 启动 dapper collector worker |
| `src/main.cpp` | 128-130 | `server.Start(FLAGS_port, &options)` 并 `RunUntilAskedToQuit()` |

服务形态：**brpc + baidu_std_reuse + graph engine DAG + dapper 观测**。

### 2.3 graph 配置根

`conf/plugins/graph/global.conf:1-3` 定义图池；`global.conf:6-39` 声明全局依赖，如 `log_id/uid/cuid/ua/ReqInfo/ExpInfo`；`global.conf:41-83` include `graph.conf/request_handle.conf/recall_graph.conf/queue_vertex.conf/response.conf/...`。

这符合 Feed GR/GRC 图引擎模式：请求进入 service 后注入 global depend，再由 graph config 中的 vertex/function 串起召回、正排、过滤、排序、响应。

## 3. GCMS/IFCS 在 feeda-mv-grc 中的定位

一句话：**GCMS/IFCS 是 feeda-mv-grc 将召回结果的 nid/rid 转成可过滤、可排序、可组装特征的内容元数据层**。

feeda-mv-grc 的召回服务或队列节点先产出 `RidTmpInfo` / `RecallResult`，其中有 rid/type/fork 等召回侧信息；随后 FillMeta 节点批量查询 IFCS/GCMS，拿到 `MicroVideoInfo`，挂到 `RidTmpInfo::_video_info` 与 `RidTmpInfo::gcms_data`。后续 Filter、Rank、Feature、Diversity、Response 都围绕这些正排字段做判断。

## 4. 代码链路图

### 4.1 queue pipeline 路径

```text
Request -> GenericGRCService -> GraphPool/Graph.run
  -> DsToRidInfoPipelineVertex
       emit QueueRecallResult
  -> FillMetaPipelineFunction
       depend QueueRecallResult/SidInfo/Follow sets
       merge_rids = rid_vec 去重
       GcmsComponent::query_common(merge_rids, meta_map, gcms_context, log_id, sid, context)
       -> IFCS SDK get(scene=recall/search, parser=MvRecallDocParser)
       -> DocInfoMap<MicroVideoInfo>
       -> RidTmpInfo._video_info / RidTmpInfo.gcms_data / const_gcms_data
       emit FillMetaPipelineResult / CardFillMetaResult
  -> GetVidClkPipelineFunction
  -> FilterPipelineFunction
  -> Rank/Adjust/Response
```

配置证据：`conf/plugins/graph/queue_vertex.conf:24-57` 声明 `FillMetaPipelineFunction` 消费 `QueueRecallResult` 并产出 `FillMetaPipelineResult/CardFillMetaResult/UpToFillMetaCost`；`queue_vertex.conf:93-160` 声明后续 `FilterPipelineFunction` 依赖 `VidClkPipelineResult` 以及多种用户/历史/内容向量数据。

### 4.2 多召回模板路径

```text
dynamic_recall -> DsRidInfoToRidInfoVecProcessor
  emit <prefix>RecallResult / <prefix>NewsRecallResult
-> FillMetaBaseProcessor
  anonymous depend <prefix>RecallResult
  query_common(..., GCMS_SECNE_RECALL)
  fill MultiQueueResult with gcms_data
-> VidClkRpcProcessor
-> FilterProcessor
```

配置证据：`conf/plugins/graph/conf_template/recall_fill_filter.conf:1-27` 定义 `DsRidInfoToRidInfoVecProcessor`；同文件 `:28-45` 定义 `FillMetaBaseProcessor`；`:61-176` 定义 `FilterProcessor` 对 `RecallResultWithMetaVidClk` 做过滤。`conf/plugins/graph/conf_value/recall_fill_filter.conf:1-220` 将该模板实例化到几十个召回服务/fork，如 `video_micro_rec_new`、`grc_haokan_session`、`grc_haokan_content`、`shoubai_live_video` 等。

## 5. Producer -> Transform -> Consumer 端到端 Trace

### 5.1 Producer：召回结果转换为 RidTmpInfo

`src/processor/ds_ridinfo_to_ridinfo_vec.cpp`：

| 行号 | 事实 |
|---:|---|
| 16-44 | `setup()` 读取 `handle_name/fork_type`，声明依赖 `recall_ds_vec/recall_ds_status/SidInfo/MarkSid/recall_conf`，emit `TransformedRecallResult/news_recall_result` |
| 69-76 | 运行期取 mutable `RidInfo`，emit `RecallResult` / `NewsRecallResult` 并 clear |
| 92-106 | 从 `recall_ds_vec_p->nids/result` 取召回结果，`reinterpret_cast` 为 `std::vector<RidTmpInfo*>` |
| 114-124 | 对每个 `RidTmpInfo` 设置默认值，根据 `is_news_resource` 分流到 news 或视频 result |

这一步只完成召回数据结构对齐，还没有填正排。

### 5.2 Transform：FillMeta 查询 IFCS/GCMS 并挂载 MicroVideoInfo

#### 5.2.1 FillMetaPipelineFunction

`src/processor/video_launch/fill_meta_pipeline.cpp`：

| 行号 | 事实 |
|---:|---|
| 19-24 | 定义 `FillMetaPipelineFunction`，初始化 output channel |
| 43-47 | 将 `_queue_recall_result` 作为 mutable input，设置 batch 后 `parallel_consume` |
| 54-56 | 从 `queue_context` 取 `rid_vec`，用 `queue_context.rids` 构造去重 `merge_rids` |
| 63-72 | 缓存多个 abtest 命中结果，避免循环重复查询 |
| 74-109 | 构造“正排小流量” sid 列表，填入 `gcms_context.gcms_smfw_columns_str_vec` 和 `sids_str_vec` |
| 118-124 | `GcmsComponent::get_instance().query_common(merge_rids, meta_map, gcms_context, log_id, _sid_info, &context)` 请求 pb 正排 |
| 155-166 | 遍历每个 rid，在 `meta_map` 命中时把 `MicroVideoInfo` 指针挂到 `queue_iter->_video_info`，并构造 `queue_iter->gcms_data` |
| 176-186 | `collection_ai_zpsmfw` 小流量下复制并改写合集字段，再重建 `gcms_data` |
| 314-449 | 用 `fc_tag/del_tag` 等正排字段做过滤/豁免，不符合条件的资源直接 `continue` |
| 477-479 | 通过 `fork_type_recall_num` 统计正排后各 fork 数量，并设置 `queue_iter->const_gcms_data = queue_iter->gcms_data.get()` |
| 486-491 | 未命中正排：卡片类型单独保留到 card；普通资源写 `GCMS not found, deleted` 日志并丢弃 |
| 513-521 | card 与普通资源分别发布到不同 output channel |
| 643 | `REGISTER_GRC_FUNCTION(FillMetaPipelineFunction)` 注册图函数 |

#### 5.2.2 FillMetaBaseProcessor

`src/processor/fill_meta.cpp`：

| 行号 | 事实 |
|---:|---|
| 126-155 | `setup()` 读取 `handle_name`，声明 anonymous dependency 为 mutable `RecallResult`，绑定 `SidInfo/RecallResultWithMeta/FollowedAuthorSet` 等 |
| 191-200 | 收集所有 anonymous recall result 的 rids 到 `merge_rids` |
| 203-245 | 构造 `gcms_context`，带上 ua/flow_loc/small_flow，然后 `query_common(..., GCMS_SECNE_RECALL)` |
| 253-265 | `open_gcms_statistics` 下打印总 nid 数和正排命中数/未命中 nid |
| 285-317 | 遍历每个召回 item，命中 `gcms_common_pb_meta_map` 后挂载 `_video_info` 与 `gcms_data` |
| 324-333 | 合集字段小流量修正并重建 `gcms_data` |
| 342-462 | IP 名称小流量修正，读取 `vertical_type/content_type/newhot/ip_strategy/search_movie_ip_name` 等正排字段并重建 `gcms_data` |
| 467-478 | `fc_tag/fc_tag_source/fc_tag_reason` 与实验开关控制过滤豁免 |
| 496-500 | 用 `mthid/siteAccountId` 与关注集合判断 `is_follow_author` |
| 705 | `BABYLON_REGISTER_COMPONENT_WITH_TYPE_NAME(FillMetaBaseProcessor, GraphProcessor, FillMetaBaseProcessor)` 注册 processor |

### 5.3 IFCS/GCMS 访问组件

`src/plugin/gcms_component.h` 与 `.cpp` 是业务侧访问入口：

| 文件 | 行号 | 事实 |
|---|---:|---|
| `src/plugin/gcms_component.h` | 19-24 | 定义场景枚举：RECALL/NEWS/HISTORY/SEARCH |
| 同上 | 30-37 | `query_common` 输入 nid set，输出 `DocInfoMap<MicroVideoInfo>` |
| 同上 | 54 | 成员 `_ifcs_sdk` 是 `feed::ifcs::IfcsSdk<BaseDocInfo>*` |
| `src/plugin/gcms_component.cpp` | 5 | `enable_ifcs` gflag |
| 同上 | 18-30 | `init()` 创建 `IfcsSdk<BaseDocInfo>` 并加载 `ifcs_sdk.conf` |
| 同上 | 40-47 | `query_common` 方法签名 |
| 同上 | 48-59 | 根据 ua/flow_loc/searchc 实验将 scene 切到 SEARCH，并关闭 `server_cache_only` |
| 同上 | 62-70 | 构造 `feed::ifcs::Context`，传入 log_id、scene、sids、server_cache_only |
| 同上 | 72-78 | `_ifcs_sdk->get(ifcs_ctx, nids, result)`，失败打印 `call_ifcs_recall fail` |
| 同上 | 80-101 | `query_news` 走 `GCMS_SECNE_NEWS`，输出 `DocInfoMap<NewsInfo>` |

### 5.4 IFCS 配置与 parser

`conf/ifcs_sdk.conf`：

| 行号 | 事实 |
|---:|---|
| 1 | `module_name: ifcs_client-mv_grc` |
| 6-10 | recall accessor：`scene: 0`，`parser: MvRecallDocParser` |
| 11-41 | recall 场景配置 10 个 query/hot_meta shard |
| 42-55 | recall sid_conf、freq_update_queue、hot_cache，`max_cache_size: 600000` |
| 57-61 | search accessor：`scene: 3`，同样使用 `MvRecallDocParser` |
| 99-102 | search hot_cache `max_cache_size: 60000` |
| 108-112 | news accessor：`scene: 1`，`parser: MvNewsDocParser` |
| 150-153 | news hot_cache `max_cache_size: 600000` |

未在当前仓库直接命中 `MvRecallDocParser` / `MvNewsDocParser` 的实现代码；结合内网文档，它们属于 IFCS SDK/外部依赖侧 parser，用于把 IFCS 返回的序列化 pb 转为 `MicroVideoInfo` / `NewsInfo`。

### 5.5 Consumer：下游如何使用正排

#### Filter

`src/processor/filter.cpp`：

| 行号 | 事实 |
|---:|---|
| 13-28 | `FilterProcessor::setup()` 绑定 `RecallResultWithMeta`，并声明 mutable |
| 75-82 | `process()` 取 `fill_meta_result`，emit filtered result |
| 136-152 | 遍历正排后的 item，访问 `item_ptr->gcms_data->_video_info.get_new_cate_smfw()` 等字段并转成 `DynamicStruct` 交给 filter engine |
| 162-170 | 小列表强制串行，否则执行 `engine->run(item_list)` 或分 effect/non-effect run |
| 266 | 注册 `FilterProcessor` |

Filter 的关键是：它依赖 FillMeta 后的 `gcms_data`，将 `RidTmpInfoPtr` 包装为动态结构体，filter engine 内的规则可直接读取正排字段。

#### CtrRank / Rank 特征

`src/processor/ctr_rank.cpp`：

| 行号 | 事实 |
|---:|---|
| 42-68 | `CtrRankProcessor::setup()` 绑定 `CtrInput/CommonInfo/SidInfo/...`，emit `CtrRankResult/CtrRidVec/MultiObjectInfoNew` 等 |
| 97-104 | 运行期取 `vfs_result` 并初始化多个输出 |
| 123-180 | 遍历每个 item，初始化 rank 分数字段；后续文件中大量逻辑使用 `gcms_data->_video_info` 构造特征/路由 |
| 2216 | 注册 `CtrRankProcessor` |

由于 `ctr_rank.cpp` 很长，本次只确认入口和它位于正排之后消费 item 的事实。后续如要写“精排如何消费正排字段”，建议另开一篇窄主题。

#### Doc Feature 构造

`src/processor/doc_feature_with_cache_yitu.cpp`：

| 行号 | 事实 |
|---:|---|
| 43-50 | 遍历 `RidTmpInfoPtr` 构造 `SampleContext` 的 `ContentFeature` 与 `RecommendFeature` |
| 60-64 | 如果 `rid_info->_video_info != nullptr`，调用 `contruct_video_feature` |
| 77-90 | 把 `manual_tags/uploader/new_cate_v2/new_sub_cate_v2/mthid/video_duration/is_microvideo/vertical_type` 写入 mlarch `ContentFeature` |
| 96-99 | 若 `rid_info->gcms_data` 为空，打印 warning 并返回 |
| 111-129 | 从 `rid_info->gcms_data->_video_info.get_sc_duanju()` 抽取短剧结构标签写入 `ContentFeature` |

这说明正排不仅用于过滤，也会反向组装成模型特征或样本特征。

## 6. 数据结构与字段

### 6.1 MicroVideoInfo

`src/data/video_info.h:680` 定义 `struct MicroVideoInfo : public licache::AbstractData, public BaseDocInfo`。关键字段以 `DYNAMIC_MEMBER` 声明：

| 行号 | 字段/类别 | 作用 |
|---:|---|---|
| 689-693 | `rid/del_tag/fc_tag/fc_tag_source/fc_tag_reason` | 内容 ID 与审核/删除标签，FillMeta 中直接用于过滤 |
| 698-700 | `video_duration/siteAccountId/tb_fid` | 时长、作者、贴吧 fid，用于关注/排序/策略 |
| 730-734 | `contentcf_vec/new_cate_smfw/new_sub_cate_smfw/new_cate_v2/new_sub_cate_v2` | 内容向量与类目字段 |
| 741-744 | `is_microvideo/video_type/vertical_type/yingshi_leixing` | 资源类型与垂类 |
| 763-766 | `mthid/video_collections_strategy/target_location/public_time` | 作者/合集/地域/发布时间 |
| 776-800 | `title/.../content_type` | 标题、质量、内容类型 |
| 844-845 | `search_movie_ip_name/vote_strategy_ip` | IP 名称相关字段，FillMeta 小流量逻辑读取 |
| 909-913 | `article_collections_info/yingshi_ip/source_type` | 合集/IP/来源类型 |
| 931-935 | `is_ip/is_hot_ip/sc_ip/sc_newhot_info/sc_explore` | 结构化 IP、新热、探索信息 |
| 957-958 | `video_collections_ai/llm_quality_disu` | AI 合集与 LLM 质量字段 |
| 1001-1005 | `yingshi_ip_cluster_id/yingshi_ip_score_smfw/content_collect_score` | IP 聚类与小流量分数字段 |

### 6.2 RidTmpInfo 上的挂载点

虽然本次未完整展开 `RidTmpInfo` 定义，但从 FillMeta 代码可见其关键挂载点：

- `rid`：查询 IFCS/GCMS 的 key；
- `type`：召回 mark/type，用于 card/no card、过滤日志；
- `_video_info`：指向 IFCS 返回的 `MicroVideoInfo`；
- `_video_info_copy`：小流量修正时复制一份可改写结构；
- `gcms_data`：包装后的正排数据，供下游统一读取；
- `const_gcms_data`：在正排成功后设置，供下游只读访问。

证据：`fill_meta_pipeline.cpp:160-166`、`:176-186`、`:477-479`；`fill_meta.cpp:306-317`、`:324-333`。

## 7. 配置/实验开关/降级逻辑

### 7.1 IFCS 与缓存配置

- `conf/ifcs_sdk.conf:48-55`：recall hot cache 最大 600000；ring cache 关闭。
- `conf/ifcs_sdk.conf:95-106`：search hot cache 最大 60000；ring cache 关闭。
- `conf/ifcs_sdk.conf:146-154`：news hot cache 最大 600000。
- `src/plugin/gcms_component.cpp:48-59`：默认 `server_cache_only=true`，但 search 场景会设为 false 并切换 scene。

### 7.2 小流量字段/实验

FillMeta 中存在多类实验控制：

| 实验/开关 | 证据 | 作用 |
|---|---|---|
| `collection_ai_zpsmfw` | `fill_meta_pipeline.cpp:64`、`:176-186`；`fill_meta.cpp:324-333` | 正排小流量下用 `video_collections_ai` 补 `article_collections_info` |
| `sc_ipname_smfw` | `fill_meta_pipeline.cpp:65`、`:194-310`；`fill_meta.cpp:342-462` | 正排 IP 名称小流量修正 |
| `stage_exp3/4/5` | `fill_meta_pipeline.cpp:69-72`、`:451-475` | 对动态垂类资源按实验抽样过滤 |
| `dsvideo_skip_review_exp` | `fill_meta_pipeline.cpp:317-319`；`fill_meta.cpp:467-471` | fc_tag 过滤豁免相关 |
| `ertiao_fc_tag_prescription_exempt` | `fill_meta_pipeline.cpp:320-324`；`fill_meta.cpp:472-476` | 二跳处方药推广 fc_tag 豁免 |
| `open_gcms_statistics` | `fill_meta_pipeline.cpp:125-139`；`fill_meta.cpp:253-265` | 打印正排命中/未命中统计 |

### 7.3 降级与失败行为

1. `GcmsComponent::query_common` 失败时返回 -1 并打印 `call_ifcs_recall fail`（`src/plugin/gcms_component.cpp:72-78`）。
2. `FillMetaPipelineFunction` 中如果 query 失败，记录 warning 后返回 `ERR_OK`，避免整个图失败（`fill_meta_pipeline.cpp:140-143`）。这意味着正排失败可能表现为后续结果为空/少，而不是 RPC 失败。
3. 单个 nid 未命中正排：普通资源写 `GCMS not found, deleted` 并丢弃（`fill_meta_pipeline.cpp:486-491`）；`FillMetaBaseProcessor` 中也会跳过未命中项（`fill_meta.cpp:306-317` 周边）。
4. 卡片类型有特殊保留逻辑：`fill_meta_pipeline.cpp:486-489` 将 card type 放入 `card_rid_vec`。

## 8. 风险与排查方法

### 8.1 风险

1. **新增字段链路长**：ContentFeature、IFCS proto、IFCS parser、业务 `MicroVideoInfo`、业务 parser/config 都可能漏一处。内网文档明确要求 feeda-mv-grc 新增正排字段需同时开发 IFCS 与业务模块。
2. **正排未命中会直接删资源**：FillMeta 中“not found, deleted”意味着 IFCS/cache/字段配置问题可能转化为召回结果大量减少。
3. **小流量字段合并可能覆盖基线语义**：`collection_ai_zpsmfw`、`sc_ipname_smfw` 都会复制并改写 `MicroVideoInfo` 后重建 `gcms_data`。
4. **search 场景 server_cache_only=false**：`gcms_component.cpp:53-59` 对若干 ua/flow_loc 切 search scene 并关闭 server cache only，可能带来穿透流量或延迟差异。
5. **下游依赖广**：Filter、Rank、DocFeature、Diversity、Response 大量读取 `gcms_data->_video_info`，字段异常影响面大。

### 8.2 排查 checklist

```bash
# 1. 入口和图配置
rg "FillMetaPipelineFunction|FillMetaBaseProcessor|RecallResultWithMeta|FillMetaPipelineResult" conf/plugins/graph src/processor

# 2. IFCS/GCMS 配置
rg "ifcs|MvRecallDocParser|MvNewsDocParser|hot_cache|server_cache_only" conf src/plugin

# 3. 正排字段使用
rg "gcms_data->_video_info|get_fc_tag|get_del_tag|get_vertical_type|get_new_cate" src/processor src/operator src/strategy

# 4. 未命中/失败日志
rg "call_ifcs_recall fail|gcms request failed|not found, deleted|open_gcms_statistics" src conf
```

运行时重点看：

- `fill_meta_pipeline_rpc` / `fill_meta_gcms_pb_rpc` SIA 耗时和错误码；
- `open_gcms_statistics` 打印的 total nid size vs after gcms rpc nid size；
- `GCMS` 日志中的 `not found, deleted`、`fc tag marked, skip`、`del tag marked, skip`；
- IFCS SDK query/hot_meta shard 的错误率与 cache hit。

## 9. 未确认问题与下一步检索计划

1. **未在当前仓库直接命中 `MvRecallDocParser` / `MvNewsDocParser` 实现**：搜索关键词 `MvRecallDocParser`、`MvNewsDocParser`、`IfcsItem`、`ParseFromString` 未在 `feeda-mv-grc/src` 命中。下一步应检索 IFCS SDK 或 `baidu/feed-gr/ifcs`、`ifcs-proto`、`common-processor` 依赖仓库。
2. **`GcmsData` 定义未在本次报告中展开**：当前代码通过 `boost::make_shared<const microvideo::grc::GcmsData>(...)` 构造，定义可能来自 `src/data/gcms.h` 或公共依赖，后续可单独分析其 wrapper 语义。
3. **GenericGRCService 请求注入 graph 的细节未展开**：本次入口确认到 `src/main.cpp` 注册服务与 graph 配置，后续如写“请求如何进入 graph”，应继续读 `src/service/grc_service.*`。
4. **ku 全站搜索不可用**：当前 `ku` CLI 无 `search` 子命令，本次只读取了代码配置引用文档。若需要更多业务背景，可通过已知 repo/目录 `query-repo` 或其它内网搜索工具补充 `GCMS/正排/ContentFeature/doc feature` 文档。
