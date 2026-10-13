# 2026-08-25 周二代码理解：GraphPool 复用与 reset 生命周期
<!-- topic-nav:start -->
> 主题：`base/graphpool-reset` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260827-graphpool-try-get-run-reset-lifecycle.md)
<!-- topic-nav:end -->


> 日期：2026-08-25  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的历史候选项；本次没有独立的当日 daily-plan 可直接读取，KU/业务背景需人工补充。  
> 范围：只看 `src/service/grc_service.cpp` 中 `GraphPool::try_get`、`graph->run()`、`graph->reset()`、图名选择与 `DynamicTimeOutPlugin` 获取链路，外加 `GraphPool` 的复用语义。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:14px;background:#f8fafc;line-height:1.45;">
  <div style="display:grid;grid-template-columns:1.05fr 1.25fr 1fr;gap:12px;align-items:stretch;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求入口</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`grc_service.cpp` 入口选择图实例</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">根据请求场景、超时插件和图名，取出可复用对象。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">核心生命周期</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`GraphPool::try_get` → `graph->run()` → `graph->reset()`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">复用的关键不是“拿到图”，而是“跑完后恢复到可再入状态”。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">回收出口</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">reset 清空上下文与临时态</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">保证同一图对象在后续请求中不会携带脏状态。</div>
    </div>
  </div>
  <div style="margin-top:12px;display:grid;grid-template-columns:1fr 80px 1fr 80px 1fr;gap:10px;align-items:center;">
    <div style="background:#eef6ff;border:1px solid #bfdbfe;border-radius:8px;padding:10px;text-align:center;color:#1d4ed8;">请求上下文</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">图池取对象</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">执行 + reset</div>
  </div>
</div>

## 1. 核心流程
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "grc_service.cpp" as S
participant "GraphPool" as P
participant "Graph" as G
participant "DynamicTimeOutPlugin" as T
Client -> S : request
S -> T : fetch timeout policy
S -> P : try_get(graph_name)
P --> S : graph instance
S -> G : run(context)
G --> S : status / result
S -> G : reset()
S --> Client : response
@enduml
```

## 2. 图池复用与 reset 关系
```infographic
sequence-ascending-steps
  data
    title GraphPool 生命周期拆解
    desc 先拿对象，再执行，最后归还到可复用状态
    items
      - label 图名解析
        desc 由请求场景和插件配置决定要拿哪一个图
      - label try_get
        desc 从池中取出图实例，避免每次重新构造
      - label run
        desc 执行整条图链路，填充输出和临时上下文
      - label reset
        desc 清空临时字段，归还到下一次请求可用状态
```

## 3. 关键判断
- `try_get` 解决的是对象获取成本，不解决状态污染问题。
- `run()` 解决的是业务执行问题，不负责让对象天然可复用。
- `reset()` 是复用成立的前提；没有它，图池只是缓存了带脏数据的执行器。
- `DynamicTimeOutPlugin` 的存在说明这条链路的图选择和超时控制是一起生效的，不能只看 `GraphPool`。

## 4. 架构卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:4px solid #2563eb;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#2563eb;text-transform:uppercase;letter-spacing:.04em;">关键结论</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">GraphPool 的价值在于把高频构图成本压低，但真正的正确性边界在 `reset()`。只要 reset 做得不完整，复用越充分，错误越隐蔽。</div>
</div>

<div style="height:10px"></div>

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff7ed;border:1px solid #fed7aa;border-left:4px solid #f97316;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#c2410c;text-transform:uppercase;letter-spacing:.04em;">调试提示</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">如果复用后出现跨请求串值，优先查临时上下文、重复字段和插件状态是否都被 reset；不要先怀疑池本身。</div>
</div>

## 5. Pitfalls
- 只看 `try_get` 容易误判为“对象池优化完毕”，实际上必须验证 `reset` 覆盖面。
- 若图实例内部还持有外部上下文指针，单纯清字段不够，可能需要回收绑定关系。
- 超时插件若依赖请求级配置，池化对象就不能缓存会话态参数。

## 6. 调试 Checklist
```infographic
list-column-done-list
  data
    title GraphPool 调试清单
    items
      - label 检查图名来源
        desc 确认请求场景是否选中了预期的 graph_name
        done true
      - label 检查 run 前状态
        desc 确认对象出池时没有残留上下文
        done true
      - label 检查 reset 覆盖面
        desc 重点看临时字段、插件状态、输出缓存
        done true
      - label 检查异常路径
        desc 出错分支也必须归还到一致状态
        done true
```

## 7. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/service/grc_service.cpp:199`
- `src/service/grg_service.cpp:65`
- `notes/base-lib/20260822-graphpool-reset-lifecycle.md`

> 说明：当前运行环境没有直接展开代码仓库源码正文，以上代码路径来自 daily-plan 的候选证据摘要；KU/源码正文需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260825-graphpool-reset-lifecycle.md)。
