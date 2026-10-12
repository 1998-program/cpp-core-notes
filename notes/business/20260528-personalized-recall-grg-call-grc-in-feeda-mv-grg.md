# 个性化召回：GRG 调 GRC 通用召回链路深度解析

> 生成时间：2026-05-28  
> 目标代码库：`feeda-mv-grg`（汇聚层）+ `feeda-mv-grc`（内容推荐中心）  
> 关联周级计划选题：`个性化召回：GRG 调 GRC 通用召回`（P0/中难度）  
> 内网文档检索状态：失败（未在代码库中发现可直接 query-content 的 KU URL；按 cpp-service-architecture 要求标注"未检索到，需人工补充"）

---

## 一、服务角色与目的

**GRG（General Rank Gateway）** 是推荐系统的汇聚层（GRG = General + Rank + Gateway），负责整合多路召回结果、做排序和多样性处理。

**GRC（General Rank Center）** 是内容推荐中心，负责从多种队列（loads/effect/rule/function/news）生成候选内容。

**数据流**：GRG → GrcRecallFunction → GrcPlugin RPC → GRC → GRCResponse → GRG 回填 `recall_nid_vec` / `recall_nid_map` → 下游 fill-meta / filter / rank

```
GRG Request (is_grg_request=true)
  ↓
GrcRecallFunction.process()
  ├─ 构造 GRCRequest，标记 is_grg_request=true
  ├─ 设置 backup_sign（容灾路由）
  ├─ GrcPlugin.call() → brpc RPC 到 GRC
  └─ 解析 GRCResponse 回填队列
      ↓
recall_nid_vec (vector<uint64_t>)
recall_nid_map (map<uint64_t, RidTmpInfoPtr>)
LoadsQueue / RuleQueue / FunctionQueue / EffectQueue / NewsQueue
      ↓
下游 FillMeta → Filter → Rank → GRGResponse
```

---

## 二、关键模块表

| 模块 | 文件 | 角色 |
|------|------|------|
| GrcRecallFunction | `src/process/grc_recall_function.cpp` | GRG 调用 GRC 的主函数，解析 GRCResponse 回填各队列 |
| GrcPlugin | `src/plugin/grc.cpp` + `src/plugin/grc.h` | GRC RPC client 封装，注册为 `microvideo_grc` / `no_backup_microvideo_grc` |
| GrcService | GRC 服务端 | GRC 的服务入口（不在 GRG 代码库，需从 GRC 代码追踪） |
| vertex.conf | `conf/plugins/graph/short_micro_video/vertex.conf:50-175` | GrcRecallFunction 图节点配置，声明 emit/depend |

---

## 三、图配置追踪

### 3.1 vertex.conf 中的 GrcRecallFunction 声明

**文件**：`conf/plugins/graph/short_micro_video/vertex.conf:50-175`

```ini
[@vertex]
function: GrcRecallFunction
[.@depend]
name: _request
data: Request
[.@depend]
name: _sid_info
data: SidInfo
[.@emit]
name: _pass_though_response
data: PassThoughResponse
[.@emit]
name: _reqnum
data: Reqnum
[.@emit]
name: _error_num
data: GrcErrorNum
[.@emit]
name: _loads_queue
data: LoadsQueue
[.@emit]
name: _rule_queue
data: RuleQueue
[.@emit]
name: _news_rule_queue
data: NewsRuleQueue
[.@emit]
name: _function_queue
data: FunctionQueue
[.@emit]
name: _effect_queue
data: EffectQueue
[.@emit]
name: _news_queue
data: NewsQueue
[.@emit]
name: _effect_queue_for_doc_feature
data: EffectQueueForDocFeature
[.@emit]
name: _recall_nid_vec
data: RecallNidVec
[.@emit]
name: _recall_nid_map
data: RecallNidMap
[.option]
channel_name: microvideo_grc
```

**关键依赖**：
- `_request`：GRG 请求透传到 GRC
- `_sid_info`：实验参数，用于控制容灾路由（如 `backup_sign=2` 时切换到 `no_backup_microvideo_grc`）

**关键 emit**：
- `RecallNidVec` / `RecallNidMap`：向量形式的召回结果
- 6 个队列：LoadsQueue、RuleQueue、FunctionQueue、EffectQueue、NewsQueue、EffectQueueForDocFeature

