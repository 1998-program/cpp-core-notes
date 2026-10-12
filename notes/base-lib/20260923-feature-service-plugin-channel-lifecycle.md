# 2026-09-23 基础库理解：FeatureServicePlugin 的组件注册、动态通道与 RPC 生命周期

> 日期：2026-09-23（周三）  
> 主题选择：目标日期未发现有效 daily-plan。本次按缺失计划回退，避开最近已经覆盖的 `TransientCache`、`SerializedResponse/IOBuf`、ObjectPool 与 Future，选择在 MV-GRC/MV-GRG 两侧都被多个 graph vertex 复用、但尚未单独成文的 `FeatureServicePlugin` 框架合同。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：Babylon 组件注册、`ApplicationContext` 查找、`ChannelGroup`/`DynamicChannel` wireup、`FeatureService_Stub` 构造、log_id/request_code 注入、RPC 错误归一化与动态通道 feedback。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">组件注册层</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">Babylon factory</div><div style="margin-top:8px;font-size:12px;color:#52606d;">同一插件类型按 `request_feature_service`、`user_feature_service`、`doc_feature_service` 注册三份实例。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">运行时绑定层</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">ApplicationContext → ChannelGroup</div><div style="margin-top:8px;font-size:12px;color:#52606d;">vertex 只保存 channel_name；init 查插件，wireup 解析静态 Channel 和 DynamicChannel。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">RPC 执行层</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">FeatureService_Stub</div><div style="margin-top:8px;font-size:12px;color:#52606d;">统一设置 log_id/request_code，调用 `get_general_feature`，按 Controller 与 response error_code 归一化返回。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">vertex init</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">plugin lookup</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">dynamic on</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:10px;text-align:center;color:#991b1b;font-size:13px;">RPC + feedback</div></div></div>

**一句话结论：** `FeatureServicePlugin` 把“配置中的逻辑 channel 名”转换成可观测、可动态调度的 brpc client；vertex 不应该自行构造 Channel 或 Controller，而应通过 `ApplicationContext` 获取已 wireup 的插件，由插件统一完成路由、采样和错误归一化。

## 1. 核心时序

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title FeatureServicePlugin 的初始化与一次 RPC
participant "Graph vertex" as V
participant "ApplicationContext" as AC
participant "FeatureServicePlugin" as FP
participant "ChannelGroup" as CG
participant "DynamicChannel" as DC
participant "FeatureService_Stub" as ST
participant "Feature Service" as FS
V -> AC : get<FeatureServicePlugin>(channel_name)
AC --> V : plugin pointer
FP -> CG : wireup(context)
CG --> FP : static Channel + DynamicChannel
FP -> ST : construct(channel)
V -> FP : get_general_feature(request, response, log_id)
FP -> FP : set log_id + MurmurHash request_code
FP -> DC : on(cntl, dt_cntl, rpc_name)
FP -> ST : get_general_feature()
ST -> FS : RPC request
FS --> ST : FeatureResponse / error
ST --> FP : Controller state
alt controller failed
  FP -> FP : return Controller.ErrorCode()
else response has error_code
  FP -> FP : return response.error_code()
else success
  FP -> DC : feedback(cntl, dt_cntl, rpc_name)
  FP --> V : 0
end
@enduml
```

## 2. 生命周期合同信息图

```infographic
infographic sequence-ascending-steps
data
  title FeatureServicePlugin 的五个合同面
  desc 组件创建、依赖绑定和 RPC 反馈必须保持同一 channel_name 语义
  items
    - label 注册
      desc 三个逻辑实例名映射到同一个 FeatureServicePlugin 类型，实例构造时保存 channel_name
      icon mdi:shape-plus
    - label wireup
      desc ChannelGroup 同时提供 DynamicChannel 与 brpc Channel，任一缺失都返回 -1
      icon mdi:lan-connect
    - label 预处理
      desc log_id 写入 Controller，MurmurHash32(log_id) 写入 request_code，保持链路可追踪
      icon mdi:identifier
    - label RPC
      desc Stub 调用 get_general_feature；Controller 失败与 FeatureResponse.error_code 都统一成 int32 返回
      icon mdi:remote
    - label 反馈
      desc 成功调用 DynamicChannel::feedback，把本次耗时与通道状态反馈给动态路由层
      icon mdi:chart-line
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 注册名是运行时拓扑的一部分

`src/plugin/feature_service.cpp:110-120`（GRC）和 `src/plugin/feature_service.cpp:115-125`（GRG）分别注册 `request_feature_service`、`user_feature_service`、`doc_feature_service`。它们都构造 `FeatureServicePlugin`，差异只由实例名和配置中的 `channel_name` 决定。

因此排查“vertex 能拿到插件但 RPC 失败”时，要把三层名称分开看：graph 配置的 vertex 名、ApplicationContext 的组件实例名、ChannelGroup 中的实际 channel 名。只核对类名不够。

### 3.2 wireup 把静态连接和动态路由绑定在一起

`src/plugin/feature_service.cpp:21-36`（GRC）与 `src/plugin/feature_service.cpp:26-42`（GRG）先从 `ApplicationContext` 得到 `ChannelGroup`，再调用 `get_dynamic(_channel_name)` 和 `get(_channel_name)`。前者用于 `on/feedback`，后者用于构造 `FeatureService_Stub`。

这形成一个关键不变量：同一 `_channel_name` 必须同时存在静态 Channel 和 DynamicChannel。只配置 brpc channel 而没有动态通道时，stub 可能可构造，但调用前的动态超时/路由状态无法建立。

### 3.3 Controller 预处理统一追踪字段

