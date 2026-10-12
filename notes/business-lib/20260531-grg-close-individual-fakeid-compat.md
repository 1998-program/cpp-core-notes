# GRG 个性化推荐关闭开关 fakeid 兼容

> 日期：2026-05-31  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 `sun.business` 计划项  
> 服务：`feeda-mv-grg`  
> 范围：最近提交 `feed-arch-37111` 在 GRG 服务入口识别 `CommonInfo::is_close_individual()` 后，用 `fake_id` 替换 `uid/cuid/baiduid`，并把替换后的请求贯穿 GraphData、召回、融合、日志链路。  
> 内网文档：今日计划未提供 `business_doc_urls`；本文未读取 KU 正文，业务背景需人工补充。

---

## 0. 架构全景图

<div class="arch-wrapper fakeid-arch"><style scoped>.fakeid-arch{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d7e2ef;border-radius:14px;padding:22px;margin:16px 0;color:#172033}.fakeid-arch .arch-title{font-size:18px;font-weight:900;margin-bottom:14px}.fakeid-arch .arch-layer{border-radius:10px;padding:14px;margin:10px 0}.fakeid-arch .user{background:#dbeafe;border-left:5px solid #2563eb}.fakeid-arch .application{background:#dcfce7;border-left:5px solid #16a34a}.fakeid-arch .ai{background:#fef3c7;border-left:5px solid #d97706}.fakeid-arch .data{background:#fce7f3;border-left:5px solid #db2777}.fakeid-arch .external{background:#f1f5f9;border:1px dashed #64748b;border-left:5px solid #64748b}.fakeid-arch .arch-layer-title{font-size:13px;font-weight:800;margin-bottom:8px}.fakeid-arch .arch-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.fakeid-arch .arch-box{background:rgba(255,255,255,.86);border:1px solid rgba(15,23,42,.08);border-radius:8px;padding:9px;font-size:12px;line-height:1.35}.fakeid-arch .arch-box.highlight{border:2px solid #d97706;background:#fff7ed;font-weight:800}.fakeid-arch small{display:block;color:#64748b;margin-top:3px}</style><div class="arch-title">GRG 个性化关闭：入口 fake_id 替换架构</div><div class="arch-layer user"><div class="arch-layer-title">① Upstream Request</div><div class="arch-grid"><div class="arch-box">真实 `uid`<small>common_info.uid()</small></div><div class="arch-box">真实 `cuid`<small>common_info.cuid()</small></div><div class="arch-box">真实 `baiduid`<small>common_info.baiduid()</small></div><div class="arch-box highlight">`is_close_individual`<small>关闭个性化推荐开关</small></div></div></div><div class="arch-layer application"><div class="arch-layer-title">② GRG Service Entry</div><div class="arch-grid"><div class="arch-box">`query()`<small>先取 graph 与 dt controller</small></div><div class="arch-box highlight">`Util::caculate_fake_id()`<small>uid/cuid/bid/logid → hash</small></div><div class="arch-box">`grg_request.CopyFrom()`<small>只改本地副本</small></div><div class="arch-box">`run_request` 指针切换<small>后续统一用替换请求</small></div></div></div><div class="arch-layer ai"><div class="arch-layer-title">③ Graph Data / Recommendation Pipeline</div><div class="arch-grid"><div class="arch-box highlight">`fill_basic_data_for_graph()`<small>uid/cuid/baiduid 注入 fake_id</small></div><div class="arch-box">GRC Recall<small>透传替换后的请求</small></div><div class="arch-box">Diversity / Rank<small>读取 context 用户标识</small></div><div class="arch-box">Response / Logs<small>print_log 看到 fake_id</small></div></div></div><div class="arch-layer data"><div class="arch-layer-title">④ Observability / Risk Boundary</div><div class="arch-grid"><div class="arch-box">NOTICE 日志<small>old ids + fake_id</small></div><div class="arch-box">VIP 判定<small>基于替换后的 cuid/uid</small></div><div class="arch-box">GraphMonitor<small>fake_id 作为 cuid 初始化</small></div><div class="arch-box highlight">隐私边界<small>真实身份不进入图内个性化链路</small></div></div></div></div>

---

## 1. Role and Purpose

`feed-arch-37111` 的核心改动很小，但位置非常关键：它没有在每个召回/模型/融合 processor 内部分散判断“关闭个性化”，而是在 **GRG 服务入口** 复制一份请求，把 `common_info.uid/cuid/baiduid` 一次性替换成 `fake_id`，之后 `fill_basic_data_for_graph()`、`run()`、日志、Graph processors 都只看到替换后的 `run_request`。

这相当于把“关闭个性化推荐”降维成“请求身份匿名化/稳定伪造化”问题：下游仍按原来的用户标识字段工作，但拿到的是 fake id，而不是真实用户 id。

---

## 2. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam sequenceMessageAlign center
skinparam ParticipantPadding 18

title GRG is_close_individual fake_id 替换时序

actor Upstream as U
participant "GenericGRGService::query" as Q
participant "Util::caculate_fake_id" as F
participant "grg_request copy" as C
participant "fill_basic_data_for_graph" as B
participant "GraphData" as D
participant "Graph Processors" as P
participant "print_log / GraphMonitor" as L

U -> Q: GRCRequest(real uid/cuid/baiduid, is_close_individual)
Q -> Q: graph_engine->try_get(graph_name)
Q -> Q: get_dt_controller()
alt is_close_individual != 0
  Q -> F: uid + cuid + baiduid + logid
  F --> Q: fake_id = hash(string)
  Q -> C: CopyFrom(original request)
  Q -> C: set_uid(fake_id)
  Q -> C: set_cuid(fake_id)
  Q -> C: set_baiduid(fake_id)
  Q -> Q: run_request = &grg_request
else is_close_individual == 0
  Q -> Q: run_request = original request
end
Q -> B: fill_basic_data_for_graph(graph, run_request, ...)
B -> D: emit Request / uid / cuid / baiduid / ExpInfo
Q -> P: run(graph_name, graph, run_request, response)
P -> P: recall / diversity / model / response use fake identity
P --> L: response + trace + graph path
L -> L: NOTICE includes uid/cuid/baiduid from run_request
@enduml
```

---

## 3. 配置/结构信息图

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title 原始请求 vs 关闭个性化请求
  desc 入口替换后，下游 processor 不需要感知开关细节
  left
    label Normal Request
    desc uid/cuid/baiduid 保持真实值；VIP、GraphMonitor、召回、融合均按真实用户上下文执行
  right
    label Close Individual Request
    desc 复制请求并将 uid/cuid/baiduid 全部替换为 fake_id；Graph 内个性化链路只见伪身份
```

```infographic
infographic list-grid-badge-card
data
  title fake_id 兼容改动清单
  desc commit 8ed39c0 涉及三个文件
  items
    - label grg_service.cpp
      desc 新增 run_request 分支；关闭个性化时 CopyFrom 并替换三类用户标识
      icon mdi/source-branch
    - label util.cpp
      desc 新增 caculate_fake_id，按 uid/cuid/bid/logid 计算 std::hash
      icon mdi/function-variant
    - label util.h
      desc 声明 caculate_fake_id 静态工具函数
      icon mdi/file-code
    - label log evidence
      desc NOTICE 打印 old ids 与 fake_id，便于单 logid 回溯
      icon mdi/text-search
```

---

## 4. 关键代码路径

### 4.1 服务入口替换点

`src/service/grg_service.cpp:64-89` 是整个链路的控制面。最近提交在 graph 获取和 controller 获取之后，插入了以下逻辑：

- 创建本地 `feed::grc::GRCRequest grg_request`。
- 默认 `run_request = request`。
- 如果 `request->has_common_info()` 且 `common_info().is_close_individual() != 0`：
  - 调用 `Util::caculate_fake_id(uid, cuid, baiduid, logid, fake_id)`。
  - `grg_request.CopyFrom(*request)`。
  - 将 `uid/cuid/baiduid` 全部设为 `fake_id`。
  - `run_request = &grg_request`。
- 后续 `fill_basic_data_for_graph()` 和 `run()` 全部传入 `run_request`。

这个位置的优点是 **影响面集中**：所有依赖 `Request` 或图上 `uid/cuid/baiduid` 的下游逻辑天然兼容。

### 4.2 fake_id 生成方式

`src/util/util.cpp` 新增 `Util::caculate_fake_id()`：拼接 `uid_cuid_bid_logid` 后使用 `std::hash<std::string>`，再转成十进制字符串。

这意味着 fake_id 与 `logid` 绑定：同一个用户在不同请求 logid 下会产生不同 fake id。这样能降低跨请求串联真实用户行为的风险，但也意味着如果某些下游期望稳定用户画像，关闭个性化场景下不会获得稳定画像。

### 4.3 GraphData 注入边界

`src/service/grg_service.cpp:121-200` 的 `fill_basic_data_for_graph()` 从 `request->common_info()` 读取 `uid/cuid/baiduid` 并以 `ref()` 方式注入 GraphData。由于传入的是 `run_request`，关闭个性化时图内 `uid/cuid/baiduid` 都引用 `grg_request` 副本中的 fake id。

需要注意：`grg_request` 是 `query()` 栈上的局部变量，但 `run()` 和 `closure.get()/wait()` 都在 `query()` 返回前完成，因此当前同步等待模型下引用生命周期是闭合的。如果未来把 graph run 改成真正异步返回，需要重新审查该引用生命周期。

---

## 5. 业务影响面

| 层级 | 影响 | 证据 |
|---|---|---|
| Graph 基础数据 | `uid/cuid/baiduid` 被 fake_id 替换 | `src/service/grg_service.cpp:127-140` |
| VIP 判断 | `is_hit_vip()` 读替换后的 cuid/uid，真实 VIP 可能不再命中 | `src/service/grg_service.cpp:153-154`, `src/service/grg_service.cpp:361-374` |
| 日志打印 | `print_log()` 中 common_info 来源为 `run_request`，会打印 fake_id | `src/service/grg_service.cpp:281-293` |
| GraphMonitor | `graph_monitor.init(common_info.cuid(), logid)` 使用 fake cuid | `src/service/grg_service.cpp:326-328` |
| GRC Recall | GRC 子请求从当前 request 拷贝，关闭个性化后会继续透传 fake id | `src/process/grc_recall_function.cpp:153-162` |

---

## 6. Pitfalls 卡片

<div class="card-frame fakeid-card"><style scoped>.fakeid-card{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;margin:18px 0}.fakeid-card .card{background:#f5f7fa;border:1px solid #d8e0ea;border-radius:18px;padding:24px;color:#1f2937}.fakeid-card .card-meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800}.fakeid-card .card-title{font-size:28px;line-height:1.1;font-weight:900;margin:8px 0 12px}.fakeid-card .card-grid{display:grid;grid-template-columns:2fr 1fr;gap:14px}.fakeid-card .card-panel{background:rgba(255,255,255,.78);border-top:4px solid #3d5a80;border-radius:10px;padding:14px;font-size:14px;line-height:1.65}.fakeid-card .card-panel strong{color:#172033}.fakeid-card .card-tag{display:inline-block;background:#e0e7ff;color:#3730a3;border-radius:999px;padding:3px 8px;margin:3px;font-size:12px;font-weight:700}</style><div class="card"><div class="card-meta">Pitfalls · Close Individual</div><div class="card-title">入口替换很干净，但要盯住“引用生命周期”和“日志语义”</div><div class="card-grid"><div class="card-panel"><strong>生命周期：</strong>`fill_basic_data_for_graph()` 对请求字段使用 `ref()`，当前 `run()` 同步等待闭环可行；若未来异步化，栈上 `grg_request` 会成为风险点。<br><strong>稳定性：</strong>fake_id 包含 logid，同一用户跨请求不稳定；这符合关闭个性化语义，但会影响依赖稳定用户画像的缓存/模型命中。<br><strong>排查：</strong>关闭个性化后日志里的 uid/cuid/baiduid 可能是 fake_id，不要直接当真实用户标识使用。</div><div class="card-panel"><span class="card-tag">ref lifetime</span><span class="card-tag">logid salt</span><span class="card-tag">VIP mismatch</span><span class="card-tag">fake cuid logs</span></div></div></div></div>

---

## 7. 调试 checklist

```infographic
infographic list-column-done-list
data
  title fake_id 兼容排查 checklist
  desc 遇到关闭个性化请求异常时按顺序确认
  items
    - label 确认请求是否携带 is_close_individual
      desc 只有非 0 才触发入口替换
      done true
    - label 用 logid 搜 NOTICE 替换日志
      desc 关注 old_uid/old_cuid/old_baiduid/fake_id 是否打印
      done true
    - label 检查 fill_basic_data_for_graph 入参
      desc 确认传入的是 run_request 而不是原始 request
      done true
    - label 检查 GraphData uid/cuid/baiduid
      desc 下游 processor 应看到 fake_id
      done false
    - label 检查 GRC Recall 子请求
      desc grc_req = *_request 会继续携带 fake_id
      done false
    - label 检查 VIP/GraphMonitor/日志口径
      desc fake_id 会改变 VIP 命中和按 cuid 检索日志的方式
      done false
```

---

## 8. 证据来源

- `src/service/grg_service.cpp:35-91`：GRG 服务入口主流程；`feed-arch-37111` 在此插入 `run_request` 替换逻辑。
- `src/service/grg_service.cpp:64-89`：graph 选择、controller 获取、fill/run 调用边界。
- `src/service/grg_service.cpp:121-200`：`fill_basic_data_for_graph()` 注入 `Request`、`uid`、`cuid`、`baiduid`、`is_vip_cuid`、`is_debug`。
- `src/service/grg_service.cpp:203-264`：`run()` 执行 graph、Swap response、send_response、日志打印与 release。
- `src/service/grg_service.cpp:281-293`：NOTICE 日志打印 uid/cuid/baiduid 和 graph_name。
- `src/service/grg_service.cpp:326-328`：GraphMonitor 使用 `common_info.cuid()` 初始化。
- `src/service/grg_service.cpp:361-374`：VIP 命中读取 `d_target_cuid`。
- `src/util/util.cpp`：新增 `Util::caculate_fake_id()`，由 `uid/cuid/bid/logid` 计算 fake id。
- `src/util/util.h`：声明 `caculate_fake_id()`。
- `src/process/grc_recall_function.cpp:153-162`：GRG 内部调用 GRC 时从当前 request 拷贝并设置 `is_grg_request`。
- `git show 8ed39c0`：提交 `feed-arch-37111 [Story] 【架构】grg 兼容关闭个性化推荐关闭开关_fakeid 代替 cuid`，涉及 `grg_service.cpp`、`util.cpp`、`util.h`。

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-20T19:23:01.807912
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析：GRG 个性化推荐关闭开关 fake_id 兼容

## 1. 分析摘要

- 这次 `fake_id` 兼容的核心价值在于：**把“关闭个性化推荐”从下游各处分散判断，收敛为入口层统一改写请求身份**。对业务代码库来说，这类改造通常更适合放在服务入口、请求包装层、或者统一上下文注入层，而不是散落到每个召回/排序/日志 processor 中逐个处理。
- 从扫描结果看，`feeda-mv-grg` 已经具备较好的承接基础，且技术笔记中的 `feed-arch-37111` 已在 `src/service/grg_service.cpp`、`src/util/util.cpp`、`src/util/util.h` 落地。`feeda-mv-grc` 暂未发现直接使用 `fake_id` 的代码，但其 `response_for_grg.cpp`、多条 diversity 规则和上下文处理文件，说明它是**很可能被透传影响的下游链路**，迁移潜力主要体现在“请求身份统一改写”和“日志/缓存/特征键口径统一”。

---

## 2. 代码库详情

### `feeda-mv-grg`（序列生成服务）

- 扫描到的相关文件共 10 个，集中在以下模块：
  - `operator/diversity/set2set_short_soft_rule.cpp`
  - `operator/diversity/mcv_recpage_tgi_manju_rule.cpp`
  - `operator/diversity/microvideo_pcs_adjudt_mereg.cpp`
  - `process/microvideo_adjust_merge_get_result_function.cpp`
  - `process/feature_service/doc_feature_with_cache_pipeline.cpp`
- 结合技术笔记可确认，`grg` 侧已经有入口级兼容实现：
  - `src/service/grg_service.cpp`：在 `query()` 入口识别 `is_close_individual()`，复制请求并替换 `uid/cuid/baiduid` 为 `fake_id`
  - `src/util/util.cpp` / `src/util/util.h`：提供 `caculate_fake_id()` 工具函数
- 这意味着 `grg` 的迁移方式不是“新增一套分支逻辑”，而是**沿用已有入口替换模型**，对下游 operator / process 的侵入性较低。
- `std` 使用规模较大，说明该库代码量和工程成熟度都较高：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 这类规模下，引入一个小而稳定的请求改写工具，通常收益高于成本，适合做“少点改、多处复用”的迁移。

### `feeda-mv-grc`（召回汇聚服务）

- 扫描到的相关文件共 6 个，集中在：
  - `processor/video_launch/response_for_grg.cpp`
  - `operator/diversity/rollback_rule.cpp`
  - `operator/diversity/author_vec_diversity_rule.cpp`
  - `processor/vids_gcf_embeddings.cpp`
  - `operator/diversity/scatter_context.cpp`
- 当前未发现 `fake_id` 兼容的直接实现，但从文件职责看，这些位置更像是：
  - 接收 GRG 侧请求结果
  - 做多路召回结果组织
  - 处理 diversity / 回滚 / context 透传
- 因此 `grc` 更适合做**兼容性承接层**，重点关注请求是否已经被匿名化、是否继续以 fake 身份构造子请求、是否把 fake 身份写入缓存键或日志链路。
- `std` 使用规模更大，说明其工程面更广，迁移前需要更强的边界约束：
  - `std::vector`：8442 次，1279 个文件
  - `std::string`：7170 次，1234 个文件
  - `std::unordered_map`：2834 次，639 个文件
- 由于使用面很广，如果要推广 fake_id 兼容，建议只落在**请求入口、跨服务桥接、上下文传播、日志与缓存关键路径**，不要扩散到每个 operator 内部。

---

## 3. 💡 适用性评估与建议

- **`feeda-mv-grg/src/service/grg_service.cpp`：继续作为 fake_id 入口改写的主控点**
  - 建议保持“入口复制请求 + 统一替换 `uid/cuid/baiduid`”的模式，不要把关闭个性化判断下沉到 `operator/diversity/*` 和 `process/*`。
  - 适用场景：`query()`、`fill_basic_data_for_graph()`、`run()` 之间的统一请求流转。
  - 价值：减少重复判断，降低漏改风险，保证下游 processor 无需感知开关细节。

- **`feeda-mv-grg/src/process/feature_service/doc_feature_with_cache_pipeline.cpp`：重点检查缓存键是否依赖真实用户身份**
  - 如果该 pipeline 里使用 `uid/cuid/baiduid` 作为特征缓存 key、请求签名或用户画像定位键，建议明确区分：
    - 正常请求：使用真实身份
    - 关闭个性化请求：使用 fake_id
  - 适用场景：特征缓存、文档特征取值、用户上下文拼装。
  - 价值：避免“入口改写了身份，但缓存仍按真实用户命中”的口径不一致。

- **`feeda-mv-grg/operator/diversity/set2set_short_soft_rule.cpp`、`mcv_recpage_tgi_manju_rule.cpp`、`microvideo_pcs_adjudt_mereg.cpp`：检查是否直接读取 `common_info.uid/cuid` 做用户相关判定**
  - 如果这些 diversity 规则内部有用户维度的偏好、黑白名单、频控、去重逻辑，建议统一改为读取 `run_request` 里的身份字段，而不是旁路拿原始 request。
  - 适用场景：排序去重、偏好控制、召回融合规则。
  - 价值：保证关闭个性化时逻辑一致，避免局部模块绕开入口改写。

- **`feeda-mv-grc/processor/video_launch/response_for_grg.cpp`：作为下游承接 fake_id 的优先改造点**
  - 建议在这个文件里明确区分“来自 GRG 的请求”与普通请求，避免把 fake_id 再反向映射成真实用户身份。
  - 适用场景：响应回填、跨服务结果合并、GRG 专用返回路径。
  - 价值：保证 fake 身份在跨服务链路中持续生效，不被二次转换破坏。

- **`feeda-mv-grc/operator/diversity/rollback_rule.cpp`、`author_vec_diversity_rule.cpp`、`scatter_context.cpp`：统一校验上下文传播口径**
  - 如果这些模块使用 `context`、`vector`、`unordered_map` 等结构保存用户信息，建议约定只保存“已改写后的用户标识”或显式保存 `identity_type` 标记。
  - 适用场景：多路召回调度、回滚策略、作者向量多样性。
  - 价值：减少“部分字段是真实身份、部分字段是假身份”的混乱状态。

---

## 4. ⚠️ 引入风险与限制

- **fake_id 带有 `logid` 参与计算，天然不稳定**
  - 同一用户跨请求生成的 fake_id 可能不同，这非常适合关闭个性化场景，但会影响依赖“稳定用户画像”的缓存、召回命中率和历史行为聚合。
  - 需要明确：这是“匿名化/伪身份化”，不是“稳定用户 ID 替换”。

- **日志和监控口径会变化**
  - 一旦入口改写，`NOTICE` 日志、`GraphMonitor`、VIP 命中判断可能都看到 fake_id 而不是真实 `uid/cuid/baiduid`。
  - 排障时不能把 fake_id 直接当真实用户身份使用，建议保留 old id 与 fake_id 的对应关系日志，但注意访问权限和隐私边界。

- **`grg_service.cpp` 中存在引用生命周期依赖**
  - 当前模式依赖 `run_request` 指向局部 `grg_request`，之所以安全，是因为流程看起来是同步闭环。
  - 如果未来 `run()`、`fill_basic_data_for_graph()` 或子调用改成真正异步，需要重新审查 `ref()` 注入和栈上对象生命周期。

- **下游模块容易“绕过入口改写”**
  - `feeda-mv-grg` 与 `feeda-mv-grc` 都有较多 operator / process 文件，如果某些模块直接从别的上下文拿真实用户字段，就可能破坏 fake_id 兼容。
  - 建议统一约束：**所有个性化相关模块只信任入口传入的 run_request**，不要重复读取原始 request。

---

如果你需要，我可以继续把这份内容整理成你技术笔记里可直接粘贴的章节样式，或者补一版“适配优先级矩阵（高/中/低）”。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