### 3.2 global.conf 中图配置结构

**文件**：`conf/plugins/graph/short_micro_video/global.conf:1-48`

```ini
[graph]
pool_size: 20
name: short_micro_video
[@global_depend]
data:Request
data:uid
data:cuid
...
```

---

## 四、端到端数据链路追踪

### 4.1 GrcRecallFunction::init() — 插件初始化

**文件**：`src/process/grc_recall_function.cpp:20-38`

```cpp
int32_t init(const comcfg::ConfigGroup* config) noexcept {
    GET_CONF_FIELD_CHECK((*config), _channel_name, channel_name, cstr);
    _grc_plugin = ApplicationContext::instance().get<GrcPlugin>(_channel_name);
    _rpc_name = _channel_name + "_recall_rpc";
    // dapper set graph vertex
    _loads_queue.set_graph_vertex(context.get_vertex());
    _rule_queue.set_graph_vertex(context.get_vertex());
    // ...
    _effect_queue.set_graph_vertex(context.get_vertex());
    return 0;
}
```

- 从 ApplicationContext 获取 `GrcPlugin`（由 vertex.conf 的 `channel_name: microvideo_grc` 指定）
- 为 6 个队列设置 `graph_vertex`（用于后续 ChannelPublisher 发布 RidTmpInfo）

### 4.2 GrcRecallFunction::process() — RPC 调用与响应解析

**文件**：`src/process/grc_recall_function.cpp:39-427`

#### 4.2.1 构造 GRCRequest 并设置容灾标记

```cpp
// 第 153-160 行
feed::grc::GRCRequest grc_req = *_request;
grc_req.mutable_common_info()->set_is_grg_request(true);
if (grc_req.common_info().backup_sign() > 0) {
    SIA_ADD("backup_sign", grc_req.common_info().backup_sign());
    if (grc_req.common_info().backup_sign() == 2) {
        _grc_plugin = ApplicationContext::instance().get<GrcPlugin>("no_backup_microvideo_grc");
    }
}

// 第 162 行：发起 RPC
auto ret = _grc_plugin->call(grc_req, _grc_response, *(context.get_logid()), dt_cntl, &_rpc_name);
CUSTOM_SIA_END(grc_recall_rpc, _rpc_name, ret);
if (ret != ERR_OK) {
    *error_num = ret;
}
```

**关键设计**：
- `is_grg_request=true`：告知 GRC 这是 GRG 发起的请求，GRC 会做一些特殊处理（如透传 `dt_user_activity`、`intent_score` 等）
- `backup_sign=2` 时切换到 `no_backup_microvideo_grc` 插件（不走备份机房的 GRC）

#### 4.2.2 透传 attachment 字段

GRC 返回的 GRCResponse 通过 `attachment` 透传额外信息：

```cpp
// 第 191-198 行：actual_reqnum
if (_grc_response.attachment().has_actual_reqnum()) {
    *reqnum = _grc_response.attachment().actual_reqnum();
}

// 第 195-203 行：dt_user_activity / dt_rk_uplift
if (_grc_response.attachment().has_dt_user_activity()) {
    *dt_user_activity = _grc_response.attachment().dt_user_activity();
}
if (_grc_response.attachment().has_dt_rk_uplift()) {
    *dt_user_uplift_val = _grc_response.attachment().dt_rk_uplift();
}

// 第 206-233 行：各资源类型偏好
if (_grc_response.attachment().has_sv_prefer()) {
    *sv_prefer = _grc_response.attachment().sv_prefer();
}
if (_grc_response.attachment().has_mv_prefer()) {
    *mv_prefer = _grc_response.attachment().mv_prefer();
}
// ...
```

#### 4.2.3 解析 content 并回填队列

**文件**：`src/process/grc_recall_function.cpp:353-384`

```cpp
_pass_though_content_vec.reserve(_grc_response.content_size());
for (size_t i = 0; i < _grc_response.content_size(); ++i) {
    auto content = _grc_response.mutable_content(i);
    if (content->key() == "loads") {
        loads_insert_conf->Swap(content->mutable_ext());
        parse_content(*content, loads_queue);
    } else if (content->key() == "rule") {
        rule_insert_conf->Swap(content->mutable_ext());
        parse_content(*content, rule_queue, &news_rule_queue);
    } else if (content->key() == "function") {
        parse_content(*content, function_queue);
    } else if (content->key() == "effect") {
        parse_content(*content, effect_queue, nullptr, &effect_queue_for_doc_feature);
    } else if (content->key() == "news_effect") {
        parse_content(*content, news_queue);
    } else {
        _pass_though_content_vec.emplace_back(std::move(*content));
    }
}
```

