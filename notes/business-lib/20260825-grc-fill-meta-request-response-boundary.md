# 2026-08-25 周二代码理解：GRC 正排 fill_meta 请求与响应边界
<!-- topic-nav:start -->
> 主题：`business/gcms-fillmeta` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260601-grc-gcms-ifcs-fill-meta-chain.md)
<!-- topic-nav:end -->


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

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260825-grc-fill-meta-request-response-boundary.md)。
