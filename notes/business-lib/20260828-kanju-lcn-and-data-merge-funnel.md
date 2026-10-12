# 看剧 / 合集 / 短剧 LCN 与扶持策略

> 日期：2026-08-28  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 Friday business slot  
> 范围：`src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp` 的 LCN 惩罚链路，以及 `src/processor/video_launch/data_merge.cpp` 的品类合并和漏斗日志。
> 内网文档状态：本次未成功读取 KU 正文，业务口径需人工补充；以下以代码证据为主。

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:16px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px;"><div style="background:#fff;border:1px solid #dbe4ee;border-radius:12px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">内容识别</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`collection_id` / `is_heji` / `get_is_duanju()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先把合集、短剧、微视频、长短视频分出来，再进入后面的权重和 quota 计算。</div></div><div style="background:#fff;border:1px solid #dbe4ee;border-radius:12px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">LCN 惩罚</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`exp1/2/3` + `d_dy_hot_info_attract_dict`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">通过实验和词典参数，把 long / recent / latest 三段 LCN 压到不同 q 值。</div></div><div style="background:#fff;border:1px solid #dbe4ee;border-radius:12px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">融合配额</div><div style="margin-top:8px;font-size:14px;color:#102a43;">保量 / 非保量 / 兜底三层合并</div><div style="margin-top:8px;font-size:12px;color:#52606d;">以 `_total_quota` 为硬边界，再按兴趣、质量和多样性决定落点。</div></div><div style="background:#fff;border:1px solid #dbe4ee;border-radius:12px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">观测闭环</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`[fsq_1400]` + `Merge Video Stats`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">输入输出两段漏斗日志一起看，才能区分是筛选问题还是 quota 问题。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:10px;padding:10px;text-align:center;color:#4338ca;">GCMS / VideoInfo</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:10px;padding:10px;text-align:center;color:#0f766e;">Kanju LCN adjuster</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:10px;padding:10px;text-align:center;color:#a16207;">quota merge</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:10px;padding:10px;text-align:center;color:#166534;">funnel logs</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title Kanju LCN factor and data merge flow
participant "Adjuster" as ADJ
participant "ExpManager" as EXP
participant "Common Dict" as DICT
participant "RidTmpInfo" as RID
participant "DataMergeFunction" as DM
participant "EffectOtherDataMergeContext" as CTX
participant "SIA / LOG" as LOGS
ADJ -> EXP : hit kanju_juji_lcn_hit_exp1/2/3?
alt exp1
  EXP --> ADJ : do_juji=true
else exp2 / exp3
  EXP --> ADJ : do_juji=true + do_session_juji=true
else default
  EXP --> ADJ : no-op
end
ADJ -> DICT : get d_dy_hot_info_attract_dict
DICT --> ADJ : bound / weight params
ADJ -> RID : read gcms_data.video_info.collection_id
ADJ -> ADJ : get_long_lcn_q()
ADJ -> ADJ : get_recent_lcn_q()
ADJ -> ADJ : get_latest_lcn_q()
ADJ -> RID : lcn_adjuster_factor = min(qs) * session_q
ADJ -> DM : process()
DM -> DM : merge_data()
DM -> LOGS : [fsq_1400] input funnel
DM -> CTX : init_effect_other_data_merge_context()
DM -> DM : adjust_quota by quality / interest
DM -> DM : bthread_async merge per type
DM -> LOGS : Merge Video Stats / output funnel
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title 看剧 / 合集 / 短剧链路关键字段
  desc 这些字段决定识别、惩罚、合并和观测分别怎么落地
  items
    - label collection_id
      desc 从 `VideoInfo.get_article_collections_info().collection_id` 读取，是 LCN map 的 key
      icon mdi:key-variant
    - label is_heji
      desc 合集识别字段，既影响 adjuster 也影响 merge 漏斗
      icon mdi:folder-multiple-outline
    - label get_is_duanju
      desc 短剧识别字段，决定 Duanju 统计和 quota 分流
      icon mdi:movie-open-outline
    - label long/recent/latest q
      desc 三段 LCN 分别映射到 long_lcn_q / recent_lcn_q / latest_lcn_q
      icon mdi:timeline-clock-outline
    - label session_q
      desc exp2/exp3 才会叠乘，属于更强的长周期惩罚/调节因子
      icon mdi:account-clock-outline
    - label total_quota
      desc DataMerge 输出上限，保量、非保量、兜底都受它约束
      icon mdi:counter
