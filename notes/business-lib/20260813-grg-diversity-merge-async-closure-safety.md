# 2026-08-13 周四代码理解：GRG DiversityMerge 并行合并与闭包安全
<!-- topic-nav:start -->
> 主题：`business/diversity-merge` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260619-grg-new-diversity-merge-multistream-engine.md)
<!-- topic-nav:end -->


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

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260813-grg-diversity-merge-async-closure-safety.md)。
