# 2026-09-12 业务库理解：GRC 多品类 quota 调整与精排请求分配

> 日期：2026-09-12  
> 主题选择：未发现当日有效 daily-plan；历史 Saturday 业务主题“GRC fake_id 关闭/替换链路”已有多篇覆盖，按邻近未覆盖候选回退到 **GRC DeepesQuotaInfo / deepes quota 调整与多品类精排 quota 分配**。KU 正文未读取，业务背景需人工补充；本文只陈述代码可验证的行为。  
> 范围：`gr-convergence/src/common/session_context.cc`、`data_framework/processor/ums_processor.cpp`、`recall_framework/processor/jp_processor/new_predictor_processor.cpp`，聚焦历史行为统计、实验参数、品类权重、资源浪费再分配和 Predictor 请求构造。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">历史信号层</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">UMS / HistoryFeatureInfo</div><div style="margin-top:8px;font-size:12px;color:#52606d;">按近 7 天 show/click、频道和实验开关收集新闻、短视频、微视频等历史行为。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">quota 计算层</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">权重 → 上下限 → waste reallocate</div><div style="margin-top:8px;font-size:12px;color:#52606d;">基础比例先归一化，再叠加 Deepes 实验、点击/展现阈值和资源供给差异，形成各品类精排 quota。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">执行层</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">NewPredictorProcessor</div><div style="margin-top:8px;font-size:12px;color:#52606d;">quota 为 0 时跳过精排；否则按 predictor type 选择 queue/token，并把 demand_size 写入请求。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 48px 1fr 48px 1fr 48px 1fr;gap:8px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">show / click history</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;font-size:13px;">experiment weights</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;font-size:13px;">category quota</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">queue / predictor request</div></div></div>

**一句话结论：** 这条链路不是简单把总 quota 按固定比例切三份，而是“行为统计驱动权重、实验调整边界、召回供给再分配、精排请求执行”的闭环。

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRC Deepes quota 到 Predictor 请求
participant "UMS Processor" as UMS
participant "GRCSessionContext" as SCTX
participant "HistoryFeatureInfo" as HIS
participant "Experiment" as EXP
participant "Quota calculator" as QC
participant "NewPredictorProcessor" as JP
participant "Predictor RPC" as RPC
participant "Monitor / Dapper" as OBS

UMS -> EXP : hit deepes_quota_adjust_grc_exp
alt experiment hit
  UMS -> SCTX : get_user_readlist_dur_click()
  UMS -> HIS : fill recent show/click + duration/click ratios
end
SCTX -> EXP : read jp_quota / quota_micro_video params
SCTX -> QC : compute category weights
QC -> QC : normalize scene percentages
QC -> EXP : read ratio, threshold, exp_type
alt below show/click thresholds
  QC -> QC : increase min ratios with log10(show gap)
else above thresholds
  QC -> QC : decrease min ratios with log10(click + adjust_click)
end
QC -> QC : derive max ratios and jp quota
QC -> QC : compare allocated quota with recall supply
QC -> QC : reallocate waste quota by weights
QC --> SCTX : _jp_news / _jp_short_video / _jp_micro_video
JP -> SCTX : read predictor-specific quota
alt quota == 0
  JP -> OBS : jp_quota_is_zero
  JP --> SCTX : skip predictor RPC
else quota > 0
  JP -> JP : choose queue, token, predictor_name
  JP -> JP : set demand_size + pass_through
  JP -> RPC : call predictor
  RPC --> JP : response / error / recovery
  JP -> OBS : quota, nid_size, queue_quota, latency
end
@enduml
```

## 2. quota 参数信息图

```infographic
infographic list-grid-badge-card
data
  title Deepes quota 的五个业务输入
  desc 每个输入都影响“比例怎么调”或“请求是否执行”
  items
    - label jp_quota
      desc 默认值 1580，并可由 jp_quota_adjust__quota1/2/3 实验调整到 1500、1422 或 1343
      icon mdi:counter
    - label show / click threshold
      desc oc 或 dc 口径的近 7 天展现与点击阈值，决定提高还是降低类别最小比例
      icon mdi:chart-timeline-variant
    - label duration / click ratio
      desc deepes_quota_adjust_grc_exp 使用时长占比或点击占比重新加权 news、short video、micro video
      icon mdi:percent-outline
    - label recall supply
      desc 每类实际 recall 数与目标 quota 的差额形成 waste quota，决定跨品类再分配
      icon mdi:database-arrow-down
    - label predictor quota
      desc _jp_*_total_quota 写入精排请求 demand_size；为 0 时直接跳过下游 RPC
      icon mdi:cloud-sync-outline
