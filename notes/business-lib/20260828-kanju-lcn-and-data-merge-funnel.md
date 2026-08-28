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
