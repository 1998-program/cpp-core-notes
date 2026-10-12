# 2026-09-09 业务库理解：hudong_v5 互动信号调权

> 日期：2026-09-09  
> 主题选择：未发现当日有效 daily-plan；旧计划中已覆盖的成本、quota、看剧/合集策略主题不再重复。本次按邻近未覆盖候选回退到 `MicrovideoHudongV5PreciseAdjuster`，聚焦互动信号的请求级统计基线、item 级缺失值回填和乘积调权。KU 正文未读取，业务背景需人工补充；本文只陈述代码可验证的行为。  
> 范围：GRC precise adjuster 的基类契约、互动 v5 实现、GCMS/NewsInfo 字段读取、实验参数与采样日志。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">依赖层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">SidInfo + CommonInfo + rid_vec</div><div style="margin-top:8px;font-size:12px;color:#52606d;">请求级参数来自实验上下文，候选逐项携带 NewsInfo 或 GCMS VideoInfo。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">统计与调权层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">P50 baseline → missing-value fallback → power transform</div><div style="margin-top:8px;font-size:12px;color:#52606d;">收藏、点赞、评论、分享四个信号分别做中位数基线，再按实验系数幂变换。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">输出层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">factor × grg_new_score</div><div style="margin-top:8px;font-size:12px;color:#52606d;">只对满足 item 条件的候选生效；日志按 logid % 200 采样，clear() 在复用前清空请求状态。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 62px 1fr 62px 1fr 62px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">四类互动信号</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">P50 统计基线</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">四项幂变换</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">最终 factor</div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title hudong_v5 调权：请求级统计到 item 级 factor
participant "AdjustEngine" as ENG
participant "HudongV5 adjuster" as ADJ
participant "Experiment manager" as EXP
participant "rid_vec" as RID
participant "NewsInfo / VideoInfo" as INFO
participant "score output" as OUT

ENG -> ADJ : calculate_list_conditions(rid_vec)
ADJ -> EXP : read collet_digg_comment_share_t_1400_400_vec
EXP --> ADJ : four exponent coefficients
loop each candidate
  ADJ -> INFO : read collect / digg / comment / share
  ADJ -> ADJ : keep positive samples per signal
end
ADJ -> ADJ : sort each vector and calculate P50
ADJ --> ENG : request-level baseline ready

loop each eligible item
  ENG -> ADJ : calculate_conditions(item)
  ADJ -> INFO : read four interaction fields
  alt field > 0
    ADJ -> ADJ : use item value
  else missing or non-positive
    ADJ -> ADJ : use corresponding P50
  end
  ADJ -> ADJ : pow(max(value, 1e-6), exponent)
  ADJ -> OUT : factor *= collect * digg * comment * share
  ADJ -> OUT : grg_new_score *= factor component
end
ENG -> ADJ : clear()
ADJ -> ADJ : reset coefficients and P50 state
@enduml
```

## 2. 参数与信号信息图

```infographic
infographic hierarchy-structure
data
  title hudong_v5 的四信号调权结构
  desc 请求级先建立统计基线，候选级再计算乘积因子
  items
    - label 收藏 collect
      desc get_content_collect_es；正值进入 P50 样本，非正值回填 collect_50
      icon mdi:bookmark-outline
    - label 点赞 digg
      desc get_content_digg_es；指数来自实验参数向量第 2 个槽位
      icon mdi:thumb-up-outline
    - label 评论 comment
      desc get_content_comment_es；缺失时使用 comment_50，避免直接把缺失当成零
      icon mdi:comment-outline
    - label 分享 share
      desc get_content_share_es；与其他三个信号相乘形成 score_tmp
      icon mdi:share-variant-outline
    - label 实验参数
      desc collet_digg_comment_share_t_1400_400_vec 至少 4 个值，依次对应四项 exponent
      icon mdi:tune-variant
    - label 结果写回
      desc factor *= score_tmp；候选分数由调用链决定是否叠加到 grg_new_score
      icon mdi:chart-line
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 基类把 adjuster 拆成请求级、候选级和清理级

`src/operator/adjuster/base/base_adjuster.h:21-29` 将执行引擎入口固定为 `preprocess`、`calculate_list_conditions`、`calculate_conditions` 和 `calculate_factor`；`src/operator/adjuster/base/base_adjuster.h:41-56` 则规定业务实现的三个扩展点，并要求 `clear()` 纯虚实现。互动 v5 因此不是在单个候选上直接算四项信号，而是先在请求级准备参数和基线。

### 3.2 请求级阶段同时读取实验参数与候选分布

`src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:22-41` 先读取 `SidInfo`、`CommonInfo` 和 `CommonAdjustContext`，再从实验管理器读取 `collet_digg_comment_share_t_1400_400_vec`。只有参数至少四项时才覆盖默认系数。

`src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:48-85` 遍历候选：图文从 `_news_info` 读取字段，视频从 `gcms_data->_video_info` 读取字段；每个信号只把大于零的值放入独立 vector。这个设计把“无互动”与“缺少可用观测”统一交给后续 P50 回填处理。

### 3.3 P50 是候选级缺失值的请求内基线

`src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:88-91` 对四个向量分别计算 50 分位数。`calculateAllPercentilesOptimized` 在 `:161-179` 中排序后按 `(size - 1) * percentile` 计算精确位置；空向量返回 `1e-1`，避免后续幂函数直接作用于零。

