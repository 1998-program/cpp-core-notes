---
title: "Dup Service（去重服务）在 feeda-mv-grc 中的作用：VFS 过滤链路与多策略去重"
generated_at: 2026-05-23T20:00:00+08:00
代码库路径: /home1/code_read/code-read-mv-grc/baidu/feed-gr/feeda-mv-grc
对照代码库: /home1/code_read/code-read-mv-grg/baidu/feed-gr/feeda-mv-grg（仅作背景，不把结论直接套用）
内网文档检索关键词:
  - Dup Service
  - VFS 去重
  - 视频去重策略
  - DupClient
  - feeda-mv-grc
代码检索关键词:
  - DupServicePlugin
  - VfsRpcFunction
  - VfsFilterFunction
  - DupClient
  - REQTYPE_GET_DUP
  - is_replicated_with_history
  - show_map
  - dup_strategy
  - DUPTYPE_HISTORY_GROUP
置信度: 中高；核心链路由本地代码直接命中。babylon::ObjectPool 连接池管理与 Dup Service 通信协议细节依赖外部依赖库源码（baidu/feed/dup_client）。
---

# Dup Service（去重服务）在 feeda-mv-grc 中的作用：VFS 过滤链路与多策略去重

## 0. 运行日志摘要

本次业务主题：追踪 Dup Service（去重服务）在 `feeda-mv-grc` 中的完整链路。

**脑暴后收敛的窄 scope**：
- `feeda-mv-grc` 的去重不是独立的"服务"，而是通过 `DupClient` 调用独立的 Dup Service（`10.201.66.33:8863`），由 `VfsProcessor` / `VfsRpcFunction` / `VfsFilterFunction` 三个节点完成去重链路；
- Dup Service 支持多种去重策略：NID 精确匹配、VFS（Video Fingerprint Similarity）、Bloom filter、标题去重等；
- 去重输入包括用户的**下发历史（show_map）**和**当前候选队列（rank_vec）**。

## 1. 内网知识检索阶段

### 1.1 检索动作与结果

可用工具：`ku-doc-manage` CLI，路径 `/root/.hermes/skills/ku-doc-manage/bin/ku`。

**动作**：设置环境变量后执行 `ku query-user-info`（验证认证）和 `ku query-content`（按文档 ID 查询）。

**结果**：认证失败（returnCode 60103，`开放应用不存在，检查下ak/sk或x-ac-Authorization值是否准确`），无法读取 KU 正文。替代方案：基于本地代码中的注释引用（`src/processor/vfs.cpp:382`）和 `conf/common_component` 配置确认业务语义。

**代码中的 KU 引用**：

- `src/processor/vfs.cpp:382-383`：
  ```cpp
  // 参考https://ku.baidu-int.com/knowledge/HFVrC7hq1Q/tmhTYDa4JS/eB9plaUbRg/0e20D3dAHWIVlW
  // 新增动态、图文的去重策略，在此处会过滤重复动态，不会影响视频
  ```
  链接指向的标题/摘要（本地配置中可见）：涉及图文/动态去重的扩展策略。

### 1.2 基于代码可确认的业务背景

Dup Service 是公司级去重基础设施，服务于所有 Feed 类业务（小视频、图文、直播等）。`feeda-mv-grc` 通过 `DupClient` 调用该服务，传入：
- **用户下发历史**（show_map）：用户历史上已下发的视频 nid 及状态；
- **当前候选队列**（rank_vec）：当前请求待下发的视频 nid。

返回：**哪些候选视频已在历史中出现**，供 GRC 做过滤。

## 2. 代码入口识别：feeda-mv-grc 的服务角色

`README.md:1-4` 写明这是"小视频业务 grc 层模块"，并给出图引擎可视化入口。

服务入口 `src/main.cpp:73-130`：
- `src/main.cpp:93`：`GlobalInitializer::instance().init()` 加载 babylon 组件，包括 `DupServicePlugin`（通过 `BABYLON_REGISTER_COMPONENT` 注册）；
- `src/main.cpp:104-114`：创建 `GenericGRCService` 和 `GrcHttpServiceImpl`，协议 `baidu_std_reuse`，端口 8888。