```

## 3. 代码链路拆解
### 3.1 KanjuLcnPreciseAdjuster：实验 + 词典驱动的 LCN 惩罚
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:43-55`：根据 `kanju_juji_lcn_hit_exp1/2/3` 选择参数集，exp2/exp3 会打开 `do_session_juji`。
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:57-73`：从 `d_dy_hot_info_attract_dict` 读取 12 个参数，分别给 recent/latest 的 bound 和 weight 赋值。
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:99-130`：从 `gcms_data->_video_info.get_article_collections_info().collection_id` 取合集 id，并计算 long/recent/latest LCN q。
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:151-165`：`factor` 取三段 q 的最小值，若命中 session 逻辑再叠乘 `final_q`，最后写回 `item->lcn_adjuster_factor`。
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:204-310`：long/recent/latest/session 的衰减方式不同，属于典型“同一业务维度多时间窗”的分层权重设计。

### 3.2 DataMergeFunction：品类合并与漏斗观测闭环
- `src/processor/video_launch/data_merge.cpp:79-151`：先统计保量和非保量中的 mv/sv/dt/dj/heji 数量，再输出 `[fsq_1400]` 输入漏斗。
- `src/processor/video_launch/data_merge.cpp:153-178`：`reserve(_total_quota)` 后，先放保量，再处理非保量效果队列，最后兜底补齐到 quota。
- `src/processor/video_launch/data_merge.cpp:160-169`：低活用户 + 兴趣 quota 实验 + 兴趣信息存在时走 `handle_effect_other_data_v2`，否则走常规 `handle_effect_other_data`。
- `src/processor/video_launch/data_merge.cpp:369-445`：按类型并发 merge，不同类型先挑选再合并，最后再做一次突破多样性约束的兜底。
- `src/processor/video_launch/data_merge.cpp:283-337`：输出 `Merge Video Stats` 和二次 `[fsq_1400]` 漏斗，给策略效果提供可观测性。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#faf8f4;border:1px solid #e7d8c9;border-radius:16px;padding:18px;margin:16px 0;color:#3f342a;"><div style="font-size:12px;font-weight:800;color:#7c6853;text-transform:uppercase;letter-spacing:.08em;">business pitfalls</div><div style="font-size:24px;font-weight:900;margin:6px 0 12px;letter-spacing:-.02em;">“调权惩罚”与“扶持配额”不是同一层逻辑</div><div style="display:grid;grid-template-columns:1.1fr 1fr;gap:12px;"><div style="background:#fff;border-top:4px solid #7c6853;border-radius:12px;padding:12px;font-size:14px;line-height:1.65;"><strong>Adjuster 层：</strong>输出的是 item 级 factor，核心是抑制重复消费，不要把 `lcn_adjuster_factor` 直接解释成扶持结果。<br><strong>Merge 层：</strong>输出的是 quota 结果和品类构成，`total_quota`、兴趣策略和兜底路径共同决定最终分布。</div><div style="background:#fff;border-top:4px solid #7c6853;border-radius:12px;padding:12px;font-size:14px;line-height:1.65;"><strong>观测陷阱：</strong>`[fsq_1400]` 有输入和输出两段，必须连起来看。只看最终输出会误判是过滤器问题，实际上也可能是上游输入分布本来就变了。<br><strong>人工补充：</strong>本次没有 KU 正文，业务背景、实验目标和收益口径仍需人工补齐。</div></div><div style="margin-top:10px;font-weight:900;color:#7c6853;">∎ 同时看 factor、quota、漏斗三层证据</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title 看剧 / 合集 / 短剧策略排查清单
  desc 适用于策略不生效、合集过多/过少、短剧占比异常、实验收益波动
  items
    - label 先确认实验命中
      desc 看 `kanju_juji_lcn_hit_exp1/2/3`，未命中时 `do_juji` 为 false
      done true
    - label 校验词典长度
      desc `d_dy_hot_info_attract_dict` 至少需要 12 个值，否则 bound/weight 保持默认值
      done true
    - label 检查 collection_id
      desc LCN map key 来自 `collection_id`，缺失时 q 可能一直是 1
      done true
    - label 对比输入输出漏斗
      desc 同时看 input_dj/heji_rate 与 output_dj/heji_rate，不只看最终输出
      done true
    - label 区分 heji 与 duanju
      desc `is_heji` 来自 collection info；`get_is_duanju()` 来自视频类型判断
      done true
    - label 补充业务口径
      desc 本次 KU 正文不可用，实验目标和收益口径需人工补充
      done false
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:43-55`
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:57-73`
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:99-165`
- `src/operator/adjuster/precise/kanju_lcn_precise_adjuster.cpp:204-310`
- `src/processor/video_launch/data_merge.cpp:79-178`
- `src/processor/video_launch/data_merge.cpp:283-337`
- `src/processor/video_launch/data_merge.cpp:369-445`

## 7. 说明
本次没有读取 KU 正文，业务背景需人工补充；本笔记只基于本地源码和计划文件整理。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-01T19:18:12.372940
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 从扫描结果看，这套“看剧 / 合集 / 短剧 LCN 与扶持策略”并不是完全陌生的业务能力，而是已经在两个业务库里出现了**相邻的落点**：  
  - `feeda-mv-grg` 侧已有 `process/cal_kanju_prefer.cpp`、`process/cal_kanju_kezi_prefer.cpp`、`process/get_kanju_novel_vec.cpp` 等看剧相关逻辑；
  - `feeda-mv-grc` 侧已有 `processor/compute_duanju_explore_lcn_info.cpp`、`processor/filter/duanju_video_filter_operator.cc` 等短剧与 LCN 相关链路。  
  这说明该策略具备**直接接入的业务基础**，适合做增量改造，而不是从零重建。

- 从工程规模看，两库对 STL 的使用都很重：  
  - `feeda-mv-grg` 中 `std::vector` / `std::string` / `std::unordered_map` 使用量分别为 1969 / 2443 / 734 次；
  - `feeda-mv-grc` 中分别为 8520 / 7267 / 2860 次。  
  这表明代码形态本身已经适合承载“多维特征 + 规则分层 + 漏斗观测”的实现方式。若要迁移 LCN 惩罚、合集识别和漏斗日志能力，**收益主要体现在策略统一、观测补齐、配置化增强**，而不是底层数据结构层面的替换收益。

---

## 2. 代码库详情

### 2.1 `feeda-mv-grg`：序列生成服务

- 扫描到的相关文件有 6 个，说明该库里已经存在较完整的看剧/短剧/合集方向的业务分支：
  - `operator/diversity/mcv_recpage_tgi_manju_set2setq_rule.cpp`
  - `operator/diversity/douyin_popular_soft_rule.cpp`
  - `process/cal_kanju_kezi_prefer.cpp`
  - `process/get_kanju_novel_vec.cpp`
  - `process/cal_kanju_prefer.cpp`
- 这些文件更偏向**候选排序、偏好计算、规则去重、多样性控制**，与技术笔记里的 `KanjuLcnPreciseAdjuster` 很匹配。  
- 适配潜力：
  - `cal_kanju_prefer.cpp`、`cal_kanju_kezi_prefer.cpp` 适合承接 **LCN 惩罚因子**；
  - `mcv_recpage_tgi_manju_set2setq_rule.cpp`、`douyin_popular_soft_rule.cpp` 适合承接 **合集/短剧的多样性与 soft rule 调整**；
  - `get_kanju_novel_vec.cpp` 可作为**特征向量扩展**的入口，用于挂载 `collection_id`、`is_heji`、`lcn_adjuster_factor` 等字段。
- 工程特征上，该库 STL 使用非常普遍，说明新增 `q` 值、实验开关、词典参数、分层 factor 等结构不会破坏既有编码风格。

### 2.2 `feeda-mv-grc`：召回汇聚服务

- 扫描到的相关文件有 3 个，命中更直接：
  - `data/base.h`
  - `processor/filter/duanju_video_filter_operator.cc`
  - `processor/compute_duanju_explore_lcn_info.cpp`
- 其中：
  - `compute_duanju_explore_lcn_info.cpp` 与笔记中的 LCN 计算链路最贴近，可作为 **LCN 信息计算/汇总** 的直接参考；
  - `duanju_video_filter_operator.cc` 更适合承接 **短剧过滤、分流、扶持前置条件判断**；
  - `data/base.h` 适合作为 **公共数据结构扩展点**，例如加入 `collection_id`、`is_heji`、`duanju` 标识、三段 q 值、session 因子、漏斗统计字段。
- 该库 STL 使用量更大，说明它本身是一个偏数据流处理、聚合、过滤的高频业务仓。对这类库来说，LCN 和漏斗日志的接入成本通常较低，适合先做：
  - 数据结构补字段
  - 过滤链路打点
  - 聚合层输出观测值
  - 再逐步引入策略开关

---

## 3. 💡 适用性评估与建议

- **优先在 `feeda-mv-grc/processor/compute_duanju_explore_lcn_info.cpp` 落地 LCN 计算逻辑**
  - 建议把 `collection_id`、`is_heji`、`get_is_duanju()`、long/recent/latest q、session_q 统一封装成一个中间结果结构。
  - 这个文件最适合做“**LCN 信息生产者**”，后续 `duanju_video_filter_operator.cc` 和汇聚逻辑直接消费。
  - 如果当前是散落的条件判断，建议整理成一个轻量结构体，避免每个调用点重复解析。

- **在 `feeda-mv-grc/processor/filter/duanju_video_filter_operator.cc` 中把 `lcn_adjuster_factor` 作为过滤/排序因子，而不是最终扶持结果**
  - 这是最容易产生误解的点：笔记里已经明确 `lcn_adjuster_factor` 是 item 级惩罚结果，不等于扶持配额。
  - 建议这里只做：
    - 合集/短剧识别
    - 低活用户与实验开关判断
    - 根据 factor 做过滤或降权
  - 不要把 quota 逻辑塞进过滤器，否则会让职责边界变乱。

- **在 `feeda-mv-grc/data/base.h` 补齐统一数据模型，降低跨文件字段漂移**
  - 建议增加统一字段承载：
    - `collection_id`
    - `is_heji`
    - `is_duanju`
    - `lcn_long_q` / `lcn_recent_q` / `lcn_latest_q`
    - `lcn_session_q`
    - `lcn_adjuster_factor`
  - 这样 `grg` 和 `grc` 两侧都能使用一致的数据口径，减少多处拼装字符串或临时 map 的开销。
  - 由于 STL 使用已经很重，建议优先用结构体 + 小型容器，而不是继续扩散 `unordered_map<string, string>` 这种松散表示。

- **在 `feeda-mv-grg/process/cal_kanju_prefer.cpp` 和 `process/cal_kanju_kezi_prefer.cpp` 中接入 LCN 惩罚因子**
  - 这两个文件属于看剧偏好计算核心，适合把 `lcn_adjuster_factor` 作为：
    - 偏好分的乘子
    - 召回后重排的修正项
    - 合集/短剧重复消费的抑制项
  - 如果当前已有基于“历史偏好”或“内容类型”的分数模型，LCN 因子可以作为后处理项接入，改动风险较小。
  - 可参考 `process/get_kanju_novel_vec.cpp` 的向量组织方式，把因子作为特征维度之一，而不是临时变量。

- **在 `feeda-mv-grg/operator/diversity/mcv_recpage_tgi_manju_set2setq_rule.cpp` 和 `douyin_popular_soft_rule.cpp` 中补充合集/短剧多样性约束**
  - 如果这两个规则已经负责热门 soft rule 或多样性控制，可以把 `heji` 和 `duanju` 作为独立维度参与分桶。
  - 这与技术笔记中“保量 / 非保量 / 兜底三层合并”的思路一致：  
    先保证基础覆盖，再对合集和短剧做软约束，最后再用兜底策略补齐。
  - 适合做“**扶持配额的分发辅助**”，而不是重写主排序。

---

## 4. ⚠️ 引入风险与限制

- **职责边界容易混淆**
  - `lcn_adjuster_factor` 是惩罚/调节因子，不是扶持结果。
  - 如果在 `filter`、`prefer`、`merge` 三层都直接操作 quota，很容易造成重复放大或重复衰减。

- **业务口径未完全闭环**
  - 技术笔记已说明本次没有成功读取 KU 正文，实验目标、收益口径、扶持定义仍需人工补充。
  - 如果没有统一口径，`heji`、`duanju`、`LCN` 三者的边界很容易在不同库里实现不一致。

- **漏斗日志必须成对看**
  - `[fsq_1400]` 有输入和输出两段，`Merge Video Stats` 也需要结合看。
  - 只看最终输出，容易误判是策略无效；实际上可能是上游输入分布、实验命中率或过滤条件变化导致。

- **热路径性能要控制额外字段与查表开销**
  - 虽然 STL 使用广泛，但 LCN 逻辑天然会带来更多条件分支、字典读取和字段搬运。
  - 若在 `compute_duanju_explore_lcn_info.cpp` 或 `cal_kanju_prefer.cpp` 中频繁做字符串 key 查找，可能影响召回/排序链路延迟，建议提前缓存或预计算。

---

## 5. 结论

- 这套策略在 `feeda-mv-grg` 和 `feeda-mv-grc` 中**都有明确的业务落点**，属于适合增量接入的能力。
- 最优路径不是全局重构，而是：
  - `grc` 负责 **LCN 识别 + 过滤 + 数据模型补齐**
  - `grg` 负责 **偏好修正 + 多样性控制 + 候选分发**
  - 两侧共同补 **输入/输出漏斗日志**
- 如果后续要推进迁移，建议先从 `processor/compute_duanju_explore_lcn_info.cpp`、`processor/filter/duanju_video_filter_operator.cc`、`process/cal_kanju_prefer.cpp` 三个点做最小闭环。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