- GRCResponse 的 content 按 key 分发到不同队列
- key 包括：`loads`（拉活）、`rule`（规则召回）、`function`（函数召回）、`effect`（效果召回）、`news_effect`（新闻效果）
- 其余 content 作为 `pass_though_content_vec` 透传

### 4.3 parse_content() — 队列填充

**文件**：`src/process/grc_recall_function.cpp:430-465`

```cpp
size_t parse_content(feed_gr::Content& content,
                    ChannelPublisher<RidTmpInfo*>& publisher,
                    ChannelPublisher<RidTmpInfo*>* news_rule_publisher = nullptr,
                    ChannelPublisher<RidTmpInfo*>* doc_feature_publisher = nullptr) {
    auto& valid_num = _queue_cnt_map[content.key()];
    _rid_vec.reserve(_rid_vec.size() + content.items_size());
    _rid_map.reserve(_rid_map.size() + content.items_size());
    for (size_t i = 0; i < content.items_size(); ++i) {
        auto ridinfo = std::shared_ptr<RidTmpInfo>(new RidTmpInfo);
        if (0 != parse_one_item(content.mutable_items(i), ridinfo.get())) {
            continue;
        }
        ridinfo->div_index = i;
        ridinfo->content_key = content.key();
        if (doc_feature_publisher != nullptr) {
            *((*doc_feature_publisher)->publish()) = ridinfo.get();
        }
        if (news_rule_publisher != nullptr && ridinfo->_content_item.is_news_rule_queue()) {
            *((*news_rule_publisher)->publish()) = ridinfo.get();
        } else {
            *(publisher->publish()) = ridinfo.get();
        }
        ++_mark_cnt_map[ridinfo->type];
        _rid_vec.emplace_back(ridinfo->rid);
        _rid_map.emplace(ridinfo->rid, ridinfo.get());
        _ridinfo_vec.emplace_back(std::move(ridinfo));
        ++valid_num;
    }
    _total_cnt += valid_num;
    return valid_num;
}
```

### 4.4 parse_one_item() — 单条 ContentItem 解析

**文件**：`src/process/grc_recall_function.cpp:467-568`

```cpp
int32_t parse_one_item(feed_gr::ContentItem* item, RidTmpInfo* ridinfo) {
    ridinfo->rid = item->id();
    ridinfo->type = item->display_strategy().type();
    ridinfo->intervene_card_type = item->display_strategy().intervene_card_type();
    ridinfo->res_score = item->score();
    ridinfo->mix_score = item->ext().mix_score();
    ridinfo->offer_score = item->ext().merge_score();
    ridinfo->dnn_score_ctr = item->ext().dnn_score_ctr();
    ridinfo->set2set_res_score = item->ext().set2set_res_score();
    // ... 20+ 分数字段
    ridinfo->is_title_dup = item->ext().item_is_title_dup();
    ridinfo->content_agent_score = item->ext().content_agent_score();
    ridinfo->ernie_score = item->ext().ernie_score();
    // baiplus 字段
    ridinfo->baiplus_info.baiplus_place_id = item->ext().baiplus_place_id();
    ridinfo->baiplus_info.baiplus_dsp_id = item->ext().baiplus_dsp_id();
    // ...
    auto& mutable_item = const_cast<feed_gr::ContentItem&>(ridinfo->_content_item);
    mutable_item.Swap(item);
    return 0;
}
```

---

## 五、GrcPlugin RPC 封装

### 5.1 wireup() — 插件初始化

**文件**：`src/plugin/grc.cpp:10-26`