Dup Service 组件的初始化链路：
- `src/plugin/dup_service.cpp:13-44`：`DupServicePlugin::initialize()` → 调用 `DupClient::global_init()` → 创建 ObjectPool；
- `src/initializer/global.h:114-115`：`ApplicationContext::initialize(true)` 自动发现并初始化所有 babylon 组件。

## 3. 关键词扩展检索结论

搜 `dup|Dup` 在 `src/plugin/` 和 `src/processor/` 中命中核心文件：

- `src/plugin/dup_service.h/cpp`：DupServicePlugin 组件，ObjectPool 持有 DupClient；
- `src/plugin/dup_client.h`：未在本地命中（外部依赖 `baidu/feed/dup_client.h`）；
- `src/processor/vfs.cpp`：主 VFS 去重 processor，组装 DupRequest、调用 RPC、过滤；
- `src/processor/video_launch/vfs_rpc_function.cpp`：Graph Function 版本的 Dup RPC 调用（用于新闻/更新场景）；
- `src/processor/video_launch/vfs_filter_function.cpp`：Graph Function 版本的 Dup 过滤（用于新闻/更新场景）；
- `src/processor/searchc_related_deepin/searchc_immersive_vfs.cpp`：搜索相关沉浸式 VFS；
- `src/processor/searchc_related_deepin/searchc_deepin_vfs.cpp`：搜索深度页 VFS。

## 4. 端到端代码链路图

### 4.1 主链路（`VfsProcessor`）

```text
Request → GenericGRCService
  → Graph(default/global.conf)
  → Recall → FillMeta → PreciseScoreInit
  → VfsProcessor::process()
      ├─ _dup_plugin->get() → PooledObject<DupClient>
      ├─ client.init(cuid, uid, baiduid, logid)
      ├─ DupRequest 构造
      │   ├─ show_map → showlist_item[]（历史下发记录）
      │   ├─ dup_group → items[]（当前候选 nid）
      │   └─ dup_strategies[]（多策略组合）
      ├─ dup_rpc() → brpc → Dup Service (10.201.66.33:8863)
      └─ 过滤
          ├─ is_replicated_with_history(nid) → erase if true
          ├─ dup_nids_among_queues(nid) → erase related dups
          └─ vfs_result / vfs_rid_vec emit
  → Diversity → Adjust → Response
```

### 4.2 辅助链路（Graph Function 版本，用于新闻/更新场景）

```text
Graph: news_in_video_immersion / news_updates_dibar
  → VfsRpcFunction::process_impl()
      ├─ _dup_plugin->get() → PooledObject<DupClient>
      ├─ dup_server_prepare_client() → 构建 DupRequest
      └─ dup_rpc() → emit _dup_client (DupClient as GraphData)
  → VfsFilterFunction::process()
      ├─ _dup_client->is_replicated_with_history(rid) → skip if true
      ├─ _dup_client->is_replicated_with_nid(rid, merged_rid) → skip if dup
      └─ emit _output (过滤后的 vector<RidTmpInfoPtr>)
```

## 5. Producer → Transform → Consumer 三段追踪

### 5.1 Producer：show_map 与 rank_vec 的来源

**show_map**（用户下发历史）：

- `src/processor/vfs.cpp:92-93`：从 `vfs_context->show_map` 获取，是 `ReusableUnorderedMap<uint64_t, uint32_t>`，key=nid，value=status；
- 来源：`show_map` 在图引擎中由上游 `HistoryRead` / `ShowlistFill` 相关 processor 填充。

**rank_vec**（当前候选队列）：

- `src/processor/vfs.cpp:29`：从 `vfs_context->rank_result` 获取，是 `std::vector<RidTmpInfoPtr>`；
- 来源：经过 FillMeta 填充正排后、CtrRank 排序后的结果。

### 5.2 Transform：DupRequest 组装

`src/processor/vfs.cpp:198-431` 构造 DupRequest：

**SessionReq**（用户上下文）：
```cpp
// src/processor/vfs.cpp:202-203
session_req->set_sid(common_info_ptr->request_ptr->common_info().sid());
// src/processor/vfs.cpp:240-264：填充 showlist_item[]
show_list_item->set_nid(nid);
show_list_item->set_status(STATUS_CLICKED);  // 统一置为 CLICKED
show_list_item->set_from_type(FROM_VIDEO);    // 视频来源
```