`src/plugin/feature_service.cpp:39-44`（GRC）或 `:44-49`（GRG）的 `common_pre_call` 将 `log_id` 写入 Controller，并对十进制 log_id 做 MurmurHash32 后设置 `request_code`。这不是业务字段，而是 RPC 层的请求路由和诊断字段。

调用方传入的 log_id 必须来自 graph context。若 vertex 自己生成新 id，会导致 SIA、动态路由和远端日志无法通过同一请求键对账。

### 3.4 错误归一化有两条来源

`src/plugin/feature_service.cpp:66-107`（GRC）或 `:71-112`（GRG）先判断 `cntl.Failed()`，失败时返回 `cntl.ErrorCode()`；Controller 未失败但 response 带非零 `error_code` 时，返回 response code。`error_code == 1` 被单独视为“用户无特征”，记录 notice；其他非零错误记录 warning。

成功路径最后才执行 `DynamicChannel::feedback`。因此调用方不能只依据 RPC 返回对象是否存在判断成功，必须同时检查插件返回值和 response 的 `error_code`。

### 3.5 GRC 与 GRG 的实现差异

GRC 版本在 `feature_service.cpp:17-19` 的 `initialize()` 中保持空实现，并注释掉延迟 BVAR；GRG 版本在 `:40` 启用 `BVAR_LATENCY_EXPOSE`，在 `:68-69` 将 Controller latency 写入 recorder。两侧共有的生命周期合同相同，但观测能力不同。

跨服务比较延迟时，不能直接把 GRC 的业务 RPC 时间与 GRG 的 `_fs_bvar_latency` 对齐；前者缺少插件层 latency recorder，需结合 `request_feature_rpc`/`user_feature_rpc` SIA 或 Controller 日志补齐。

## 4. 诊断对账信息图

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title FeatureService 失败表现与优先检查点
  desc 先定位绑定层，再判断远端服务或业务特征缺失
  items
    - label 插件为空
      desc 检查 vertex init 的 channel_name、ApplicationContext 实例注册名和 wireup 是否完成
      icon mdi:link-off
    - 动态通道为空
      desc 检查 ChannelGroup 是否为同一名称提供 get_dynamic，避免只注册静态 brpc channel
      icon mdi:transit-connection-variant
    - Controller failed
      desc 记录 ErrorCode、ErrorText、remote_side、timeout_ms 与 log_id，优先判断网络/超时/路由
      icon mdi:timer-alert-outline
    - error_code=1
      desc 代表远端没有用户特征，不等同于 RPC 传输失败；按业务缺失处理并保留 cuid/uid
      icon mdi:account-question-outline
    - 返回成功但字段空
      desc 检查 FeatureResponse.error_code、Sample 内容和上游 vertex 是否正确取 mutable_sample/request_feature
      icon mdi:database-alert-outline
theme
  palette #3D5A80 #0F766E #DC2626
```

## 5. Pitfalls

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;color:#263238;"><div style="border-left:4px solid #d97706;background:#fff7ed;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#9a3412;text-transform:uppercase;">不要在 vertex 里自建 stub</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">自建 stub 会绕过 DynamicChannel 的 on/feedback 与统一 request_code，造成路由和观测断裂。</div></div><div style="border-left:4px solid #2563eb;background:#eff6ff;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#1d4ed8;text-transform:uppercase;">不要只看 ret</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">ret=0 只代表插件层没有发现传输或响应错误；还要确认 response.error_code 与 sample 是否有效。</div></div><div style="border-left:4px solid #dc2626;background:#fef2f2;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#991b1b;text-transform:uppercase;">区分无特征与超时</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">error_code=1 是远端业务缺失，Controller.Failed 则是 RPC 层失败，两者的降级策略和告警级别不同。</div></div><div style="border-left:4px solid #059669;background:#ecfdf5;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#047857;text-transform:uppercase;">关注 GRC/GRG 差异</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">GRG 有插件级 latency BVAR，GRC 该逻辑被注释；跨服务延迟对账需要补看 SIA。</div></div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title FeatureServicePlugin 排查清单
  items
    - label 注册
      desc 确认 request/user/doc 三种组件名与目标 vertex 的 channel_name 对应
      done false
    - label wireup
      desc 同时验证 ChannelGroup::get 与 get_dynamic 均成功，且 stub 已构造
      done false
    - label 请求标识
      desc 对齐 log_id、request_code、rpc_name 和远端日志，不要在 vertex 内重写 log_id
      done false
    - label 结果状态
      desc 同时记录 Controller.Failed、ErrorCode、FeatureResponse.error_code 和 error_msg
      done false
    - label 动态反馈
      desc 成功路径确认 feedback 被调用；失败路径确认是否需要保留动态通道的失败反馈
      done false
    - label 复用清理
      desc 检查 vertex reset 是否清理 FeatureResponse 和临时 protobuf，避免跨请求字段残留
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/plugin/feature_service.h:20-53`：插件类型、channel/rpc 名、DynamicChannel 与 stub 成员。
- `src/plugin/feature_service.cpp:17-36`：GRC initialize/wireup 与 stub 构造。
- `src/plugin/feature_service.cpp:39-107`：GRC Controller 预处理、RPC 调用、错误归一化和 feedback。
- `src/plugin/feature_service.cpp:110-120`：GRC 三个 Babylon 组件注册名。
- `src/plugin/feature_service.cpp:26-42,44-112`：GRG wireup、latency BVAR 和相同 RPC 合同。
- `src/main.cpp:73-130`：GRC 入口、GlobalInitializer、brpc service 与 server 启动顺序。
