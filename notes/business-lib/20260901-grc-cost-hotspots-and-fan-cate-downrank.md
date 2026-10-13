# 2026-09-01 周二业务库理解：GRC 成本热点、Set2set 调权与 FanCate DownRank 边界
<!-- topic-nav:start -->
> 主题：`business/cpu-cost` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260707-grc-cpu-cost-optimization-hotspots.md)
<!-- topic-nav:end -->


> 日期：2026-09-01  
> 主题来源：当前可用 daily-plan 仍停留在旧的周计划，未找到 2026-09-01 当日计划；按历史候选回退到 `GRC 成本/CPU 性能优化热点复盘`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/processor/set2set_predict_function.cpp`、`src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp`，聚焦请求链上的成本热点、实验参数分流、PCS 特征回灌和 fan cate 调权。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.05fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">入口链</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/processor/set2set_predict_function.cpp:51-143`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">从 common info、sid info 和实验位里决定 set2set 预测、fallback 和分流路径。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">成本敏感区</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`GET_COMMON_DICT` / `reserve()` / `LOG(TRACE)`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">高频字典读取、容器扩容与 trace 打点构成请求链上的主要成本面。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">业务修正层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`MvFanCateDownRankAdjuster`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">消费 PCS impression / category 结果，按 cocoon_downrank_vec 计算 factor 并回写得分。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">实验位分流</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">PCS 读回</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">factor 计算</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">分数回写</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRC cost hotspot and fan cate downrank boundary
participant "Set2setPredictFunction" as S2S
participant "ExpManager" as EXP
participant "common dict" as DICT
participant "Predictor plugin" as PRED
participant "PCS result" as PCS
participant "MvFanCateDownRankAdjuster" as ADJ
participant "RidTmpInfo" as RID
S2S -> EXP : read many experiment switches
S2S -> DICT : GET_COMMON_DICT for scene-specific factors
S2S -> PRED : predict(request, response, logid, dt_cntl)
PRED --> S2S : model_result / mt_q
S2S -> S2S : reserve(sorted_doc_vec)
S2S -> S2S : SIA_START / SIA_END / SIA_END_WITH_VAL
ADJ -> PCS : read MicrovideoFanCateImpressionInfoGet
ADJ -> EXP : read cocoon_downrank_vec0514
ADJ -> ADJ : parse ratio vector and clamp factor
ADJ -> RID : item->grg_new_score *= factor
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title GRC 成本热点的 6 个信号
  desc 这些信号帮助判断请求链上的 CPU、日志和容器开销来自哪里
  items
    - label 高频实验位读取
      desc `src/processor/set2set_predict_function.cpp:53-143` 连续读取多组 `hit_abtest` 和 `EXPERIMENT_GET_PARAM`
      icon mdi-flask-outline
    - label 字典键拼接
      desc `src/processor/set2set_predict_function.cpp:299-449` 用 flag 拼出 `set2set_dict_*` 键
      icon mdi-pound-box-outline
    - label vector reserve
      desc `src/processor/set2set_predict_function.cpp:787-788` 先预留 `sorted_doc_vec` 容量
      icon mdi-vector-arrange-above
    - label reply reserve
      desc `src/processor/set2set_predict_function.cpp:3332-3337` 按 `mt_q_size` 预留结果向量
      icon mdi-vector-rectangle
    - label trace logging
      desc `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:100-104` 直接拼接 trace 字符串
      icon mdi-text-long
    - label factor clamp
      desc `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:171-181` 用上下界防止数值失控
      icon mdi-arrow-expand-vertical
```

## 3. 代码链路拆解
### 3.1 成本热点的主轴不是一次大循环，而是很多小型高频动作叠加
- `src/processor/set2set_predict_function.cpp:53-143`：大量 `EXPERIMENT_GET_PARAM()`、`EXPERIMENT_HIT_PARAM()` 和 `get_sid_map_var()` 说明每个请求都在读取一串配置开关，逻辑本身不重，但频率高。
- `src/processor/set2set_predict_function.cpp:299-449`：同类 `GET_COMMON_DICT` 调用按不同场景拼接键名，这种模式一旦扩散，会把 CPU 消耗分散在字符串拼接和字典查找上。
- `src/processor/set2set_predict_function.cpp:716-726`：`SerializeToString` 之后立刻打印完整 request，说明调试日志也属于成本面的一部分。

### 3.2 性能优化点集中在容器预留和重复构造上
- `src/processor/set2set_predict_function.cpp:787-788`：`sorted_doc_vec.reserve(input_vec.size())` 是明确的优化信号，作者试图避免后续批量插入的反复扩容。
- `src/processor/set2set_predict_function.cpp:3332-3337`：`model_result.reserve(mt_q_size)` 后再 `emplace_back`，属于比单纯 `push_back` 更稳的批处理写法。
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:81-98`：把两组向量逐项拼成字符串再日志输出，这类 code path 在高 QPS 下会形成可见的字符串分配开销。

### 3.3 FanCate 调权是业务修正层，不是独立召回层
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:47-68`：从 PCS 结果里读取 `MicrovideoFanCateImpressionInfoGet`，再把预测结果里对应 vector 值塞进 `fancate_ratio_vec`。
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:70-78`：调权上下文来自 graph_context 和 exp manager，说明它依赖上游编排结果。
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:150-181`：用 show threshold、ratio threshold、k1/k2 及 factor_min/factor_max 做 clamp，然后回写 `item->grg_new_score`，这是典型的后置修正，不是原始分生成。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">成本问题常常藏在“短路径重复”里</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`GET_COMMON_DICT`、`EXPERIMENT_GET_PARAM` 和日志字符串拼接本身都不复杂，但每个请求都可能触发几十次。排查 CPU 时，不能只看单次函数复杂度，要看这些动作在 hot path 上的频率。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`factor` 的 clamp 逻辑会把极端值压回边界，因此线上看到的结果经常是“有效但偏硬”。要确认的是阈值表、ratio_index 和输入向量长度，而不是先怀疑乘法本身。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 排查顺序：实验位 → 字典键 → reserve → trace string → factor clamp → score 回写</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title GRC 成本热点排查清单
  desc 适用于 CPU 偏高、调权不生效、字典缺键、日志过重和 vector 抖动
  items
    - label 检查实验位读取
      desc 确认 `hit_abtest` / `EXPERIMENT_GET_PARAM` 不在不必要的重复路径里
      done true
    - label 检查字典键拼接
      desc `set2set_dict_*` 键名要和配置内容一致
      done true
    - label 检查 reserve
      desc `sorted_doc_vec` 和 `model_result` 都应按输入规模预留
      done true
    - label 检查 trace 输出
      desc 避免在高频请求路径拼接过长字符串
      done true
    - label 检查 fan cate 输入
      desc `fancate_ratio_vec` 和 `cocoon_downrank_vec` 的长度门槛要满足
      done true
    - label 检查 clamp 边界
      desc `factor_min` / `factor_max` / `1e6` 上下界要符合业务预期
      done true
```

## 6. 证据来源
- `src/processor/set2set_predict_function.cpp:51-143`
- `src/processor/set2set_predict_function.cpp:299-449`
- `src/processor/set2set_predict_function.cpp:716-726`
- `src/processor/set2set_predict_function.cpp:787-788`
- `src/processor/set2set_predict_function.cpp:3332-3337`
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:35-104`
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:115-181`

## 7. 说明
当前运行环境未找到 2026-09-01 的 daily-plan 文件；本笔记基于历史候选与本地代码回退生成，KU 正文未读取，业务背景需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260901-grc-cost-hotspots-and-fan-cate-downrank.md)。
