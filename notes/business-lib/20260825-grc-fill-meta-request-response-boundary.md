# 2026-08-25 周二代码理解：GRC 正排 fill_meta 请求与响应边界

> 日期：2026-08-25  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的历史候选项；本次没有独立的当日 daily-plan 可直接读取，KU 业务背景需人工补充。  
> 范围：只看 `src/processor/fill_meta.cpp` 的 `FillMetaBaseProcessor`、`GcmsComponent::query_common()`、`RidTmpInfo`、`GcmsData`、IFCS 解析与响应封装边界，不展开完整策略树。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:14px;background:#f8fafc;line-height:1.45;">
  <div style="display:grid;grid-template-columns:1.15fr 1.2fr 1fr;gap:12px;align-items:stretch;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求准备</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`FillMetaBaseProcessor::setup()` / `process()`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">构造 `FillMetaBaseContext`，准备 `RidTmpInfo`、请求参数和输出占位。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">核心查询</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`GcmsComponent::query_common()` + IFCS parser</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">把图元、内容元信息与业务字段拼接成可落地的响应视图。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">响应出口</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`VideoInfo` / `NewsInfo` / 结果封装</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">把结构化查询结果落成上层调用方可消费的业务响应。</div>
    </div>
  </div>
  <div style="margin-top:12px;display:grid;grid-template-columns:1fr 80px 1fr 80px 1fr;gap:10px;align-items:center;">
    <div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#4338ca;">请求入参</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">GCMS / IFCS</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">响应组装</div>
  </div>
</div>

## 1. 核心流程
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "FillMetaBaseProcessor" as P
participant "GcmsComponent" as G
participant "IFCS parser" as I
participant "Response builder" as R
Caller -> P : request
P -> G : query_common()
G -> I : parse / normalize
I --> G : data objects
G -> R : assemble response view
R --> P : VideoInfo / NewsInfo
P --> Caller : final response
@enduml
```

## 2. 请求到响应的边界
```infographic
sequence-ascending-steps
  data
    title FillMeta 主链路拆分
    desc 请求准备、GCMS 查询、IFCS 解析、响应封装四段式边界
    items
      - label 预处理
        desc Processor 先把请求参数、上下文和临时结构整理好
      - label GCMS 查询
        desc 通过 query_common 获取统一的内容元数据
      - label IFCS 解析
        desc 将原始返回拆成上层需要的结构化字段
      - label 响应组装
        desc 输出 VideoInfo / NewsInfo 等业务可读对象
```

## 3. 边界判断
- `FillMetaBaseProcessor` 负责串联流程，不负责解释业务字段语义。
- `GcmsComponent::query_common()` 负责拿到通用内容元信息，不负责最终响应形状。
- IFCS parser 负责把原始结构翻译成上层字段，不直接决定业务策略。
- 响应封装层是最后一道边界，错误通常出在字段缺失、类型转换和条件分支不一致。

## 4. 配置/结构信息卡
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:4px solid #0ea5e9;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#0369a1;text-transform:uppercase;letter-spacing:.04em;">关键结论</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">这个链路的核心不是“查到数据”，而是“把 GCMS/IFCS 的通用结构稳定地翻译成业务响应”。边界一旦混乱，后面所有策略分支都会变得难以维护。</div>
</div>

<div style="height:10px"></div>

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fefce8;border:1px solid #fde68a;border-left:4px solid #eab308;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#a16207;text-transform:uppercase;letter-spacing:.04em;">调试提示</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">如果响应字段丢失，先确认是查询侧没拿到，还是 parser 没映射，还是封装层把字段过滤掉。三者不要混为一谈。</div>
</div>

## 5. Pitfalls
- 只检查 `query_common()` 成功会误以为链路正常，实际可能在 parser 或封装层丢字段。
- `RidTmpInfo` 一类临时对象如果生命周期错位，很容易造成响应引用悬空或重用脏值。
- `VideoInfo` 与 `NewsInfo` 这类输出分支如果条件不一致，会出现“同一请求、不同出口”的维护噪音。

## 6. 调试 Checklist
```infographic
list-column-done-list
  data
    title fill_meta 调试清单
    items
      - label 检查请求入口
        desc 确认 processor 是否进入预期分支
        done true
      - label 检查 GCMS 返回
        desc 确认 query_common 是否拿到完整元信息
        done true
      - label 检查 IFCS 解析
        desc 确认原始结构是否被正确映射
        done true
      - label 检查响应封装
        desc 确认最终输出字段没有被条件过滤
        done true
```

## 7. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/fill_meta.cpp`
- `notes/business-lib/20260822-grc-fill-meta-request-response-boundary.md`
- `notes/base-lib/20260822-graphpool-reset-lifecycle.md`

> 说明：当前运行环境没有直接展开代码仓库源码正文，以上代码路径来自 daily-plan 的候选证据摘要；KU/源码正文需人工补充。

---

## 七、业务代码库适配分析
> **分析时间**：2026-09-01T19:15:07.398408
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 1. 分析摘要

- 从扫描结果看，`fill_meta` 这类“请求预处理 → GCMS/中间层查询 → 结构化解析 → 响应封装”的边界拆分模式，在两个业务库里**都有局部落点，但没有形成全局统一范式**。  
- `feeda-mv-grgc` 中仅发现 **1 个文件**使用到目标相关模式，`feeda-mv-grg` 中发现 **10 个文件**有相关使用，说明它更像是**少量试点 + 局部复用**，还谈不上大规模迁移。

