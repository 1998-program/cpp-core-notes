# 2026-08-14 周五代码理解：feeda-pcs 特征路由与响应装配边界

> 日期：2026-08-14  
> 主题来源：2026-08-14 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 `feeda-pcs` 业务装配链路；KU/业务背景需人工补充。  
> 范围：只分析 `client_tag` 驱动的请求分支、特征字段装配、响应参数回填与日志出口；不展开全部 PCS 协议。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.15fr 1.85fr;gap:12px}.arch-col{display:flex;flex-direction:column;gap:12px}.arch-box{background:#fff;border:1px solid #dbe3ea;border-radius:10px;padding:12px;box-shadow:0 1px 0 rgba(15,23,42,.03)}.arch-title{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#5b6b7c;margin-bottom:8px}.arch-main{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.arch-mini{background:#f8fafc;border:1px solid #e5ecf2;border-radius:8px;padding:10px}.arch-mini strong{display:block;font-size:13px;margin-bottom:4px;color:#0f172a}.arch-mini span{display:block;font-size:12px;line-height:1.45;color:#475569}.arch-flow{display:flex;flex-direction:column;gap:8px}.arch-flow .step{padding:10px 12px;border-radius:8px;border:1px solid #dbe3ea;background:#fff;font-size:12px;line-height:1.45}.arch-flow .step b{display:block;color:#0f172a;margin-bottom:3px}.arch-note{font-size:12px;line-height:1.55;color:#475569}.tag{display:inline-block;padding:2px 8px;border-radius:999px;background:#e8f1ff;color:#1d4ed8;font-size:11px;font-weight:700;margin-right:6px}</style><div class="arch-wrap"><div class="arch-col"><div class="arch-box"><div class="arch-title">业务分流</div><div class="arch-flow"><div class="step"><b>client_tag = gr</b>走标准 `PCSReqConv::convert`，把召回上下文、通用字段和 feature 请求合成 PCS 请求。</div><div class="step"><b>client_tag = gr_sample</b>走 sample / 偏好字段路径，和同步装配方式配合，适合调试与样本观测。</div><div class="step"><b>client_tag = lite-gr</b>走 lite 变体，对应更轻量的请求和更强的触发控制。</div><div class="step"><b>client_tag = shoubai-gr</b>走独立业务 tag，体现同一 PCS 服务对不同产品线的路由隔离。</div></div></div><div class="arch-box"><div class="arch-title">结果出口</div><div class="arch-main"><div class="arch-mini"><strong>全局参数</strong><span>从 `PcsResponse` 解析并注入上下文，后续推荐/实验链路可直接读取。</span></div><div class="arch-mini"><strong>样本日志</strong><span>`sample_log` 记录特征命中与回填结果，便于离线核对。</span></div><div class="arch-mini"><strong>feature 结果</strong><span>按 `parameter_result` 遍历，决定哪些字段进入最终回复。</span></div><div class="arch-mini"><strong>channel 语义</strong><span>`microvideo-pcs` 与 `lite-microvideo-pcs` 决定是完整特征链路还是更轻的调用。</span></div></div></div></div><div class="arch-col"><div class="arch-box"><div class="arch-title">边界说明</div><div class="arch-note"><span class="tag">业务边界</span>`feeda-pcs` 的业务价值不是单纯“发 RPC”，而是把用户、实验、特征、样本和响应状态装配到同一条可控链路里。`client_tag` 是最关键的业务开关，`channel` 是它背后的物理路由。</div></div><div class="arch-box"><div class="arch-title">观测点</div><div class="arch-flow"><div class="step"><b>请求前</b>看 `FeedReq` 是否携带正确的 `command`、`tab`、`product`、`channel_id`、`uid`。</div><div class="step"><b>请求中</b>看 `PCSReqConv` 是否进入预期分支，是否带上实验和偏好字段。</div><div class="step"><b>响应后</b>看 `PCSRespConv` 是否把 `global_param` 与 `sample_log` 写回。</div></div></div></div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam shadowing false
left to right direction
actor ProductCaller
participant "pcs.cpp\nclient_tag router" as Router
participant "PCSReqConv\npcs_reqconv.cc" as Req
participant "PcsService_Stub" as Stub
participant "PCSRespConv\npcs_respconv.cc" as Resp
participant "sample_log / global_param" as Log
ProductCaller -> Router : FeedReq + client_tag
Router -> Req : choose convert branch
Req -> Stub : build PcsRequest
Stub --> Resp : PcsResponse + parameter_result
Resp -> Log : parse_global_param + sample_log
Resp --> ProductCaller : assembled response
@enduml
```

## 2. 配置与路由信息图
```infographic
infographic list-grid-badge-card
data
  title 路由与触发配置
  desc 这些字段决定 PCS 请求是走完整链路还是轻量分支
  items
    - label microvideo-pcs
      desc 主业务 channel，配合 gr / gr_sample 使用
      value 2
    - label lite-microvideo-pcs
      desc 轻量业务 channel，配合 lite-gr 使用
      value 1
    - label shoubai-pcs
      desc 独立业务线 channel，配合 shoubai-gr 使用
      value 1
    - label async_call
      desc 异步调用标记，决定是否在请求链路里提前返回或并发处理
      value 1
    - label trigger_all
      desc 是否触发全部相关组件
      value 2
```

## 3. 业务分支拆解
- `feeda-dc-gr/plugin/pcs.cpp:23-110`：`client_tag` 分支路由的入口，决定调用 `PCSReqConv` 的哪条变换路径。
- `feeda-dc-gr/conv/pcs_reqconv.cc:17-33`：通用字段从 `FeedReq` 传入 `CommonInfo`，是业务上下文的最小公共集。
- `feeda-dc-gr/conv/pcs_respconv.cc:19-56`：`parse_global_param` 与 `convert` 把服务端结果转成可消费的业务输出。
- `feeda-mv-gr/conf/common_component/pcs_common_plugin.conf:1-42`：业务 tag 与 channel 的映射，定义哪条业务线能触发哪类 PCS 请求。

## 4. Pitfalls
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #dbe3ea;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px"><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">tag 误配</div><div style="font-size:12px;line-height:1.55;color:#475569">`gr`、`gr_sample`、`lite-gr`、`shoubai-gr` 不是等价名，错配后业务会落到错误的 convert 分支。</div></div><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">字段漏传</div><div style="font-size:12px;line-height:1.55;color:#475569">`CommonInfo` 里缺 `uid`、`channel_id` 或实验字段时，后续特征与日志都会失真。</div></div><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">回填不完整</div><div style="font-size:12px;line-height:1.55;color:#475569">只写响应不写 `sample_log`，会让业务看起来“返回了”，但实验和排障没有证据链。</div></div></div></div>

## 5. 调试 Checklist
```infographic
infographic list-column-done-list
data
  title 业务调试检查清单
  items
    - label 确认 client_tag 进入预期分支
      done true
    - label 确认 channel 与 tag 的组合和配置一致
      done true
    - label 确认 CommonInfo 已带上 command/tab/product/channel_id/uid
      done true
    - label 确认 parameter_result 能在响应里回写
      done true
    - label 确认 sample_log 可用于离线复盘
      done true
    - label KU 背景缺失时明确写“需人工补充”
      done true
```

## 证据来源
- `feeda-mv-gr/conf/common_component/pcs_common_plugin.conf:1-42`
- `feeda-dc-gr/plugin/pcs.cpp:23-110`
- `feeda-dc-gr/conv/pcs_reqconv.cc:17-33`
- `feeda-dc-gr/conv/pcs_respconv.cc:19-56`
- `common-component/src/plugin/pcs_common_plugin.cpp:13-36`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260814-feeda-pcs-feature-routing-and-response-assembly.md)。
