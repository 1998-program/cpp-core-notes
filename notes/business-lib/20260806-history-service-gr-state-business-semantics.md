# 2026-08-06 周四代码理解：`gr_state` 业务语义与展现状态边界

> 日期：2026-08-06  
> 主题来源：2026-08-06 daily-plan 缺失，按历史未覆盖主题 fallback 到 `gr_state` 业务语义与展现边界  
> 服务：`feeda-dc-gr`  
> 范围：分析 `ShowStateInfo` / `CardShowStateInfo` 的状态枚举、历史传递逻辑与业务过滤条件；本文不展开 History Service 存储实现。  
> 内网文档：当前环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px;min-height:150px}.arch-col h3{margin:0 0 8px;font-size:14px;color:#102a43}.arch-box{background:#f1f5f9;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;margin:8px 0;font-size:13px;line-height:1.45}.arch-box strong{display:block;color:#102a43;margin-bottom:2px}.arch-note{font-size:12px;color:#52606d;line-height:1.5;margin-top:8px}.arch-arrow{font-size:12px;color:#64748b;text-align:center;margin:4px 0}</style><div class="arch-wrap"><div class="arch-col"><h3>输入侧</h3><div class="arch-box"><strong>历史上下文</strong><span>`_news_showstate` / `_video_showstate` 里保存已下发、未展现、已展现、已点击等状态。</span></div><div class="arch-box"><strong>请求条件</strong><span>`channel_id`、实验开关、`_feed_user_beha_obtained`、`show_num`、`refresh_state` 等共同决定是否透传。</span></div><div class="arch-note">这里的重点不是“展示什么”，而是“某条历史是否应该继续影响当前请求”。</div></div><div class="arch-col"><h3>状态语义</h3><div class="arch-box"><strong>ShowStateInfo</strong><span>`NO_ACTUAL_ISSUED / ISSUED / UNSHOW / SHOWED / CLICKED` 描述下发到点击的完整演进。</span></div><div class="arch-box"><strong>CardShowStateInfo</strong><span>敏捷卡也复用同样的状态层次，`show_tss` / `click_tss` 只是时间戳附属字段。</span></div><div class="arch-note">状态枚举是业务边界的核心，不要把它误读成纯存储字段。</div></div><div class="arch-col"><h3>输出侧</h3><div class="arch-box"><strong>历史传递过滤</strong><span>命中传递实验时，ISSUED 且过期的条目会被过滤掉。</span></div><div class="arch-box"><strong>最终下发</strong><span>未点击、未命中历史、且满足 refresh / 场景门槛的条目才保留下来。</span></div><div class="arch-note">这条链路是“业务决定状态可见性”，不是“状态决定业务行为”。</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "GRSessionContext" as Ctx
participant "NewsHistory" as NH
participant "ShowStateInfo" as SSI
participant "NewsMessage" as Msg
Ctx -> NH : get_shown_pb()
NH -> SSI : lookup nid in _news_showstate
NH -> NH : check experiment / channel / refresh_state
NH -> SSI : compare get_state()
alt ISSUED and expired
  NH -> NH : filter item
else SHOWED or CLICKED
  NH -> Msg : keep or attach shown pb
end
NH -> Msg : append output state
@enduml
```

## 2. 业务状态信息图

infographic compare-hierarchy-left-right-circle-node-pill-badge
 data
  title `ShowStateInfo` 状态与业务动作
  desc 同一套状态枚举贯穿下发、展现、点击和未实际下发分支，避免业务判断散落在多个 if 分支里
  items
    - label `NO_ACTUAL_ISSUED`
      desc 竞品替换 / 图片去重等场景下的未实际下发 nid
      icon mdi/close-circle
      children
        - label 不参与正常展现链路
          desc 只作为边界标记存在
    - label `ISSUED`
      desc 已下发但可能尚未展现
      icon mdi/send
      children
        - label 过期后可被历史传递逻辑过滤
          desc `ISSUED` + 过期窗口触发过滤
    - label `UNSHOW`
      desc 已下发但未展现
      icon mdi/timer-sand
      children
        - label 仍是后续去重 / 保留判断的输入
          desc 不等于无效状态
    - label `SHOWED`
      desc 已展现
      icon mdi/eye
    - label `CLICKED`
      desc 已点击
      icon mdi/cursor-default-click
 theme
  palette #3d5a80 #2d6a4f #64748b

## 3. 关键结论

<div style="display:grid;grid-template-columns:1.15fr 1fr;gap:12px;margin:16px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-left:4px solid #3d5a80;border-radius:10px;padding:14px"><div style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:#486581;margin-bottom:6px">结论</div><div style="font-size:14px;line-height:1.7;color:#1f2937">`NewsHistory::get_shown_pb()` 通过实验开关、频道、行为缓存和状态枚举共同决定历史是否继续参与当前下发。`ShowStateInfo` 的状态不是摆设，它直接控制历史传递、去重和展示保留的边界。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-left:4px solid #2d6a4f;border-radius:10px;padding:14px"><div style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:#486581;margin-bottom:6px">内网文档</div><div style="font-size:14px;line-height:1.7;color:#1f2937">当前环境没有可用 KU 文档 URL/doc-id，历史背景与指标口径需要人工补充。</div></div></div>

## 4. Pitfalls

<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px"><div style="font-size:13px;font-weight:700;color:#102a43;margin-bottom:6px">状态误读</div><div style="font-size:14px;line-height:1.7;color:#243b53">`ISSUED` 不等于“已经成功展现”；它只是下发态。真正的展现、点击要看后续状态和时间戳。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px"><div style="font-size:13px;font-weight:700;color:#102a43;margin-bottom:6px">场景误配</div><div style="font-size:14px;line-height:1.7;color:#243b53">`TAB_RECOMMENDATION`、实验命中和 `show_num` 门槛一起生效，单看某一个条件会误判历史是否该透传。</div></div></div>

## 5. 调试 Checklist

infographic sequence-snake-steps-simple
 data
  title `gr_state` 业务排查步骤
  items
    - label 检查实验开关
      desc 确认历史传递实验是否命中。
      done true
    - label 检查频道条件
      desc 重点看 `TAB_RECOMMENDATION` 等入口条件。
      done true
    - label 检查状态枚举
      desc 确认 `ISSUED` / `UNSHOW` / `SHOWED` / `CLICKED` 的解释一致。
      done true
    - label 检查时间窗
      desc `FLAGS_issue_expire_time` 会影响 ISSUED 过滤分支。
      done true
    - label 检查行为缓存
      desc `_news_showstate` 为空时，过滤条件会短路。
      done true

## 证据来源

- `feeda-dc-gr/history/news_history.cc:139-158`
- `feeda-dc-gr/history/show_dup.h:55-134`
- `feeda-dc-gr/history/news_history.cc:150-170`
- `feeda-dc-gr/history/news_history.cc:241-246`

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-08T19:02:06.240527
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 这次分析的 `gr_state` 主题，本质上是把 **下发态 / 未展现 / 已展现 / 已点击** 这套业务状态边界，稳定地嵌入到业务链路中，避免状态语义散落在多个 if 分支里。
- 从扫描结果看，两个业务代码库都已经有较重的容器化数据流：`std::vector`、`std::string`、`std::unordered_map` 使用非常广泛，说明它们天然适合承载“状态枚举 + 历史过滤 + 场景条件”的增量改造。  
  - `feeda-mv-grg` 目前只发现 1 个相关文件，适合做 **小范围试点**。
  - `feeda-mv-grc` 发现 10 个相关文件，说明它更适合做 **集中抽象、统一接入**，迁移收益也更高。

---

## 2. 代码库详情

### `feeda-mv-grg`（序列生成服务）

- 已发现目标库使用：`1` 个文件
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 现有 std 等价物使用规模：
  - `std::vector`：`1969` 次，分布在 `356` 个文件
  - `std::string`：`2443` 次，分布在 `425` 个文件
  - `std::unordered_map`：`734` 次，分布在 `205` 个文件
- 代码特征判断：
  - 这个库的核心是候选集处理、排序和多样性控制，和 `ShowStateInfo` 这种“是否继续参与当前链路”的状态控制天然相邻。
  - `model/model.h`、`model/paddle_model.h` 这类接口大量使用 `std::vector<RidTmpInfoPtr>` 传参，说明候选对象本身就是状态扩展的合适载体。
- 可作为参考的已有代码风格：
  - `model/model.h`
  - `model/paddle_model.h`
- 迁移潜力判断：
  - 适合在 `low_clarity_diversity_rule.cpp` 里先做 **状态过滤试点**，例如跳过 `NO_ACTUAL_ISSUED`、对 `SHOWED / CLICKED` 做去重或降权。
  - 由于相关文件较少，适合先验证状态语义，不建议一开始做全库抽象。

### `feeda-mv-grc`（召回汇聚服务）

- 已发现目标库使用：`10` 个文件
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/new_adjust/precise_score_init.cpp`
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
- 现有 std 等价物使用规模：
  - `std::vector`：`8520` 次，分布在 `1290` 个文件
  - `std::string`：`7267` 次，分布在 `1247` 个文件
  - `std::unordered_map`：`2860` 次，分布在 `646` 个文件
- 代码特征判断：
  - 这是一个高覆盖、强流程型代码库，状态判断往往分散在多个 processor / operator / adjuster 中。
  - 更适合把 `gr_state` 的业务边界抽成 **统一的状态判定工具或过滤器**，避免每个处理环节重复实现“历史是否透传”“是否过期”“是否已展示”等逻辑。
- 可作为参考的已有代码风格：
  - `service/grc_http_service.cpp` 中大量使用 `std::unordered_map<std::string, std::vector<int>>`、`std::set`、`std::vector<std::string>`，适合作为容器组织方式参考。
- 迁移潜力判断：
  - 由于文件分布广，适合做 **平台级统一接入**。
  - 如果直接在各个 `processor/*`、`operator/*` 中散点修改，后续维护成本会很高。

---

## 3. 💡 适用性评估与建议

- **建议 1：在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 先做状态过滤试点**
  - 场景：多样性规则通常在最终候选集上做二次裁剪，非常适合接入 `ShowStateInfo`。
  - 建议：
    - 对 `NO_ACTUAL_ISSUED` 直接跳过；
    - 对 `ISSUED` 结合过期窗口判断是否继续保留；
    - 对 `SHOWED / CLICKED` 作为去重或降权输入。
  - 价值：改动面小，便于验证“状态边界是否真的影响最终排序质量”。

- **建议 2：在 `feeda-mv-grc/processor/filter/user_explore_interest_ugc_filter_operator.cc` 提前统一历史状态判断**
  - 场景：过滤器层最适合做“是否进入后续链路”的第一道闸门。
  - 建议：
    - 把 `channel_id`、实验开关、`refresh_state`、历史缓存是否为空等条件收敛成一个公共判断函数；
    - 将 `ISSUED + expired` 的过滤逻辑前置，避免后续 processor 白算。
  - 价值：减少后续链路无效计算，性能收益通常比在后段过滤更稳定。

- **建议 3：在 `feeda-mv-grc/operator/adjuster/sketchy/duanju_adjuster.cpp` 和 `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp` 中，避免把状态语义混入模型特征**
  - 场景：adjuster 层往往同时承担打分和业务修正，容易把“状态”当成“特征”。
  - 建议：
    - `NO_ACTUAL_ISSUED` 这类边界状态不要直接进入模型输入；
    - `SHOWED / CLICKED` 只作为业务约束，不要污染训练/推理特征空间；
    - 状态判断应在进入调整逻辑前完成。
  - 价值：降低状态语义漂移风险，避免线上规则和离线训练口径不一致。

- **建议 4：在 `feeda-mv-grc/processor/new_adjust/precise_score_init.cpp` 做统一的状态初始化字段接入**
  - 场景：该文件名显示它可能是调整链路的初始化入口，适合作为状态信息的标准化入口。
  - 建议：
    - 统一补齐 `show_tss / click_tss / state` 等字段；
    - 若历史信息缺失，明确降级策略，而不是默默当作 `UNSHOW`。
  - 价值：后续所有 adjuster 都能依赖一致的状态结构，减少分支爆炸。

- **建议 5：抽一个公共 helper，供 `feeda-mv-grc` 的多个 processor/operator 共用**
  - 建议落点：
    - 新增类似 `history_state_helper.h/.cc` 的工具层；
    - 封装 `is_expired_issued()`、`should_pass_history()`、`has_valid_show_state()` 等函数。
  - 价值：
    - 你现在已经有 `10` 个相关文件，继续分散复制逻辑会很快失控；
    - 公共 helper 更适合承接 `ShowStateInfo` / `CardShowStateInfo` 的统一语义。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：状态语义误读**
  - `ISSUED` 不是“已展现”，只是“已下发”。
  - 如果直接把它当作展示成功，会导致历史过滤、去重、点击归因全部偏移。

- **风险 2：实验开关与场景条件耦合**
  - 这套逻辑不是单一状态判断，而是 `channel_id`、实验开关、`show_num`、`refresh_state`、行为缓存共同作用。
  - 只改某一个文件、某一个 if 分支，容易造成线上口径不一致。

- **风险 3：历史缓存为空时的短路行为**
  - `_news_showstate` 为空时，很多过滤条件会直接短路。
  - 如果迁移时没有保留这个边界，可能把“无历史”误判成“全部通过”。

- **风险 4：大范围改造的回归成本高**
  - `feeda-mv-grc` 文件分布很广，涉及多个 processor / operator。
  - 如果不先抽公共 helper，后续每加一个新状态都要改很多地方，回归压力会很大。

---

如果你愿意，我可以继续把这份内容整理成你学习笔记里可直接粘贴的 **“业务代码库适配分析”标准模板**，或者进一步补一版 **“按 grg / grc 分别给出落地改造路径图”**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
