# 2026-09-03 周四业务库理解：召回 quota 与 video_launch 数据合并
<!-- topic-nav:start -->
> 主题：`business/quota-merge` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260626-grc-datamerge-baoliang-quota.md)
<!-- topic-nav:end -->


> 日期：2026-09-03  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 仍停留在旧的周计划，没有 2026-09-03 当日计划；按历史候选回退到 `召回 quota 与 video_launch 数据合并`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/processor/video_launch/data_merge.cpp`、`src/processor/video_launch/diversity_merge.cpp`、`src/service/grc_service.cpp`，聚焦保量 / 非保量 / 兜底三层合并、漏斗日志、以及桥接后的数据汇聚边界。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.05fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">输入层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/service/grc_service.cpp:172-220`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">入口层先把 request、scene、dynamic struct、graph 引入 graph 侧，再进入后续数据合并流程。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">合并层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/processor/video_launch/data_merge.cpp:48-179`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">按保量、非保量、兜底三步把结果补齐到 `_total_quota`，并记录漏斗和类型统计。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">散度层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/processor/video_launch/diversity_merge.cpp:107-260` / `src/process/diversity_merge.cpp:107-260`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">散度规则决定哪些队列能进入后续 merge，数据合并层只消费已经筛选过的候选集。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">保量输入</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">效果队列</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">兜底补齐</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">漏斗回传</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title DataMergeFunction quota funnel
participant "GRC service" as GRC
participant "DiversityMerge" as DIV
participant "DataMergeFunction" as DM
participant "MergedBaoliangData" as BQ
participant "EffectOtherData" as EO
participant "Log / SIA" as LOGS
GRC -> DIV : prepare graph input / dependencies
DIV -> DM : process()
DM -> BQ : read baoliang data
DM -> EO : read effect_other data
DM -> DM : merge_data(output_vec)
DM -> DM : reserve(_total_quota)
DM -> DM : add_all_data_to_output(baoliang)
DM -> DM : handle_effect_other_data(v1/v2)
DM -> DM : add_all_data_to_output(effect_other)
DM -> LOGS : [fsq_1400] / Merge Video Stats / SIA_END_WITH_VAL
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title 数据合并链路的 6 个关键字段
  desc 这些字段决定最终 quota 怎么分、哪些内容进入输出、以及漏斗如何回传。
  items
    - label _total_quota
      desc `src/processor/video_launch/data_merge.cpp:51-55` 是输出硬边界，也是 reserve 的目标值
      icon mdi/counter
    - label _merged_baoliang_data
      desc 保量数据先进入输出，决定基础覆盖率
      icon mdi-shield-star-outline
    - label _effect_other_data
      desc 非保量效果队列承接后续补齐逻辑
      icon mdi-format-list-bulleted
    - label low activity user
      desc `get_is_low_activity_user()` 与兴趣 quota 实验决定是否走 v2 路径
      icon mdi-account-alert-outline
    - label interest quota
      desc `is_hit_interest_quota` 与 `_video_interest_info` 共同决定是否启用兴趣分流
      icon mdi-heart-outline
    - label [fsq_1400]
      desc 输入 / 输出两段漏斗日志，帮助区分筛选问题和 quota 问题
      icon mdi-chart-funnel
```

## 3. 代码链路拆解
### 3.1 merge_data 的核心不是“挑一批数据”，而是“按优先级补齐 quota”
- `src/processor/video_launch/data_merge.cpp:48-75`：`process()` 先清空三个结果容器，再调用 `merge_data()`，说明本轮输出必须从干净状态开始。
- `src/processor/video_launch/data_merge.cpp:153-178`：先 `reserve(*_total_quota)`，再放保量，再处理效果队列，最后兜底补齐到 quota，顺序是固定的。
- `src/processor/video_launch/data_merge.cpp:181-205`：`add_all_data_to_output()` 和 `add_data_to_output()` 共同保证不超过 `_total_quota`，同时把 `is_insert_sketchy_rank` 写回原对象，防止重复插入。

### 3.2 低活用户和兴趣 quota 是合并策略的分支点
- `src/processor/video_launch/data_merge.cpp:159-169`：当低活用户、兴趣 quota 命中且兴趣信息存在时，走 `handle_effect_other_data_v2()`，否则回落到常规 `handle_effect_other_data()`。
- `src/processor/video_launch/data_merge.cpp:160-166`：这个条件说明效果队列的合并不是纯数量逻辑，而是依赖用户活跃度和兴趣状态的业务修正。
- `src/processor/video_launch/data_merge.cpp:172-178`：最后的兜底不再看多样性约束，直接补齐输出，说明兜底是“填满 quota”的最后保险。

### 3.3 漏斗日志和类型统计是这条链路的观测核心
- `src/processor/video_launch/data_merge.cpp:79-151`：先统计 mv/sv/dt/dj/heji 的输入分布，再打印 `[fsq_1400]`，能直接看出输入结构变化。
- `src/processor/video_launch/data_merge.cpp:208-261`：`log_merge_result()` 会继续统计 total_count、heji_count、duanju_count 和类型命中，便于对比输出分布。
- `src/processor/video_launch/data_merge.cpp:65-74`：`calc_ext_info()` 和 `print_log()` 在输出后执行，说明观测是结果链的一部分，不是附带 debug。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:10px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">business pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">quota 合并最容易在“最后一层兜底”里掩盖问题</div><div style="display:grid;grid-template-columns:1.1fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">如果只看最终输出是否补齐，很容易忽略前面保量、效果队列和兴趣 quota 的分流差异。真正要盯的是每一层新增了多少、丢了多少、为什么丢。</div><div style="background:#f8fafc;border-top:3px solid #2563eb;border-radius:8px;padding:12px;font-size:14px;">`reserve(_total_quota)` 是明确的性能和行为信号。若 quota 估计偏大，会增加内存占用；若太小，又会让后续插入频繁触发边界判断。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 排查顺序：保量输入 → 兴趣分支 → 兜底补齐 → [fsq_1400] 输出漏斗</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title DataMerge 链路调试清单
  desc 用于确认 quota、分支和漏斗日志是否一致
  items
    - label 检查 total_quota
      desc 确认 `_total_quota` 与实际输出规模匹配
      done true
    - label 检查保量优先级
      desc 保量数据必须先进入输出，再处理效果队列
      done true
    - label 检查兴趣分支
      desc 低活用户 + 兴趣 quota + 兴趣信息同时命中时是否走 v2
      done true
    - label 检查兜底路径
      desc 兜底阶段是否只负责补齐，不再附加复杂策略
      done true
    - label 检查重复插入标记
      desc `is_insert_sketchy_rank` 是否正确避免重复消费
      done true
    - label 检查漏斗日志
      desc 输入 / 输出两段 `[fsq_1400]` 和 `Merge Video Stats` 是否都打印
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/video_launch/data_merge.cpp:48-75`
- `src/processor/video_launch/data_merge.cpp:79-178`
- `src/processor/video_launch/data_merge.cpp:181-261`
- `src/service/grc_service.cpp:172-220`
- `src/processor/video_launch/diversity_merge.cpp:107-260`