```cpp
int32_t GrcPlugin::wireup(ApplicationContext& context) noexcept {
    auto channel_plugin = context.get<ChannelGroup>();
    _dynamic_channel = channel_plugin->get_dynamic(_channel_name);
    baidu::rpc::Channel* grc_channel = channel_plugin->get(_channel_name);
    _grc_stub = std::unique_ptr<::feed::grc::GRCService_Stub>(
        new ::feed::grc::GRCService_Stub(grc_channel));
    BVAR_LATENCY_EXPOSE(_bvar_latency, _channel_name);
    return 0;
}
```

- 从 ChannelGroup 获取 RPC Channel
- 创建 GRCService_Stub

### 5.2 call() — RPC 调用

**文件**：`src/plugin/grc.cpp:28-97`

```cpp
int32_t GrcPlugin::call(
        const ::feed::grc::GRCRequest& request,
        ::feed::grc::GRCResponse& response,
        const uint64_t& log_id,
        DTController *dt_cntl,
        const std::string* rpc_name) noexcept{ 
    baidu::rpc::Controller cntl;
    cntl.set_log_id(log_id);
    _dynamic_channel->on(cntl, dt_cntl, rpc_name);
    if (dt_cntl != nullptr) {
        auto common_info = const_cast<::feed::grc::GRCRequest&>(request).mutable_common_info();
        common_info->set_dynamic_timeout(cntl.timeout_ms());
    }
    _grc_stub->query(&cntl, &request, &response, NULL);
    auto cost = cntl.latency_us()/1000;
    _bvar_latency << cost;
    _dynamic_channel->feedback(cntl, dt_cntl, rpc_name);
    if (cntl.Failed()) {
        error_code = cntl.ErrorCode();
    } else {
        error_code = response.error_num();
    }
    // 日志打印
    GRG_LOG(NOTICE, log_id) << "[channel_name:" << _channel_name << "][uid:" << ...
    return error_code;
}
```

**关键设计**：
- `_dynamic_channel->on()`：动态超时控制（按 scene 分配 timeout 预算）
- `_grc_stub->query()`：发起同步 RPC
- `_bvar_latency`：暴露 RPC 延迟指标
- `_dynamic_channel->feedback()`：反馈延迟用于动态超时计算

---

## 六、配置与部署

### 6.1 图实例池配置

**文件**：`conf/plugins/graph/short_micro_video/global.conf:1-4`

```ini
[graph]
pool_size: 20
pool_bvar: short_micro_video_graph_pool
name: short_micro_video
```

- `pool_size=20`：short_micro_video 图实例池大小为 20
- `pool_bvar`：池化相关 bvar 用于监控

### 6.2 GrcRecallFunction 的 channel 配置

**文件**：`conf/plugins/graph/short_micro_video/vertex.conf:174-175`

```ini
[.option]
channel_name: microvideo_grc
```

- GRC 插件名注册为 `microvideo_grc` 和 `no_backup_microvideo_grc`（`src/plugin/grc.cpp:99-104`）

---

## 七、证据来源

| 文件 | 行号 | 内容 |
|------|------|------|
| `conf/plugins/graph/short_micro_video/vertex.conf` | 50-175 | GrcRecallFunction 图节点配置 |
| `conf/plugins/graph/short_micro_video/global.conf` | 1-48 | 图配置根结构 |
| `src/process/grc_recall_function.cpp` | 14-18 | GrcRecallFunction 类定义 |
| `src/process/grc_recall_function.cpp` | 20-38 | init() 插件初始化 |
| `src/process/grc_recall_function.cpp` | 39-107 | process() 前置 emit 声明 |
| `src/process/grc_recall_function.cpp` | 152-162 | is_grg_request + backup_sign 容灾 |
| `src/process/grc_recall_function.cpp` | 191-350 | attachment 透传字段解析 |
| `src/process/grc_recall_function.cpp` | 353-384 | content 分发到队列 |
| `src/process/grc_recall_function.cpp` | 430-465 | parse_content 队列填充 |
| `src/process/grc_recall_function.cpp` | 467-568 | parse_one_item 单条解析 |
| `src/plugin/grc.cpp` | 10-26 | GrcPlugin::wireup() |
| `src/plugin/grc.cpp` | 28-97 | GrcPlugin::call() RPC 调用 |
| `src/plugin/grc.cpp` | 99-104 | BABYLON_REGISTER 插件注册 |
| `src/plugin/grc.h` | 42-58 | GrcPlugin 类定义 |

---

## 八、未确认问题

