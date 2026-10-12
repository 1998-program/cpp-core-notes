# DC-GRG show-state 消费：`UNKNOWN` 兜底与关注频道去重边界

> 日期：2026-07-26  
> 类型：业务库笔记  
> 主题：业务侧并不直接消费 History Service 的存储细节，而是在 `HisInfo`、`ShowStateInfo` 与去重策略中消费状态语义；`UNKNOWN` 是可解释的兜底，不应被误改成默认已下发。

<style>.biz26-arch{font-family:Inter,Arial,sans-serif;background:#f8fafc;border:1px solid #d7dee8;border-radius:8px;padding:18px;margin:14px 0;color:#1f2937}.biz26-title{font-size:20px;font-weight:800;margin-bottom:12px}.biz26-grid{display:grid;grid-template-columns:1fr 1.25fr 1fr;gap:12px}.biz26-box{background:#fff;border:1px solid #dbe3ef;border-radius:7px;padding:12px}.biz26-box h3{font-size:14px;margin:0 0 8px;color:#2f5d50}.biz26-tag{display:inline-block;border:1px solid #b8d0c7;background:#eef7f3;border-radius:999px;padding:3px 8px;margin:3px 4px 3px 0;font-size:12px}.biz26-flow{margin-top:12px;display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.biz26-step{border-left:4px solid #3f846f;background:#fff;padding:10px;border-radius:6px;font-size:13px}.biz26-note{font-size:12px;color:#5f6b7a;margin-top:8px}</style><div class="biz26-arch"><div class="biz26-title">架构全景：业务只消费状态语义，不消费存储实现</div><div class="biz26-grid"><div class="biz26-box"><h3>History 结果</h3><span class="biz26-tag">NewsMessage</span><span class="biz26-tag">ShowStateInfo</span><p>上游把状态透传为可读语义，业务侧不需要知道 Redis/SNDB 哪条通道命中。</p></div><div class="biz26-box"><h3>Session 聚合</h3><span class="biz26-tag">HisInfo</span><span class="biz26-tag">nid -> state map</span><span class="biz26-tag">answer/card map</span><p>`session_context.h` 把 history item 写入不同 map，缺失状态时显式填充 UNKNOWN。</p></div><div class="biz26-box"><h3>策略消费</h3><span class="biz26-tag">DedupStrategy</span><span class="biz26-tag">TAB_GUANZHU</span><span class="biz26-tag">UNSHOW/UNKNOWN</span><p>关注频道会结合正排与历史状态判断是否去重，避免未真实下发资源被误杀。</p></div></div><div class="biz26-flow"><div class="biz26-step">1. History response 进入业务 session</div><div class="biz26-step">2. 缺失状态写入 `ShowStateInfo::UNKNOWN`</div><div class="biz26-step">3. 去重策略按频道和状态决定是否保留</div></div><div class="biz26-note">业务含义：UNKNOWN 表示状态不可证明，不等价于已展示，也不等价于可随意忽略。</div></div>

## 核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam roundcorner 8
left to right direction
start
:读取 History Service 返回;
:按 nid/card/answer 维度写入 HisInfo;
if (history item 有明确 state?) then (yes)
  :保存 ShowStateInfo state;
else (no)
  :showinfo.set_state(UNKNOWN);
endif
if (关注频道 && NID/VID 去重策略?) then (yes)
  :读取正排信息;
  :get_video_show_state_by_nid(id);
  if (UNSHOW 或 UNKNOWN?) then (yes)
    :未真实下发资源不去重;
  else (no)
    :按重复资源处理;
  endif
else (no)
  :走通用去重路径;
endif
stop
@enduml
```

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title `UNKNOWN` 与 `UNSHOW` 的业务边界
  desc 两者都可能让资源免于误去重，但表达的问题不同
  items
    - label UNKNOWN
      desc 状态链路没有给出可证明结果；优先排查透传、合并和开关
      icon mdi/help-circle-outline
    - label UNSHOW
      desc 明确知道未展示；优先检查真实下发、频道策略和去重条件
      icon mdi/eye-off-outline
```

## 代码理解

`session_context.h` 中业务 session 会维护多组 nid 到 show-state 的映射，包括 card、answer 等场景。对 answer 链路，代码在缺失可用状态时调用 `showinfo.set_state(ShowStateInfo::UNKNOWN)`，这说明 UNKNOWN 是显式业务状态，不是 proto 默认值的偶然结果。

`dedup_strategy.cc` 的关注频道分支会先判断去重策略类型，再读取正排和 `get_video_show_state_by_nid(id).get_state()`。当状态为 `UNSHOW` 或 `UNKNOWN` 时，注释写明“关注频道未真实下发直播资源不去重”。因此，业务排查时要把 UNKNOWN 看作保护性语义：它防止状态不可证明的内容被去重策略提前误杀。

## Pitfalls

<style>.biz26-card{font-family:Inter,Arial,sans-serif;background:#fff;border:1px solid #d7dee8;border-radius:8px;padding:16px;margin:14px 0;color:#1f2937}.biz26-card h3{margin:0 0 8px;font-size:18px}.biz26-panels{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}.biz26-panel{border-top:3px solid #3f846f;background:#f8fafc;padding:10px;border-radius:6px}.biz26-panel strong{display:block;margin-bottom:6px;color:#24483f}.biz26-panel p{margin:0;font-size:13px;line-height:1.55}</style><div class="biz26-card"><h3>排障卡片：状态兜底不要改成静默默认值</h3><div class="biz26-panels"><div class="biz26-panel"><strong>误区</strong><p>把 UNKNOWN 当成普通默认值，直接映射为 SHOWED 或 UNSHOW。</p></div><div class="biz26-panel"><strong>风险</strong><p>关注频道、直播资源和视频去重会出现误杀或重复下发。</p></div><div class="biz26-panel"><strong>做法</strong><p>保留 UNKNOWN，并向上游确认 `gr_state` 透传和 History 合并是否完整。</p></div></div></div>

```infographic
infographic list-grid-badge-card
data
  title 业务侧检查清单
  desc 定位 show-state 异常时先确认语义，再确认策略
  items
    - label HisInfo map
      desc nid/card/answer 维度是否写入对应 show-state map
      icon mdi/map-search-outline
    - label UNKNOWN 来源
      desc 是 History 缺失、合并缺失，还是业务主动兜底
      icon mdi/help-rhombus-outline
    - label 频道条件
      desc `TAB_GUANZHU` 是否进入特殊去重保护分支
      icon mdi/filter-outline
    - label 去重策略
      desc NID/Bloom NID/Bloom VID 分支是否符合预期
      icon mdi/call-split
```

## 证据来源

- `feeda-dc-grg/src/common/session_context.h:1050-1058`：answer/card 维度 history item 写入业务侧状态映射。
- `feeda-dc-grg/src/common/session_context.h:1061-1077`：answer showlist 清理中显式使用 `showinfo.set_state(ShowStateInfo::UNKNOWN)`。
- `feeda-dc-grg/src/strategy/dedup_strategy.cc:1424-1437`：关注频道未真实下发资源不去重，状态读取来自 `get_video_show_state_by_nid(id).get_state()`，并判断 `UNSHOW`/`UNKNOWN`。
- `src/history/strategy.cc:657-671`：History Service 合并阶段保留 UNKNOWN 判断，是业务 UNKNOWN 的上游来源之一。
