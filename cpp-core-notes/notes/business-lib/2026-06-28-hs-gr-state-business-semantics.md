# 2026-06-28 业务库代码理解：点展状态 gr_state 解析口径与防误用

> 今日主题来源：本机未发现可用 `daily-plan` 文件；按周日 fallback 选择 History Service 点展状态业务语义，与今日基础库 `gr_state` 通道契约配对。  
> 内网 KU：本次任务未提供 KU URL/doc-id；产品口径、实验策略、线上看板需人工补充。本文以代码库证据和基础库机制为准。  
> 范围：业务侧如何从 `gr_state` bytes 解析 `NewsMessage.state[]`，以及如何避免把 `ShowClkState`、`show_tss/click_tss`、普通 Redis protocol 字段误当同一套状态。

## 架构全景图

<style>.arch-0628-biz{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #cbd5e1;border-radius:20px;padding:18px;color:#1e293b}.arch-0628-biz .title{font-size:24px;font-weight:900;margin-bottom:6px}.arch-0628-biz .sub{font-size:13px;color:#64748b;margin-bottom:16px}.arch-0628-biz .grid{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px}.arch-0628-biz .lane{background:#fff;border:1px solid #dbeafe;border-radius:16px;padding:12px}.arch-0628-biz h3{font-size:15px;margin:0 0 10px;color:#1d4ed8}.arch-0628-biz .box{background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;padding:10px;margin:8px 0}.arch-0628-biz .box b{display:block;font-size:13px;margin-bottom:4px}.arch-0628-biz .box span{font-size:12px;color:#64748b;line-height:1.5}.arch-0628-biz .hot{background:#eff6ff;border-color:#93c5fd}.arch-0628-biz .note{margin-top:12px;border-left:4px solid #3b82f6;background:#eef6ff;border-radius:10px;padding:10px;font-size:13px;color:#334155}</style><div class="arch-0628-biz"><div class="title">点展状态业务解析全景</div><div class="sub">业务规则应绑定字段来源：只有 `gr_state -> NewsMessage -> nids[]/state[]` 是稳定状态链路；同名枚举、时间戳、透传字段都不能混用。</div><div class="grid"><div class="lane"><h3>稳定状态来源</h3><div class="box hot"><b>HistoryContent.gr_state</b><span>HS/IDM 响应中的 bytes 载体，基础库从 Redis/SNDB 读回后透出。</span></div><div class="box hot"><b>NewsMessage.nids[]</b><span>业务资源 ID 序列，解析后作为状态 map 的 key。</span></div><div class="box hot"><b>NewsMessage.state[]</b><span>字段 129：0 未实际下发，1 已下发，2 未曝光，3 已曝光，4 已点击。</span></div></div><div class="lane"><h3>高危混淆源</h3><div class="box"><b>ShowClkState</b><span>`history_service.proto` 的四值枚举，`CLICKED=2`，值域不同。</span></div><div class="box"><b>show_state/show_tss</b><span>`redisprotocol.proto` 注释为给 GRC 透传、不存 Redis。</span></div><div class="box"><b>click_tss/issued_tss</b><span>`IssuedItem` 字段注释为业务透传或不存储，不能当状态事实。</span></div></div><div class="lane"><h3>业务消费姿势</h3><div class="box hot"><b>ParseFromString</b><span>先解析 `gr_state` 为 `NewsMessage`；失败时显式标记未知。</span></div><div class="box hot"><b>下标对齐</b><span>仅当 `nids_size == state_size` 时构造 `nid -> state`。</span></div><div class="box"><b>缺失处理</b><span>没有 `gr_state` 或没有 KU 口径时，结论写“需人工补充”，不要用时间戳猜状态。</span></div></div></div><div class="note">结论：业务侧判断点展状态时，必须把“字段来源”和“枚举值域”一起固定，否则最容易把 CLICKED=2 与 CLICKED=4 混成同一件事。</div></div>

## 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
title 业务侧从 gr_state 还原点展状态
actor "business rule" as biz
participant "HS/IDM response" as resp
participant "HistoryContent.gr_state" as bytes
participant "gr.NewsMessage" as msg
collections "nids[]" as nids
collections "state[]" as states
control "nid_state_map" as map
biz -> resp: read history response
resp -> bytes: get gr_state bytes
alt bytes exists
  bytes -> msg: ParseFromString(bytes)
  alt parse ok and size aligned
    msg -> nids: nid(i)
    msg -> states: state(i)
    biz -> map: map[nid(i)] = state(i)
  else parse failed or size mismatch
    biz -> map: mark UNKNOWN / 需人工补充
  end
else no gr_state
  biz -> map: no stable state source
end
note right of states
NewsMessage.state[] value:
0 NO_ACTUAL_ISSUED
1 ISSUED
2 UNSHOW
3 SHOWED
4 CLICKED
end note
@enduml
```

## 状态结构信息图

```infographic compare-binary-horizontal-underline-text-vs
data
  title 两套“点展状态”值域不要混用
  desc `ShowClkState` 是 history_service 层四值枚举；`NewsMessage.state[]` 是 gr_state 中的五值业务状态数组
  items
    - label ShowClkState
      desc `UNKNOWN=0 / UNSEND=1 / CLICKED=2 / SHOWED=3`，字段来源是 `history_service.proto:22-28`
      value 4
    - label NewsMessage.state[]
      desc `NO_ACTUAL_ISSUED=0 / ISSUED=1 / UNSHOW=2 / SHOWED=3 / CLICKED=4`，字段来源是 `redisprotocol.proto:307`
      value 5
```

## 业务语义表

| `NewsMessage.state[]` 值 | 名称 | 业务解释 | 排查提示 |
|---:|---|---|---|
| 0 | NO_ACTUAL_ISSUED | 未实际下发 | 候选或中间状态，不可当曝光 |
| 1 | ISSUED | 已下发 | 进入下发历史，但不代表端上曝光 |
| 2 | UNSHOW | 下发但未曝光 | 关注端上反馈、展现回传或状态更新链路 |
| 3 | SHOWED | 已曝光 | 可作为曝光去重/频控依据 |
| 4 | CLICKED | 已点击 | 注意不是 `ShowClkState.CLICKED=2` |

## 关键结论

- 业务侧应把 `gr_state -> NewsMessage -> nids[]/state[]` 作为点展状态的唯一稳定解释链路。
- `ShowClkState` 与 `NewsMessage.state[]` 只是中文语义相似，但值域不同；同名 `CLICKED` 在前者为 2，在后者为 4。
- `IssuedItem.show_tss/click_tss/issued_tss` 是时间或透传字段，其中 `click_tss`、`issued_tss` 注释明确“不存储”，不能替代状态数组。
- `redisprotocol.proto` 中 `show_state/show_tss` 也标注“给 grc 传递……不存 redis”，调试时要确认拿到的是 hash field `gr_state` 的 bytes。
- 如果响应没有 `gr_state` 或无法解析，业务文档应显式写“需人工补充/缺状态来源”，不要从 `pos/channel/ts` 组合反推状态。

## Pitfalls 卡片

<style>.pit-0628-biz{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:1000px;border-radius:20px;background:#f5f7fa;border:1px solid #c7d2fe;padding:20px;color:#1e293b}.pit-0628-biz .meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#1d4ed8;font-weight:900}.pit-0628-biz .headline{font-size:28px;line-height:1.15;font-weight:900;margin:8px 0 12px}.pit-0628-biz .grid{display:grid;grid-template-columns:1.15fr .95fr;gap:14px}.pit-0628-biz .panel{background:#fff;border-top:4px solid #3b82f6;border-radius:12px;padding:12px}.pit-0628-biz .panel b{display:block;margin-bottom:6px}.pit-0628-biz .panel p{margin:0;font-size:14px;line-height:1.65;color:#334155}.pit-0628-biz .end{margin-top:12px;font-weight:900;color:#1d4ed8}</style><div class="pit-0628-biz"><div class="meta">pitfall · business semantics</div><div class="headline">看到 CLICKED，先问它来自哪一个 proto</div><div class="grid"><div class="panel"><b>同名枚举不等值</b><p>`ShowClkState.CLICKED=2`，但 `NewsMessage.state[]` 中 CLICKED=4。业务规则必须绑定字段来源，不能只比较名字。</p></div><div class="panel"><b>时间戳不等状态</b><p>`show_tss/click_tss/issued_tss` 是时间或透传字段，不能替代点展状态数组；缺 `gr_state` 时应显式标注未知。</p></div></div><div class="end">∎ 先解析 gr_state，再解释 state[]。</div></div>

## 调试 checklist

```infographic list-column-done-list
data
  title 点展状态业务排查清单
  items
    - label 响应是否包含 gr_state
      desc 检查 `HistoryContent.gr_state` bytes 是否存在且非空
      done false
    - label bytes 是否可解析
      desc 使用 `NewsMessage.ParseFromString(gr_state)`，失败则不要猜状态
      done false
    - label nids/state 是否对齐
      desc `nids_size == state_size` 后才能构造 nid 到 state 的映射
      done false
    - label 枚举是否绑错来源
      desc 不要把 `ShowClkState` 的 2 当成 `NewsMessage.state[]` 的点击态
      done false
    - label 是否误用透传字段
      desc `click_tss/issued_tss/show_tss` 不能作为状态事实
      done false
    - label 是否需要人工补充
      desc 无 KU/产品口径或无 gr_state 时，在结论中明确“需人工补充”
      done true
```

## 证据来源

- `proto/history_service.proto:22-28`：`ShowClkState` 四值枚举，`CLICKED=2`。
- `proto/history_service.proto:31-57`：`IssuedItem.show_tss/click_tss` 字段，其中 `click_tss` 注释为透传、不存储。
- `proto/history_service.proto:93-103`：`issued_tss` 与 `fetch_back_flag` 等字段包含“不存储/透传”语义。
- `proto/history_service.proto:165-175`：`HistoryContent.gr_state` bytes 字段。
- `proto/redisprotocol.proto:238-240`：`show_state/show_tss` 注释“给 grc 传递……不存 redis”。
- `proto/redisprotocol.proto:307-320`：`NewsMessage.state[]` 字段 129 与五值点展状态注释。
- `src/history/redis_history.cc:86-143`：Redis 读回只从 hash field `gr_state` 设置 `redis_history.gr_state`。
- `src/dao/sndb_dao.cc:234-273`：SNDB 读回写 `_gr_state_result_hc[news_idx].redis_history.gr_state`。
