# 2026-09-04 周五业务库理解：看剧/合集/短剧 LCN 与扶持策略
<!-- topic-nav:start -->
> 主题：`business/kanju-lcn` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260612-kanju-heji-duanju-lcn-fuchi.md)
<!-- topic-nav:end -->


> 日期：2026-09-04  
> 主题来源：当前 canonical 笔记仓库未找到 2026-09-04 当日 daily-plan；按旧计划 `notes/weekly-topic-selection/daily-plan-20260529.json` 的 Friday 配对回退到 `看剧/合集/短剧 LCN 与扶持策略`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/operator/adjuster/precise/kanju_precise_adjuster.cpp`、`src/operator/adjuster/precise/kanju_days_lt_precise_adjuster.cpp`、`src/operator/adjuster/sketchy/kanju_fuchi_boost_cp.cpp`、`src/processor/video_launch/data_merge.cpp`，聚焦合集 / 短剧 / 漫剧识别、LCN 参数、扶持 boost、q 权重、以及输出漏斗中的 heji / duanju 计数。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.08fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">资源识别层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">gcms_data / VideoInfo / collections</div><div style="margin-top:8px;font-size:12px;color:#52606d;">通过 `get_is_duanju()`、`is_heji`、`unhuman_playlet_for_c` 和 cate 判断短剧、合集、漫剧和看剧类目。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">调权层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">KanjuYanxu / KanjuDaysIt / KanjuFuchiBoost</div><div style="margin-top:8px;font-size:12px;color:#52606d;">实验位决定参数名，dict / exp 参数给出 q 权重和 factor 上限，再按 LTV、TGI、热度和滑动率计算 factor。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">输出观测层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">DataMergeFunction funnel</div><div style="margin-top:8px;font-size:12px;color:#52606d;">合并层统计 `input_dj_num`、`input_heji_num`、`Merge Video Stats`，用于判断策略是否改变输出结构。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 64px 1fr 64px 1fr 64px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">资源分类</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">参数命中</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">factor 计算</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">漏斗验证</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title Kanju / Heji / Duanju LCN and fuchi adjustment flow
participant "RidTmpInfo" as RID
participant "GCMS VideoInfo" as VIDEO
participant "SidInfo / ExpManager" as EXP
participant "common dict" as DICT
participant "KanjuYanxuAdjuster" as YX
participant "KanjuDaysItPreciseAdjuster" as DAYS
participant "KanjuFuchiBoostSketchy" as BOOST
participant "DataMergeFunction" as DM
RID -> VIDEO : get_is_duanju / is_heji / collection_id
YX -> EXP : hit kanju812_adjust_exp_v1..v5
YX -> DICT : read kanju_adjuster_para / popularity dict
YX -> RID : factor = tq_ratio_base * tq_ratio_812
DAYS -> EXP : hit kanju_days_lt_hit_v1/v2
DAYS -> DICT : read duanju / manju / heji q weights
DAYS -> RID : choose type_q_weight by resource type
BOOST -> EXP : hit kanju_fuchi_boost_flag / params
BOOST -> RID : boost manju or live_duanju by LTV q sigmoid
DM -> VIDEO : count duanju / heji / mv / sv / dt
DM -> DM : log [fsq_1400] and Merge Video Stats
@enduml
```

## 2. 策略结构信息图
```infographic
infographic list-grid-badge-card
data
  title 看剧策略链的 6 个关键分叉
  desc 这些分叉决定资源是否进入 LCN、days_lt 或扶持 boost，以及最终是否在合并漏斗中可见。
  items
    - label 合集识别
      desc `src/operator/adjuster/precise/kanju_precise_adjuster.cpp:144-153` 与 `data_merge.cpp:93-96` 读取 `is_heji`
      icon mdi/view-carousel-outline
    - label 短剧识别
      desc `kanju_fuchi_boost_cp.cpp:77-84` 用 `get_is_duanju()` 与 `unhuman_playlet_for_c` 区分真人短剧和漫剧
      icon mdi/movie-open-outline
    - label LCN 参数
      desc `kanju_precise_adjuster.cpp:55-90` 按实验位选择参数 key 并读取 14 维参数
      icon mdi/tune-variant
    - label days_lt 权重
      desc `kanju_days_lt_precise_adjuster.cpp:77-110` 从 dict 读取 duanju / manju / heji 三组 q 权重
      icon mdi/calendar-clock
    - label 扶持 boost
      desc `kanju_fuchi_boost_cp.cpp:36-58` 读取扶持实验参数，`141-161` 计算并 clamp factor
      icon mdi/rocket-launch-outline
    - label 输出漏斗
      desc `data_merge.cpp:138-151` 和 `:283-288` 打印 heji / duanju 输入输出结构
      icon mdi/chart-funnel
```

## 3. 代码链路拆解
### 3.1 看剧延续调权先建立资源语义，再叠加 q 与热度
- `src/operator/adjuster/precise/kanju_precise_adjuster.cpp:55-90`：`KanjuYanxuAdjuster` 按实验位选择 `kanju_adjuster_para_jp_v0` 或 `kanju812_adjuster_para_jp_v*`，再从 common dict 读取 factor 阈值、权重和 slide 开关。
- `src/operator/adjuster/precise/kanju_precise_adjuster.cpp:144-155`：通过 `get_is_duanju()`、`is_heji`、新类目和 812 类型判断剧集实验是否有效。
- `src/operator/adjuster/precise/kanju_precise_adjuster.cpp:221-277`：多个 LTV q、delay、热度、slide factor 相乘形成 `tq_ratio_812`，最后和 base ratio 相乘写回 `factor`。