**DupGroup**（候选与策略）：
```cpp
// src/processor/vfs.cpp:351-424：组合多种策略
dup_group->add_strategy(DUP_STRATEGY_VIDEO_VFS);         // VFS 指纹相似
dup_group->add_strategy(DUP_STRATEGY_NID);               // NID 精确
dup_group->add_strategy(DUP_STRATEGY_MICRO_VIDEO_VFS_BLOOM); // 90天内视频 Bloom
dup_group->add_strategy(DUP_STRATEGY_VIDEO_BLOOM);      // 通用 Bloom
dup_group->add_strategy(DUP_STRATEGY_DS_V3);            // 动态/图文
dup_group->add_strategy(DUP_STRATEGY_TITLE);            // 标题去重（实验）
dup_group->add_strategy(DUP_STRATEGY_MULTI_MODAL_CONTENT); // IP连播
dup_group->set_dup_type(DUPTYPE_HISTORY_GROUP);         // 按用户历史去重
// src/processor/vfs.cpp:424-431：填充 items[]
item->set_nid(rid);
item->set_need_all(true);
```

**实验控制的策略开关**（`src/processor/vfs.cpp:317-422`）：

|| 实验 | 条件 | 效果 |
|---|---|---|
| `dup_single_vfs` | sid hit | 仅 `DUP_STRATEGY_VIDEO_VFS` |
| `dup_single_vfs_bloom` | sid hit | VFS + 多种 Bloom |
| `dup_single_nid` | sid hit | 仅 `DUP_STRATEGY_NID` |
| `dup_single_nid_14/30/60/90` | sid hit | NID + 时间阈值 |
| `same_title_filter_1/2/3/4` | UA=85 + refresh=1 | `DUP_STRATEGY_TITLE` + 阈值 |
| `excess_bloom_offline` | sid hit | 冗余 Bloom 下线 |
| `filter_strategy_modi*` | sid hit | 控制 Bloom 策略开关 |
| `vfs_reverse_flag` | sid hit | 关闭 VFS |
| `vfs_bloom_reverse_flag` | sid hit | 关闭 Bloom |
| `ertiao_sim_exp` | UA=85 + sid hit | 人格化二跳豁免 |
| `rgh_fetchback_recll_exp*` | UA=85 + sid hit | 人格化兴趣回捞豁免 |

### 5.3 Consumer：过滤后的数据流

`src/processor/vfs.cpp:471-627` 执行过滤，核心逻辑：

```cpp
rank_vec->erase(
    std::remove_if(rank_vec->begin(), rank_vec->end(),
        [&](RidTmpInfoPtr rid_info_p) {
            if (rid_info_p == nullptr || rid_info_p->_video_info == nullptr) return true;
            // 1. NID 精确去重
            if (client_ptr->is_replicated_with_history(rid_info_p->rid)) { filter_num++; return true; }
            // 2. 队列内去重（相似视频去重）
            auto* dup_nids_among = client_ptr->get_dup_nids_among_queues(rid_info_p->rid);
            if (dup_nids_among) {
                for (auto& dup : *dup_nids_among) { dup_nids.emplace(dup.first); }
            }
            return false;
        }), rank_vec->end());
```

过滤后 emit 多个结果（`src/processor/vfs.cpp:175-189`）：
- `vfs_result`：主队列去重结果；
- `vfs_result1/2`：用于不同策略实验；
- `vfs_const_result`：常量结果；
- `vfs_copy_result`：副本；
- `vfs_rid_vec`：仅 nid 列表。

## 6. Graph Function 版本：VfsRpcFunction / VfsFilterFunction

这两个 Graph Function 用于新闻/更新等独立场景，配置在 `news_in_video_immersion_vertex.conf` 和 `news_updates_dibar_vertex.conf`。

### 6.1 VfsRpcFunction（RPC 调用）

`src/processor/video_launch/vfs_rpc_function.cpp:14-192`：

