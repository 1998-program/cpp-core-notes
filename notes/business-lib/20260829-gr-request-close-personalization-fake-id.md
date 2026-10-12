# 2026-08-29 周六代码理解：GR 请求解析中的关闭个性化与 fake_id 注入边界

> 日期：2026-08-29  
> 主题来源：当前没有可用的当日 daily-plan，回退到 `notes/weekly-topic-selection/daily-plan-20260529.json` 中的 fake_id / 关闭个性化候选主题；KU/业务背景需人工补充。  
> 范围：`src/processor/request.cpp` 的请求字段解析、`is_close_individual` 覆盖、`fake_user.fake_cuid` 写入、`from_scene` 分支与 `SceneInfo` 地理字段回填。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求字段层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`request_doc` → `CommonInfo`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">从 JSON 中解析 `is_close_individual`、`cmode`、`pd`、`from_scene`、定位字段和实验开关。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">隐私兼容层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`is_only_browse` → `is_close_individual` → `fake_id`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">只浏览模式会强制关闭个性化；非零时计算 fake_id 并写入 fake_user。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">场景信息层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`SceneInfo` reset + sema 合并</div><div style="margin-top:8px;font-size:12px;color:#52606d;">定位字段会同步写回 `sema`，避免后续 processor 从两个容器读到不同值。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">解析请求字段</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">判定关闭个性化</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;padding:10px;text-align:center;color:#a16207;">计算 fake_id</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">SIA + SceneInfo 输出</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title request.cpp close personalization and fake_id boundary
actor "GR Request" as REQ
participant "RequestProcessor" as P
participant "CommonInfo" as C
participant "SidInfo / abtest" as SID
participant "FakeUser" as F
participant "SceneInfo" as SC
participant "SIA" as SIA
REQ -> P : parse request_doc
P -> SID : hit_abtest(zero_to_n_recall_switch)
P -> C : read pd / outside_invoke_type / autoplay
P -> C : read is_close_individual
P -> C : derive is_only_browse from cmode
alt is_only_browse
  P -> C : force is_close_individual = 1
end
alt is_close_individual != 0
  P -> C : Util::caculate_fake_id(uid,cuid,baiduid,logid)
  P -> F : fake_cuid = common_info.fake_id
end
P -> SIA : add is_close_individual / fake_id
P -> C : read from_scene
P -> SC : reset and copy sema
P -> SC : merge loc_province / loc_city / loc_district
P --> REQ : CommonInfo + FakeUser + SceneInfo ready
@enduml
```

## 2. 字段结构信息图
```infographic
infographic list-grid-badge-card
data
  title request.cpp 里的关闭个性化关键字段
  desc 这些字段共同决定下游看到真实用户标识、fake_id，还是场景化定位信息
  items
    - label is_close_individual
      desc `src/processor/request.cpp:302-304` 从请求 JSON 直接读取，是显式关闭个性化入口
      icon mdi/account-cancel
    - label is_only_browse
      desc `src/processor/request.cpp:348-358` 可由 `is_only_browse` 或 `cmode` 派生，并强制关闭个性化
      icon mdi/eye-off-outline
    - label fake_id
      desc `src/processor/request.cpp:361-365` 非零关闭个性化时由 uid/cuid/baiduid/logid 计算
      icon mdi/incognito
    - label fake_cuid
      desc `fake_user->fake_cuid` 承接 fake_id，给下游使用兼容标识
      icon mdi/account-arrow-right
    - label from_scene
      desc `src/processor/request.cpp:712-716` 识别 `feed_after_video`，影响后续 after-read 推荐分支
      icon mdi/movie-open-play
    - label SceneInfo
      desc `src/processor/request.cpp:974-1005` reset 后复制 sema，并把定位字段同步写回 sema
      icon mdi/map-marker-radius
