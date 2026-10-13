# 2026-08-06 周四代码理解：History Service `gr_state` 双通道状态读写
<!-- topic-nav:start -->
> 主题：`base/history-state-io` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260726-hs-gr-state-dual-channel-consistency.md)
<!-- topic-nav:end -->


> 日期：2026-08-06  
> 主题来源：2026-08-06 daily-plan 缺失，按历史未覆盖主题 fallback 到 History Service `gr_state` 双通道状态 I/O  
> 范围：只分析 History Service 侧 `gr_state` 的客户端配置、请求构造、以及 put/get 通道拆分；本文不展开上层业务语义。  
> 内网文档：当前环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.1fr 1.2fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px;min-height:150px}.arch-col h3{margin:0 0 8px;font-size:14px;color:#102a43}.arch-box{background:#f1f5f9;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;margin:8px 0;font-size:13px;line-height:1.45}.arch-box strong{display:block;color:#102a43;margin-bottom:2px}.arch-arrow{color:#64748b;font-size:12px;text-align:center;margin:4px 0}.arch-note{font-size:12px;color:#52606d;line-height:1.5;margin-top:8px}</style><div class="arch-wrap"><div class="arch-col"><h3>入口与配置</h3><div class="arch-box"><strong>HistoryServiceDao::gen_request</strong><span>从 node 配置读取 `channel`，映射到运行时 channel 对象。</span></div><div class="arch-box"><strong>HistoryServiceIO</strong><span>承载 request / channel / logid / emerger 等上下文。</span></div><div class="arch-note">请求侧只负责把场景参数装配进 I/O 对象，再交给后续 RPC/IO 层。</div></div><div class="arch-col"><h3>双通道</h3><div class="arch-box"><strong>put 通道</strong><span>写入状态，配置在 `conf/common_component/dalton_client/sndb_gr_state_client.conf.template` 的 `[.put]`。</span></div><div class="arch-box"><strong>get 通道</strong><span>读取状态，配置在同一模板的 `[.get]`，与写路径分开调优。</span></div><div class="arch-arrow">write path / read path</div><div class="arch-note">`put` 和 `get` 分别设置 timeout、retry、backup request，避免一组参数同时约束两种延迟模型。</div></div><div class="arch-col"><h3>状态存储</h3><div class="arch-box"><strong>SNDB `shoubai_gr_layout_hs`</strong><span>表名由 `@table_name` 固定，client 初始化依赖这一约束。</span></div><div class="arch-box"><strong>state key</strong><span>业务状态通过 `ShowStateInfo` / `CardShowStateInfo` 透传到读写流程。</span></div><div class="arch-note">这层不解释“为什么这样下发”，只保证“写得进去、读得回来、配置可控”。</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "HistoryServiceDao" as Dao
participant "HistoryServiceIO" as IO
participant "SNDB gr_state client" as Sndb
participant "History Service state table" as Table
Caller -> Dao : gen_request()
Dao -> IO : set_channel()
Dao -> IO : set_logid()
Dao -> IO : set_emerger(false)
Dao -> IO : get_request()
Dao -> IO : fill HSRequest
Dao -> Sndb : put/get by route
Sndb -> Table : write_state / read_state
Table --> Sndb : result payload
Sndb --> IO : response
IO --> Dao : transform_response()
@enduml
```

## 2. 配置结构信息图

infographic sequence-ascending-steps
 data
  title `gr_state` 客户端拆分
  desc 写入与读取分离配置，避免把同一组重试/超时参数绑死在同一条链路上
  items
    - label 1. 表名锚定
      desc `@table_name: shoubai_gr_layout_hs`，client 初始化依赖表名正确。
      icon mdi/database
    - label 2. 写通道参数
      desc `[.put]` 使用 `timeout_ms: 300`、`max_retry: 3`、`backup_request_ms: 1000`。
      icon mdi/upload
    - label 3. 读通道参数
      desc `[.get]` 使用 `timeout_ms: 2000`、`max_retry: 3`、`backup_request_ms: 1000`。
      icon mdi/download
    - label 4. 本地测试模板
      desc `sndb_gr_state_client.conf` 仅保留 `127.0.0.1:8094` 的本地连接样例。
      icon mdi/flask-outline
 theme
  palette #3d5a80 #64748b #2d6a4f

## 3. 关键结论

<div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;margin:16px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-left:4px solid #3d5a80;border-radius:10px;padding:14px"><div style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:#486581;margin-bottom:6px">结论</div><div style="font-size:14px;line-height:1.7;color:#1f2937">`HistoryServiceDao` 的职责很窄：把 node 配置、会话上下文和 `HistoryServiceIO` 组装成一次可路由请求。真正的状态存取细节被下沉到 `gr_state` 的 SNDB 客户端配置，写和读由不同参数面控制。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-left:4px solid #2d6a4f;border-radius:10px;padding:14px"><div style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:#486581;margin-bottom:6px">边界</div><div style="font-size:14px;line-height:1.7;color:#1f2937">这篇只看存储与 I/O，不解释 `NewsHistory` 里的状态语义，也不推导前端展现策略。</div></div></div>

## 4. Pitfalls

<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px"><div style="font-size:13px;font-weight:700;color:#102a43;margin-bottom:6px">配置陷阱</div><div style="font-size:14px;line-height:1.7;color:#243b53">`@table_name` 错了会直接让 client 初始化失败；如果把读写参数混成一组，超时和 backup_request 的含义会互相污染。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px"><div style="font-size:13px;font-weight:700;color:#102a43;margin-bottom:6px">请求陷阱</div><div style="font-size:14px;line-height:1.7;color:#243b53">`HistoryServiceDao::gen_request` 依赖 `channel` 节点配置存在且可解析；缺失时直接返回错误，后续链路不会补救。</div></div></div>

## 5. 调试 Checklist

infographic list-column-done-list
 data
  title `gr_state` 链路检查项
  items
    - label 确认 channel 配置
      desc 检查 node 配置里 `channel` 是否存在且能映射到运行时 channel。
      done true
    - label 确认表名
      desc 校验 `@table_name: shoubai_gr_layout_hs` 是否与线上表一致。
      done true
    - label 区分 put/get 参数
      desc 读写超时、重试、backup_request 不要混用同一组假设。
      done true
    - label 本地模板验证
      desc 先看 `sndb_gr_state_client.conf`，再看 `.template` 是否一致。
      done true

## 证据来源

- `gr/dao/history_service_dao.cc:23-53`
- `history-service/conf/common_component/dalton_client/sndb_gr_state_client.conf.template:1-29`
- `history-service/conf/common_component/dalton_client/sndb_gr_state_client.conf:1-20`
- `gr/dao/history_service_dao.cc:104-214`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260806-history-service-gr-state-dual-channel.md)。
