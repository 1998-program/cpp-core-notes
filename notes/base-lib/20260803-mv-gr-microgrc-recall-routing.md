# MV-GR 微视频召回框架：`main.cpp` 启动链路与 `MicroVideoGrcRecallProcessor`

> 日期：2026-08-03  
> 范围：`main.cpp` 启动初始化、RPC 服务挂载、`micro_grc_recall.cpp` 召回处理器上下文绑定、`grc/grg` 分流与降级开关  
> 说明：本次未找到当天 daily-plan，按历史去重策略选择 MV-GR 微视频召回基础链路；KU 上下文需人工补充，本文以代码库证据为准。

<style>.mvgr-arch{font-family:Arial,"Microsoft YaHei",sans-serif;border:1px solid #d8e2ec;background:#f8fafc;border-radius:8px;padding:16px;color:#1f2937}.mvgr-arch h2{margin:0 0 10px 0;font-size:20px;font-weight:800}.mvgr-grid{display:grid;grid-template-columns:1.05fr 1.15fr 0.9fr;gap:12px}.mvgr-layer{background:#fff;border:1px solid #d7e0ea;border-radius:8px;padding:12px}.mvgr-layer h3{margin:0 0 8px 0;font-size:14px}.mvgr-box{border:1px solid #d7dfe8;border-radius:6px;padding:10px;margin:8px 0;background:#fbfdff}.mvgr-box strong{display:block;margin-bottom:4px}.mvgr-note{font-size:12px;color:#475569;line-height:1.5}.mvgr-flow{display:flex;gap:8px;flex-wrap:wrap}.mvgr-pill{padding:6px 10px;border-radius:999px;border:1px solid #cfd9e6;background:#f3f7fb;font-size:12px}.mvgr-side{font-size:12px;line-height:1.55;color:#334155}.mvgr-callout{margin-top:10px;border-left:4px solid #3d5a80;padding:8px 10px;background:#f6f8fb;border-radius:4px}.mvgr-card{border:1px solid #d6dee8;background:#fff;border-radius:8px;padding:12px;margin:10px 0}.mvgr-card h3{margin:0 0 6px 0;font-size:15px}.mvgr-kv{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.mvgr-kv div{border:1px solid #e1e7ef;border-radius:6px;padding:8px;background:#fcfdff}.mvgr-kv b{display:block;margin-bottom:4px}</style>

<div class="mvgr-arch">
  <h2>MV-GR 微视频召回基础层</h2>
  <div class="mvgr-grid">
    <div class="mvgr-layer">
      <h3>入口层</h3>
      <div class="mvgr-box"><strong>`src/main.cpp:44-76`</strong><span class="mvgr-note">解析命令行、加载 `comlog`、注册 reusable protocol、初始化全局环境与 `baidu::rpc::Server`。</span></div>
      <div class="mvgr-box"><strong>`options.enabled_protocols = "nshead baidu_std_reuse"`</strong><span class="mvgr-note">说明服务同时接受 nshead 与 reuse 协议，服务对象挂到 `nshead_service`。</span></div>
    </div>
    <div class="mvgr-layer">
      <h3>处理层</h3>
      <div class="mvgr-box"><strong>`src/processor/micro_grc_recall.cpp:120-230`</strong><span class="mvgr-note">`MicroVideoGrcRecallProcessor` 通过 `GraphDependency` 绑定 request/common/device/scene/history/flag 类依赖，构建统一上下文。</span></div>
      <div class="mvgr-box"><strong>分流决策</strong><span class="mvgr-note">在执行阶段根据 `is_recall_mgrc`、`is_recall_mgrg`、`FLAGS_switch_down_stream` 和场景开关选择 `grc_plugin`、`_grc_plugin` 或 `_spark_grc_plugin`。</span></div>
    </div>
    <div class="mvgr-layer">
      <h3>控制层</h3>
      <div class="mvgr-box"><strong>`conf/channels.conf` / `conf/sid.conf`</strong><span class="mvgr-note">channel、sid 开关与特定场景绑定，决定是否走微视频召回、是否打开降级和二跳控制。</span></div>
      <div class="mvgr-box"><strong>降级与兜底</strong><span class="mvgr-note">`no_need_recall_grc`、`is_skip_request_grg`、`disaster_enable`、`switch_down_stream` 等 flag 决定是否短路请求与是否降级到另一侧。</span></div>
    </div>
  </div>
  <div class="mvgr-callout">核心判断不是单纯“调哪个 RPC”，而是先把上下文拼成一个图，再用场景与开关决定是否触发 GRC、GRG 或直降级。</div>
</div>

## 核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "main.cpp\nServer bootstrap" as Main
participant "GlobalInitializer" as GI
participant "MicroVideoGrcRecallProcessor" as P
participant "GrcPlugin" as GRC
participant "GRG branch" as GRG
participant "Response" as Resp
Caller -> Main : start process
Main -> GI : init() + load conf + register protocol
Main -> P : request enters graph processor
P -> P : bind GraphDependency context
P -> P : read scene / sid / history / flags
alt recall mgrc or downstream switch on
  P -> GRC : call GRC request
  GRC --> P : recall result
else recall mgrg allowed
  P -> GRG : call GRG path
  GRG --> P : recall result
else short-circuit / disaster / skip
  P -> P : set bypass flags
end
P -> Resp : emit assembled result
@enduml
```

## 结构信息图

```infographic
infographic list-grid-badge-card
data
  title 召回基础层关键依赖
  items
    - label GraphDependency 上下文
      desc request_feature / common_info / device_info / scene_info / history / flag 类依赖先绑定后执行
      icon mdi/account-box-multiple
    - label 协议与服务
      desc `baidu::rpc::Server` + `nshead baidu_std_reuse`，入口在 `main.cpp`
      icon mdi/server
    - label 分流控制
      desc `is_recall_mgrc`、`is_recall_mgrg`、`FLAGS_switch_down_stream` 决定 GRC/GRG/降级路径
      icon mdi/swap-horizontal
    - label 场景与 sid
      desc `channels.conf`、`sid.conf` 里的场景开关决定是否激活召回分支
      icon mdi/tune-variant
```

## Pitfalls

<div class="mvgr-card"><h3>上下文没绑全就会提前失败</h3><div class="mvgr-note">`setup()` 里对多个 `GraphDependency` 做 `CHECK_NULL`，少任意一个字段都会直接返回。它不是“容错式填空”，而是强约束图式上下文。</div></div>

<div class="mvgr-card"><h3>主入口不是业务分支</h3><div class="mvgr-note">`main.cpp` 只负责 protocol、log、initializer、server/options 和服务挂载。真正的召回决策在 `micro_grc_recall.cpp` 里，不要把业务逻辑塞进启动链路里理解。</div></div>

<div class="mvgr-card"><h3>降级开关优先级高</h3><div class="mvgr-note">`FLAGS_switch_down_stream == 1` 会直接把调用拉向 GRC，优先级高于某些场景判断。排查召回异常时先看 flag 再看场景。</div></div>

## 调试 Checklist

```infographic
infographic list-column-done-list
data
  title 召回链路排查清单
  items
    - label 检查 `main.cpp` 启动是否完成
      done true
      desc `comlog`、protocol、initializer、`ServerOptions`、service 挂载是否都成功
    - label 检查 GraphDependency 绑定
      done true
      desc 重点看 `scene_info`、`sid_info`、`is_recall_mgrc`、`is_recall_mgrg`、`is_skip_request_grg`
    - label 检查场景与 sid 配置
      done true
      desc 结合 `conf/channels.conf` 与 `conf/sid.conf` 看对应场景是否真正打开
    - label 检查降级 flag
      done true
      desc `switch_down_stream`、`disaster_enable`、`no_need_recall_grc` 是否把流量短路
```

## 证据来源
- `src/main.cpp:44-76`
- `src/processor/micro_grc_recall.cpp:120-230`
- `conf/channels.conf:1-30`
- `conf/sid.conf:1-45`
- `conf/plugins/graph/feed_ufs_pre.conf:1-34`