```cpp
// src/processor/video_launch/vfs_rpc_function.cpp:24-31
_dup_pooled_object = _dup_plugin->get();
client_ptr.ref(*_dup_pooled_object);  // ref 是 PooledObject 的引用绑定语义
dup_server_prepare_client(*client_ptr);  // 组装 DupRequest
dup_rpc(*client_ptr);                   // brpc 调用
// emit DupClient 作为 GraphData，供下游使用
auto client_ptr = _dup_client.emit();
client_ptr.ref(*_dup_pooled_object);
```

依赖数据（`src/processor/video_launch/vfs_rpc_function.cpp:184-189`）：
- `CommonInfo`：用户基础信息；
- `RequestType`：请求类型（是否搜索等）；
- `ShowItemList`：历史下发列表；
- `AuthorHomePageReadListInfo`：作者页已读列表；
- `SidInfo`：实验参数。

emit 数据（`src/processor/video_launch/vfs_rpc_function.cpp:182`）：`_dup_client` (type=`DupClient`)。

### 6.2 VfsFilterFunction（过滤）

`src/processor/video_launch/vfs_filter_function.cpp:26-83`：

```cpp
// src/processor/video_launch/vfs_filter_function.cpp:38-39
auto mutable_dup_client = const_cast<::feed::dup::DupClient*>(_dup_client);
for (auto& ridinfo : *_input) {
    // 1. 历史去重
    if (mutable_dup_client->is_replicated_with_history(ridinfo->rid)) {
        filter_num++; continue;
    }
    // 2. 队列内去重
    for (auto& merged_ridinfo : *output_vec) {
        if (mutable_dup_client->is_replicated_with_nid(ridinfo->rid, merged_ridinfo->rid)) {
            is_merged_dup = true; break;
        }
    }
    if (!is_merged_dup) output_vec->emplace_back(ridinfo);
}
```

关键：`_dup_client` 作为 GraphData 从上游 `VfsRpcFunction` 传入（共享同一个 `PooledObject`），过滤在当前 vertex 中执行。注意这里用了 `const_cast`——这是安全的，因为 `VfsRpcFunction` 已经完成了 RPC 调用，`DupClient` 在当前请求内只被这个 vertex 消费。

## 7. Dup Service 配置与连接

### 7.1 连接配置

`conf/plugins/graph/multi_graph.conf`（未在本地命中，预期 SUPERPAGE 管理）中指定 `graph.pool_size`（连接池大小）。

运行时连接：`output/conf/dup_client.conf`：

```ini
name: DupServiceChannel
bns: 10.201.66.33:8863    # Dup Service BNS/直连地址
connection_type: pooled    # 复用 brpc 连接池
timeout_ms: 300            # RPC 超时
protocol: baidu_std
caller: feeda-mv-grc
```

### 7.2 动态超时

`src/processor/vfs.cpp:433-437` 使用 `get_dynamic_timeout(context, "dup_rpc")` 动态调整超时，支持流量突发时的自适应降级。

### 7.3 Dapper 监控

`src/processor/vfs.cpp:656-663` 通过 Dapper 上报 RPC 延迟和错误：

```cpp
ChannelCollectorManager::instance().DapperDirectCollect(
    cuid, logid, latency_us, "feeda-mv-grc", "call_dup", ret, ip);
```

## 8. 关键模块表

|| 模块 | 文件/行号 | 作用 | 证据 |
|---|---|---|---|
| DupClient Pool 初始化 | `src/plugin/dup_service.cpp:13-44` | 创建 ObjectPool，调用 DupClient::global_init | DupServicePlugin::initialize |
| DupServicePlugin 注册 | `src/plugin/dup_service.cpp:64` | BABYLON_REGISTER_COMPONENT 自动初始化 | 组件注册 |
| DupRequest 组装 | `src/processor/vfs.cpp:198-431` | 构造 SessionReq / DupGroup / items / strategies | 主链路 |
| showlist 填充 | `src/processor/vfs.cpp:240-264` | 从 show_map 填充历史下发记录 | Producer |
| RPC 调用 | `src/processor/vfs.cpp:438-440` | dup_rpc() → brpc → Dup Service | Consumer |
| 过滤逻辑 | `src/processor/vfs.cpp:471-627` | is_replicated_with_history / get_dup_nids_among_queues | Consumer |
| 过滤后 emit | `src/processor/vfs.cpp:175-189` | vfs_result / vfs_rid_vec 等 | Data emit |
| VfsRpcFunction | `src/processor/video_launch/vfs_rpc_function.cpp:14-192` | Graph Function 版本 Dup RPC | 独立场景 |
| VfsFilterFunction | `src/processor/video_launch/vfs_filter_function.cpp:13-108` | Graph Function 版本过滤 | 独立场景 |
| DAG 配置（沉浸式） | `conf/plugins/graph/news_in_video_immersion_vertex.conf:97-149` | VfsRpcFunction + VfsFilterFunction 配置 | Graph DAG |
| DAG 配置（更新） | `conf/plugins/graph/news_updates_dibar/news_updates_dibar_vertex.conf:106-149` | 同上 | Graph DAG |
| SIA 指标 | `src/processor/vfs.cpp:440/442` | dup_server_rpc / dup_server_process | 监控 |

