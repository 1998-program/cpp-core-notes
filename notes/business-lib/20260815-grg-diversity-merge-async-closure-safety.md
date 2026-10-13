# 2026-08-15 周六代码理解：GRG DiversityMerge 并行合并与闭包安全
<!-- topic-nav:start -->
> 主题：`business/diversity-merge` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260619-grg-new-diversity-merge-multistream-engine.md)
<!-- topic-nav:end -->


> 日期：2026-08-15  
> 主题来源：2026-06-01 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 GRG 侧 `DiversityMerge` 并行合并与闭包安全链路；KU/业务背景需人工补充。  
> 范围：只分析 `DiversityMergeFunction` 中的并发拆分、`bthread_async`、`Future` 收集、`closure.wait()` 与结果汇总；不展开全部 diversity 规则。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.1fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px}.arch-title{font-size:15px;font-weight:700;margin:0 0 8px}.arch-box{border:1px solid #94a3b8;border-radius:8px;padding:8px 10px;margin:6px 0;background:#f8fafc}.arch-box.hot{border-color:#dc2626;background:#fef2f2}.arch-box.muted{border-style:dashed;background:#f8fafc}.arch-arrow{font-size:12px;color:#64748b;margin:4px 0 4px 2px}.arch-note{font-size:12px;line-height:1.5;color:#475569}</style><div class="arch-wrap"><div class="arch-col"><div class="arch-title">并行主链路</div><div class="arch-box hot">DiversityMergeFunction</div><div class="arch-arrow">↓</div><div class="arch-box">effect_input / general_adjust_rule_operators</div><div class="arch-arrow">↓</div><div class="arch-box hot">std::vector&lt;Future&lt;void, Mutex&gt;&gt; futures</div><div class="arch-arrow">↓</div><div class="arch-box">bthread_async(lambda)</div><div class="arch-arrow">↓</div><div class="arch-box muted">closure.wait() / 汇总</div></div><div class="arch-col"><div class="arch-title">收尾与观测</div><div class="arch-box">diversity_merge_begin_us</div><div class="arch-arrow">↓</div><div class="arch-box">base::gettimeofday_us()</div><div class="arch-arrow">↓</div><div class="arch-box muted">延迟日志与 merge 结果输出</div><div class="arch-note">并发拆分发生在规则执行中，闭包等待与时间统计决定了最后的响应出口。</div></div></div></div>

## 1. 核心链路
`DiversityMergeFunction` 先拿到 `effect_input`，再固定并发度 `concurrent_num = 8`，按 `items_per_thread` 切片，给每个 worker 投递 `bthread_async`。每个异步任务在 lambda 里处理自己的子区间，任务句柄统一放进 `futures`，最终再回收等待。这个模式的核心不是“开线程”本身，而是把并发边界限定在局部输入切片上，避免整批数据共享写冲突。

在更下游的位置，代码里还多次出现 `closure.wait()`，说明部分分支依赖闭包完成后才能继续向后推进。换句话说，合并结果不是立即同步返回，而是先让异步分片跑完，再进入收口与日志统计。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant Caller
participant DiversityMergeFunction as DMF
participant Futures
participant Worker
participant Closure
Caller -> DMF: execute merge
DMF -> DMF: gather effect_input
DMF -> DMF: futures.reserve(8)
loop 8 slices
  DMF -> Futures: bthread_async(lambda)
  Futures -> Worker: process slice
  Worker --> Futures: task done
end
DMF -> Futures: iterate futures
Futures -> Closure: wait()
Closure --> DMF: finished
DMF -> DMF: merge result + timing
DMF --> Caller: response/result
@enduml
```

## 2. 配置/结构信息图

```infographic
sequence-snake-steps-simple
data
  title DiversityMerge 并发结构
  items
    - label 1. 取输入
      desc 从 effect_input 取待处理列表
    - label 2. 定并发
      desc concurrent_num 固定为 8，提前 reserve futures
    - label 3. 切片派发
      desc items_per_thread 按总量均分到 bthread_async
    - label 4. 局部计算
      desc lambda 只处理自己的 start_index/end_index
    - label 5. 汇总等待
      desc futures 与 closure.wait() 保证结果收口
```

## 3. 关键结论
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#ffffff;border:1px solid #d9e2ec;border-left:4px solid #dc2626;border-radius:8px;padding:12px 14px;margin:12px 0;color:#1f2937"><div style="font-size:13px;font-weight:700;margin-bottom:6px">并发边界是切片，不是共享集合</div><div style="font-size:13px;line-height:1.6;color:#334155">`effect_input` 被均分为多个区间，每个 `bthread_async` 只处理自己的片段，因此数据竞争风险主要来自捕获对象和共享状态，而不是调度本身。</div></div>

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#ffffff;border:1px solid #d9e2ec;border-left:4px solid #0f766e;border-radius:8px;padding:12px 14px;margin:12px 0;color:#1f2937"><div style="font-size:13px;font-weight:700;margin-bottom:6px">时间统计与收口耦合</div><div style="font-size:13px;line-height:1.6;color:#334155">`diversity_merge_begin_us` 与后续耗时统计绑定在同一条链路里，说明这里既关注结果正确性，也关注并行化带来的尾延迟。</div></div>

## 4. 调试 checklist

```infographic
list-column-done-list
data
  title DiversityMerge 调试清单
  items
    - label 确认切片
      desc 检查 items_per_thread 和 start/end_index 是否覆盖完整输入
      done true
    - label 确认捕获
      desc 检查 lambda 捕获的 this、effect_input、operators 是否在异步期间有效
      done true
    - label 确认等待
      desc 检查 futures 和 closure.wait() 是否都执行到位
      done true
    - label 确认汇总
      desc 检查并发任务结束后再做 merge 与日志统计
      done true
    - label 确认异常路径
      desc 排查子任务失败是否会导致主流程提前返回
      done true
```

## 5. Pitfalls
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin:10px 0"><div style="font-size:13px;font-weight:700;margin-bottom:6px">Pitfall 1: 捕获引用生命周期不够长</div><div style="font-size:13px;line-height:1.6;color:#334155">`bthread_async([this, &effect_input, &general_adjust_rule_operators, ...])` 依赖外层对象在等待完成前一直存活，缩短生命周期会直接引入悬空引用风险。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin:10px 0"><div style="font-size:13px;font-weight:700;margin-bottom:6px">Pitfall 2: 只看 futures 不看 closure</div><div style="font-size:13px;line-height:1.6;color:#334155">部分分支还要等 `closure.wait()`，如果只检查 `futures`，会误判主流程已经收口。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:12px 14px;margin:10px 0"><div style="font-size:13px;font-weight:700;margin-bottom:6px">Pitfall 3: 把并发度当成结果正确性保障</div><div style="font-size:13px;line-height:1.6;color:#334155">`concurrent_num = 8` 只是调度参数，不保证规则幂等或输出顺序稳定，真正的正确性仍取决于子任务和汇总逻辑。</div></div>

## 6. 证据来源
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:110-112`
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:745-770`
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:1087-1088`
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:1237-1251`
- `notes/weekly-topic-selection/daily-plan-20260529.json:1-22`

## 7. 备注
本次环境未提供可用 KU 文档正文，需人工补充业务背景与外部说明。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260815-grg-diversity-merge-async-closure-safety.md)。
