# MV-GR 业务路由：`video_launch` 场景下的 GRC / GRG 选择

> 日期：2026-08-03  
> 范围：`video_launch` / `short_micro_video` 场景，`MicroVideoGrcRecallProcessor` 的 GRC/GRG 分流，sid 与 channel 规则联动  
> 说明：本次未找到当天 daily-plan，按历史去重策略选取微视频业务路由边界；KU 上下文需人工补充，本文以代码库证据为准。

<style>.biz-wrap{font-family:Arial,"Microsoft YaHei",sans-serif;border:1px solid #d7e0ea;background:#f8fafc;border-radius:8px;padding:16px;color:#1f2937}.biz-wrap h2{margin:0 0 10px 0;font-size:20px;font-weight:800}.biz-grid{display:grid;grid-template-columns:1.2fr 0.95fr;gap:12px}.biz-panel{background:#fff;border:1px solid #d7dfe8;border-radius:8px;padding:12px}.biz-panel h3{margin:0 0 8px 0;font-size:14px}.biz-line{border-left:4px solid #2d6a4f;padding:8px 10px;background:#f7fbf8;border-radius:4px;margin:8px 0;font-size:12px;line-height:1.55}.biz-table{display:grid;grid-template-columns:1fr 1fr;gap:8px}.biz-cell{border:1px solid #e0e7ef;border-radius:6px;padding:8px;background:#fcfdff;font-size:12px;line-height:1.45}.biz-steps{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.biz-step{border:1px solid #dde5ee;border-radius:6px;padding:8px;background:#fff;font-size:12px;line-height:1.45}.biz-card{border:1px solid #d8e1eb;border-radius:8px;background:#fff;padding:12px;margin:10px 0}.biz-card h3{margin:0 0 6px 0;font-size:15px}.biz-note{font-size:12px;color:#475569;line-height:1.55}</style>

<div class="biz-wrap">
  <h2>视频场景路由面</h2>
  <div class="biz-grid">
    <div class="biz-panel">
      <h3>场景入口</h3>
      <div class="biz-line"><strong>`video_launch`</strong> 是微视频召回里最关键的业务场景之一，和 `short_micro_video` / `news_updates_dibar` 这类场景一同参与 sid 驱动的路由判断。</div>
      <div class="biz-line">业务侧不直接关心底层协议，而是关心“当前场景该走 GRC 还是 GRG”，以及是否允许降级或短路请求。</div>
      <div class="biz-table">
        <div class="biz-cell"><b>GRC</b><br/>更偏召回/兜底调用，常被 `is_recall_mgrc`、`switch_down_stream` 触发。</div>
        <div class="biz-cell"><b>GRG</b><br/>更偏增长或更完整的业务路径，由 `is_recall_mgrg`、场景和开关共同决定。</div>
        <div class="biz-cell"><b>sid 开关</b><br/>`conf/sid.conf` 里按 sid 开关细分功能，避免全量业务一起抖动。</div>
        <div class="biz-cell"><b>channel 绑定</b><br/>`conf/channels.conf` 里定义 channel、后端 BNS、超时、备份请求比例。</div>
      </div>
    </div>
    <div class="biz-panel">
      <h3>路由侧重点</h3>
      <div class="biz-line">`video_launch` 场景下，最重要的是确认当前请求是否已经带上可用的 `scene_info` 和 `sid_info`，否则后续判断没有语义基础。</div>
      <div class="biz-line">`is_dongtai`、`is_spark_app`、`is_open_person_disaster` 这类标志会把不同终端或容灾场景分到不同分支。</div>
      <div class="biz-line">如果 `no_need_recall_grc` 或 `is_skip_request_grg` 为真，业务上看到的就是“没打到后端”，这通常是策略而不是故障。</div>
    </div>
  </div>
</div>

## 核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor User
participant "video_launch request" as Req
participant "MicroVideoGrcRecallProcessor" as P
participant "sid.conf" as SID
participant "channels.conf" as CH
participant "GRC backend" as GRC
participant "GRG backend" as GRG
participant "Result" as R
User -> Req : launch feed request
Req -> P : enter business route
P -> SID : read sid switch / scene map
P -> CH : read channel and timeout policy
alt scene favors GRC or downstream switch on
  P -> GRC : send recall request
  GRC --> P : recall payload
else scene favors GRG
  P -> GRG : send grg request
  GRG --> P : business payload
else bypass / disaster / skip
  P -> P : set no-request flag
end
P -> R : assemble final response
@enduml
```

## 配置/结构信息图

```infographic
infographic list-grid-badge-card
data
  title video_launch 路由依赖
  items
    - label 场景判定
      desc `video_launch` / `short_micro_video` / `news_updates_dibar` 进入不同路由分支
      icon mdi/video-outline
    - label sid 规则
      desc `sid.conf` 负责按 sid 做小流量、开关、反转和保底配置
      icon mdi/numeric
    - label channel 规则
      desc `channels.conf` 定义 `ums`、`microvideo_fs` 等 channel 的协议、BNS、超时和备份参数
      icon mdi/transit-connection-horizontal
    - label 容灾与降级
      desc `is_open_person_disaster`、`switch_down_stream`、`no_need_recall_grc` 控制是否短路到别的路径
      icon mdi/shield-alert-outline
```

## Pitfalls

<div class="biz-card"><h3>把“没请求后端”误判成故障</h3><div class="biz-note">很多业务分支是由 sid / scene / flag 主动短路出来的。先查策略，再查链路，不然会把正常的降级行为当成线上错误。</div></div>

<div class="biz-card"><h3>场景字段比 RPC 名更重要</h3><div class="biz-note">同一个 processor 在不同场景下会走不同后端。`video_launch` 不是静态路由名，而是驱动路由决策的输入之一。</div></div>

<div class="biz-card"><h3>channel 配置决定超时语义</h3><div class="biz-note">`channels.conf` 里的 `timeout_ms`、`backup_timeout_ms`、`dynamic_backup_request_*` 会改变业务感知到的尾延时和备份请求比例。</div></div>

## 调试 Checklist

```infographic
infographic list-column-done-list
data
  title 视频场景路由排查
  items
    - label 确认场景值
      done true
      desc 检查请求是否确实属于 `video_launch` 或相关微视频场景
    - label 确认 sid 开关
      done true
      desc 在 `conf/sid.conf` 里核对对应 sid 是否被打开、反转或保底关闭
    - label 确认 channel 参数
      done true
      desc 在 `conf/channels.conf` 里核对超时、BNS、备份请求比例
    - label 确认降级 flag
      done true
      desc `switch_down_stream`、`no_need_recall_grc`、`is_skip_request_grg` 是否让请求提前结束
```

## 证据来源
- `src/processor/micro_grc_recall.cpp:120-336`
- `conf/sid.conf:1-45`
- `conf/channels.conf:1-30`
- `conf/plugins/graph/feed_ufs_pre.conf:1-34`