## 9. 豁免策略（Huomian / 豁免）

代码中有大量豁免（huomian）去重的逻辑：

|| 豁免条件 | 代码位置 | 豁免效果 |
|---|---|---|
| 人格化兴趣回捞（rgh） | `src/processor/vfs.cpp:117-136` | 特定 rec_type（8572/8573/8574/8575）豁免指定策略 |
| 二跳相似（ertiao_sim） | `src/processor/vfs.cpp:147-158` | sim_huomian_rid 豁免 VFS/Bloom |
| 流行IP自建效果（newhot_ip） | `src/processor/vfs.cpp:139-146` | rec_type=4170 且 IP 类型豁免下发去重 |
| 合集豁免（dj_heji） | `src/processor/vfs.cpp:319-328` | `dup_single_new` 实验下合集豁免指定策略 |
| 下发状态豁免 | `src/processor/vfs.cpp:205-238` | `hm_msv_issued_dup` 实验下特定状态（no_actual_issued/issued/unshow）豁免 |

## 10. 风险与排查方法

1. **Dup RPC 超时**：看 `dup_rpc` SIA 指标和 Dapper 日志；日志关键词 `dup server rpc failed`。证据：`src/processor/vfs.cpp:444-449`。
2. **去重不生效**：
   - 检查 `show_map` 是否为空（GRC_FLOW_LOG 输出 `display list count`）；
   - 检查 `rank_vec` 是否包含 nid（GRC_FLOW_LOG 输出 `rank queue count before vfs`）；
   - 检查 `dup_strategies` 是否包含目标策略（实验开关是否命中）。
3. **豁免策略异常**：特定 rec_type 未被豁免，检查实验参数 `sid_info_ptr` 和 UA。
4. **pool 耗尽**：大量并发请求时 `get dup client failed`（`vfs.cpp:191`），说明 pool size 配置不足。

## 11. 未确认问题与下一步

1. **Dup Service 内部路由未展开**：`DupClient::global_init()` 的 BNS 解析、连接池大小等内部行为依赖外部 `baidu/feed/dup_client` 库源码。下一步可搜索 `/home1/code_read` 下是否有该库。
2. **show_map 的上游生产者未完全追踪**：show_map 由历史相关 processor 填充（`HistoryReadCommonProcessor` 等），本次未展开。下一步追踪 `show_map` 的 emit 来源。
3. **Graph Function 版本的 VfsRpcFunction / VfsFilterFunction 与主 VfsProcessor 的关系**：两者可能共存于不同 graph 子图（沉浸式/更新场景），未确认是否有相互调用或数据共享。
4. **`DUP_STRATEGY_MULTI_MODAL_CONTENT`（IP连播）策略的 Dup Service 内部行为**：本地仅看到 `src/processor/vfs.cpp:419-422` 设置该策略，未展开 Dup Service 返回的 `ItemScence` 如何影响下游。

---

## 本次增量分析（2026-05-23）

### 背景

今日继续深入追踪 Dup Service 在 `feeda-mv-grc` 中的链路，新增以下维度：