1. **GRC 端如何处理 is_grg_request=true**：需要从 GRC 代码库追踪 GRCRequest 处理逻辑，确认 is_grg_request 对 GRC 行为的影响。
2. **no_backup_microvideo_grc 插件的具体路由**：需要查看 ChannelGroup 配置，确认 backup_sign=2 时的具体路由策略。
3. **各队列下游消费者的完整链路**：
   - `LoadsQueue` → FillMeta → Filter → Rank（需验证）
   - `EffectQueue` → FillMeta → Filter → Rank → set2set
   - 需要从 `conf/plugins/graph/short_micro_video/pipeline.conf` 追踪各队列的依赖关系
4. **pass_though_content_vec 的后续处理**：第 382 行透传的内容未被分发到队列，需要确认这些内容的消费方。
---

## 七、业务代码库适配分析
> **分析时间**：2026-07-20T19:18:24.175536
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 1. 分析摘要

- 从扫描结果看，**GRG 调 GRC 的通用召回链路在业务代码库中已具备部分落点，但覆盖度不高**。  
  在 `feeda-mv-grg` 中**未发现直接使用**该链路的现成代码；在 `feeda-mv-grc` 中仅发现 **1 个直接相关文件**：`processor/recall_personalized_quota.cpp`。这说明当前更多是**局部接入**，还没有形成大范围复用的统一召回入口。

- 从容器使用规模看，两个代码库都属于**重度 STL 容器驱动**的工程：  
  `feeda-mv-grg` 里 `std::vector` / `std::string` / `std::unordered_map` 使用量分别为 **1969 / 2443 / 734**；  
  `feeda-mv-grc` 中分别为 **8442 / 7170 / 2834**。  
  这意味着若要迁移到“GRG → GRC”这类统一召回链路，**业务收益主要来自链路收敛、召回能力统一、参数透传标准化**，而不是简单替换容器带来的微观性能收益。整体迁移潜力偏**中高**，但更适合先从 `feeda-mv-grc` 的局部场景切入。

---

## 2. 代码库详情

### 2.1 `feeda-mv-grg`（汇聚层）

- **扫描结论**
  - 未发现该技术的直接落地代码。
  - 现有代码更像是“候选集承接 + 模型排序”架构，核心接口大量使用 `std::vector<RidTmpInfoPtr>` 传递候选内容。
  - 说明 GRG 侧已有良好的“召回结果承接面”，适合接入 GRC 返回的 `recall_nid_vec` / `recall_nid_map`。