## 7. 说明
当前运行环境没有 2026-09-03 的 daily-plan 文件；本笔记基于历史候选和本地代码回退生成，KU 正文未读取，业务背景需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-03T19:18:12.372940
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 这条“召回 quota 与 video_launch 数据合并”能力在 `feeda-mv-grc` 里已经有很直接的落点：`src/processor/video_launch/data_merge.cpp` 就是合并、补齐和漏斗观测的核心位置，`src/processor/video_launch/diversity_merge.cpp` 则负责前置的散度与输入筛选。
- `src/service/grc_service.cpp` 提供了图入口、场景映射和对象池回收，说明这条业务线可以继续沿着“入口接线 → 中间合并 → 最终 emit”的方式收敛，不需要把 quota 逻辑散到更多层。
- `feeda-mv-grg` 侧在这次主题里更多是相邻参考，不是主落点；真正适合落地的是 `grc` 的合并链路，因为它已经天然承担保量、效果队列、兜底和统计输出。

## 2. 代码库详情

### feeda-mv-grg

- 这次不是主战场，相关能力更多是策略侧邻近逻辑。
- `std` 容器使用规模仍然很大：
  - `std::vector`：1969 次
  - `std::string`：2443 次
  - `std::unordered_map`：734 次
- 结论：
  - 如果要在 `grg` 里接入类似 quota 合并的思路，最好只做局部试点，不要直接把数据合并协议大改。

### feeda-mv-grc

- 主落点明确：
  - `src/service/grc_service.cpp`
  - `src/processor/video_launch/diversity_merge.cpp`
  - `src/processor/video_launch/data_merge.cpp`
- `src/processor/video_launch/data_merge.cpp:153-178` 已经体现出标准的 quota 合并顺序：保量 → 效果队列 → 兜底。
- `src/processor/video_launch/data_merge.cpp:79-151` 和 `:208-261` 形成输入/输出漏斗闭环，适合作为统一观测模板。

## 3. 适用性评估与建议

- **优先把 quota 逻辑收敛在 `feeda-mv-grc/src/processor/video_launch/data_merge.cpp`**
  - 这是最直接的输出补齐层，建议把所有补齐策略都在这里统一管理。
  - 保量、兴趣 quota、低活用户分支、兜底补齐都可以继续留在这个文件，不要拆散到更多 helper 里。

- **在 `feeda-mv-grc/src/processor/video_launch/diversity_merge.cpp` 保持前置筛选职责**
  - 这个文件更适合负责输入筛选、队列组织和规则侧准备。
  - 不建议把 quota 补齐逻辑提前塞到这里，否则会和 `data_merge.cpp` 的职责重叠。

- **在 `feeda-mv-grc/src/service/grc_service.cpp` 把入口、图、reset 边界继续守住**
  - 服务层应该做图获取、场景映射和执行收口。
  - quota 统计和 merge funnel 仍然应该留在 processor 层，避免 service 入口过厚。

- **如果未来要在 `feeda-mv-grg` 复用类似模式，优先做“适配层”而不是直接复制合并逻辑**
  - `grg` 当前更偏策略和序列生成，不适合直接照搬 `data_merge.cpp` 的三段式合并。
  - 更稳妥的方式是抽出统一的结果补齐协议，再让业务层按各自场景消费。

## 4. ⚠️ 引入风险与限制

- **职责边界容易混淆**
  - `diversity_merge` 负责筛选和准备，`data_merge` 负责 quota 补齐和最终输出。两者混在一起会让排查变难。

- **兜底阶段容易掩盖上游问题**
  - 最终补齐成功不代表前面的分支正确，必须看每层漏斗和每层计数。

- **性能风险集中在容器边界**
  - `reserve(_total_quota)` 和 `emplace_back()` 的组合很稳，但如果 `_total_quota` 波动大，内存和拷贝成本会同步波动。

- **业务口径仍未完全闭环**
  - 这次没有读取 KU 正文，兴趣 quota 的具体口径和实验目标仍需人工补充。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*