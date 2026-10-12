# 2026-08-28 周五代码理解：GRC fill_meta 的 GCMS 场景路由与小流量筛选

> 日期：2026-08-28  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的历史候选项；本次没有独立的当日 daily-plan 可直接读取，KU/业务背景需人工补充。  
> 范围：只看 `src/processor/fill_meta.cpp` 中的 `FillMetaBaseProcessor::setup()`、`process()`、`small_flow_list`、`GCMS_SECNE_RECALL`、`card_no_gcms_mark` 和 `RidTmpInfo` 填充路径，不展开其它策略树。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:14px;background:#f8fafc;line-height:1.45;">
  <div style="display:grid;grid-template-columns:1.1fr 1.18fr 1fr;gap:12px;align-items:stretch;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">上下文装配</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`FillMetaBaseProcessor::setup()`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">把匿名依赖、命名依赖和输出占位装进 `FillMetaBaseContext`。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">场景路由</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`small_flow_list` + `GCMS_SECNE_RECALL`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">把作者等级小流量、UA/flow_loc 条件和 GCMS 查询场景拼成一次请求策略。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">结果落点</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`gcms_common_pb_meta_map` / `RidTmpInfo`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">查回来的通用元数据再挂回到各个 rid，决定后续 fill 阶段能拿到什么字段。</div>
    </div>
  </div>
  <div style="margin-top:12px;display:grid;grid-template-columns:1fr 80px 1fr 80px 1fr 80px 1fr;gap:10px;align-items:center;">
    <div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#4338ca;">匿名依赖收集</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">GCMS 请求准备</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;padding:10px;text-align:center;color:#a16207;">字段回填</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">fill meta 输出</div>
  </div>
</div>

## 1. 核心流程
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "FillMetaBaseProcessor" as P
participant "FillMetaBaseContext" as C
participant "GcmsComponent" as G
participant "GCMS query_common()" as Q
participant "RidTmpInfo / GcmsData" as R
Caller -> P : setup(vertex, context)
P -> C : collect dependencies / config
Caller -> P : process(context)
P -> P : build small_flow_list
P -> G : get_instance()
P -> Q : query_common(merge_rids,...,GCMS_SECNE_RECALL)
Q --> P : gcms_common_pb_meta_map
P -> R : attach GcmsData to RidTmpInfo
P --> Caller : fill meta result
@enduml
```

## 2. 场景路由拆解
```infographic
sequence-ascending-steps
  data
    title fill_meta 场景路由
    desc 从依赖收集到 GCMS 查询，再到 rid 回填，主线分成四段
    items
      - label 上下文装配
        desc `setup()` 绑定 handle_name、匿名依赖和命名依赖
      - label 小流量筛选
        desc 通过 `autor_level_sm` 和 abtest 结果生成 `small_flow_list`
      - label GCMS 查询
        desc 用 `ua`、`flow_loc` 和场景常量发起 `query_common()`
      - label 元数据回填
        desc 把 `gcms_common_pb_meta_map` 里的结果贴回 `RidTmpInfo`
```

## 3. 关键判断
- `setup()` 的职责是把依赖关系和上下文对象准备好，不是做真正的业务筛选。
- `small_flow_list` 说明这个链路不是简单“查全量再过滤”，而是先按作者等级和 abtest 生成窄流量集合。
- `GCMS_SECNE_RECALL` 把这条查询明确落在召回场景，场景常量本身就是业务语义的一部分。
- `card_no_gcms_mark` 告诉我们并不是所有 rid 都走同一条回填路由，存在跳过正排的显式分支。

## 4. 结构卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:4px solid #0ea5e9;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#0369a1;text-transform:uppercase;letter-spacing:.04em;">关键结论</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">fill_meta 的关键不是“有没有拿到 GCMS 数据”，而是“哪些 rid 进入查询、哪些场景被降到小流量、哪些字段允许跳过正排”。真正的业务边界就在这三个路口。</div>
</div>

<div style="height:10px"></div>

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fefce8;border:1px solid #fde68a;border-left:4px solid #eab308;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#a16207;text-transform:uppercase;letter-spacing:.04em;">调试提示</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">如果某些 rid 的 meta 缺失，不要先怀疑 `query_common()` 一定失败，先拆开看是小流量名单没命中、`card_no_gcms_mark` 跳过了，还是 `gcms_common_pb_meta_map` 里本来就没有该键。</div>
</div>

## 5. Pitfalls
- `small_flow_list` 依赖手工维护的作者等级集合，配置变化会直接改变查询集合，不是纯代码逻辑。
- `merge_rids` 与 `gcms_common_pb_meta_map` 是两个不同阶段的数据容器，混用它们会让问题定位失真。
- `RidTmpInfo` 既承载业务字段又承载 GCMS 回填结果，生命周期不清时很容易把临时状态带到下一段处理。
- `open_gcms_statistics` 只影响统计和日志，不代表主链路正确，别把观测分支当成业务成功条件。

## 6. 调试 Checklist
```infographic
list-column-done-list
  data
    title fill_meta 调试清单
    items
      - label 检查 setup 依赖
        desc 确认匿名依赖和命名依赖都已绑定
        done true
      - label 检查小流量名单
        desc 确认 `small_flow_list` 按预期命中
        done true
      - label 检查场景常量
        desc 确认 `GCMS_SECNE_RECALL` 没写错或传错场景
        done true
      - label 检查回填键
        desc 确认 `gcms_common_pb_meta_map` 的 key 和 rid 对齐
        done true