- 从 `std` 等价物规模看，两个库对容器和字符串的依赖都很重：  
  - `feeda-mv-grg`：`std::vector` 1969 次 / 356 文件，`std::string` 2443 次 / 425 文件，`std::unordered_map` 734 次 / 205 文件。  
  - `feeda-mv-grgc`：`std::vector` 8520 次 / 1290 文件，`std::string` 7267 次 / 1247 文件，`std::unordered_map` 2860 次 / 646 文件。  
- 这意味着如果引入更严格的请求/响应边界、临时上下文对象、或更轻量的字段映射方式，**收益点主要集中在热点链路和高频处理文件**，迁移潜力是有的，但必须分批做。

---

## 2. 代码库详情

### `feeda-mv-grg`

- 仅发现 **1 个目标相关文件**：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 这表明：
  - 该库里和 `fill_meta` 风格的链路拆分/临时上下文模式结合较少；
  - 更适合先作为**验证性试点**，不建议一开始大面积改造。
- 参考的现有代码形态：
  - `model/model.h`
  - `model/paddle_model.h`
- 这些文件里已有 `std::vector<RidTmpInfoPtr>` 这类临时对象载体，说明在候选集/中间态传递上已经有基础，可以作为后续边界抽象的承载形式参考。

### `feeda-mv-grc`

- 发现 **10 个文件**使用到目标相关模式，集中在：
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
- 这说明 `grc` 更接近 `fill_meta` 这类“中间态转换 + 业务封装”的处理风格，迁移适配空间更大。
- 参考的现有代码形态：
  - `service/grc_http_service.cpp`
    - 已有 `std::unordered_map<std::string, std::vector<int>> depend_map`
    - 已有查询参数解析和响应字符串拼装逻辑
  - 这类代码非常适合作为“请求参数解析 / 中间结果构建 / 响应输出”拆分的落点参考。

---

## 3. 💡 适用性评估与建议

- **建议 1：优先改 `feeda-mv-grc/service/grc_http_service.cpp`，把请求解析和响应拼装拆开。**  
  - 现状里这个文件同时承担了 query 解析、依赖图构造、响应字符串组装等职责。  
  - 建议参考 `fill_meta` 的边界方式，把逻辑拆成：
    - `parse_request_params()`
    - `build_depend_map()`
    - `assemble_response_view()`
  - 适合先在这个文件做局部重构，因为它已经具备明显的“请求→中间态→响应”链路。

- **建议 2：在 `feeda-mv-grc/processor/multi_factor/session_ltr_dibar_factor_gen.cpp` 和 `processor/multi_factor/ltr_factor_gen_scene.cpp` 中引入统一的临时上下文对象。**  
  - 如果这两个文件里存在多层函数共享候选对象、特征对象或临时字段的情况，建议仿照 `RidTmpInfo` 的思路抽出 `Context`/`TmpInfo`。  
  - 目标是避免在多层函数间反复传 `std::vector`、`std::string` 和临时 map，降低拷贝和生命周期混乱风险。

- **建议 3：在 `feeda-mv-grgc/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 先做试点。**  
  - 这个库里当前只发现 1 个相关文件，适合做最小可控改造。  
  - 可以先验证：
    - 解析层是否独立于策略层；
    - 临时对象是否能稳定复用；
    - 响应字段是否可以统一封装。  
  - 如果试点效果稳定，再考虑向 `grc` 扩散。

- **建议 4：对 `feeda-mv-grc/processor/new_adjust/precise_score_init_first_refresh.cpp` 做“字段映射”和“业务决策”分离。**  
  - 这类文件通常容易把原始输入解析、分数修正、条件分支写在一起。  
  - 建议单独抽出一层“IFCS/元数据映射”逻辑，先把原始数据归一化，再进入策略判断。  
  - 这样能减少“字段缺失、类型不一致、条件分支交叉”的问题。

- **建议 5：对 `feeda-mv-grc/operator/adjuster/sketchy/duanju_adjuster.cpp` 重点检查 `std::unordered_map` 和 `std::string` 的临时构造。**  
  - 该类调节器通常是高频路径，若存在频繁构造临时 map / string 的情况，建议：
    - 预留容器容量；
    - 能复用就复用；
    - 只读场景尽量用轻量视图或只读引用。  
  - 这类优化和 `fill_meta` 的“边界清晰 + 中间态稳定”是兼容的。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：临时对象生命周期错位。**  
  - 如果把 `RidTmpInfo` 式的中间态抽到更上层，一定要避免返回局部对象引用、悬空指针或跨层复用脏值。

- **风险 2：额外的字段拷贝和封装开销。**  
  - 边界拆分后，`std::string`、`std::vector`、`std::unordered_map` 之间的转换可能增加复制成本。  
  - 在热点链路里要关注 `reserve()`、移动语义和只读视图的使用。

- **风险 3：不同出口分支语义不一致。**  
  - `VideoInfo`、`NewsInfo` 或其他业务响应分支如果封装规则不一致，容易出现“同一请求、多种出口”的维护问题。  
  - 需要统一响应 schema 和错误处理方式。

- **风险 4：不要对低覆盖库强行全量迁移。**  
  - `feeda-mv-grg` 当前只有 1 个相关文件，说明它更适合试点，不适合直接做全仓级别的结构调整。  
  - 否则会出现改动成本高、收益不稳定的问题。

---

如果你愿意，我可以继续把这份内容整理成你技术笔记里可直接粘贴的“章节版”文案，或者再补一版“更偏落地迁移清单”的格式。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