```

## 3. 业务链路拆解
### 3.1 显式请求字段不是唯一入口
- `src/processor/request.cpp:302-304`：如果请求 JSON 中带 `is_close_individual`，直接写入 `common_info->is_close_individual`。
- `src/processor/request.cpp:348-355`：`is_only_browse` 既能从同名字段读取，也能从 `cmode` 第一段是否为 `2` 推导。这个推导发生在 fake_id 计算前。
- `src/processor/request.cpp:357-358`：一旦 `is_only_browse` 为真，会强制把 `is_close_individual` 置为 1。排查时不能只看原始请求是否显式带了关闭个性化字段。

### 3.2 fake_id 是下游兼容标识，不只是日志字段
- `src/processor/request.cpp:361-365`：当 `is_close_individual != 0` 时，代码调用 `Util::caculate_fake_id()`，输入包含 `uid`、`cuid`、`baiduid` 和 logid，然后把结果写到 `common_info->fake_id` 与 `fake_user->fake_cuid`。
- `src/processor/request.cpp:367-369`：同一段落立即写 SIA 指标 `is_close_individual`、`doublecolumn_status` 和 `fake_id`，说明这里也是定位线上请求行为的观测点。

### 3.3 场景字段与定位字段在同一个请求 processor 中收束
- `src/processor/request.cpp:712-716`：`from_scene == "feed_after_video"` 会打开 `is_recommend_after_read`，这是和 fake_id 相邻但语义不同的场景分支。
- `src/processor/request.cpp:974-980`：`SceneInfo` 每次先 reset，再把 `sema` 对象复制进场景信息，避免复用对象残留。
- `src/processor/request.cpp:981-1004`：`loc_province`、`loc_city`、`loc_district` 会同时写入 `SceneInfo` 字段和 `sema` 对象。后续从结构化字段或 sema 读取定位时，应该得到一致值。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">关闭个性化不是一个字段的开关，而是一段请求归一化链</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">如果只检查请求里的 `is_close_individual`，会漏掉 `cmode` 推导出的 `is_only_browse`。线上出现 fake_id 时，先同时打印原始字段、cmode、is_only_browse 和最终 common_info 值。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`fake_user->fake_cuid` 是下游兼容边界。排查“个性化仍生效”时，要确认下游 processor 读的是真实 cuid、fake_cuid，还是 common_info.fake_id。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 判断顺序：显式字段 → cmode 派生 → 强制关闭 → fake_id 写入 → 下游读取点</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title fake_id 与关闭个性化排查清单
  desc 适用于关闭个性化不生效、fake_id 缺失、下游仍用真实 cuid、定位字段不一致
  items
    - label 打印原始请求字段
      desc 同时看 `is_close_individual`、`is_only_browse`、`cmode`，不要只看一个字段
      done true
    - label 检查派生覆盖
      desc `cmode` 第一段为 2 会把 `is_only_browse` 置真，并强制关闭个性化
      done true
    - label 验证 fake_id 写入
      desc 关闭个性化后检查 `common_info->fake_id` 与 `fake_user->fake_cuid` 是否同时有值
      done true
    - label 对齐 SIA
      desc 用 SIA 中的 `is_close_individual` 和 `fake_id` 确认线上真实路径
      done true
    - label 检查下游读取点
      desc 确认 rank、recall、UFS 或特征服务读取的是 fake_cuid 还是真实 cuid
      done true
    - label 检查 SceneInfo 同步
      desc 定位字段需要同时存在于 SceneInfo 字段和 sema，避免条件图读到旧值
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/request.cpp:279-369`
- `src/processor/request.cpp:712-716`
- `src/processor/request.cpp:974-1005`
- `notes/business-lib/20260819-grg-close-personalization-fake-id-boundary.md`

## 7. 说明
当前运行环境未发现 2026-08-29 的 daily-plan 文件，也没有读取 KU 正文；本笔记使用本地代码包与历史候选主题回退生成，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-01T19:19:54.017178
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 分析摘要

- 从技术笔记看，这套能力的本质不是单一字段判断，而是**请求归一化链**：`is_close_individual`、`cmode` 派生的 `is_only_browse`、`fake_id`/`fake_cuid` 注入、以及 `from_scene` 和 `SceneInfo` 的同步回填。
- 结合扫描结果，**feeda-mv-grc（召回汇聚服务）更适合优先落地**：它已经在多个过滤/调整链路里接触用户行为、兴趣和分数初始化，适合接入“关闭个性化”标志做统一下发。**feeda-mv-grg（序列生成服务）目前只有 1 个相关文件命中**，说明直接适配面较小，更像是按需接入。
- 迁移潜力总体偏高，但重点不在“大面积改造”，而在**在入口统一解析，在下游统一消费**，避免多个模块各自判断真实 uid / fake uid / browse-only 状态。

## 代码库详情