```

## 3. 代码链路拆解

### 3.1 UMS 只在命中实验时补齐 Deepes 行为信号

`data_framework/processor/ums_processor.cpp:421-425` 显示，只有推荐频道、非 Discover 来源且 `deepes_quota_adjust_grc_exp__is_hit` 命中时，才调用 `get_user_readlist_dur_click()`。因此后续 quota 计算依赖的 duration/click 比例不是所有请求都存在；排查实验未生效时，首先要确认 UMS 阶段是否被条件短路。

### 3.2 基础 quota 先建立比例，再进入 Deepes 调整

`session_context.cc:7214-7239` 先根据 `jp_quota_adjust__quota1/2/3` 选择总精排 quota，并保存各品类调整前的 quota。`session_context.cc:7240-7277` 读取 sv/mv/news ratio、base/max percent 等参数，以 scene percentage 作为初始权重并归一化。

`session_context.cc:7280-7351` 是 Deepes 实验的第一处业务边界：命中后读取 `exp_type` 和各品类 duration/click ratio，在输入有效时对 news、short video、micro video 权重重新加权，再记录 before/after 日志。实验只改变权重，不直接改写召回响应。

### 3.3 show/click 阈值形成“增下限”与“减下限”两条分支

`session_context.cc:7367-7405` 读取 `ratio`、`ratio_v2`、click threshold、show threshold。当近 7 天 show/click 同时低于阈值时，按 `log10(show_threshold - show + 10)` 增加多个品类的最小比例；否则结合 Deepes 返回的 `satisfy_click + 4 * hudong_click`，按 `log10(click + adjust_click + 1)` 扣减最小比例。

随后代码根据最小比例反推最大比例，并在 fix_dt 实验下给 dt 单独保留下限。这意味着 quota 异常不能只看最终权重，必须同时打印：阈值、近 7 天 show/click、Deepes response、min/max percent。

### 3.4 召回供给不足会触发 waste quota 再分配

`session_context.cc:7913-7934` 分别计算 news、short video、micro video 的 waste：目标 quota 大于召回量时记录“未消耗 quota”，召回量大于目标时记录“可承接空间”。

`session_context.cc:7935-7963` 先处理全充足和全不足两种边界，否则按不同品类作为来源，最多尝试三次 `compute_jp_recall_quota()`。该 helper `:6912-6947` 在目标数组、供给数组有效且权重和非零时，按照权重把来源 waste 分给承接品类，并用接收方容量做 `min` 限制。

再分配完成后，`session_context.cc:7964-7974` 以修正后的各品类 quota 重新计算权重，并回写 `_jp_*_total_quota`。这里的权重调整是供给感知的，不是单纯的实验比例缩放。

### 3.5 NewPredictorProcessor 把 quota 变成可执行请求

`recall_framework/processor/jp_processor/new_predictor_processor.cpp:271-280` 对三类 predictor 做 quota 为零的短路：short video、micro video、news 分别读取对应 `_jp_*_total_quota`，为零时记录 `jp_quota_is_zero` 并不调用精排服务。

`new_predictor_processor.cpp:525-588` 根据 origin channel、predictor type 和 dihuo 实验选择 queue/token，设置 `predictor_name` 与 `demand_size`。`new_predictor_processor.cpp:592-617` 继续透传 sid/pass-through，并构造 queue_quota；`new_predictor_processor.cpp:302-357` 完成 RPC、容灾判断、响应解析、quota/queue/nid_size/耗时监控。

因此“quota 算对了但结果少”可能发生在三个不同位置：计算层将 quota 调小、执行层 quota 为零被短路、或 Predictor RPC/容灾/响应解析阶段失败。必须把三层日志对齐。

## 4. 观测字段对照信息图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title 计算层与执行层的日志对账
  desc 先对齐 quota，再定位供给、请求或响应问题
  items
    - label 计算层
      desc 关注 jp_quota、before/after weight、min/max percent、waste quota、_jp_*_total_quota
      value 1
      icon mdi:calculator-variant
      children
        - label history
          desc 近 7 天 show/click 与 duration/click ratio
        - label experiment
          desc deepes_quota_adjust、jp_quota_adjust、quota_micro_video
        - label supply
          desc news/short/micro recall 数与可再分配 waste
    - label 执行层
      desc 关注 predictor_type、queue、token、predictor_name、demand_size、queue_quota 和 response error
      value 2
      icon mdi:rocket-launch-outline
      children
        - label short circuit
          desc jp_quota_is_zero 表示没有发起精排 RPC
        - label RPC
          desc call io cost、remote side、error code、recovery count
        - label output
          desc parse response、reply_size、unreliable_num 与最终 nid map
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#f4f3f0;border:1px solid #d9d3c8;border-left:8px solid #0f766e;border-radius:16px;padding:20px;margin:18px 0;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#0f766e;font-weight:800;">Pitfalls · Multi-category Quota</div><div style="font-size:26px;line-height:1.15;font-weight:900;margin:8px 0 12px;">最终 quota 是多个边界叠加后的结果</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:14px;"><div style="background:#fff;border-top:4px solid #0f766e;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;"><b>常见误读：</b><br>① 把 `jp_quota` 当成每类 quota；② 只看实验命中，不看 UMS 是否填入行为比例；③ 只看权重，不看 recall supply 和 waste reallocate；④ 看到 `demand_size` 就以为请求一定发出，忽略 quota 为 0 的短路。</div><div style="background:#fff7ed;border-top:4px solid #f59e0b;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;color:#7c2d12;"><b>对账顺序</b><br>历史信号 → 实验命中 → 初始权重 → min/max 调整 → recall 供给 → waste 再分配 → demand_size → RPC/解析。</div></div><div style="margin-top:12px;font-weight:900;color:#0f766e;">∎ 先确认“算了多少”，再确认“发了多少”，最后确认“回了多少”。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title Deepes quota / Predictor 排查清单
  desc 让 quota 计算、供给再分配和精排执行可以逐层对账
  items
    - label 确认入口频道
      desc 核对 channel_id、origin_channel_id 和是否属于推荐/Discover 分支
      done true
    - label 确认 UMS 条件
      desc 检查 deepes_quota_adjust_grc_exp 命中后是否调用行为历史读取
      done true
    - label 记录总 quota
      desc 对齐 jp_quota、原始 predictor quota 与调整前 quota
      done true
    - label 记录实验参数
      desc 打印 exp_type、ratio、ratio_v2、show/click threshold 和 min/max percent
      done false
    - label 检查行为定义域
      desc 确认近 7 天 show/click、duration/click ratio 非空且使用的是正确频道口径
      done false
    - label 对账 recall supply
      desc 对比三类目标 quota、实际 recall 数和 waste quota，确认再分配路径
      done false
    - label 检查零 quota 短路
      desc 查找 jp_quota_is_zero，确认是预期配额还是计算异常
      done true
    - label 对齐 Predictor 请求
      desc 核对 predictor_type、queue、token、predictor_name、demand_size 和 queue_quota
      done true
    - label 对齐 RPC 结果
      desc 检查 error code、容灾标记、reply_size、unreliable_num 和 parse response cost
      done false
```