- **可参考的现有代码**
  - `model/model.h`
    ```cpp
    class Model {
    public:
      virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    };
    ```
  - `model/paddle_model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```
  - `model/paddle_model.h`
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```

- **适配判断**
  - GRG 侧的主链路已经是“候选向量驱动”的设计，若引入 GRC 召回，通常只需要在**召回入口层**接入，而不必大面积改动模型推理层。
  - 迁移价值主要在于：  
    - 扩展召回来源  
    - 统一请求参数透传  
    - 提升召回可配置性和容灾能力

---

### 2.2 `feeda-mv-grc`（内容推荐中心）

- **扫描结论**
  - 已发现 1 个直接相关文件：`processor/recall_personalized_quota.cpp`
  - 说明 GRC 侧已经存在**个性化配额/召回控制**相关实现，可作为 GRG 调用 GRC 链路的参考入口。
  - GRC 侧整体 STL 使用量更大，说明其更偏向**复杂召回编排与集合处理**，与“多队列召回”的技术形态匹配度较高。

- **可参考的现有代码**
  - `processor/recall_personalized_quota.cpp`
    - 这是当前扫描中唯一确认与目标技术相关的落地点，建议作为 **GRC 侧适配参考样板**。
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
    - 说明 GRC 已有较成熟的图结构/依赖映射处理逻辑，适合承载 `GRCRequest` 到多队列响应的分发逻辑。
  - `service/grc_http_service.cpp`
    ```cpp
    std::string resp_str;
    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    ```
    - 表明服务端已广泛使用向量/字符串进行请求参数和响应处理。

- **适配判断**
  - GRC 侧更适合成为**通用召回编排中心**，将 loads / rule / function / effect / news 等队列统一输出。
  - 从代码结构看，GRC 已具备承接 `is_grg_request=true` 这类特殊请求标记的条件，适合继续增强为 GRG 的标准召回上游。

---

## 3. 💡 适用性评估与建议

- - **建议 1：优先在 `feeda-mv-grc/processor/recall_personalized_quota.cpp` 旁边补一层“GRG 请求适配器”**
  - 场景：GRG 发起的请求需要统一打标 `is_grg_request=true`，并透传 `backup_sign`、`dt_user_activity`、`intent_score` 等字段。
  - 建议：将这类请求判断和字段透传逻辑抽成可复用适配层，避免散落在多个 processor 中。
  - 收益：减少重复判断逻辑，增强 GRC 侧对 GRG 请求的可控性。

- - **建议 2：在 `feeda-mv-grg/model/model.h` 和 `model/paddle_model.h` 保持 `candidate_vec` 接口不变，只在召回入口接入 GRC 返回结果**
  - 场景：GRG 当前模型层已经依赖 `std::vector<RidTmpInfoPtr>`。
  - 建议：不要改动下游 `predict()` 接口，而是在召回阶段把 `GRCResponse` 解析成 `recall_nid_vec` / `recall_nid_map` 后，再映射到现有候选结构。
  - 收益：迁移成本低，避免影响排序和特征计算链路。

- - **建议 3：参考 `feeda-mv-grc/service/grc_http_service.cpp` 的图依赖处理方式，统一 GRC 服务端的队列输出组织**
  - 场景：GRC 会输出 loads / rule / function / effect / news 等多队列结果。
  - 建议：将队列组装、依赖映射、结果聚合逻辑集中管理，保证 `GRCResponse` 的 attachment 与队列结果同步一致。
  - 收益：便于后续扩展新的召回类型，也更容易做灰度和排障。

- - **建议 4：在 GRG 侧新增一层“GRC 结果归一化”逻辑，优先落在召回解析模块而不是模型模块**
  - 场景：`GRCResponse` 中会返回多种附加信息，如 `actual_reqnum`、`dt_user_activity`、`sv_prefer` 等。
  - 建议：在 GRG 的召回处理文件中做统一归一化，将这些字段映射到现有请求上下文和候选特征中。
  - 收益：避免模型层混入 RPC 协议细节，职责更清晰。

- - **建议 5：对 `backup_sign=2` 的容灾分支单独做配置化，避免硬编码切换插件**
  - 场景：当前逻辑会在 `backup_sign == 2` 时切到 `no_backup_microvideo_grc`。
  - 建议：将这类路由规则抽成配置或策略表，放到 `conf/plugins/graph/short_micro_video/vertex.conf` 这类配置层管理。
  - 收益：后续机房切换、灰度发布、容灾回滚都更容易控制。

---

## 4. ⚠️ 引入风险与限制

- - **风险 1：请求透传字段不完整会导致召回结果偏差**
  - `is_grg_request`、`backup_sign`、`dt_user_activity`、`intent_score` 这类字段如果在 GRG → GRC 之间丢失，可能导致 GRC 走错分支或返回不一致候选。
  - 建议在接入时做字段白名单校验和日志对账。

- - **风险 2：GRG 侧当前没有直接使用经验，迁移需要补齐联调与回归**
  - `feeda-mv-grg` 扫描结果显示没有直接的 GRC 链路代码可复用。
  - 这意味着需要额外做接口联调、候选集对齐、召回数量一致性验证，不能仅靠静态代码迁移完成。

- - **风险 3：多队列结果回填可能影响下游排序稳定性**
  - GRC 返回的多个队列如果顺序、去重策略、优先级定义不清晰，会影响 GRG 下游 `fill-meta / filter / rank` 的结果稳定性。
  - 尤其要关注 `recall_nid_vec` 与 `recall_nid_map` 的一致性。

- - **风险 4：容灾插件切换带来配置与运行时双重复杂度**
  - `microvideo_grc` 与 `no_backup_microvideo_grc` 的切换策略如果过于依赖运行时判断，容易出现灰度环境和正式环境行为不一致的问题。
  - 建议将路由规则、备机房策略、超时策略统一纳入配置管理。

---

如果你愿意，我可以继续把这份内容整理成**“可直接粘贴到技术笔记中的最终章节版”**，或者补一版**更偏“迁移落地方案”的执行清单**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
