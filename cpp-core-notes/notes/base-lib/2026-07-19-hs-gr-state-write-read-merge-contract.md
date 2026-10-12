# 2026-07-19 周度代码理解：History Service `gr_state` 写入、读取与合并契约

> 本文聚焦 History Service 的基础链路：`HSService` 入口如何落到 `HistoryService`，`HistoryContent.gr_state` 如何在 proto、DAO、SNDB 配置和服务初始化之间形成稳定契约，以及它为何是业务侧状态透传的唯一可靠通道之一。

<style>
.hs-arch{font-family:Inter,Arial,sans-serif;background:#f8fafc;border:1px solid #d7dee8;border-radius:8px;padding:18px;margin:16px 0;color:#1f2937}.hs-arch h3{margin:0 0 12px;font-size:18px}.hs-arch-grid{display:grid;grid-template-columns:1.05fr 1.35fr 1fr;gap:12px}.hs-arch-box{background:#fff;border:1px solid #dbe3ef;border-radius:8px;padding:12px}.hs-arch-box h4{margin:0 0 8px;font-size:13px;color:#334155}.hs-arch-box ul{margin:0;padding-left:18px}.hs-arch-box li{margin:4px 0;line-height:1.45}.hs-tag{display:inline-block;padding:2px 8px;border-radius:999px;background:#e2e8f0;color:#334155;font-size:11px;font-weight:700;letter-spacing:0}.hs-flow{margin-top:12px;display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.hs-node{border:1px solid #cfd9e5;border-radius:8px;background:#fff;padding:10px;min-height:86px}.hs-node strong{display:block;margin-bottom:6px;font-size:12px;color:#0f172a}.hs-node p{margin:0;font-size:12px;line-height:1.45;color:#334155}.hs-arrow{margin-top:8px;color:#64748b;font-size:12px}.hs-note{margin-top:10px;font-size:12px;color:#475569}.hs-mini{margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:10px}.hs-mini .hs-mini-card{border:1px solid #dbe3ef;border-radius:8px;background:#fff;padding:10px}.hs-mini .hs-mini-card h5{margin:0 0 6px;font-size:12px;color:#334155}.hs-mini .hs-mini-card p{margin:0;font-size:12px;line-height:1.45;color:#475569}</style>

<div class="hs-arch">
  <h3>History Service 基础架构全景</h3>
  <div class="hs-arch-grid">
    <div class="hs-arch-box">
      <h4>RPC 入口</h4>
      <ul>
        <li><span class="hs-tag">src/main.cc</span> 启动 brpc `Server`。</li>
        <li>注册 `fhs::HistoryService`，服务版本设为 `v2.0`。</li>
        <li>统一 `baidu_std_reuse` 协议，`RunUntilAskedToQuit()` 常驻。</li>
      </ul>
    </div>
    <div class="hs-arch-box">
      <h4>服务层与 DAO</h4>
      <ul>
        <li><span class="hs-tag">src/service/history_service.h</span> 定义 `HistoryService : HSService`。</li>
        <li>实现 `query(...)`，把 RPC 请求转给内部调度器。</li>
        <li><span class="hs-tag">src/dao/history_service_dao.h</span> 提供可克隆 DAO，承接读写访问。</li>
      </ul>
    </div>
    <div class="hs-arch-box">
      <h4>数据与存储契约</h4>
      <ul>
        <li><span class="hs-tag">gr-proto/proto/history_service.proto</span> 定义 `HistoryContent.gr_state`。</li>
        <li>SNDB 配置通过 `gr_state_sndb_meta_bns` 解析元数据。</li>
        <li>`sndb_gr_state_client.conf.template` 固化表名、超时和 slot 访问约束。</li>
      </ul>
    </div>
  </div>
  <div class="hs-flow">
    <div class="hs-node"><strong>1. 进程启动</strong><p>main.cc 读取 flags、初始化全局组件、创建 brpc server。</p><div class="hs-arrow">↓</div></div>
    <div class="hs-node"><strong>2. 服务挂载</strong><p>HistoryService 暴露 `query` 入口，接收 HSService RPC。</p><div class="hs-arrow">↓</div></div>
    <div class="hs-node"><strong>3. DAO 访问</strong><p>DAO 负责把历史状态读写映射到 SNDB / Redis / 其他后端。</p><div class="hs-arrow">↓</div></div>
    <div class="hs-node"><strong>4. gr_state 传播</strong><p>proto 里的 `gr_state` 作为状态数据载体，供业务侧读回或合并。</p></div>
  </div>
  <p class="hs-note">这个链路的核心不是某个字段本身，而是“服务初始化 + proto 约束 + 配置约束 + DAO 访问策略”共同把 `gr_state` 固化成可读可写的契约。</p>
  <div class="hs-mini">
    <div class="hs-mini-card"><h5>读路径</h5><p>业务请求先进入 `HistoryService::query`，再由内部处理器决定是否读取 gr_state，并将结果返回给调用方。</p></div>
    <div class="hs-mini-card"><h5>写路径</h5><p>写入时优先通过 proto 中的 `HistoryContent.gr_state` 承接状态，再由 DAO / SNDB 配置完成落盘或同步。</p></div>
  </div>
</div>

## 核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam shadowing false
left to right direction
actor Client
participant "brpc Server" as S
participant "HistoryService" as HS
participant "DAO Layer" as DAO
database "SNDB / Redis" as DB
Client -> S : RPC request
S -> HS : query(...)
HS -> DAO : resolve read/write op
DAO -> DB : read/write gr_state
DB --> DAO : state payload
DAO --> HS : normalized result
HS --> S : HSResponse
S --> Client : response
@enduml
```

## 配置与结构

```infographic
sequence-roadmap-vertical-simple
data
  title gr_state 契约的四个落点
  desc 服务入口、proto、DAO、SNDB 配置必须同时对齐
  items
    - label 入口初始化
      desc src/main.cc 启动 server、设置 version 和 protocol
      icon mdi/flash
    - label RPC 服务定义
      desc src/service/history_service.h 暴露 HistoryService::query
      icon mdi/api
    - label 状态字段
      desc gr-proto/proto/history_service.proto 定义 HistoryContent.gr_state
      icon mdi/database
    - label 存储配置
      desc conf/common_component/dalton_client/sndb_gr_state_client.conf.template 约束表与超时
      icon mdi/cog
    - label DAO 访问
      desc src/dao/history_service_dao.h 负责 clone 与访问策略
      icon mdi/source-branch
theme
  palette #3d5a80 #2d6a4f #f97316
```

## Pitfalls

<div style="background:#fff;border:1px solid #dbe3ef;border-radius:8px;padding:14px;margin:14px 0;">
  <div style="border-left:4px solid #b45309;padding-left:12px;">
    <div style="font-weight:700;color:#0f172a;margin-bottom:6px;">`gr_state` 不是随便塞进响应体的附件字段</div>
    <div style="color:#334155;line-height:1.55;">它同时受 proto 定义、SNDB 表配置和 DAO 路径约束。若只改业务侧拼装逻辑，不改存储契约，读回就会断裂。</div>
  </div>
</div>
<div style="background:#fff;border:1px solid #dbe3ef;border-radius:8px;padding:14px;margin:14px 0;">
  <div style="border-left:4px solid #b45309;padding-left:12px;">
    <div style="font-weight:700;color:#0f172a;margin-bottom:6px;">`HistoryService` 入口与协议必须一致</div>
    <div style="color:#334155;line-height:1.55;">`src/main.cc` 里注册的服务类、`query` 方法签名和 `HSService` 生成代码必须匹配，否则 RPC 可以启动但业务调用会落空。</div>
  </div>
</div>

## 调试 Checklist

```infographic
list-column-done-list
data
  title 排障顺序
  desc 先确认入口，再确认配置，最后确认状态字段
  items
    - label 检查 src/main.cc 是否注册 HistoryService 并启动 brpc server
      done true
    - label 检查 src/service/history_service.h 是否暴露 query 入口
      done true
    - label 检查 gr-proto/proto/history_service.proto 是否存在 HistoryContent.gr_state
      done true
    - label 检查 conf/common_component/dalton_client/sndb_gr_state_client.conf.template 是否对齐表名和超时
      done true
    - label 检查 DAO 是否按预期访问 SNDB / Redis
      done true
    - label 若读回为空，先确认不是表名、元数据或 protocol 配置错误
      done true
theme
  palette #3d5a80 #64748b #2d6a4f
```

## 证据来源

- `src/main.cc:39-78` — server 启动、服务注册、协议配置
- `src/service/history_service.h:23-25` — `HistoryService` 入口定义
- `src/service/history_service.cc:11-20` — 服务实现起点与 gflag 校验
- `src/dao/history_service_dao.h:27-37` — DAO 克隆与访问骨架
- `gr-proto/proto/history_service.proto:159-168` — `HistoryContent.gr_state`
- `conf/common_component/dalton_client/sndb_gr_state_client.conf.template:1-29` — SNDB gr_state 客户端配置
- `conf/common_component/dalton_client/sndb_gr_state_client.conf:1-20` — 本地测试配置