- ### feeda-mv-grg
  - 扫描到的目标相关文件仅 1 个：`strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 这说明 GRG 侧目前对“关闭个性化 / fake_id”没有形成明显的通用入口，更多可能是某个策略点在消费用户特征。
  - 现有 STL 使用规模很大：
    - `std::vector`：1969 次，356 个文件
    - `std::string`：2443 次，425 个文件
    - `std::unordered_map`：734 次，205 个文件
  - 结论：GRG 侧如果引入该能力，建议从**单一策略点或上下文对象**切入，而不是改动整条链路。

- ### feeda-mv-grc
  - 扫描到的目标相关文件共 10 个，已确认的代表文件包括：
    - `service/grc_http_service.cpp`
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
    - `processor/new_adjust/precise_score_init.cpp`
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`
    - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - 这类文件分布在**HTTP 服务入口、过滤器、分数初始化、调整器**多个层面，说明 GRC 侧更容易承接“请求字段归一化 + 下游统一消费”的模式。
  - 现有 STL 使用规模也很大：
    - `std::vector`：8520 次，1290 个文件
    - `std::string`：7267 次，1247 个文件
    - `std::unordered_map`：2860 次，646 个文件
  - 结论：GRC 侧已有较多业务处理点，适合将 `is_close_individual`、`fake_id`、`from_scene` 作为**上下文字段**向下游透传。

## 💡 适用性评估与建议

- **建议 1：先在入口统一做请求归一化**
  - 文件建议：`service/grc_http_service.cpp`
  - 做法：参考笔记里的 `request.cpp` 逻辑，在 HTTP 入口统一解析 `is_close_individual`、`is_only_browse`、`cmode`、`from_scene`，并生成统一的 request context。
  - 价值：避免 `processor/filter/*`、`operator/adjuster/*` 各自重复判断，减少“只看原始字段导致漏判”的问题。

- **建议 2：在过滤链路里优先屏蔽个性化依赖**
  - 文件建议：`processor/filter/low_agile_goodrate_filter_operator.cc`、`processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - 做法：当 `is_close_individual != 0` 或 browse-only 成立时，过滤器不要再依赖真实用户画像、历史偏好或个性化特征。
  - 价值：这两个文件本身就属于用户兴趣/好率过滤位置，最适合作为关闭个性化的第一落点。

- **建议 3：在分数初始化阶段切换“脱敏用户标识”**
  - 文件建议：`processor/new_adjust/precise_score_init.cpp`
  - 做法：如果当前链路会初始化用户相关特征，建议引入 `fake_id` / `fake_cuid` 概念，或者至少使用统一的“匿名用户 key”。
  - 价值：避免后续 adjuster 仍然拿真实 cuid 做特征拼接，导致关闭个性化失效。

- **建议 4：在调整器中显式支持“只浏览模式”**
  - 文件建议：`operator/adjuster/sketchy/duanju_adjuster.cpp`、`operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - 做法：对依赖长期用户价值、历史行为强相关的因子，增加 `is_close_individual` 分支；browse-only 时使用弱个性化或非个性化系数。
  - 价值：这两个文件从命名上看都属于强策略调整位，适合做最终行为收敛。

- **建议 5：GRG 侧只保留最小必要接入**
  - 文件建议：`strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 做法：如果 GRG 侧也需要支持关闭个性化，优先在该规则内读取统一上下文，控制多样性/个性化权重，而不是改动生成主流程。
  - 价值：GRG 当前只命中 1 个相关文件，说明适合“小切口接入”，降低迁移成本。

## ⚠️ 引入风险与限制

- **风险 1：字段只改入口，不改下游，会出现“半生效”**
  - 如果 `service/grc_http_service.cpp` 解析了 `is_close_individual`，但 `processor/filter/*` 仍读真实用户标识，就会出现部分模块按匿名、部分模块按实名的混乱状态。

- **风险 2：fake_id / fake_cuid 的稳定性要统一**
  - 笔记里的逻辑依赖 `uid`、`cuid`、`baiduid`、`logid` 组合生成 fake_id。
  - 如果不同服务或不同线程使用不同生成规则，会导致同一请求在多个模块里“身份不一致”，影响召回、排序和实验统计。

- **风险 3：默认值和 reset 逻辑容易漏配**
  - 参考笔记中的 `SceneInfo reset + sema 回填` 思路，新增上下文对象时必须明确初始化。
  - 否则旧请求残留字段可能污染当前请求，尤其是在长生命周期对象或对象池场景中。

- **风险 4：实验与观测口径会变化**
  - 一旦 `is_close_individual` 生效，线上 A/B 的分组、CTR、停留时长等指标可能被重置或偏移。
  - 建议在日志和埋点里同时记录：原始字段、归一化结果、是否进入 fake_id 分支，便于排查。

- **风险 5：GRG 侧收益有限，别做过度改造**
  - GRG 当前只有 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 命中，说明大范围迁移的 ROI 不高。
  - 更合适的做法是先在 GRC 落地统一上下文，再按需把同样的上下文字段透传到 GRG。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
