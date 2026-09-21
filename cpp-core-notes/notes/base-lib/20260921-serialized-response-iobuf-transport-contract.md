# 2026-09-21 基础库理解：SerializedResponse、IOBuf 与 RPC 响应传输合同

> 日期：2026-09-21（周一）  
> 主题选择：未发现目标日期的有效 daily-plan；近期笔记已覆盖 DeepEs 推理池、bthread Future、fork/framework Flow/Context 等相邻主题，因此按“邻近未覆盖主题”回退到 **brpc `SerializedResponse` 与 `IOBuf` 响应承载、RPC attachment 发包边界**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：`SerializedResponse` 的已序列化 Message 合同、`Controller::response_attachment()` 的生命周期、HTTP/RPC 协议层的 attachment 发送，以及失败/重试时的清理语义。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">消息对象层</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">SerializedResponse</div><div style="margin-top:8px;font-size:12px;color:#52606d;">以 protobuf Message 形态承载一段已经序列化的数据，真实字节位于内部 IOBuf。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">控制器边界</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">Controller::response_attachment()</div><div style="margin-top:8px;font-size:12px;color:#52606d;">用户代码可直接写入 body；reset、retry、失败路径会清空或替换 attachment。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">协议发送层</div><div style="margin-top:8px;font-size:16px;color:#102a43;font-weight:700;">RPC / HTTP protocol</div><div style="margin-top:8px;font-size:12px;color:#52606d;">协议头记录 attachment 长度，发送时移动 IOBuf；HTTP 还可能压缩 body 或包装 gRPC 前缀。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">protobuf / raw bytes</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">IOBuf ownership</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">header + attachment</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:10px;text-align:center;color:#991b1b;font-size:13px;">socket write</div></div></div>

**一句话结论：** `SerializedResponse` 不是普通 protobuf 字段容器，而是把已完成序列化的 bytes 放进 `IOBuf`；响应能否正确发出取决于 attachment ownership、协议头长度和发送前后的清理/移动顺序。

## 1. 核心调用链

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title SerializedResponse 到 RPC 响应发送
participant "RPC handler" as H
participant "SerializedResponse" as SR
participant "Controller" as C
participant "RPC protocol" as P
participant "Socket" as S
H -> SR : serialized_data().append(bytes)
H -> C : response = SR
C -> P : build response meta
P -> P : read attachment length
P -> SR : swap serialized_data into payload
P -> S : write header + IOBuf payload
S --> P : write result
alt retry / reset / failed
  C -> C : clear response_attachment()
else normal completion
  C -> C : release request state
end
@enduml
```

## 2. 传输合同信息图

```infographic
infographic sequence-ascending-steps
data
  title 响应字节的五个合同面
  desc 从已序列化对象到网络发送，每一层都必须保持 ownership 一致
  items
    - label 载荷
      desc SerializedResponse 只暴露 serialized_data，ByteSize 直接返回 IOBuf size
      icon mdi:package-variant
    - label 写入
      desc 调用方向 IOBuf append 或 swap，避免再次把 payload 解析成普通 protobuf 字段
      icon mdi:content-save-outline
    - label 计长
      desc RPC meta 和 HTTP body 都依据 attachment length 组织协议输出
      icon mdi:ruler
    - label 移交
      desc 协议层使用 movable 或 swap 将 IOBuf 转入发送 buffer，发送后原对象不再拥有同一段数据
      icon mdi:swap-horizontal
    - label 清理
      desc retry、reset、失败响应会 clear attachment，不能复用上一轮请求的 body
      icon mdi:broom
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 `SerializedResponse` 的语义是“已序列化 Message”

`src/baidu/rpc/serialized_response.h:43-83` 定义 `SerializedResponse`。它在普通构建下继承 protobuf `Message`，在 unreflectable 配置下继承 `UnreflectableMessage`，但两种模式都只暴露一个 `base::IOBuf _serialized`。`serialized_data()` 返回该 IOBuf 的可写或只读引用。

`src/baidu/rpc/serialized_response.cpp:148-172` 的 `Clear()` 只清理 IOBuf，`ByteSize()` 直接返回 `_serialized.size()`；`src/baidu/rpc/serialized_response.cpp:174-180` 明确禁止通过普通 protobuf `MergeFrom` 解析/合并。这说明调用方不应期待反射字段，也不应把它当作可增量构造的 protobuf message。

### 3.2 copy/swap 仍然围绕 IOBuf ownership

`src/baidu/rpc/serialized_response.cpp:184-209` 中，`CopyFrom` 复制 `_serialized`，`Swap` 交换两个对象的 IOBuf。对大响应来说，业务层应优先明确使用移动或 swap 的时机，避免在 response 组装和协议发送之间出现额外复制。与此同时，复制后的对象仍共享同一套“已序列化”语义，不能对其调用普通字段级 API。

### 3.3 Controller attachment 是独立的 body 通道

`src/baidu/rpc/controller.h:451-459` 将 `response_attachment()` 定义为直接接到网络的用户数据或 HTTP body，而不是序列化进 protobuf message。成员实际是 `base::IOBuf _response_attachment`，见 `src/baidu/rpc/controller.h:931-934`。

