# 2026-08-28 周五代码理解：ReusableRPCProtocol 与响应 Closure 生命周期
<!-- topic-nav:start -->
> 主题：`base/rpc-closure` · 主文 · [主题目录](README.md)
<!-- topic-nav:end -->


> 日期：2026-08-28  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的历史候选项；本次没有独立的当日 daily-plan 可直接读取，KU/业务背景需人工补充。  
> 范围：只看 `src/main.cpp` 的服务启动、`src/service/grc_service.cpp` 的 `GenericGRCService::query()` / `run()` / `print_log()`，以及 `ReusableRPCProtocol`、`closure_done`、`graph->reset()` 这一条请求闭环。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:14px;background:#f8fafc;line-height:1.45;">
  <div style="display:grid;grid-template-columns:1.08fr 1.2fr 1fr;gap:12px;align-items:stretch;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">启动装配</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`src/main.cpp:73-136`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">注册 reusable protocol、服务、插件和线程配置，给后续请求闭环提供运行时外壳。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求闭环</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`query()` → `try_get()` → `run()` → `send_response()`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">核心不是“跑完就结束”，而是要让 response、graph 和 done 的生命周期对齐。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">回收边界</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`graph->reset()` + `closure_done->release()`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">复用成立的前提是每次请求结束时都能把图对象和协议对象归位。</div>
    </div>
  </div>
  <div style="margin-top:12px;display:grid;grid-template-columns:1fr 80px 1fr 80px 1fr 80px 1fr;gap:10px;align-items:center;">
    <div style="background:#eef6ff;border:1px solid #bfdbfe;border-radius:8px;padding:10px;text-align:center;color:#1d4ed8;">main.cpp 装配</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">brpc 服务入口</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;padding:10px;text-align:center;color:#a16207;">graph run</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">response / reset</div>
  </div>
</div>

## 1. 核心流程
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "main.cpp" as M
participant "GenericGRCService::query()" as Q
participant "GraphEngine" as E
participant "Graph" as G
participant "ReusableRPCProtocol::Closure" as C
Client -> M : start server
M -> M : register protocol / services
Client -> Q : RPC request
Q -> E : try_get(graph_name)
E --> Q : pooled graph
Q -> G : emit request data
Q -> G : run(end)
G --> Q : closure
Q -> Q : copy response if needed
Q -> C : send_response()
Q -> G : reset()
Q -> C : release()
Q --> Client : response
@enduml
```

## 2. 启动与请求闭环拆解
```infographic
sequence-ascending-steps
  data
    title ReusableRPCProtocol 请求闭环
    desc 启动阶段先完成协议和服务装配，运行阶段再把响应、图对象和 closure 串起来
    items
      - label 协议注册
        desc `ReusableRPCProtocol::register_protocol()` 在 `src/main.cpp:84-88` 完成
      - label 服务装配
        desc brpc server 挂载 `GenericGRCService` 和 HTTP service
      - label 图对象获取
        desc `GraphEngine::try_get(graph_name)` 取出可复用图实例
      - label 响应发送
        desc `closure_done->send_response()` 先把结果发出去，再释放协议闭包
      - label 状态归位
        desc `graph->reset()` 清空图态，`closure_done->release()` 释放请求态
```

## 3. 关键判断
- `main.cpp` 不是业务逻辑本身，但它决定了 reusable protocol、服务、线程和插件是否以可复用方式启动。
- `query()` 里真正重要的是把 `GraphPool::PooledObject`、`GRCSessionContext` 和 `done` 绑成一个短生命周期单元。
- `run()` 里先 `send_response()` 再 `closure.wait()`，说明发送链路和图执行链路是分开的，不能把它们当成一个同步函数看。
- `graph->reset()` 必须发生在请求末尾，否则下一次 `try_get()` 拿到的就是带脏状态的图实例。

## 4. 架构卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:4px solid #2563eb;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#2563eb;text-transform:uppercase;letter-spacing:.04em;">关键结论</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">这条链路的核心约束不是 graph 能不能跑，而是 response 什么时候出、graph 什么时候归、done 什么时候释放。三个对象的生命周期只要错开一点，复用就会把问题放大。</div>
</div>

<div style="height:10px"></div>

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff7ed;border:1px solid #fed7aa;border-left:4px solid #f97316;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#c2410c;text-transform:uppercase;letter-spacing:.04em;">调试提示</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">如果客户端已经收到响应但后续状态异常，优先查 `send_response()` 前后的对象归位顺序，再看 `graph->reset()` 是否覆盖了所有临时数据，而不是先怀疑 brpc 本身。</div>
</div>

## 5. Pitfalls
- 只看 `ReusableRPCProtocol::register_protocol()` 成功，会误以为整条链路已经具备复用条件，实际还要看 `closure_done` 的回收时机。
- `graph->reset()` 放得太早，会让后续日志、监控或响应拷贝拿到被清理的状态。
- `closure_done->send_response()` 和 `closure_done->release()` 之间不能插入会修改响应对象生命周期的额外逻辑。
- `graph_name` 的分支映射如果和 `run()` 里的 end data 不一致，复用再好也会把错误图跑进来。

## 6. 调试 Checklist
```infographic
list-column-done-list
  data
    title ReusableRPCProtocol 调试清单
    items
      - label 检查 protocol 注册
        desc 确认 `src/main.cpp:84-88` 没有失败
        done true
      - label 检查服务装配
        desc 确认 `GenericGRCService` 和 HTTP service 都被挂到 server
        done true
      - label 检查 graph 归还
        desc 确认 `graph->reset()` 在请求末尾执行
        done true
      - label 检查 closure 生命周期
        desc 确认 `send_response()` 后再 `release()`，没有提前释放
        done true
```

## 7. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/main.cpp:73-136`
- `src/service/grc_service.cpp:151-220`
- `src/service/grc_service.cpp:226-455`
- `src/service/grc_service.cpp:457-563`

> 说明：当前运行环境没有独立的当日 daily-plan，可用历史候选项回退；KU/业务背景需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260828-reusable-rpc-protocol-and-closure-lifecycle.md)。
