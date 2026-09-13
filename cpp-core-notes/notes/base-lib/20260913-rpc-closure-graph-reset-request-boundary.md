# 2026-09-13 基础库理解：RPC Closure 与 Graph reset 请求边界

> 日期：2026-09-13  
> 主题选择：未发现本日有效 daily-plan；旧计划中的 TraceLog/VIP/HashLog 主题已有覆盖，因此回退到邻近未覆盖的 **ReusableRPCProtocol 响应 Closure、Graph::run 等待与请求级 reset 边界**。KU 正文未读取，业务背景需人工补充；本文只依据代码证据。  
> 范围：GRC/GRG 服务入口、Graph::run 返回的异步 Closure、`ReusableRPCProtocol::Closure::send_response()`、请求结束后的 Graph reset。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">RPC 入口</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">CallMethod → service::run</div><div style="margin-top:8px;font-size:12px;color:#52606d;">请求校验、Graph/Controller 获取和 graph_name 分支在响应 Closure 之前完成。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">异步执行边界</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">Graph::run → graph Closure → wait/get</div><div style="margin-top:8px;font-size:12px;color:#52606d;">Graph Closure 负责等待执行引擎；RPC Closure 负责将已组装的 response 发送给客户端。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">复用边界</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">send_response → wait → reset</div><div style="margin-top:8px;font-size:12px;color:#52606d;">只有请求数据写完、响应发送触发且执行完成后，池化 Graph 才能清理并交还下一请求。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 48px 1fr 48px 1fr 48px 1fr;gap:8px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">request</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;font-size:13px;">graph run</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:10px;text-align:center;color:#166534;font-size:13px;">response send</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">reset / reuse</div></div></div>

**一句话结论：** RPC Closure 的“发送完成”与 Graph Closure 的“执行完成”是两个不同事件；请求结束前必须同时闭合，才能安全执行 Graph reset。

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title RPC Closure、Graph Closure 与 reset 的请求时序
participant "brpc CallMethod" as RPC
participant "GRC/GRG service" as SVC
participant "Graph" as GRAPH
participant "Graph Closure" as GC
participant "ReusableRPCProtocol::Closure" as RC
participant "response" as RESP

RPC -> SVC : validate request / choose graph
SVC -> GRAPH : run(end_node[, err_node])
GRAPH --> GC : async execution handle
SVC -> GC : get() / wait()
GC --> SVC : graph_ret
SVC -> RESP : fill response and error state
SVC -> RC : send_response()
RC --> RPC : response bytes become available
SVC -> GRAPH : reset()
note over GC,RC
Graph Closure waits execution;
RPC Closure sends the protocol response.
They must not be conflated.
end note
@enduml
```

## 2. 生命周期信息图

```infographic
infographic sequence-snake-steps-simple
data
  title 请求完成的五道闸门
  desc 从入口对象到池化 Graph 复用，顺序决定响应完整性与状态隔离
  items
    - label 入口校验
      desc 检查 Controller、request、response、done 与 GraphEngine
      icon mdi:shield-check
    - label 选择执行终点
      desc 按 UA/graph_name 选择 GRGResponse、GrcResponse 或其他 end node
      icon mdi:source-branch
    - label 等待 Graph
      desc Graph Closure 执行 get/wait，先确认 graph_ret 再决定失败状态
      icon mdi:timer-check-outline
    - label 发送 RPC
      desc ReusableRPCProtocol::Closure::send_response() 触发协议响应
      icon mdi:send-check
    - label 清理复用
      desc Graph::reset() 清除请求态，随后对象才可进入下一次请求
      icon mdi:broom
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 GRC 将 Graph Closure 等待放在响应发送之前

`src/service/grc_service.cpp:227-234` 进入统一 `run()`，随后按 UA 选择不同的 Graph end node（`src/service/grc_service.cpp:295-310`）。`src/service/grc_service.cpp:340-345` 先根据执行结果设置 Controller 错误，再调用 `closure_done->send_response()`，之后才 `closure.wait()`。这说明协议响应触发和 Graph 执行等待在实现上是两个明确步骤，排查时不能把 `send_response()` 当成 Graph 已完全收敛的证明。

服务外层在 `src/service/grc_service.cpp:212-221` 继续执行 trace/adjust 日志并调用 `graph->reset()`。因此 reset 的安全前提不是单独的“已经拿到响应”，还包括执行引擎相关 Closure 已闭合。

