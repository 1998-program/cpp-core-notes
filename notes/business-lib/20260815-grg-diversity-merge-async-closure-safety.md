# 2026-08-15 周六代码理解：GRG DiversityMerge 并行合并与闭包安全

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
---

## 七、业务代码库适配分析
> **分析时间**：2026-08-27T19:02:13.501755
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 这次技术点对应的是 **GRG DiversityMerge 的并行切片执行模式**：通过 `bthread_async` 将输入按区间拆分、用 `Future` 收集任务、再通过 `closure.wait()` 做收口，适合 **规则计算、候选项批处理、因子/特征并行评估** 这类“输入独立、汇总集中”的场景。
- 从扫描结果看，**feeda-mv-grg** 仅发现 1 个文件已有目标技术相关使用，说明已有局部实践但覆盖面很小；**feeda-mv-grc** 没有直接使用经验，但 `std::vector` / `std::unordered_map` 等高频遍布大量文件，说明业务代码里存在大量“可切片处理”的循环与聚合逻辑，**迁移潜力主要体现在批量规则、因子生成、调整器计算链路**。

---

## 2. 代码库详情

### feeda-mv-grg：序列生成服务

- 已发现目标库使用：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 说明：
  - 这是当前代码库里**最接近目标模式的参考实现**，可作为并行拆分、闭包生命周期、等待收口的样板。
  - 由于只命中 1 个文件，说明并行合并类逻辑**尚未形成统一抽象**，后续迁移要优先考虑封装公共并发工具，而不是在各规则文件里散落写法。
- 现有 std 等价物使用规模：
  - `std::vector`：1969 次 / 356 文件
  - `std::string`：2443 次 / 425 文件
  - `std::unordered_map`：734 次 / 205 文件
- 解读：
  - 这类数据结构高频意味着业务里存在大量“列表遍历 + 哈希聚合 + 字符串拼装”的逻辑，具备做 **切片并行** 的基础。
  - 但是否值得并行，要看单次处理是否足够重、是否存在共享写冲突。

### feeda-mv-grc：召回汇聚服务

- 已发现目标库使用：
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
- 说明：
  - 这些路径集中在 **因子生成、初始化刷新、调整器、队列式调整**，天然更接近“批量规则/因子计算”模型，比纯 I/O 请求链路更适合做并发切片。
  - 当前扫描结果没有看到该技术的直接落地代码，说明可以优先从 **局部批处理环节** 做试点。
- 现有 std 等价物使用规模：
  - `std::vector`：8520 次 / 1290 文件
  - `std::string`：7267 次 / 1247 文件
  - `std::unordered_map`：2860 次 / 646 文件
- 解读：
  - 这是一个数据遍历和聚合非常重的代码库，若存在“每个候选/特征/因子独立计算”的循环，迁移到 `bthread_async + Future` 这类模式的收益空间更大。
  - 但如果大部分逻辑是小粒度操作，线程调度开销可能抵消收益。

---

## 3. 💡 适用性评估与建议

- **建议 1：优先在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 做并行模式基线**
  - 该文件已出现目标技术相关使用，适合直接梳理出统一模板：
    - 输入按区间切片
    - 子任务只处理局部下标
    - 用 `Future` 汇总
    - 在主线程统一 `wait()` 和收口
  - 建议把这段逻辑抽成公共辅助函数，避免后续其他 diversity rule 重复实现。

- **建议 2：在 `feeda-mv-grc/processor/multi_factor/ltr_factor_gen_scene.cpp` 试点“候选集分片并行”**
  - 如果该文件里存在按候选、按特征、按场景逐项计算的循环，可将其改造成：
    - `items_per_thread` 切片
    - 每个 `bthread_async` 处理一个区间
    - 子任务返回局部结果，最后主线程合并
  - 该文件属于多因子场景，通常比单条请求更适合并行化。

- **建议 3：在 `feeda-mv-grc/processor/new_adjust/precise_score_init_first_refresh.cpp` 评估“初始化批处理”并行化**
  - 若这里有刷新、重算、初始化列表等批量步骤，适合采用和 `DiversityMergeFunction` 类似的并发切片方式。
  - 重点检查：
    - 是否依赖共享缓存
    - 是否有全局计数器或单例状态
    - 是否需要严格输出顺序

- **建议 4：在 `feeda-mv-grc/operator/adjuster/sketchy/duanju_adjuster.cpp`、`ltv_factor_cp_opt.cpp`、`function_queue/youzhi_queue_adjust.cpp` 这类调整器链路中做“局部独立计算”改造**
  - 这些文件名称显示它们更像规则/调整器执行点，适合拆成“前处理 + 并发子任务 + 后处理汇总”。
  - 如果调整逻辑只依赖输入切片，不依赖后续结果顺序，迁移收益通常较高。
  - 若存在共享容器写入，建议先改为：
    - 子任务写局部 `vector` / `map`
    - 主线程再 merge

- **建议 5：对 `feeda-mv-grc` 先做“热路径识别”，不要直接全量替换**
  - 由于扫描到的是大量 `std::vector` / `std::unordered_map` 的常规使用，不能默认每处都适合并行。
  - 建议先在热点文件中确认：
    - 单次处理耗时是否足够高
    - 是否存在可拆分的独立分支
    - 是否有稳定的收口逻辑
  - 只有满足这三点，再引入 `bthread_async` 与 `closure.wait()`。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：闭包/引用生命周期问题**
  - 若像 `bthread_async([this, &effect_input, ...])` 这样捕获引用，必须保证外层对象在所有 `Future` 完成前一直存活。
  - 在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 这种已有实现旁边扩展时，最容易踩到悬空引用。

- **风险 2：只等 `Future` 不等 `closure.wait()` 会导致误判完成**
  - 技术笔记里明确提到存在 `closure.wait()` 收口链路。
  - 如果迁移时只检查任务句柄结束，而忽略闭包完成，可能出现“任务跑完但结果没真正提交”的问题。

- **风险 3：并发度固定不等于性能提升**
  - `concurrent_num = 8` 只是调度参数，不代表任何场景都能变快。
  - 对于粒度很小、逻辑很轻的调整器，线程调度和同步成本可能高于收益。

- **风险 4：结果顺序与幂等性**
  - 并行切片后，子任务完成顺序不再稳定。
  - 如果 `feeda-mv-grc/operator/adjuster/*` 或 `processor/*` 中依赖“先后顺序”或“累计状态”，需要补充稳定排序或线程局部累积方案。

---

如果你愿意，我可以继续把这份报告进一步整理成你笔记里可直接粘贴的版本，或者补一版 **“按代码库分开的迁移优先级表”**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