## 7. 证据来源

- `baidu/feed-gr/gr-convergence/src/data_framework/processor/ums_processor.cpp:421-425`：Deepes 实验命中后读取用户时长/点击历史。
- `baidu/feed-gr/gr-convergence/src/common/session_context.cc:7214-7239`：jp_quota 实验选择与调整前 quota 保存。
- `baidu/feed-gr/gr-convergence/src/common/session_context.cc:7240-7277`：基础品类比例、参数读取和归一化。
- `baidu/feed-gr/gr-convergence/src/common/session_context.cc:7280-7351`：Deepes 实验按时长/点击比例调整权重。
- `baidu/feed-gr/gr-convergence/src/common/session_context.cc:7367-7405`：show/click threshold、log10 调整与 min/max ratio。
- `baidu/feed-gr/gr-convergence/src/common/session_context.cc:6912-6947`：compute_jp_recall_quota 的供给再分配 helper。
- `baidu/feed-gr/gr-convergence/src/common/session_context.cc:7913-7974`：三类 waste quota 计算、再分配与 `_jp_*_total_quota` 回写。
- `baidu/feed-gr/gr-convergence/src/recall_framework/processor/jp_processor/new_predictor_processor.cpp:271-280`：quota 为零时跳过精排。
- `baidu/feed-gr/gr-convergence/src/recall_framework/processor/jp_processor/new_predictor_processor.cpp:525-588`：按 predictor/channel 选择 queue、token、predictor_name 和 demand_size。
- `baidu/feed-gr/gr-convergence/src/recall_framework/processor/jp_processor/new_predictor_processor.cpp:592-617`：sid、pass-through 和 queue_quota。
- `baidu/feed-gr/gr-convergence/src/recall_framework/processor/jp_processor/new_predictor_processor.cpp:302-357`：Predictor RPC、容灾、解析和耗时监控。

> KU 正文未读取，需人工补充 DeepesQuotaInfo 的产品口径、实验版本对应关系、线上 quota 基线和多品类收益验收指标。