这意味着同一请求内不同候选的缺失互动字段使用相同基线，能够减少极端长尾值对调权的影响。但该函数会原地排序输入 vector；当前代码只在本次请求构建的临时 vector 上调用，因此没有破坏候选原始顺序。

### 3.4 四项幂变换最终以乘积进入 factor

`src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:111-136` 读取 item 的四个字段，对非正值回填对应 P50，然后执行 `pow(max(value, 1e-6), exponent)`。四个结果相乘形成 `score_tmp`，再乘到传入的 `factor`。

`calculate_conditions` 在 `:97-99` 只允许 `item != nullptr && item->gcms_data != nullptr` 的候选进入调权；因此图文虽然在请求级统计阶段读取了 `_news_info`，但当前 item 级条件本身要求 `gcms_data`，这里是一个需要在联调时明确的边界。

### 3.5 日志采样与 clear 共同承担线上排查与复用隔离

`src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:107-109` 以 `logid % 200 == 0` 采样，`:139-147` 同时输出四项 before/after、实验系数、P50、score_tmp 和 factor。`src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:151-159` 的 `clear()` 将四个 exponent 和四个 P50 全部归零，避免对象池或引擎复用时残留上一个请求的统计状态。

### 3.6 注册宏把实现接入状态算子工厂

`src/operator/adjuster/base/base_adjuster.h:64-65` 将 `REGISTER_ADJUSTER` 展开为 `FACTORY_REG_STATE_OPERATOR_CONF`；实现文件末尾 `microvideo_hudong_v5_precise_adjuster.cpp:192` 注册 `MicrovideoHudongV5PreciseAdjuster`。配置侧只需使用注册名称即可把该算法接入 adjust engine。

## 4. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#F5F7FA;border-radius:12px;padding:20px;margin:18px 0;border-left:8px solid #3D5A80;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:800;">Pitfalls / Hudong V5</div><h3 style="font-size:24px;line-height:1.2;margin:8px 0 12px;">互动调权的结果取决于“参数顺序、P50 基线、item 条件、对象清理”四个环节同时成立。</h3><div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;"><div><ul style="font-size:14px;line-height:1.65;margin:0 0 0 18px;"><li><b>参数顺序错位：</b><code>[collect, digg, comment, share]</code> 的位置是隐式协议，字典或实验配置改顺序会改变算法含义。</li><li><b>空样本基线：</b>空 vector 返回 <code>1e-1</code>，不能在重构时改成零，否则 <code>pow</code> 结果和排序行为都会变。</li><li><b>图文/视频边界：</b>请求级支持 NewsInfo 和 GCMS，但 item 级 <code>calculate_conditions</code> 当前只放行 GCMS。</li><li><b>统计污染：</b>请求级 vector 应是临时样本；不要把候选原始容器传给会原地排序的 P50 函数。</li><li><b>复用残留：</b><code>clear()</code> 漏清任一 P50 或 exponent 都可能把上一个请求的状态带入本请求。</li><li><b>分数写回口径：</b>当前实现更新 <code>factor</code>，而 <code>grg_new_score</code> 写回需结合调用方确认是否会叠加。</li></ul></div><div style="border-left:3px solid #F59E0B;padding-left:12px;color:#374151;font-size:14px;line-height:1.65;">评审顺序：先确认参数协议，再验证请求级 P50，最后用采样日志核对单 item 的四项 before/after。</div></div></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title hudong_v5 调试 checklist
  desc 覆盖输入、统计、候选条件、数值和生命周期
  items
    - label 依赖对象完整
      desc SidInfo、CommonInfo、CommonAdjustContext 和 graph_context 均非空
      done true
    - label 参数向量顺序正确
      desc 实验参数前四项依次是收藏、点赞、评论、分享指数
      done true
    - label P50 样本只收正值
      desc 四类 vector 的构造逻辑与字段来源符合预期，空样本走 1e-1
      done true
    - label item 条件符合场景
      desc 检查 gcms_data 条件是否与图文/视频流量范围一致
      done true
    - label 缺失值回填稳定
      desc 非正字段使用对应 P50，且 max(value, 1e-6) 防止数值异常
      done true
    - label factor 乘积可解释
      desc 记录四个幂变换结果、score_tmp 与最终 factor，核对指数影响方向
      done true
    - label clear 完整
      desc 请求结束或对象复用前清除四个系数和四个 P50
      done true
    - label 采样日志可关联
      desc 使用 logid % 200 的样本同时核对 rid、before/after、P50 和 factor
      done true
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 6. 证据来源

- `src/operator/adjuster/base/base_adjuster.h:21-29`：Adjuster 引擎 final 入口。
- `src/operator/adjuster/base/base_adjuster.h:41-56`：业务扩展点与 clear 契约。
- `src/operator/adjuster/base/base_adjuster.h:64-75`：注册宏与依赖读取宏。
- `src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:22-41`：请求级参数读取。
- `src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:48-93`：NewsInfo/GCMS 信号收集与 P50。
- `src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:97-149`：item 条件、幂变换、factor 与采样日志。
- `src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:151-179`：clear 与百分位实现。
- `src/operator/adjuster/precise/microvideo_hudong_v5_precise_adjuster.cpp:192`：adjuster 注册点。
