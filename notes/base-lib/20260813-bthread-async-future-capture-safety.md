# 2026-08-13 周四代码理解：bthread_async 批量并行与 Future 捕获安全
<!-- topic-nav:start -->
> 主题：`base/bthread-future` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260528-bthread-async-fan-out-future-join-usage-deep-dive.md)
<!-- topic-nav:end -->


> 日期：2026-08-13  
> 主题来源：2026-06-01 daily-plan 缺失，按历史未覆盖主题 fallback 到 `bthread_async` 批量并行模式与捕获安全；KU 正文未逐篇读取，需人工补充业务背景。  
> 范围：只分析 GRC 侧 `ParallelPredictorPlugin`、`doc_feature_with_cache` 相关路径里的并发发起、Future 汇总、lambda 捕获与容器预分配；本文不扩展到全部 RPC 框架。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.1fr .9fr;gap:14px}.arch-layer{border:1px solid #cbd5e1;border-radius:10px;background:#fff;padding:12px}.arch-title{font-size:15px;font-weight:700;margin:0 0 8px;color:#0f172a}.arch-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.arch-box{border:1px solid #dbe4ee;border-radius:8px;padding:10px;background:#f8fafc;font-size:13px;line-height:1.4}.arch-box strong{display:block;margin-bottom:2px}.arch-box.highlight{border-color:#4f7cff;background:#eef4ff}.arch-arrow{display:flex;align-items:center;justify-content:center;font-size:18px;color:#64748b;padding:6px 0}.arch-note{font-size:12px;color:#475569;line-height:1.55}</style><div class="arch-wrap"><div class="arch-layer"><div class="arch-title">GRC 并行请求执行面</div><div class="arch-grid"><div class="arch-box highlight"><strong>ParallelPredictorPlugin</strong>入口处根据 `rpc_context_map` 批量触发异步调用</div><div class="arch-box"><strong>Future 列表</strong>预分配容量，避免并行发起时二次扩容</div><div class="arch-box"><strong>Context / timeout</strong>把动态超时注入每个请求</div><div class="arch-box"><strong>RPC context map</strong>承载单个子调用的状态与结果写回</div></div></div><div class="arch-layer"><div class="arch-title">性能与安全关注点</div><div class="arch-grid"><div class="arch-box"><strong>lambda 捕获</strong>并发闭包里只捕获必要对象，避免悬挂引用</div><div class="arch-box"><strong>bthread_async</strong>把阻塞调用并发化，降低串行等待</div><div class="arch-box"><strong>reserve()</strong>控制容器重分配成本</div><div class="arch-box"><strong>get_dynamic_timeout</strong>把业务场景和超时策略解耦</div></div><div class="arch-note" style="margin-top:8px">主线是“批量发起 - 独立等待 - 汇总结果”。这里的关键不是把所有调用都变成异步，而是把并行边界收紧到真正独立的 RPC 段上。</div></div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam shadowing false
left to right direction
actor Caller
participant "ParallelPredictorPlugin" as P
participant "Util" as U
participant "bthread_async" as A
participant "Future list" as F
participant "RPC context map" as M
Caller -> P : predict(rpc_context_map, log_id, context, rpc_name)
P -> U : get_dynamic_timeout(context, rpc_name)
P -> F : reserve(paral_size)
loop each item in rpc_context_map
  P -> A : launch lambda(item, timeout, log_id, context)
  A --> F : Future<int, bthread::Mutex>
end
loop wait all futures
  P -> F : get()/wait()
  F --> P : status / error code
end
P -> M : merge call results back
P --> Caller : aggregated status
@enduml
```

## 2. 配置与结构信息图
```infographic
sequence-snake-steps-simple
data
  title 并发预测执行步骤
  desc GRC 侧并行调用的最小闭环
  items
    - label 1. 计算动态超时
      desc 通过 rpc_name 和 Context 决定单次 RPC budget
      icon mdi/timer-outline
    - label 2. 预分配 Future 容器
      desc 先 reserve 再 push，避免并发发起时重复扩容
      icon mdi/all-inclusive-box-outline
    - label 3. bthread_async 发起
      desc 每个 item 独立启动，闭包只带必要状态
      icon mdi/lightning-bolt-outline
    - label 4. 汇总结果
      desc 等待所有 Future 完成后统一合并返回
      icon mdi/playlist-check
```

## 3. 关键实现片段
- `src/plugin/parallel_predictor.cpp:26-43` 显式引入 `bthread_async`，并在 `predict()` 内创建 `std::vector<Future<int, bthread::Mutex>>` 后调用 `reserve(paral_size)`。
- `src/plugin/parallel_predictor.cpp:32-33` 的注释直接点明：未来新 RPC 可以用 `babylon::bthread_async` + Future/Promise 方式简化并行实现。
- `src/plugin/parallel_predictor.cpp:44-48` 在遍历 `rpc_context_map` 时逐项发起异步任务，说明并行边界是按子 RPC 切分，而不是对整个请求体做粗粒度并行。
- `src/processor/doc_feature_with_cache.cpp` 与 `src/processor/doc_feature_with_cache_yitu.cpp` 同样属于“并行发起 + 局部缓存/局部状态”的一类路径，适合复用同一套并发约束。

## 4. Pitfalls 卡片
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border-left:4px solid #d97706;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:14px 0;color:#1f2937"><div style="font-size:15px;font-weight:700;margin-bottom:6px">并发闭包不要带大对象</div><div style="font-size:13px;line-height:1.55;color:#374151">`bthread_async` 的 lambda 如果捕获整个上下文对象，复制成本和生命周期风险都会上升。这里更稳妥的做法是只捕获 `timeout`、`log_id`、必要引用和当前 item，并保证这些引用在 Future 结束前有效。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border-left:4px solid #2563eb;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:14px 0;color:#1f2937"><div style="font-size:15px;font-weight:700;margin-bottom:6px">reserve 不只是优化</div><div style="font-size:13px;line-height:1.55;color:#374151">这里先 `reserve(paral_size)`，不仅是减少扩容分配，也是在表达“Future 数量上界已知”。对并发代码来说，这种显式边界能减少不可预期的抖动。</div></div>

## 5. 调试 checklist
```infographic
list-column-done-list
data
  title 调试清单
  items
    - label 是否所有子请求都进入异步分支
      done false
    - label Future 列表是否预分配
      done false
    - label lambda 捕获是否只保留必要状态
      done false
    - label timeout 是否按 rpc_name 动态计算
      done false
    - label 汇总阶段是否处理单个 Future 失败
      done false
```

## 6. 证据来源
- `src/plugin/parallel_predictor.cpp:26-48`
- `src/processor/doc_feature_with_cache.cpp`
- `src/processor/doc_feature_with_cache_yitu.cpp`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/base-lib/20260813-bthread-async-future-capture-safety.md)。
