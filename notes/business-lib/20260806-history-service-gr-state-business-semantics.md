# 2026-08-06 周四代码理解：`gr_state` 业务语义与展现状态边界
<!-- topic-nav:start -->
> 主题：`business/state-semantics` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](2026-06-21-hs-state-business-semantics-grstate.md)
<!-- topic-nav:end -->


> 日期：2026-08-06  
> 主题来源：2026-08-06 daily-plan 缺失，按历史未覆盖主题 fallback 到 `gr_state` 业务语义与展现边界  
> 服务：`feeda-dc-gr`  
> 范围：分析 `ShowStateInfo` / `CardShowStateInfo` 的状态枚举、历史传递逻辑与业务过滤条件；本文不展开 History Service 存储实现。  
> 内网文档：当前环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px;min-height:150px}.arch-col h3{margin:0 0 8px;font-size:14px;color:#102a43}.arch-box{background:#f1f5f9;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;margin:8px 0;font-size:13px;line-height:1.45}.arch-box strong{display:block;color:#102a43;margin-bottom:2px}.arch-note{font-size:12px;color:#52606d;line-height:1.5;margin-top:8px}.arch-arrow{font-size:12px;color:#64748b;text-align:center;margin:4px 0}</style><div class="arch-wrap"><div class="arch-col"><h3>输入侧</h3><div class="arch-box"><strong>历史上下文</strong><span>`_news_showstate` / `_video_showstate` 里保存已下发、未展现、已展现、已点击等状态。</span></div><div class="arch-box"><strong>请求条件</strong><span>`channel_id`、实验开关、`_feed_user_beha_obtained`、`show_num`、`refresh_state` 等共同决定是否透传。</span></div><div class="arch-note">这里的重点不是“展示什么”，而是“某条历史是否应该继续影响当前请求”。</div></div><div class="arch-col"><h3>状态语义</h3><div class="arch-box"><strong>ShowStateInfo</strong><span>`NO_ACTUAL_ISSUED / ISSUED / UNSHOW / SHOWED / CLICKED` 描述下发到点击的完整演进。</span></div><div class="arch-box"><strong>CardShowStateInfo</strong><span>敏捷卡也复用同样的状态层次，`show_tss` / `click_tss` 只是时间戳附属字段。</span></div><div class="arch-note">状态枚举是业务边界的核心，不要把它误读成纯存储字段。</div></div><div class="arch-col"><h3>输出侧</h3><div class="arch-box"><strong>历史传递过滤</strong><span>命中传递实验时，ISSUED 且过期的条目会被过滤掉。</span></div><div class="arch-box"><strong>最终下发</strong><span>未点击、未命中历史、且满足 refresh / 场景门槛的条目才保留下来。</span></div><div class="arch-note">这条链路是“业务决定状态可见性”，不是“状态决定业务行为”。</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "GRSessionContext" as Ctx
participant "NewsHistory" as NH
participant "ShowStateInfo" as SSI
participant "NewsMessage" as Msg
Ctx -> NH : get_shown_pb()
NH -> SSI : lookup nid in _news_showstate
NH -> NH : check experiment / channel / refresh_state
NH -> SSI : compare get_state()
alt ISSUED and expired
  NH -> NH : filter item
else SHOWED or CLICKED
  NH -> Msg : keep or attach shown pb
end
NH -> Msg : append output state
@enduml
```

## 2. 业务状态信息图

infographic compare-hierarchy-left-right-circle-node-pill-badge
 data
  title `ShowStateInfo` 状态与业务动作
  desc 同一套状态枚举贯穿下发、展现、点击和未实际下发分支，避免业务判断散落在多个 if 分支里
  items
    - label `NO_ACTUAL_ISSUED`
      desc 竞品替换 / 图片去重等场景下的未实际下发 nid
      icon mdi/close-circle
      children
        - label 不参与正常展现链路
          desc 只作为边界标记存在
    - label `ISSUED`
      desc 已下发但可能尚未展现
      icon mdi/send
      children
        - label 过期后可被历史传递逻辑过滤
          desc `ISSUED` + 过期窗口触发过滤
    - label `UNSHOW`
      desc 已下发但未展现
      icon mdi/timer-sand
      children
        - label 仍是后续去重 / 保留判断的输入
          desc 不等于无效状态
    - label `SHOWED`
      desc 已展现
      icon mdi/eye
    - label `CLICKED`
      desc 已点击
      icon mdi/cursor-default-click
 theme
  palette #3d5a80 #2d6a4f #64748b

## 3. 关键结论

<div style="display:grid;grid-template-columns:1.15fr 1fr;gap:12px;margin:16px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-left:4px solid #3d5a80;border-radius:10px;padding:14px"><div style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:#486581;margin-bottom:6px">结论</div><div style="font-size:14px;line-height:1.7;color:#1f2937">`NewsHistory::get_shown_pb()` 通过实验开关、频道、行为缓存和状态枚举共同决定历史是否继续参与当前下发。`ShowStateInfo` 的状态不是摆设，它直接控制历史传递、去重和展示保留的边界。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-left:4px solid #2d6a4f;border-radius:10px;padding:14px"><div style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:#486581;margin-bottom:6px">内网文档</div><div style="font-size:14px;line-height:1.7;color:#1f2937">当前环境没有可用 KU 文档 URL/doc-id，历史背景与指标口径需要人工补充。</div></div></div>

## 4. Pitfalls

<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px"><div style="font-size:13px;font-weight:700;color:#102a43;margin-bottom:6px">状态误读</div><div style="font-size:14px;line-height:1.7;color:#243b53">`ISSUED` 不等于“已经成功展现”；它只是下发态。真正的展现、点击要看后续状态和时间戳。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px"><div style="font-size:13px;font-weight:700;color:#102a43;margin-bottom:6px">场景误配</div><div style="font-size:14px;line-height:1.7;color:#243b53">`TAB_RECOMMENDATION`、实验命中和 `show_num` 门槛一起生效，单看某一个条件会误判历史是否该透传。</div></div></div>

## 5. 调试 Checklist

infographic sequence-snake-steps-simple
 data
  title `gr_state` 业务排查步骤
  items
    - label 检查实验开关
      desc 确认历史传递实验是否命中。
      done true
    - label 检查频道条件
      desc 重点看 `TAB_RECOMMENDATION` 等入口条件。
      done true
    - label 检查状态枚举
      desc 确认 `ISSUED` / `UNSHOW` / `SHOWED` / `CLICKED` 的解释一致。
      done true
    - label 检查时间窗
      desc `FLAGS_issue_expire_time` 会影响 ISSUED 过滤分支。
      done true
    - label 检查行为缓存
      desc `_news_showstate` 为空时，过滤条件会短路。
      done true

## 证据来源

- `feeda-dc-gr/history/news_history.cc:139-158`
- `feeda-dc-gr/history/show_dup.h:55-134`
- `feeda-dc-gr/history/news_history.cc:150-170`
- `feeda-dc-gr/history/news_history.cc:241-246`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260806-history-service-gr-state-business-semantics.md)。