### 3.2 days_lt 把合集、短剧、漫剧拆成不同 q 权重
- `src/operator/adjuster/precise/kanju_days_lt_precise_adjuster.cpp:33-52`：v1/v2 实验位决定 `_params_name`，未命中时直接不参与调权。
- `src/operator/adjuster/precise/kanju_days_lt_precise_adjuster.cpp:77-110`：dict 至少 20 个值时，分别填充真人短剧、漫剧、合集的 per-q type 权重，第 21/22 位继续控制 factor 上限和 sigmoid 开关。
- `src/operator/adjuster/precise/kanju_days_lt_precise_adjuster.cpp:146-171`：只处理合集，再按 `is_duanju`、`is_manju`、`is_heji_not_duanju` 选择不同 `type_q_weight`。

### 3.3 扶持 boost 聚焦短剧内部的版权/任务类型分支
- `src/operator/adjuster/sketchy/kanju_fuchi_boost_cp.cpp:36-58`：从 ExpManager 读取 `kanju_fuchi_boost_flag` 和 12 维参数，拆成普通短剧和真人短剧两组权重。
- `src/operator/adjuster/sketchy/kanju_fuchi_boost_cp.cpp:77-125`：非短剧直接跳过；真人短剧和 live 短剧分别用 settlement、new_trending、free_debut、copyright 等字段决定是否 boost。
- `src/operator/adjuster/sketchy/kanju_fuchi_boost_cp.cpp:141-161`：对四个 LTV q 先做 sigmoid，再按权重乘积形成 factor，并用 `max_factor` / `min_factor` clamp。

### 3.4 DataMerge 是验证策略是否改变输出结构的收口点
- `src/processor/video_launch/data_merge.cpp:79-151`：输入阶段分别统计 mv、sv、dt、duanju、heji 并打印 `[fsq_1400]`。
- `src/processor/video_launch/data_merge.cpp:153-178`：合并顺序仍是保量、效果队列、兜底；看剧策略只应改变候选得分和排序位置，不应破坏 quota 三段式。
- `src/processor/video_launch/data_merge.cpp:283-288`：输出阶段继续打印 `Total`、`Type3248`、`Heji`、`Duanju`，是回看策略效果的最小观测面。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">business pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">看剧策略最容易把“资源分类”和“调权强度”混成一个问题</div><div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">合集、短剧、漫剧的识别字段来自 GCMS VideoInfo，不是调权器自己推断。若 `is_heji` 或 `unhuman_playlet_for_c` 口径变化，调权参数再正确也会命中错资源。</div><div style="background:#f8fafc;border-top:3px solid #2563eb;border-radius:8px;padding:12px;font-size:14px;">最终输出补齐可能掩盖策略效果。要同时看输入 `[fsq_1400]`、输出 `Merge Video Stats`、factor 抽样日志，不能只看结果数是否达到 quota。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 排查顺序：资源字段 → 实验位 → dict 长度 → q 权重 → factor clamp → heji/duanju 漏斗</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title 看剧/合集/短剧策略排查清单
  desc 适用于合集追打、短剧扶持、LCN 参数不生效、factor 过大和输出结构异常
  items
    - label 检查资源分类
      desc `is_heji`、`get_is_duanju()`、`unhuman_playlet_for_c` 是否符合当前业务口径
      done true
    - label 检查实验命中
      desc `kanju812_adjust_exp_*`、`kanju_days_lt_hit_*`、`kanju_fuchi_boost_flag` 是否命中
      done true
    - label 检查 dict 长度
      desc LCN 至少 14 维，days_lt 权重至少 20 维，扶持 boost 至少 12 维
      done true
    - label 检查 q 均值
      desc LTV q 与 avg 都要大于阈值，否则 factor 分支会自然退化
      done true
    - label 检查 clamp
      desc `factor_th`、`factor_max_th`、`max_factor/min_factor` 是否过硬
      done true
    - label 检查输出漏斗
      desc 对比 `[fsq_1400]` 输入结构和 `Merge Video Stats` 输出结构
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/operator/adjuster/precise/kanju_precise_adjuster.cpp:55-90`
- `src/operator/adjuster/precise/kanju_precise_adjuster.cpp:144-155`
- `src/operator/adjuster/precise/kanju_precise_adjuster.cpp:221-277`
- `src/operator/adjuster/precise/kanju_days_lt_precise_adjuster.cpp:33-52`
- `src/operator/adjuster/precise/kanju_days_lt_precise_adjuster.cpp:77-110`
- `src/operator/adjuster/precise/kanju_days_lt_precise_adjuster.cpp:146-171`
- `src/operator/adjuster/sketchy/kanju_fuchi_boost_cp.cpp:36-58`
- `src/operator/adjuster/sketchy/kanju_fuchi_boost_cp.cpp:77-125`
- `src/operator/adjuster/sketchy/kanju_fuchi_boost_cp.cpp:141-161`
- `src/processor/video_launch/data_merge.cpp:79-178`
- `src/processor/video_launch/data_merge.cpp:283-288`

## 7. 说明
当前运行环境未找到 2026-09-04 的 daily-plan 文件；本笔记基于历史 Friday 配对和本地代码回退生成。KU 正文未读取，业务背景需人工补充。