`src/baidu/rpc/controller.cpp:195-200` 的 reset 清空 `_response_attachment`；`src/baidu/rpc/controller.cpp:425-430` 的失败处理还会用错误文本替换 body。因此业务代码如果在失败后继续读取旧 attachment，会得到错误语义而非上一轮成功响应。

### 3.4 RPC 协议先记长度，再移动 payload

`src/baidu/rpc/policy/baidu_rpc_protocol.cpp:421-427` 先用 `response_attachment().length()` 生成响应头，再把 attachment `movable()` 到发送 buffer。长度计算和 payload 移动必须处于同一条发送路径；如果用户代码在协议层读取长度之后仍修改 attachment，可能造成头部长度与实际 body 不一致。

同文件 `src/baidu/rpc/policy/baidu_rpc_protocol.cpp:1011-1018` 处理接收侧的压缩类型；若 response descriptor 是 `SerializedResponse`，协议层会取出 `serialized_data()` 并 swap 到接收 buffer，而不是走普通 protobuf 反序列化。这是 raw serialized response 的反向闭环。

### 3.5 HTTP 层可能再次包装或压缩

`src/baidu/rpc/policy/http_rpc_protocol.cpp:936-947` 在 attachment 为空、response 是普通 protobuf 且请求成功时，将 protobuf 序列化到 attachment。`src/baidu/rpc/policy/http_rpc_protocol.cpp:1043-1077` 则在满足阈值时压缩 attachment；HTTP/2 gRPC 路径还会在 `:1097-1101` 增加 gRPC 前缀。

因此排查“服务端对象有内容但客户端 body 不对”时，必须区分：普通 protobuf response、用户填充 attachment、`SerializedResponse` raw bytes、HTTP 压缩以及 gRPC framing，不能只在业务对象层打印字段。

## 4. 故障对账信息图

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title 表现相似但定位层不同
  desc 先确认 bytes 是否存在，再确认协议是否按同一份 bytes 发出
  items
    - label 空 body
      desc 检查 response_attachment 是否被 reset、失败路径替换，或 HTTP 层因 progressive writer 忽略 body
      icon mdi:tray-remove
    - label 长度不一致
      desc 检查协议头取 length 的时点，以及发送前是否再次 append/clear
      icon mdi:alert-outline
    - label protobuf parse 失败
      desc 检查是否误把 SerializedResponse 当普通 Message 解析，或压缩/base64 层级未还原
      icon mdi:code-json
    - label 重试污染
      desc 检查 retry 前 response_attachment clear，避免上一轮 payload 拼入本轮响应
      icon mdi:reload-alert
theme
  palette #3D5A80 #0F766E #DC2626
```

## 5. Pitfalls

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;color:#263238;"><div style="border-left:4px solid #d97706;background:#fff7ed;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#9a3412;text-transform:uppercase;">不要反射访问</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">SerializedResponse 的有效数据在 IOBuf，不在 protobuf field table。反射或 MergeFrom 不是它的正常使用方式。</div></div><div style="border-left:4px solid #2563eb;background:#eff6ff;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#1d4ed8;text-transform:uppercase;">不要混淆两条 body 通道</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">protobuf response 与 response_attachment 可同时存在，但协议层对空 body、压缩和 streaming 有不同分支。</div></div><div style="border-left:4px solid #dc2626;background:#fef2f2;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#991b1b;text-transform:uppercase;">失败会改写 body</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">失败处理可能 clear 后写入 ErrorText；日志必须同时输出 ErrorCode、Failed 和 attachment size。</div></div><div style="border-left:4px solid #059669;background:#ecfdf5;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#047857;text-transform:uppercase;">关注移动后的对象</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">movable/swap 后原 Controller 或 SerializedResponse 不再代表发送 buffer，异步回调不能继续假定其拥有 payload。</div></div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 响应传输排查清单
  items
    - label 对象类型
      desc 确认 response 是否为 SerializedResponse，避免误走普通 protobuf parse
      done false
    - label 原始字节
      desc 在写入、协议取长、socket write 前分别记录 IOBuf size
      done false
    - label ownership
      desc 核对 append、swap、movable 后是否仍在异步回调中访问原对象
      done false
    - label 失败路径
      desc 检查 retry、reset、SetFailed 是否 clear 或替换 response_attachment
      done false
    - label 协议分支
      desc 区分 RPC、HTTP、HTTP/2、gRPC、压缩和 progressive writer
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/baidu/rpc/serialized_response.h:43-105`：SerializedResponse 与 IOBuf 字段
- `src/baidu/rpc/serialized_response.cpp:148-209`：Clear、ByteSize、CopyFrom、Swap
- `src/baidu/rpc/controller.h:451-459,931-934`：response attachment API 与成员
- `src/baidu/rpc/controller.cpp:195-200,425-430`：清理和失败替换
- `src/baidu/rpc/policy/baidu_rpc_protocol.cpp:421-427,1011-1018`：RPC header、payload swap、SerializedResponse 接收
- `src/baidu/rpc/policy/http_rpc_protocol.cpp:936-947,1043-1101`：HTTP 序列化、压缩与 gRPC framing