1. 明确 `VfsProcessor`（主链路）vs `VfsRpcFunction` + `VfsFilterFunction`（Graph Function 版本）的架构关系；
2. 追踪 DupRequest 的完整组装过程，包括 SessionReq、DupGroup、Strategies；
3. 补充实验控制的策略开关表，展示 10+ 个去重实验；
4. 追踪 Graph Function 版本的依赖/emit Data 类型和配置位置；
5. 补充豁免策略（huomian）体系，展示 5 类豁免条件。

> ⚠️ 本次分析**不**把 `feeda-mv-grg` 的 Dup Service 结论直接套用到 `feeda-mv-grc`；所有证据均来自 `feeda-mv-grc` 本地代码。

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-20T19:15:36.437054
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告：Dup Service / VFS 去重链路

## 1. 分析摘要

- `feeda-mv-grc` 已经**真实接入** Dup Service，核心链路集中在 `src/processor/vfs.cpp`、`src/plugin/dup_service.cpp`、`src/processor/video_launch/vfs_rpc_function.cpp` 和 `src/processor/video_launch/vfs_filter_function.cpp` 等文件，说明该技术在召回汇聚侧已经具备可运行的业务落地能力。
- `feeda-mv-grg` 当前**没有发现直接使用** Dup Service / DupClient 的代码，但代码库中 `std::vector`、`std::string`、`std::unordered_map` 使用规模很大，且候选集处理场景明显，说明若要引入去重能力，具备较好的**插入点和迁移潜力**，尤其适合在候选生成后、排序前增加过滤层。

## 2. 代码库详情

- **`feeda-mv-grc`：已有完整参考实现**
  - 已发现目标技术使用，且覆盖链路较完整：
    - `src/plugin/dup_service.cpp` / `src/plugin/dup_service.h`
      - 负责 `DupServicePlugin` 初始化，创建并管理 `DupClient` 连接池。
    - `src/processor/vfs.cpp`
      - 主去重逻辑入口，负责构造 `DupRequest`、拼装 `show_map` 和候选 `rank_vec`、发起 RPC、执行过滤。
    - `src/processor/video_launch/vfs_rpc_function.cpp`
      - Graph Function 版本的 Dup RPC 调用，适合新闻/更新类场景复用。
    - `src/processor/video_launch/vfs_filter_function.cpp`
      - Graph Function 版本的去重过滤逻辑，可作为“RPC 与过滤解耦”的参考。
    - `src/processor/searchc_related_deepin/searchc_immersive_vfs.cpp`
    - `src/processor/searchc_related_deepin/searchc_deepin_vfs.cpp`
      - 说明该能力不仅用于单一场景，而是可扩展到搜索/沉浸式链路。
  - 典型业务语义已经明确：
    - `show_map` 代表用户历史下发记录；
    - `rank_vec` 代表当前候选队列；
    - `dup_strategy` 支持多策略组合，如 `DUPTYPE_HISTORY_GROUP`、`DUP_STRATEGY_VIDEO_VFS`、`DUP_STRATEGY_NID`、`DUP_STRATEGY_VIDEO_BLOOM` 等。
  - 这部分代码可直接作为**迁移蓝本**，特别适合参考其“插件初始化 + 请求组装 + 结果过滤”的三段式结构。

- **`feeda-mv-grg`：未发现直接使用，但具备较强迁移基础**
  - 扫描结果显示：
    - **未发现目标库直接使用** Dup Service / DupClient。
    - `std::vector`：1969 次，356 个文件
    - `std::string`：2443 次，425 个文件
    - `std::unordered_map`：734 次，205 个文件
  - 代表性文件：
    - `model/model.h`
      - 以 `std::vector<RidTmpInfoPtr>` 作为候选输入，说明业务天然存在“候选集处理”接口。
    - `model/paddle_model.h`
      - 多处基于候选向量做预测/打分，适合作为去重前后的处理节点。
  - 从结构上看，`grg` 更像是**序列生成/预测链路**，如果未来要引入去重，建议优先落在：
    - 候选生成后；
    - 排序/预测前；
    - 或输出前的统一过滤层。
  - 由于已有大量容器型接口，迁移时主要改造点会是**数据流接入与服务调用封装**，而不是数据结构本身。

## 3. 💡 适用性评估与建议

