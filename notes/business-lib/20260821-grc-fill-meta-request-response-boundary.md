# 2026-08-21 周五代码理解：GRC fill_meta 请求与响应边界

> 日期：2026-08-21  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的高优先级候选项回退；本次 cron 未发现可直接执行的当日 daily-plan，KU/业务背景需人工补充。  
> 范围：只分析 GRC 正排补全链路中 `FillMetaBaseProcessor`、`GcmsComponent::query_common()`、`RidTmpInfo`、`MicroVideoInfo/GcmsData` 的请求封装、字段回填、响应出网边界；不展开完整业务策略。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1fr 1.1fr;gap:12px}.arch-col{border:1px solid #d9e2ec;border-radius:10px;padding:12px;background:#fff}.arch-h{font-size:18px;font-weight:800;margin:0 0 8px}.arch-s{font-size:12px;color:#64748b;margin:0 0 10px}.arch-box{border:1px solid #cbd5e1;border-radius:10px;padding:10px 12px;margin:8px 0;background:#f8fafc}.arch-box strong{display:block;font-size:13px;margin-bottom:4px}.arch-arrow{font-size:12px;color:#475569;margin:4px 0;text-align:center}.arch-tag{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px;background:#dcfce7;color:#166534;margin-bottom:8px}.arch-note{font-size:12px;color:#475569;line-height:1.45}</style><div class="arch-wrap"><div class="arch-col"><div class="arch-h">请求到正排的链路</div><div class="arch-s">从召回结果到 Gcms 查询，再回填到 response payload</div><div class="arch-box"><span class="arch-tag">Input</span><strong>召回结果 / rid 列表</strong><span class="arch-note">上游召回提供候选内容，fill_meta 只负责把正排字段补齐。</span></div><div class="arch-arrow">↓</div><div class="arch-box"><span class="arch-tag">Query</span><strong>GcmsComponent::query_common()</strong><span class="arch-note">按 rid 批量取正排信息，返回可写入业务对象的字段集合。</span></div><div class="arch-arrow">↓</div><div class="arch-box"><span class="arch-tag">Output</span><strong>RidTmpInfo / MicroVideoInfo / GcmsData</strong><span class="arch-note">把正排字段写回业务临时结构，并交给后续 response 组装。</span></div></div><div class="arch-col"><div class="arch-h">边界与约束</div><div class="arch-s">fill_meta 不是策略层，也不是完整响应层</div><div class="arch-box"><strong>负责</strong><span class="arch-note">字段拉取、默认值回填、必要的结构适配、上下游结构对接。</span></div><div class="arch-box"><strong>不负责</strong><span class="arch-note">召回排序、策略选择、最终出网格式设计、复杂业务兜底。</span></div><div class="arch-box"><strong>风险</strong><span class="arch-note">字段空洞、正排/召回语义混淆、重复写回、性能热点聚集。</span></div></div></div></div>

## 1. 入口链路
1. 上游召回结果进入 GRC 的正排补全阶段。
2. `FillMetaBaseProcessor` 逐条收集需要补齐的 rid。
3. `GcmsComponent::query_common()` 拉取正排字段。
4. 结果写入 `RidTmpInfo`、`MicroVideoInfo`、`GcmsData` 等中间结构。
5. 后续 response 组装阶段读取这些临时结构完成出网。

## 2. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
start
:进入 fill_meta 阶段;
:收集 rid 与上下文;
:调用 GcmsComponent::query_common();
if (GCMS 返回字段完整?) then (yes)
  :回填 RidTmpInfo;
  :写入 MicroVideoInfo / GcmsData;
  :进入后续 response 组装;
  :输出正排补齐结果;
  stop
else (no)
  :使用默认值或降级字段;
  :记录缺失项与性能观察点;
  :继续 response 组装;
  stop
endif
@enduml
```

## 3. 字段与结构信息图
infographic list-grid-badge-card
data
  title fill_meta 的字段流转
  desc 这一层把召回侧候选转换成可出网的正排补齐结果。
  items
    - label rid 收集
      desc 从召回候选中抽取待补齐对象
      value 1
      icon mdi/numeric-1-box
    - label Gcms 查询
      desc 通过 query_common 批量取正排字段
      value 2
      icon mdi/database-search
    - label 中间结构
      desc RidTmpInfo、MicroVideoInfo、GcmsData
      value 3
      icon mdi/cube-outline
    - label 响应回填
      desc 将字段写回 response 组装链路
      value 4
      icon mdi/reply

theme
  palette #16a34a #0f766e #f59e0b

## 4. 关键观察
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0;">
<div style="border:1px solid #dbe4ee;border-radius:12px;padding:14px;background:#fff;">
<strong>语义边界</strong>
<p style="margin:8px 0 0;color:#334155;line-height:1.6">fill_meta 只负责把业务对象补齐到“可出网”，不应该承担召回决策。越早把职责分开，后面的 response 链路越容易定位问题。</p>
</div>
<div style="border:1px solid #dbe4ee;border-radius:12px;padding:14px;background:#fff;">
<strong>性能边界</strong>
<p style="margin:8px 0 0;color:#334155;line-height:1.6">批量 `query_common()` 的成本通常集中在字段读取和对象回填，排障时优先看字段膨胀、重复查询和重复序列化。</p>
</div>
</div>

## 5. Pitfalls
<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:14px 0;">
<div style="border:1px solid #d9e2ec;border-left:4px solid #16a34a;border-radius:10px;padding:12px;background:#fff;"><strong>字段语义混淆</strong><div style="margin-top:6px;color:#475569;line-height:1.55">把正排字段当成召回特征，或者反过来，会让 response 结果看起来“有值但不对”。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #0f766e;border-radius:10px;padding:12px;background:#fff;"><strong>默认值掩盖缺失</strong><div style="margin-top:6px;color:#475569;line-height:1.55">缺字段时如果静默回填，问题会延后到前端或策略层才暴露。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #f59e0b;border-radius:10px;padding:12px;background:#fff;"><strong>重复回填</strong><div style="margin-top:6px;color:#475569;line-height:1.55">同一 rid 被多次写入，会扩大对象变更范围并放大成本。</div></div>
<div style="border:1px solid #d9e2ec;border-left:4px solid #dc2626;border-radius:10px;padding:12px;background:#fff;"><strong>响应层越权</strong><div style="margin-top:6px;color:#475569;line-height:1.55">fill_meta 不应把最终 response 结构的拼装逻辑一并吃掉。</div></div>
</div>

## 6. 调试 checklist
infographic list-column-done-list
data
  title 调试 checklist
  items
    - label 确认 rid 抽取和 query_common 入参一致
      done true
    - label 确认 Gcms 返回字段写回了对应业务结构
      done true
    - label 确认默认值只用于缺失场景
      done true
    - label 确认没有重复查询或重复回填
      done true
    - label 确认 response 组装仍然只做出网拼接
      done true

theme
  palette #16a34a #0f766e #f59e0b

## 7. 结论
GRC 的 fill_meta 链路是正排补齐边界，不是策略边界。把 `query_common()`、临时结构回填和 response 组装拆开，才能在字段增长和性能波动时快速定位责任点。本文的 KU 业务语义仍需人工补充，但代码职责边界已经清楚。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-01T19:11:38.402084
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析

## 1. 分析摘要

- 从技术笔记看，这次关注的核心不是“完整业务策略”，而是 **GRC 正排补全链路的边界控制**：`FillMetaBaseProcessor` 收集 rid，`GcmsComponent::query_common()` 批量取正排字段，再回填到 `RidTmpInfo / MicroVideoInfo / GcmsData`，最后交给 response 组装出网。
- 对业务代码库来说，这种模式的迁移价值主要体现在 **职责拆分清晰、便于定位字段缺失、降低重复回填和重复序列化成本**。其中 `feeda-mv-grc` 更适合承接这类改造；`feeda-mv-grg` 目前只有极少量目标相关使用，适合做局部参考，不适合大范围硬迁移。

- 从扫描规模看，两个库都大量使用 `std::vector`、`std::string`、`std::unordered_map`，说明代码本身已经是典型的容器驱动型 C++ 工程，适合做“批量查询 + 临时结构回填”的改造。
- 但 `feeda-mv-grg` 只有 1 个文件发现目标库使用，说明经验非常局部；`feeda-mv-grc` 有 10 个文件发现目标库使用，说明已有一定接触面，**迁移收益更大、落地阻力更小**。

---

## 2. 代码库详情

### feeda-mv-grg：序列生成服务

- 目标库使用仅发现 1 个文件：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 这说明在 grg 中，相关能力更像是 **策略规则层的局部应用**，而不是统一的链路性能力。
- 现有代码风格上，grg 已经大量使用标准容器：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 参考代码可见：
  - `model/model.h:9`
  - `model/paddle_model.h:103`
  - `model/paddle_model.h:107`
- 结合这些文件看，grg 的主干更偏 **模型预测、候选集处理、策略计算**，不是典型的 HTTP 响应补全链路，因此填充型边界能力的适配空间有限。

### feeda-mv-grc：召回汇聚服务

- 目标库使用发现 10 个文件，说明适配点更分散，也更接近主链路：
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
- 这类文件多数位于 **processor / operator / service**，与技术笔记中的 `FillMetaBaseProcessor`、`query_common()`、临时结构回填、response 组装边界非常接近。
- 现有 std 使用规模非常大：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 参考代码可见：
  - `service/grc_http_service.cpp:62`
  - `service/grc_http_service.cpp:81`
  - `service/grc_http_service.cpp:152`
- 这说明 grc 已经具备很强的 **请求解析、批量容器处理、响应拼装** 基础，非常适合引入“只负责补齐，不负责策略”的 fill_meta 边界。

---

## 3. 💡 适用性评估与建议

- **建议 1：在 `service/grc_http_service.cpp` 中先做边界收敛，再谈功能迁移**
  - 该文件已有 HTTP 入参解析与响应拼接逻辑，是最适合切出“rid 收集 / 正排查询 / 临时回填”三段式流程的位置。
  - 建议把与 `query_common()` 对应的逻辑收束为一个独立的补齐函数，只输出 `RidTmpInfo` / `MicroVideoInfo` / `GcmsData`，不要直接写最终 response。
  - 这样可以避免 service 层继续膨胀，也方便后续排查“字段是没查到，还是没回填，还是组包漏了”。

- **建议 2：在 `processor/multi_factor/ltr_factor_gen_scene.cpp` 和 `processor/multi_factor/subcate_future_factor_gen.cpp` 中统一临时结构写回方式**
  - 这两个文件都属于特征/因子生成场景，适合采用“只写中间结构，不碰出网结构”的模式。
  - 如果已有候选对象或中间上下文，建议把补齐字段写入与 `RidTmpInfo` 对齐的中间层，而不是散落在多个分支里直接修改业务对象。
  - 这样可以减少重复回填、默认值覆盖和字段语义混淆。

- **建议 3：在 `operator/adjuster/sketchy/duanju_adjuster.cpp`、`operator/adjuster/sketchy/ltv_factor_cp_opt.cpp` 中控制默认值策略**
  - 这类 adjuster 往往会做兜底和修正，和技术笔记中的“缺失时默认值回填”非常接近。
  - 建议把默认值逻辑和缺字段告警分开：默认值只处理缺失场景，不能掩盖正排查询失败。
  - 如果未来接入 fill_meta 式补齐能力，这些文件适合作为“是否需要兜底”的判断参考点。

- **建议 4：在 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 中保留单点参考，不建议扩散到整条策略链**
  - 这是 grg 中唯一发现的目标相关文件，可作为局部实现模板。
  - 如果需要在 grg 中引入类似补齐机制，建议只在策略规则层增加轻量适配，不要把 `Model` 层和策略层揉在一起。
  - 对应参考文件可继续沿用 `model/model.h`、`model/paddle_model.h` 的候选集接口风格，但不要让模型层承担 response 拼装职责。

- **建议 5：如果要做跨库统一，优先抽象“补齐接口”而不是“业务字段列表”**
  - grc 更像链路补齐，grg 更像候选生成；两个库职责不同。
  - 更稳妥的方式是定义统一的补齐入口/中间结构协议，让各自业务只关心“输入 rid / 输出临时字段”，而不是共享最终 response 格式。
  - 这样可以降低后续字段增长带来的改造范围。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：职责边界混淆**
  - 如果 `service/grc_http_service.cpp` 继续直接承担补齐、策略和出网拼接，后续很容易出现“查数逻辑”和“响应逻辑”耦合，问题定位会变慢。
  - 这与技术笔记强调的“fill_meta 不是策略层，也不是完整响应层”一致。

- **风险 2：默认值掩盖缺失**
  - 在 `processor/*` 或 `operator/*` 中如果过早回填默认值，可能让缺字段问题延迟到前端或策略层才暴露。
  - 建议保留缺失统计、告警计数或日志埋点，不要静默吞掉。

- **风险 3：重复回填和重复查询会放大成本**
  - `std::vector`、`std::string` 使用量本来就很大，若同一 rid 在多个阶段被重复写入，会增加对象变更范围和序列化开销。
  - 尤其要警惕 `query_common()` 风格的批量查询被重复触发。

- **风险 4：grg 的迁移基础较弱**
  - `feeda-mv-grg` 只有 1 个文件发现目标相关使用，说明它不是天然的主承载面。
  - 如果强行把补齐边界模式铺开，可能会和现有模型预测流程产生耦合，收益不一定覆盖改造成本。

---

## 结论

- **`feeda-mv-grc` 是主适配对象**：它已经具备请求解析、批量容器操作、响应拼装的天然链路，最适合落地“只负责补齐、不负责策略”的 fill_meta 边界模式。
- **`feeda-mv-grg` 适合做局部参考，不适合重构主链路**：当前目标相关使用太少，更多应保留在策略规则层做轻量适配。
- 总体建议是：先在 `service/grc_http_service.cpp` 这样的入口处收窄职责，再向 `processor/multi_factor/*` 和 `operator/adjuster/*` 横向扩展，避免把补齐逻辑扩散成新的业务耦合点。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