### 3.2 GRG 显式保存 Graph Closure，再报告耗时并完成日志

`src/service/grg_service.cpp:203-221` 获取 `GRGResponse`、错误节点和时间戳节点，调用 `graph->run(end_node, err_node)` 后用 `closure.get()` 等待执行结果，再上报动态超时耗时。入口 `src/service/grg_service.cpp:86-92` 在 `run()` 返回后执行 `pooled_graph->reset()`。

这形成一个稳定的请求级边界：`fill_basic_data_for_graph()` → `run()` → `closure.get()` → reset。若把 reset 提前到 `closure.get()` 之前，池化 Graph 中仍可能存在 worker 读写的请求态。

### 3.3 失败路径也必须闭合 done

GRC 的错误处理 `src/service/grc_service.cpp:64-83` 在设置 Controller 失败信息后调用 `done->Run()`；GRG 对应路径在 `src/service/grg_service.cpp:96-111` 同样调用 `done->Run()`。这说明异常分支不能只记录错误或只释放 Graph：RPC 调用方等待的是 Closure 完成，漏掉 `Run()` 会表现为请求悬挂。

### 3.4 两种 Closure 的排查维度不同

- **RPC Closure**：关注是否被 `send_response()` 或 `Run()` 触发、是否重复触发、response 是否已经完成写入。
- **Graph Closure**：关注 `run()` 返回后是否 `get()/wait()`、返回码是否被读取、reset 是否发生在执行结束之后。
- **池化 Graph**：关注 reset 是否清理请求字段、trace 数据和中间 GraphData；下一请求不应观察到上一请求残留。

## 4. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#f5f7fa;border:1px solid #d8e0ea;border-left:8px solid #3d5a80;border-radius:16px;padding:20px;margin:18px 0;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800;">Pitfalls · Closure Boundary</div><div style="font-size:26px;line-height:1.15;font-weight:900;margin:8px 0 12px;">“响应已发”不等于“Graph 已可复用”</div><div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;"><div style="background:#fff;border-top:4px solid #3d5a80;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;"><b>三类风险：</b><br>① 错误分支漏掉 `done->Run()`，客户端会一直等待；② `send_response()` 与 Graph Closure 的等待顺序被误读，导致执行未收敛就 reset；③ reset 只清了 Graph 外壳，未清掉请求级 GraphData 或 trace PB，下一次请求可能串状态。</div><div style="background:#fff7ed;border-top:4px solid #f59e0b;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;color:#7c2d12;"><b>评审顺序</b><br>入口校验 → graph run → get/wait → response fill → send_response/Run → reset。</div></div><div style="margin-top:12px;font-weight:900;color:#3d5a80;">∎ 先画出两条 Closure 线，再讨论线程和延迟。</div></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title RPC / Graph 生命周期排查清单
  desc 用同一 logid 回放成功、Graph 失败和早退三类路径
  items
    - label 固定请求入口
      desc 记录 logid、UA、graph_name、Controller 和 done 类型
      done true
    - label 核对 end node
      desc 确认不同 UA 分支选择了预期的 response data
      done true
    - label 检查 Graph Closure
      desc 确认每个 run() 返回值都执行 get() 或 wait()
      done true
    - label 检查 RPC Closure
      desc 成功走 send_response()，失败走 done->Run()，且每次请求只完成一次
      done true
    - label 检查 reset 时序
      desc reset 必须晚于执行收敛和响应字段写入
      done false
    - label 回放复用请求
      desc 连续复用同一 Graph，检查 trace、error、response 和 GraphData 是否串请求
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 6. 证据来源

- `src/service/grc_service.cpp:64-83`：GRC 错误路径完成 RPC Closure。
- `src/service/grc_service.cpp:153-224`：GRC 请求入口、run 返回后的日志与 Graph reset。
- `src/service/grc_service.cpp:227-345`：GRC end node、Graph Closure、response 发送与等待。
- `src/service/grg_service.cpp:36-92`：GRG 请求入口、Graph 执行和 pooled Graph reset。
- `src/service/grg_service.cpp:96-111`：GRG 错误路径完成 RPC Closure。
- `src/service/grg_service.cpp:203-221`：GRG Graph Closure、执行结果与耗时上报。

> KU 正文未读取，需人工补充线上 Closure 实现、协议发送线程模型、Graph reset 的监控指标和异常回放基线。
