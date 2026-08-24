# 2026-08-13 周四代码理解：GRG DiversityMerge 并行合并与闭包安全

> 日期：2026-08-13  
> 主题来源：2026-06-01 daily-plan 缺失，按历史未覆盖主题 fallback 到 GRG 侧并行召回/合并闭包安全；KU 正文未逐篇读取，需人工补充业务背景。  
> 范围：只分析 `DiversityMerge`、`UserPredictFunction` 以及相关并发调用链里 Future、bthread_async、lambda 捕获和结果汇总；本文不覆盖全部 diversity 规则。

---

## 0. 架构全景图
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1fr 1fr;gap:14px}.arch-layer{border:1px solid #cbd5e1;border-radius:10px;background:#fff;padding:12px}.arch-title{font-size:15px;font-weight:700;margin:0 0 8px;color:#0f172a}.arch-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.arch-box{border:1px solid #dbe4ee;border-radius:8px;padding:10px;background:#f8fafc;font-size:13px;line-height:1.4}.arch-box strong{display:block;margin-bottom:2px}.arch-box.highlight{border-color:#4f7cff;background:#eef4ff}.arch-note{font-size:12px;color:#475569;line-height:1.55}</style><div class="arch-wrap"><div class="arch-layer"><div class="arch-title">GRG 并行合并执行面</div><div class="arch-grid"><div class="arch-box highlight"><strong>DiversityMerge</strong>承接多路召回结果并执行合并与排序</div><div class="arch-box"><strong>UserPredictFunction</strong>负责用户行为/兴趣预测前的并发准备</div><div class="arch-box"><strong>Future / bthread_async</strong>把多个分支预测并行化</div><div class="arch-box"><strong>结果收敛点</strong>在合并前统一等待并聚合状态</div></div></div><div class="arch-layer"><div class="arch-title">业务边界</div><div class="arch-grid"><div class="arch-box"><strong>召回 vs 合并</strong>并行只发生在独立分支，合并逻辑保持单线程可控</div><div class="arch-box"><strong>规则 vs 结果</strong>闭包不能偷偷修改共享规则集合</div><div class="arch-box"><strong>用户态状态</strong>预测上下文在异步任务间传播但不应被悬空引用</div><div class="arch-box"><strong>容量预估</strong>与 base 侧一样，先把 Future 容器边界定死</div></div><div class="arch-note" style="margin-top:8px">这条链路的核心不是“多线程越多越好”，而是把可并行的预测/召回拆出去，再回到确定性的 merge 阶段收口。</div></div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam shadowing false
left to right direction
actor Caller
participant "UserPredictFunction" as U
participant "bthread_async" as A
participant "Future list" as F
participant "DiversityMerge" as D
participant "Merge result" as R
Caller -> U : init(config) / predict(...)
U -> F : reserve(paral_size)
loop spawn prediction branches
  U -> A : launch branch lambda(need state)
  A --> F : Future<...>
end
loop await all branches
  U -> F : get()/wait()
  F --> U : branch output
end
U -> D : pass merged candidates
D -> R : apply diversity / sort / dedup
D --> Caller : final response
@enduml
```

## 2. 配置与结构信息图
```infographic
sequence-ascending-steps-simple
data
  title DiversityMerge 并发收口流程
  desc 多路预测先并行，后统一合并
  items
    - label 1. 初始化预测函数
      desc 读取配置并建立预测上下文
      icon mdi/cog-outline
    - label 2. 并行发起分支
      desc 每个分支单独进入 bthread_async
      icon mdi/source-branch
    - label 3. 收集 Future
      desc 先 reserve 再 push，减少调度抖动
      icon mdi/view-list-outline
    - label 4. DiversityMerge 收口
      desc 把候选流统一做去重、排序与多样性控制
      icon mdi/filter-cog-outline
```

## 3. 关键实现片段
- `src/process/user_predict.cpp:9-12` 同时引入 `Future` 和 `bthread_async`，说明这个业务函数本身就是并行执行模型的一部分。
- `src/process/user_predict.cpp:13-23` 体现它是一个独立的 `GRGGraphFunction`，并非底层工具函数，意味着异步闭包的状态约束要按业务生命周期来审视。
- `src/process/diversity_merge.cpp:1-18` 的头文件依赖显示它位于 exec_engine / diversity operator 交叉层，说明最终收口是在引擎侧而不是散落在分支里。
- `src/process/diversity_merge.cpp:720-790` 与后续片段包含合并主逻辑入口，配合并行预测分支，构成“预测先行、合并后收”的业务链路。

## 4. Pitfalls 卡片
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border-left:4px solid #b45309;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:14px 0;color:#1f2937"><div style="font-size:15px;font-weight:700;margin-bottom:6px">闭包里不要拿会变的共享容器</div><div style="font-size:13px;line-height:1.55;color:#374151">业务分支常常需要读取候选集、规则集或上下文。如果 lambda 捕获的是可变共享容器的引用，而异步任务又跨出原作用域，就会把 merge 阶段的确定性变成偶发问题。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border-left:4px solid #0891b2;border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin:14px 0;color:#1f2937"><div style="font-size:15px;font-weight:700;margin-bottom:6px">业务合并必须单点收口</div><div style="font-size:13px;line-height:1.55;color:#374151">多路并发可以提升吞吐，但最后的多样性控制、去重、排序要在一个明确收口点完成，避免每个分支自己做一遍导致规则漂移。</div></div>

## 5. 调试 checklist
```infographic
list-column-done-list
data
  title 调试清单
  items
    - label 并行分支是否真正独立
      done false
    - label Future 结果是否全部等待
      done false
    - label lambda 是否只捕获业务必需状态
      done false
    - label DiversityMerge 是否是唯一收口点
      done false
    - label 合并前后候选数是否可解释
      done false
```

## 6. 证据来源
- `src/process/user_predict.cpp:9-23`
- `src/process/diversity_merge.cpp:1-18`
- `src/process/diversity_merge.cpp:720-790`


---

## 七、业务代码库适配分析
> **分析时间**：2026-08-24T19:02:56.655721
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 这次技术关注的是 **“并行分支 + Future 收口 + `bthread_async` + 闭包安全 + 统一 merge”** 的业务链路，核心目标不是无限扩线程，而是把可独立的预测/因子计算拆并行，把最终去重、排序、多样性控制收口到单点。
- 从扫描结果看，`feeda-mv-grc` 对这类模式的适配潜力更高：已发现相关目标库使用分布在 **10 个文件**，且代码中 `std::vector/std::string/std::unordered_map` 的使用规模很大，说明很适合做 Future 容器预分配、并行任务汇聚和结果合并优化。`feeda-mv-grg` 目前只在 **1 个文件**发现目标库使用，说明落点较集中，更适合先做局部验证，再决定是否扩展。

---

## 2. 代码库详情

### feeda-mv-grg（序列生成服务）

- 已发现目标库使用仅 **1 个文件**：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 结合技术笔记中的主题，这个文件更像是 **多样性规则/结果收口阶段** 的实现点，适合承接“合并、去重、排序”的逻辑。
- 现有 `std` 等价物使用规模：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 代表性代码：
  - `model/model.h:9`、`model/paddle_model.h:103`、`model/paddle_model.h:107`
- 结论：
  - 该库已经有较多候选集/模型输入相关容器使用，说明如果未来在预测阶段引入 `Future` 容器或分支并行，**容器预分配、状态只读化、闭包捕获收敛** 会比较容易落地。
  - 当前可直接参考的落点偏少，迁移更适合先从 `low_clarity_diversity_rule.cpp` 这类收口规则文件切入。

### feeda-mv-grc（召回汇聚服务）

- 已发现目标库使用 **10 个文件**，代表性文件包括：
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `processor/new_adjust/precise_score_init.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
  - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
- 这组文件覆盖了 **因子生成、初始化、场景级处理、队列调整、会话级因子生成**，天然适合做“并行生成 + 统一汇聚”。
- 现有 `std` 等价物使用规模非常大：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 代表性代码：
  - `service/grc_http_service.cpp:62`
  - `service/grc_http_service.cpp:81`
  - `service/grc_http_service.cpp:152`
- 结论：
  - 该库已经具备大量容器和服务层聚合逻辑，说明 **Future 列表预留、并发结果回收、最终单点 merge** 很有推广空间。
  - 从文件分布看，这里比 `grg` 更适合优先引入“并行分支 + 确定性收口”的模式。

---

## 3. 💡 适用性评估与建议

- **建议 1：在 `feeda-mv-grc/processor/multi_factor/subcate_future_factor_gen.cpp` 优先试点并行化**
  - 如果该文件内部存在多个互不依赖的因子计算分支，可改造成 `bthread_async` 启多个 branch，再用 `Future` 列表统一等待。
  - 适用场景：特征拉取、分场景打分、多个子模块候选生成。
  - 重点：分支闭包只捕获 **只读上下文**，不要直接捕获会变化的共享容器引用。

- **建议 2：在 `feeda-mv-grc/processor/multi_factor/ltr_factor_gen_scene.cpp` 和 `session_ltr_dibar_factor_gen.cpp` 做“先 reserve 后 push”**
  - 这两类场景通常会维护分支结果容器或候选集，建议在发起异步前先 `reserve(paral_size)`，减少 Future 容器扩容抖动。
  - 适用场景：分支数固定或可预估、每路返回结构大小接近。
  - 价值：降低调度抖动和内存重分配带来的尾延迟。

- **建议 3：把 `feeda-mv-grc/operator/adjuster/function_queue/youzhi_queue_adjust.cpp` 作为统一收口点**
  - 如果当前存在各分支自己做去重/排序/规则修正，建议收敛到这里，保证业务规则一致。
  - 适用场景：多路召回后做队列调整、排序打散、去重、多样性控制。
  - 参考思路：严格按“预测先行、合并后收”执行，不要把 merge 逻辑分散到每个 branch 中。

- **建议 4：在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为 merge 规则参考**
  - 这个文件是 `grg` 里已发现的目标库使用点，适合检查是否已经具备单点收口的规则雏形。
  - 如果当前规则实现中有多次访问共享候选集的写操作，建议改成：
    - 分支阶段只产出局部结果
    - 规则阶段统一合并
    - 最终阶段再做排序/去重
  - 这样能降低闭包悬空引用和规则漂移风险。

- **建议 5：在 `feeda-mv-grc/processor/new_adjust/precise_score_init.cpp` 评估“只读上下文 + 返回值汇聚”**
  - 如果初始化阶段需要并行计算多个 score 或依赖项，建议把上下文对象改成只读快照，异步任务只返回结果，不直接修改共享状态。
  - 适用场景：初始化依赖多、但各分支互不影响的场景。
  - 对于大规模 `std::vector` 使用场景，建议结果容器统一预分配后再合并。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：lambda 捕获引用导致悬空**
  - 异步任务跨出原作用域后，如果捕获的是可变共享容器引用，很容易出现未定义行为或偶发错误。
  - 建议只捕获必要字段，优先用值捕获或只读指针/`shared_ptr<const T>`。

- **风险 2：并行开销可能掩盖收益**
  - 如果单个分支计算很轻，`bthread_async + Future` 的调度成本可能超过收益。
  - 建议只对重计算、I/O 等待、分支数明确的路径开启并行。

- **风险 3：合并顺序变化可能影响线上结果**
  - 并行后结果到达顺序不稳定，如果 merge 逻辑依赖隐含顺序，可能导致排序/去重结果变化。
  - 建议在 `DiversityMerge` 或等价收口点显式定义稳定排序规则。

- **风险 4：异常与部分失败处理更复杂**
  - 多个 Future 中只要有一个失败，就需要明确是降级继续、局部忽略，还是整体失败。
  - 建议在收口层统一定义超时、异常、空结果的处理策略。

---

## 5. 结论

- `feeda-mv-grc` 是这套技术的 **优先迁移目标**，因为相关文件分布更广、容器使用更多、并行收益更明显。
- `feeda-mv-grg` 更适合 **从 `low_clarity_diversity_rule.cpp` 这类收口规则文件做局部试点**，验证闭包安全和单点 merge 逻辑后再扩展。
- 这类改造的核心不是“把所有逻辑都并行化”，而是 **把可并行部分拆出去，把业务确定性留在收口阶段**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
