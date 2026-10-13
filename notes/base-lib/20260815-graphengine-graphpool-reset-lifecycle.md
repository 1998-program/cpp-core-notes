# 2026-08-15 周六代码理解：GraphEngine GraphPool 对象池复用与 reset 生命周期
<!-- topic-nav:start -->
> 主题：`base/graphpool-reset` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260827-graphpool-try-get-run-reset-lifecycle.md)
<!-- topic-nav:end -->


> 日期：2026-08-15  
> 主题来源：2026-06-01 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 `GraphEngine` / `GraphPool` 对象池复用与重置链路；KU/业务背景需人工补充。  
> 范围：只分析 `GraphUnit::get()`、`GraphUnit::try_get()`、`GraphEngine::get()`、`GraphEngine::clear()` 与底层 `GraphPool` 的对象获取、回收、重建链路；不扩展到整个 graph engine。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.2fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px}.arch-title{font-size:15px;font-weight:700;margin:0 0 8px}.arch-box{border:1px solid #94a3b8;border-radius:8px;padding:8px 10px;margin:6px 0;background:#f8fafc}.arch-box.hot{border-color:#2563eb;background:#eff6ff}.arch-box.muted{border-style:dashed;background:#f8fafc}.arch-arrow{font-size:12px;color:#64748b;margin:4px 0 4px 2px}.arch-note{font-size:12px;line-height:1.5;color:#475569}</style><div class="arch-wrap"><div class="arch-col"><div class="arch-title">入口与池</div><div class="arch-box hot">GraphUnit::get()</div><div class="arch-arrow">↓</div><div class="arch-box hot">GraphUnit::try_get()</div><div class="arch-arrow">↓</div><div class="arch-box">GraphPool::get / try_get</div><div class="arch-arrow">↓</div><div class="arch-box">Graph::create()</div><div class="arch-arrow">↓</div><div class="arch-box muted">builder.build().release()</div></div><div class="arch-col"><div class="arch-title">生命周期控制</div><div class="arch-box">GraphEngine::get(graph_name)</div><div class="arch-arrow">↓</div><div class="arch-box">GraphEngine::clear()</div><div class="arch-arrow">↓</div><div class="arch-box muted">_graph_names.clear()</div><div class="arch-arrow">↓</div><div class="arch-box muted">_graphs.clear()</div><div class="arch-note">对象获取和清理分离，池负责复用，Engine 负责图名索引和容器收口。</div></div></div></div>

## 1. 核心链路
GraphUnit 对外只暴露 `get()` / `try_get()`，本质是把调用转发到 `_pool`。`GraphEngine::get()` 则按图名找到对应的 `GraphUnit` 并取出池化对象。真正创建图对象的地方在 `GraphUnit::create()`，这里通过 `_builder.build()` 构造后再 `release()`，说明对象所有权会脱离临时 `unique_ptr`，交给池体系管理。

GraphEngine 的清理逻辑在 `clear()`，会先清掉图名集合，再清掉 `_graphs` 容器。这里的关键不是“销毁单个对象”，而是“收束图注册表”，让后续 `get()` 只能走新的初始化结果。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant Caller
participant GraphUnit
participant GraphPool
participant GraphEngine
participant Builder
Caller -> GraphEngine: get(graph_name)
GraphEngine -> GraphUnit: get()
GraphUnit -> GraphPool: get()
GraphPool -> GraphUnit: create()
GraphUnit -> Builder: build()
Builder --> GraphUnit: unique_ptr<Graph>
GraphUnit -> GraphUnit: release()
GraphPool --> GraphUnit: pooled Graph
GraphEngine --> Caller: pooled object
Caller -> GraphEngine: clear()
GraphEngine -> GraphEngine: _graph_names.clear()
GraphEngine -> GraphEngine: _graphs.clear()
@enduml
```

## 2. 配置/结构信息图

```infographic
sequence-ascending-steps
data
  title GraphPool 生命周期关注点
  items
    - label 1. 入口分发
      desc GraphUnit::get() 与 try_get() 只是转发到池对象
    - label 2. 构造脱壳
      desc create() 中 build() 后 release()，对象所有权进入池体系
    - label 3. 引擎索引
      desc GraphEngine::get() 先按 graph_name 找 unit，再取池对象
    - label 4. 容器清理
      desc clear() 清空图名与图容器，避免旧图继续被命中
    - label 5. 复用边界
      desc 复用发生在池内，不发生在 GraphEngine 索引层
```

## 3. 关键结论
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#ffffff;border:1px solid #d9e2ec;border-left:4px solid #2563eb;border-radius:8px;padding:12px 14px;margin:12px 0;color:#1f2937"><div style="font-size:13px;font-weight:700;margin-bottom:6px">对象池复用的真正边界</div><div style="font-size:13px;line-height:1.6;color:#334155">`GraphPool` 负责对象复用，`GraphEngine` 负责图名到 `GraphUnit` 的索引和整体清理。只要 `clear()` 没有触发，旧的图容器就可能持续服务请求；一旦 `clear()` 执行，后续请求必须依赖重新装配的图注册表。</div></div>

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#ffffff;border:1px solid #d9e2ec;border-left:4px solid #0f766e;border-radius:8px;padding:12px 14px;margin:12px 0;color:#1f2937"><div style="font-size:13px;font-weight:700;margin-bottom:6px">风险点</div><div style="font-size:13px;line-height:1.6;color:#334155">最容易误读的是把 `GraphEngine::clear()` 当成单对象析构。它更像注册表级别的收口动作，真正的对象生命周期仍受池与 builder 返回值控制。</div></div>

## 4. 调试 checklist

```infographic
list-column-done-list
data
  title GraphPool 调试清单
  items
    - label 确认入口
      desc 先看 GraphUnit::get()/try_get() 是否真的走到目标池
      done true
    - label 确认构造路径
      desc 检查 create() 是否通过 _builder.build().release() 构造对象
      done true
    - label 确认索引命中
      desc 检查 GraphEngine::get(graph_name) 是否命中正确的 GraphUnit
      done true
    - label 确认清理边界
      desc 看 GraphEngine::clear() 是否清掉图名和容器
      done true
    - label 确认回收时机
      desc 排查池对象是否在外部长期持有导致复用失效
      done true
```

## 5. Pitfalls
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin:10px 0"><div style="font-size:13px;font-weight:700;margin-bottom:6px">Pitfall 1: 只看 Engine 不看 Pool</div><div style="font-size:13px;line-height:1.6;color:#334155">对象是否复用，关键在 `GraphPool`，不是 `GraphEngine` 的 map 容器本身。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI,Roboto,sans-serif;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin:10px 0"><div style="font-size:13px;font-weight:700;margin-bottom:6px">Pitfall 2: 把 clear 误当析构</div><div style="font-size:13px;line-height:1.6;color:#334155">`clear()` 是注册表收口，不等于所有对象立即从进程内存中消失。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin:10px 0"><div style="font-size:13px;font-weight:700;margin-bottom:6px">Pitfall 3: 忽略 release 语义</div><div style="font-size:13px;line-height:1.6;color:#334155">`build().release()` 说明对象所有权会离开临时智能指针，后续必须沿池和封装层检查回收责任。</div></div>

## 6. 证据来源
- `baidu/feed-general/framework/src/graph_engine.cpp:56-65`
- `baidu/feed-general/framework/src/graph_engine.cpp:299-343`
- `baidu/feed-general/framework/src/graph_engine.cpp:561-569`
- `notes/weekly-topic-selection/daily-plan-20260529.json:1-22`

## 7. 备注
本次环境未提供可用 KU 文档正文，需人工补充业务背景与外部说明。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260815-graphengine-graphpool-reset-lifecycle.md)。
