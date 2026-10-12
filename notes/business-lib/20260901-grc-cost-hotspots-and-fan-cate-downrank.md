# 2026-09-01 周二业务库理解：GRC 成本热点、Set2set 调权与 FanCate DownRank 边界

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

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-03T19:02:52.129966
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 分析摘要

- 结合本次技术笔记，这里更适合按**“标准库容器/字符串热点优化实践”**来理解：重点是 `std::vector` 的 `reserve/emplace_back`、`std::string` 的临时对象控制、`std::unordered_map` 的键查找与预留，以及高频路径上的日志拼接控制。
- 从扫描结果看，**feeda-mv-grc** 的相关使用面更广，已经有较多 `std::vector/std::string/std::unordered_map` 的落点，适合做局部热路径优化和规则链改造；**feeda-mv-grg** 只有 1 个目标文件命中，说明迁移/优化更适合先做小范围试点，再决定是否扩散。

## 代码库详情

### feeda-mv-grg

- 目标技术命中较少，仅发现 1 个文件：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 现有标准库使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件
- 结论：
  - 该库整体已经大量使用标准容器，但和本次笔记对应的**热路径优化写法**并未形成明显的集中落点。
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 可作为**规则分流/局部调权**类逻辑的参考点，适合先验证 `reserve()`、减少临时字符串、减少重复查表等改造收益。

### feeda-mv-grc

- 目标技术命中较多，共发现 10 个文件，覆盖面更广：
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
- 典型参考代码：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    ```
  - 这类写法说明该库已经在使用标准容器组织请求图、依赖图或响应数据，和本次笔记里的“实验位分流、PCS 回灌、factor 计算”模式是兼容的。
- 现有标准库使用规模：
  - `std::vector`：8520 次，分布在 1290 个文件
  - `std::string`：7267 次，分布在 1247 个文件
  - `std::unordered_map`：2860 次，分布在 646 个文件
- 结论：
  - 这是一个**高适配、可规模化优化**的代码库。
  - 由于相关容器已经广泛使用，适合在热路径文件里做定点优化，而不是一次性大面积替换。

## 💡 适用性评估与建议

- `feeda-mv-grc/service/grc_http_service.cpp`
  - 适合优先做**请求处理热路径优化**：若这里存在 query 解析、依赖图构建、响应拼装等逻辑，建议给 `std::vector` 和 `std::unordered_map` 补 `reserve()`，减少扩容和 rehash。
  - 如果有频繁的字符串拼接，建议把只读参数改成更轻量的传参方式，并避免在循环里反复构造临时 `std::string`。

- `feeda-mv-grc/processor/multi_factor/ltr_factor_gen_scene.cpp`
  - 适合做**factor 向量生成优化**：如果这里按 item 批量生成特征/因子，建议统一使用 `reserve()` + `emplace_back()`，减少拷贝和二次分配。
  - 这类场景和笔记中的 `sorted_doc_vec.reserve()`、`model_result.reserve()` 是同一类优化模型。

- `feeda-mv-grc/processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - 适合检查是否存在**多轮构造临时对象**的问题，尤其是字符串拼接、重复查字典、重复构造中间 vector。
  - 如果这里的逻辑涉及多条件分支，可参考笔记中的“实验位分流 + 后置修正”思路，把开关读取集中在入口，减少每个 item 重复判断。

- `feeda-mv-grc/processor/new_adjust/precise_score_init_first_refresh.cpp`
  - 适合承接笔记里的 **fan cate downrank / factor clamp** 思路。
  - 如果这里有“初始分数修正、阈值裁剪、上下界保护”的逻辑，建议把边界参数集中配置，并将 clamp 放在统一函数里，避免多处手写导致行为不一致。

- `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 这是 `feeda-mv-grg` 里最适合做**试点迁移**的文件。
  - 建议先在这个规则文件里验证：  
    - 是否能通过 `reserve()` 降低 vector 反复扩容；  
    - 是否能减少日志中的字符串拼接；  
    - 是否能把重复读取的配置项缓存到局部上下文中。
  - 如果试点收益明显，再扩展到同类 diversity / rule 文件。

## ⚠️ 引入风险与限制

- **预留容量不是越大越好**
  - `reserve()` 需要对输入规模有较准的估计，否则可能带来额外内存占用。
  - 对于波动较大的请求，建议按真实分布做压测后再定容量策略。

- **字符串优化可能影响可读性与调试效率**
  - 过度拆分日志、减少拼接，虽然能降 CPU，但也可能削弱排障信息完整性。
  - 建议对高频路径做分级日志：默认轻日志，必要时再开 trace 细节。

- **阈值和 clamp 改造会影响业务结果**
  - 笔记中的 `factor_min/factor_max`、ratio 阈值、上下界保护，本质上会影响最终排序分数。
  - 迁移时必须配套 A/B 和回放验证，避免“性能变快但排序漂移”。

- **热路径缓存需要注意生命周期和线程安全**
  - 如果把实验位、配置项或中间结果做缓存，一定要确认请求级生命周期、并发读写安全和失效策略。
  - 尤其在 `processor/*` 这类链路中，跨请求共享状态容易引入隐蔽 bug。

如果你愿意，我可以继续把这份报告整理成**可直接放进技术笔记的成稿版**，或者进一步补成**“迁移优先级表”**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
