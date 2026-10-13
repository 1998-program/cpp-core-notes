# 2026-08-21 周五代码理解：GRC fill_meta 请求与响应边界
<!-- topic-nav:start -->
> 主题：`business/gcms-fillmeta` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260601-grc-gcms-ifcs-fill-meta-chain.md)
<!-- topic-nav:end -->


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

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260821-grc-fill-meta-request-response-boundary.md)。