- **建议 1：优先复用 `feeda-mv-grc` 的去重接入方式，作为 `feeda-mv-grg` 的参考模板**
  - 参考文件：
    - `src/plugin/dup_service.cpp`
    - `src/processor/vfs.cpp`
    - `src/processor/video_launch/vfs_rpc_function.cpp`
    - `src/processor/video_launch/vfs_filter_function.cpp`
  - 建议做法：
    - 在 `grg` 中单独抽一个“候选过滤层”，模仿 `vfs.cpp` 的处理流程：
      - 先收集历史命中；
      - 再对候选向量做去重；
      - 最后输出过滤后的 `std::vector<RidTmpInfoPtr>`。
  - 适用场景：
    - 候选量较大、重复内容较多、需要降低重复曝光的业务链路。

- **建议 2：如果 `grg` 有候选生成模块，优先在“模型预测前”插入去重**
  - 参考文件：
    - `model/model.h`
    - `model/paddle_model.h`
  - 建议做法：
    - 在 `predict(std::vector<RidTmpInfoPtr>& candidate_vec, ...)` 之前先做一次去重过滤；
    - 对重复候选直接裁剪，减少无效推理和后续排序压力。
  - 预期收益：
    - 降低候选规模；
    - 减少重复样本对模型排序的干扰；
    - 节省计算资源。

- **建议 3：为 `grg` 新增独立的 DupClient 适配层，不要直接把 RPC 逻辑散落在各个 model 文件里**
  - 参考文件：
    - `src/plugin/dup_service.h`
    - `src/plugin/dup_service.cpp`
  - 建议做法：
    - 新增一个类似 `DupServiceAdapter` 的封装层，统一负责：
      - 初始化；
      - 连接池获取；
      - 请求构造；
      - 异常兜底。
  - 这样做的好处：
    - 避免 `model/` 下代码和外部服务强耦合；
    - 后续切换策略、灰度、回滚更容易。

- **建议 4：复用 `grc` 中的“多策略开关”思路，但先从单策略接入开始**
  - 参考文件：
    - `src/processor/vfs.cpp`
  - 建议做法：
    - 第一阶段仅接入 `DUP_STRATEGY_NID` 或单一 VFS 策略；
    - 稳定后再扩展 Bloom、标题、历史组等多策略。
  - 原因：
    - 多策略组合会增加请求复杂度和排查成本；
    - 先保证链路稳定，再逐步扩展策略收益更高。

- **建议 5：对于 `grc` 现有实现，整理成可复用的“过滤工具函数”**
  - 参考文件：
    - `src/processor/vfs.cpp`
    - `src/processor/video_launch/vfs_filter_function.cpp`
  - 建议做法：
    - 把历史去重、队列内去重、空指针过滤等逻辑抽成公共函数；
    - 方便 `searchc_related_deepin/` 和 `video_launch/` 场景复用。
  - 价值：
    - 降低重复代码；
    - 统一去重口径；
    - 便于后续策略升级。

## 4. ⚠️ 引入风险与限制

- **风险 1：外部依赖强，协议和行为不完全受本仓库控制**
  - `DupClient`、连接池、RPC 协议细节依赖外部库和服务端实现；
  - 一旦服务协议变更，`src/plugin/dup_service.cpp` 和 `src/processor/vfs.cpp` 都可能需要联动调整。

- **风险 2：RPC 去重会引入额外时延**
  - 去重链路会多一次远程调用；
  - 对高 QPS 场景，需要关注：
    - 超时；
    - 降级；
    - 连接池复用；
    - 批量请求大小控制。

- **风险 3：多策略并存会增加排障难度**
  - `dup_strategy`、实验开关、阈值配置较多时，容易出现“同一请求在不同实验下结果不同”的情况；
  - 建议先固定最小策略集，再逐步放开。

- **风险 4：历史下发与当前候选的语义必须对齐**
  - `show_map`、`rank_vec`、`rid`、`nid` 的定义要统一；
  - 如果 `grg` 的候选数据结构与 `grc` 不一致，需要先做字段映射，否则容易出现误杀或漏杀。

如果你愿意，我可以进一步把这份内容整理成**适合直接粘贴到技术笔记中的正式章节版**，或者补一版**“grg 迁移落地方案（接口设计 + 文件改造点）”**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
