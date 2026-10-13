# 2026-08-19 周三代码理解：GRG 个性化关闭开关与 fake_id 兼容边界
<!-- topic-nav:start -->
> 主题：`business/personalization-id` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](2026-07-04-close-individual-fakeid-cross-service-boundary.md)
<!-- topic-nav:end -->


> 日期：2026-08-19  
> 主题来源：2026-06-01 daily-plan 文件缺少明确日计划，按历史未覆盖主题 fallback 到 `GRG` 的个性化关闭开关与 `fake_id` 兼容链路；KU/业务背景需人工补充。  
> 范围：只分析 `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp`、`baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp`、`baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp`，关注基础请求信息、个性化开关注入、散度合并入口与关闭策略的出网边界。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #cbd5e1;border-radius:8px;padding:14px;background:#f8fafc;color:#1f2937;">
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;align-items:stretch;">
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">请求入口</div>
      <div>grg_service.cpp</div>
      <div>解析 uid / cuid / vip</div>
      <div>注入基础上下文</div>
    </div>
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">策略边界</div>
      <div>fake_id / close-personalization</div>
      <div>业务策略分支</div>
      <div>散度合并前的拦截或降级</div>
    </div>
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">合并与响应</div>
      <div>diversity_merge.cpp</div>
      <div>scatter_context.cpp</div>
      <div>返回可出网结果</div>
    </div>
  </div>
  <div style="margin-top:12px;border-top:1px solid #cbd5e1;padding-top:10px;line-height:1.6;">
    这个边界的核心不是“是否推荐”，而是请求基础信息如何影响后续散度合并和个性化关闭策略；`fake_id` 与关闭开关都属于上下文信号，必须在合并前就稳定下来。
  </div>
</div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "grg_service" as Svc
participant "scatter_context" as Ctx
participant "diversity_merge" as Merge
participant "Response" as Resp
Client -> Svc : request(uid/cuid/vip/fake_id)
Svc -> Svc : normalize base info
Svc -> Ctx : build scatter context
Ctx -> Ctx : apply close-personalization flag
Ctx -> Merge : enter diversity merge
Merge -> Merge : branch by scene / fake_id / open flag
Merge --> Resp : assemble result
Resp --> Client : final response
@enduml
```

## 2. 业务结构信息图
```infographic
compare-binary-horizontal-underline-text-vs
data
  title 个性化开关与普通路径对比
  items
    - label 开启个性化
      desc 走散度合并与策略增强，结果更依赖上下文
    - label 关闭个性化
      desc 降到兼容路径，强调稳定出网与基础召回
```

```infographic
list-grid-badge-card
data
  title 请求上下文检查点
  items
    - label uid / cuid
      desc 用户基础标识
    - label vip
      desc 等级或用户分层信号
    - label fake_id
      desc 个性化关闭兼容入口
    - label scene
      desc 决定后续 merge 分支
    - label response budget
      desc 合并后的结果预算
```

## 3. 关键证据
- `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:1-16`：服务入口依赖与上下文相关头文件。
- `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:130-160`：请求基础信息、用户标识与扩展字段注入。
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:89-160`：散度合并主入口与策略分支。
- `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp:1646-1695`：上下文读取与 merge 入口前准备。
- `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp:2174-2208`：解析与字段传递边界。

## 4. Pitfalls
<div style="border:1px solid #cbd5e1;border-left:4px solid #f59e0b;border-radius:6px;padding:10px;background:#fffdf7;">
  <div style="font-weight:700;margin-bottom:4px;">fake_id 只在后段处理</div>
  <div>如果关闭开关在 merge 之后才生效，会出现策略已经分叉、结果却被回收的错位。</div>
</div>
<div style="border:1px solid #cbd5e1;border-left:4px solid #ef4444;border-radius:6px;padding:10px;background:#fffdf7;margin-top:8px;">
  <div style="font-weight:700;margin-bottom:4px;">基础信息缺失导致兼容路径误判</div>
  <div>uid/cuid/vip 任一缺失都可能让关闭逻辑和普通用户路径混在一起，排查时要先看请求上下文是否完整。</div>
</div>
<div style="border:1px solid #cbd5e1;border-left:4px solid #6366f1;border-radius:6px;padding:10px;background:#fffdf7;margin-top:8px;">
  <div style="font-weight:700;margin-bottom:4px;">散度合并与开关配置耦合</div>
  <div>如果 merge 代码同时负责策略和开关判定，后续会很难单独验证关闭开关是否真的生效。</div>
</div>

## 5. 调试 Checklist
```infographic
list-column-done-list
data
  title 调试检查项
  items
    - label 检查请求基础字段
      done true
      desc uid / cuid / vip / fake_id 是否都到位
    - label 检查关闭开关
      done true
      desc 是否在 scatter_context 之前完成
    - label 检查 merge 分支日志
      done true
      desc diversity_merge 是否按预期走兼容路径
    - label 检查结果预算
      done true
      desc 合并后的条数是否被错误截断
    - label 检查场景映射
      done true
      desc scene 是否把关闭分支带偏
```

## 6. 证据来源
- `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:1-16`
- `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:130-160`
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:89-160`
- `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp:1646-1695`
- `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp:2174-2208`

> 内网文档背景需人工补充；这里先用代码库证据固定个性化关闭与 `fake_id` 兼容边界。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260819-grg-close-personalization-fake-id-boundary.md)。
