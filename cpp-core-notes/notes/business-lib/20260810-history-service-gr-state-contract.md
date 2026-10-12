# History Service：gr_state 透传与读写契约

> 生成日期：2026-08-10  
> 计划状态：未找到今日 daily-plan，按相邻未覆盖主题 fallback。KU/业务上下文需人工补充；本文以代码证据为主。

## 架构全景

<style>.hs-arch{font-family:Inter,Arial,sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:8px;padding:18px;margin:16px 0;color:#1f2937}.hs-title{font-size:20px;font-weight:800;margin-bottom:12px}.hs-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.hs-box{background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;font-size:12px;line-height:1.45;min-height:72px}.hs-box strong{display:block;font-size:13px;color:#7c2d12;margin-bottom:3px}.hs-layer{border:1px solid #cbd5e1;border-radius:8px;background:#fff;padding:12px;margin-top:10px}.hs-layer h3{font-size:14px;margin:0 0 8px;color:#334155}.hs-flow{font-size:12px;color:#475569;margin-top:8px}</style>
<div class="hs-arch"><div class="hs-title">History service state contract</div><div class="hs-layer"><h3>Business view</h3><div class="hs-grid"><div class="hs-box"><strong>producer</strong>Upstream GR / HS logic serializes state into protocol fields</div><div class="hs-box"><strong>transport</strong>`gr_state` travels as opaque bytes between layers</div><div class="hs-box"><strong>storage</strong>history content persists the user state blob alongside issued items</div><div class="hs-box"><strong>consumer</strong>read path rehydrates state without interpreting unrelated payloads</div></div><div class="hs-flow">State is a contract boundary, not a free-form bag of fields.</div></div><div class="hs-layer"><h3>Storage split</h3><div class="hs-grid"><div class="hs-box"><strong>HistoryContent</strong>`issued_items`, `other`, `prefix_key`, `del_key`, `gr_state`</div><div class="hs-box"><strong>IdmContent</strong>`issued_items_str`, `msg_str`, `gr_state`, and other typed payloads</div><div class="hs-box"><strong>redisprotocol.proto</strong>state-adjacent fields such as `show_state` and `show_tss` are marked as not stored in redis</div><div class="hs-box"><strong>boundary</strong>some fields are pass-through only; the service must not infer persistence where comments say otherwise</div></div></div></div>

## 核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam shadowing false
left to right direction
actor Upstream
participant "History Service" as HS
participant "Protocol layer" as Proto
database "Redis / SNDB" as Store
participant "Downstream reader" as Down
Upstream -> Proto : serialize gr_state
Proto -> HS : write HistoryContent.gr_state
HS -> Store : persist content + state
Down -> HS : read request
HS -> Store : fetch stored blob
HS -> Proto : return gr_state unchanged
Proto -> Down : deliver opaque state
@enduml
```

```infographic sequence-snake-steps-simple
 data
  title gr_state contract steps
  desc How the state blob moves through the service boundary
  items
    - label Serialize state
      desc Upstream packs user state into `gr_state`
    - label Store with history content
      desc `HistoryContent.gr_state` persists with issued items
    - label Preserve opacity
      desc The service treats the blob as pass-through data
    - label Rehydrate on read
      desc Consumers recover the same bytes from the response path
    - label Avoid semantic drift
      desc Do not confuse not-stored fields with persisted state
 theme
  palette #b45309 #0f172a #94a3b8
```

## 关键代码证据

- `gr-proto/proto/history_service.proto:159-169` 定义 `HistoryContent.gr_state`，注释明确是用户状态数据。
- `gr-proto/proto/idm_service.proto:11-18` 在 `IdmContent` 中并列出现 `gr_state` 和其他业务 payload。
- `gr-proto/proto/redisprotocol.proto:236-239` 明确标注 `show_state`、`show_tss` 是给 GRC 透传且不存 redis。
- `gr-proto/proto/redisprotocol.proto:343-344` 还能看到同类 show-state 字段在其他消息结构中的存放方式。

## Pitfalls

<div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:14px 0;"><div style="background:#fff;border:1px solid #d8dee8;border-radius:8px;padding:12px;"><strong>Opaque does not mean optional</strong><div style="margin-top:6px;color:#334155;font-size:13px;line-height:1.5">`gr_state` 是契约字段，不能因为它是 bytes 就把它当成临时附属信息。</div></div><div style="background:#fff;border:1px solid #d8dee8;border-radius:8px;padding:12px;"><strong>Not-stored comments matter</strong><div style="margin-top:6px;color:#334155;font-size:13px;line-height:1.5">`show_state` / `show_tss` 的注释已经说明不入库，业务侧不要按持久化字段设计校验。</div></div><div style="background:#fff;border:1px solid #d8dee8;border-radius:8px;padding:12px;"><strong>Proto coupling</strong><div style="margin-top:6px;color:#334155;font-size:13px;line-height:1.5">状态字段横跨多个 proto，改名或改注释都要同步读写链路的解释。</div></div></div>

## 调试 Checklist

```infographic list-column-done-list
 data
  title Debug checklist
  desc Verify the state contract end to end
  items
    - label Check proto definitions
      desc Confirm `HistoryContent.gr_state` still exists
      done true
    - label Inspect pass-through readers
      desc Trace consumers that read the blob unchanged
      done true
    - label Check not-stored comments
      desc Validate `show_state` and `show_tss` are still documented as non-persistent
      done true
    - label Compare producer and consumer expectations
      desc Ensure both sides agree on opaque bytes semantics
      done true
    - label Review related payload fields
      desc Inspect `IdmContent` and adjacent state-bearing messages
      done true
 theme
  palette #7c2d12 #0f172a #64748b
```

## 证据来源

- `gr-proto/proto/history_service.proto:159-169`
- `gr-proto/proto/idm_service.proto:11-18`
- `gr-proto/proto/redisprotocol.proto:236-239`
- `gr-proto/proto/redisprotocol.proto:343-344`