```

## 7. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/fill_meta.cpp:126-255`
- `src/processor/fill_meta.cpp:268-352`
- `notes/business-lib/20260825-grc-fill-meta-request-response-boundary.md`

> 说明：当前运行环境没有独立的当日 daily-plan，可用历史候选项回退；KU/业务背景需人工补充。
---

## 七、业务代码库适配分析
> **分析时间**：2026-09-01T19:16:42.912407
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 1. 分析摘要

- 从本次扫描看，这类“上下文装配 + 场景路由 + 小流量筛选 + 元数据回填”的业务模式，在两个代码库里的落点都不算多，但分布特征很明确：`feeda-mv-grg` 只有 1 个相关文件，`feeda-mv-grc` 有 10 个相关文件，且更贴近“召回汇聚/过滤/调度”链路。  
- 结合技术笔记里的 `setup()`、`small_flow_list`、`GCMS_SECNE_RECALL`、`card_no_gcms_mark`、`RidTmpInfo` 这些关键点来看，**最适合迁移的不是整条链路复制，而是“路由判定 + 候选集筛选 + 结果回填”的局部能力**。  
- 从迁移潜力上看：`feeda-mv-grc` 更适合作为主落地库，`feeda-mv-grg` 更适合做轻量试点或复用规则层能力。两个库里 `std::vector` / `std::string` / `std::unordered_map` 的使用都很广，说明基础容器生态成熟，做“map 化回填”和“候选集分流”改造的阻力不大。

## 2. 代码库详情

### feeda-mv-grg（序列生成服务）

- 本次仅发现 1 个与目标主题相关的文件：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 这说明在 grg 中，相关能力更偏“规则式分流/多样性控制”，而不是完整的召回回填链路。
- 现有 std 容器使用很普遍，迁移基础较好：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 可参考的典型写法：
  - `model/model.h`
  - `model/paddle_model.h`
- 结论：
  - grg 更适合承接“**轻量规则判断、候选分层、局部标记**”这类改造；
  - 不太像 fill_meta 那种“完整的场景回填中枢”。

### feeda-mv-grc（召回汇聚服务）

- 本次发现 10 个相关文件，说明这里更接近 fill_meta 的业务语义：
  - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
- 这些文件覆盖了“调度、场景生成、首刷初始化、过滤、局部优化”等多个环节，和 `setup()` → `process()` → 回填的链路较接近。
- std 容器使用规模更大，说明 GRC 已有较强的通用数据结构承载能力：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 可参考的典型写法：
  - `service/grc_http_service.cpp`
- 结论：
  - grc 是更合适的主迁移目标；
  - 这里更容易落地“按场景分流 + 结果 map 回填 + 可观测性增强”的模式。

## 3. 💡 适用性评估与建议

- **优先在 `feeda-mv-grc/processor/new_adjust/precise_score_init_first_refresh.cpp` 做场景化回填改造。**  
  这个文件从命名上就接近“首刷/初始化”阶段，适合引入类似 `card_no_gcms_mark` 的跳过标记和 `RidTmpInfo` 的回填结构，把“是否进入后续处理”与“是否有可回填数据”分开。

- **在 `feeda-mv-grc/processor/multi_factor/ltr_factor_gen_scene.cpp` 统一场景常量与小流量路由。**  
  可参考 `GCMS_SECNE_RECALL` 的做法，把场景常量显式化，避免把召回、过滤、调度逻辑散落在多个 if 分支里；同时将 `small_flow_list` 的筛选前置，减少无效计算。

- **在 `feeda-mv-grc/processor/filter/user_explore_interest_ugc_filter_operator.cc` 增加“候选级元数据”承载。**  
  可以借鉴 `gcms_common_pb_meta_map` 的思路，把过滤原因、命中场景、跳过原因等信息挂到一个 `std::unordered_map<rid, meta>` 上，便于后续 debug 和链路追踪。

- **在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 做轻量试点，而不是直接做全链路迁移。**  
  该文件更像规则层入口，适合试验“先筛小流量、再做规则决策”的思路；如果效果稳定，再把“路由 + 回填”模式扩展到更高层流程。

- **参考现有 std 容器写法，优先用现成容器承接迁移，不要新造数据结构。**  
  例如 `model/model.h`、`model/paddle_model.h`、`service/grc_http_service.cpp` 中已经大量使用 `std::vector`、`std::unordered_map`，说明现有代码风格对标准容器兼容良好，适合直接用来承载候选集、场景集和回填结果。

## 4. ⚠️ 引入风险与限制

- **场景常量和分流条件容易出错。**  
  如果把 `GCMS_SECNE_RECALL`、`small_flow_list` 这类条件翻译到业务代码里时写错，最直接的后果不是编译失败，而是“查不到数据但链路看起来正常”。

- **配置驱动的名单/流量策略会放大灰度风险。**  
  `small_flow_list` 本质上是依赖配置或手工维护集合的，迁移到 grc/grg 后，如果没有统一配置治理，很容易出现线上线下不一致。

- **回填容器和业务对象生命周期要隔离。**  
  `RidTmpInfo` 既承载业务状态又承载回填结果，如果直接复用到其他阶段，容易把临时态带到后续处理，形成隐蔽 bug。

- **容器替换不等于行为等价。**  
  `std::unordered_map`、`std::vector` 的使用虽然成熟，但如果原逻辑隐含了顺序依赖、重复 key 处理或默认值语义，迁移时需要补齐测试，否则容易出现回填结果顺序变化或覆盖问题。

如果你愿意，我还可以进一步把这份内容整理成你笔记里可直接粘贴的“章节版” Markdown。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
