# 2026-08-14 周五代码理解：feeda-pcs 请求生命周期与特征装配边界

> 日期：2026-08-14  
> 主题来源：2026-08-14 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 `feeda-pcs` C++ brpc 服务架构；KU/业务背景需人工补充。  
> 范围：只分析服务入口、PCS 插件初始化、请求转换与响应回填链路；不展开全部业务策略。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.2fr 1.8fr;gap:12px}.arch-col{display:flex;flex-direction:column;gap:12px}.arch-box{background:#fff;border:1px solid #dbe3ea;border-radius:10px;padding:12px;box-shadow:0 1px 0 rgba(15,23,42,.03)}.arch-title{font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#5b6b7c;margin-bottom:8px}.arch-main{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.arch-mini{background:#f8fafc;border:1px solid #e5ecf2;border-radius:8px;padding:10px}.arch-mini strong{display:block;font-size:13px;margin-bottom:4px;color:#0f172a}.arch-mini span{display:block;font-size:12px;line-height:1.45;color:#475569}.arch-flow{display:flex;flex-direction:column;gap:8px}.arch-flow .step{padding:10px 12px;border-radius:8px;border:1px solid #dbe3ea;background:#fff;font-size:12px;line-height:1.45}.arch-flow .step b{display:block;color:#0f172a;margin-bottom:3px}.arch-note{font-size:12px;line-height:1.55;color:#475569}.tag{display:inline-block;padding:2px 8px;border-radius:999px;background:#e8f1ff;color:#1d4ed8;font-size:11px;font-weight:700;margin-right:6px}</style><div class="arch-wrap"><div class="arch-col"><div class="arch-box"><div class="arch-title">入口与初始化</div><div class="arch-flow"><div class="step"><b>main.cpp:39-85</b>进程启动时加载日志、插件、全局初始化与实验参数，然后把 `general_gr_service` 挂到 brpc server 并监听端口。</div><div class="step"><b>pcs_common_plugin.conf:1-42</b>按 `channel`、`client_tag`、`async_call`、`trigger_all`、`murhash` 把 PCS 调用模式分组。</div><div class="step"><b>pcs_common_plugin.cpp:13-36</b>从 channel group 取 stub，再把 schema 装进 `DynamicStruct`，给后续请求/响应转换器提供统一入口。</div></div></div><div class="arch-box"><div class="arch-title">关键数据面</div><div class="arch-main"><div class="arch-mini"><strong>请求面</strong><span>`pcs.cpp` 把 `FeedReq` 转成 `PcsRequest`，再按 `client_tag` 选择具体 convert 分支。</span></div><div class="arch-mini"><strong>响应面</strong><span>`pcs_respconv.cc` 从 `PcsResponse` 解析全局参数、特征结果和样本日志，回填到会话上下文。</span></div><div class="arch-mini"><strong>配置面</strong><span>channel 决定走 `microvideo-pcs`、`lite-microvideo-pcs` 还是 `shoubai-pcs`，决定 stub、tag 和异步语义。</span></div><div class="arch-mini"><strong>上下文面</strong><span>`GRSessionContext` 贯穿请求、全局参数、日志与响应出口，避免散落式状态复制。</span></div></div></div></div><div class="arch-col"><div class="arch-box"><div class="arch-title">生命周期</div><div class="arch-flow"><div class="step"><b>brpc 进程</b>装载 `general_gr_service`，准备处理外部 PCS 请求。</div><div class="step"><b>PcsCommonPlugin</b>从配置初始化 stub 与 schema，完成服务端请求协议绑定。</div><div class="step"><b>PCSReqConv</b>把通用字段、用户标识、实验参数和特征请求整理成 PCS 请求。</div><div class="step"><b>PCSRespConv</b>把全局参数、feature result 与 sample log 写回上下文，形成最终响应。</div><div class="step"><b>业务调用方</b>只看到统一 `gen` / convert 入口，不直接依赖底层 channel 细节。</div></div></div><div class="arch-box"><div class="arch-title">边界判断</div><div class="arch-note"><span class="tag">安全边界</span>本主题的核心不是某个算法，而是“服务进程如何把请求、配置和特征装配串成可复用的调用链”。真正的变化点在 `main.cpp`、PCS plugin 和 converter，而不是单个业务字段本身。</div></div></div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam shadowing false
left to right direction
actor Client
participant "brpc server\nmain.cpp" as S
participant "PcsCommonPlugin\npcs_common_plugin.cpp" as P
participant "PCSReqConv\npcs_reqconv.cc" as R
participant "PcsService_Stub" as Stub
participant "PCSRespConv\npcs_respconv.cc" as C
participant "GRSessionContext" as Ctx
Client -> S : 请求进入 / general_gr_service
S -> P : init_plugin + schema/stub 装载
P -> R : build request from FeedReq
R -> Stub : gen / 选择 channel + tag
Stub --> Ctx : PcsResponse + parameter_result
Ctx -> C : parse_global_param / convert
C -> Ctx : 回填 sample_log / feature / global_param
C --> S : 最终响应
S --> Client : 返回
@enduml
```

## 2. 配置与结构信息图
```infographic
infographic list-grid-badge-card
data
  title PCS 关键配置面
  desc 这些字段共同决定服务走哪条 stub 与特征路径
  items
    - label channel
      desc microvideo-pcs-model / microvideo-pcs / lite-microvideo-pcs / shoubai-pcs
      value 4
    - label client_tag
      desc gr / gr_sample / lite-gr / shoubai-gr
      value 4
    - label async_call
      desc 1 表示异步调用，0 表示同步或显式特征计算
      value 2
    - label trigger_all
      desc 是否触发完整链路
      value 2
    - label murhash
      desc 按 hash 规则分流到对应 channel
      value 1
```

## 3. 关键实现边界
- `feeda-mv-gr/src/main.cpp:39-85`：进程入口、plugin init、global init、exp manager、server start。
- `feeda-mv-gr/conf/common_component/pcs_common_plugin.conf:1-42`：不同 `channel` / `client_tag` 的 PCS 组装规则。
- `common-component/src/plugin/pcs_common_plugin.cpp:13-36`：stub 与 schema 初始化。
- `feeda-dc-gr/plugin/pcs.cpp:23-110`：按 `client_tag` 选择不同 `PCSReqConv` 分支。
- `feeda-dc-gr/conv/pcs_reqconv.cc:17-33`：`CommonInfo` 基础字段装配。
- `feeda-dc-gr/conv/pcs_respconv.cc:19-56`：全局参数和样本日志回填。

## 4. Pitfalls
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #dbe3ea;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><div style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px"><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">配置与代码不同步</div><div style="font-size:12px;line-height:1.55;color:#475569">`channel` 和 `client_tag` 的组合决定 stub 与 convert 分支，改配置不改代码会出现请求落错路径。</div></div><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">初始化顺序敏感</div><div style="font-size:12px;line-height:1.55;color:#475569">`init_plugin()`、全局初始化与实验参数初始化必须在 server 启动前完成，否则请求上下文不完整。</div></div><div style="background:#fff;border:1px solid #e5ecf2;border-radius:10px;padding:12px"><div style="font-weight:700;color:#0f172a;margin-bottom:6px">响应回填漏字段</div><div style="font-size:12px;line-height:1.55;color:#475569">`PCSRespConv` 既写全局参数也写 sample log，任一环节漏掉都会影响后续实验和调试可观测性。</div></div></div></div>

## 5. 调试 Checklist
```infographic
infographic list-column-done-list
data
  title 调试检查清单
  items
    - label 确认 main.cpp 已完成 plugin / initializer / ExpManager 初始化
      done true
    - label 确认 pcs_common_plugin.conf 中的 channel 与 client_tag 组合正确
      done true
    - label 确认 PcsCommonPlugin 成功拿到 stub 与 schema
      done true
    - label 确认 PCSReqConv 进入正确分支
      done true
    - label 确认 PCSRespConv 已回填 global_param 和 sample_log
      done true
    - label 如果 KU 背景不可用，保留“需人工补充”说明
      done true
```

## 证据来源
- `feeda-mv-gr/src/main.cpp:39-85`
- `feeda-mv-gr/conf/common_component/pcs_common_plugin.conf:1-42`
- `common-component/src/plugin/pcs_common_plugin.cpp:13-36`
- `feeda-dc-gr/plugin/pcs.cpp:23-110`
- `feeda-dc-gr/conv/pcs_reqconv.cc:17-33`
- `feeda-dc-gr/conv/pcs_respconv.cc:19-56`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260814-feeda-pcs-request-lifecycle-and-feature-assembly-boundary.md)。
