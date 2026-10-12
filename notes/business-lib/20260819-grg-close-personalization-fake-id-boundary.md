# 2026-08-19 周三代码理解：GRG 个性化关闭开关与 fake_id 兼容边界

> 日期：2026-08-19  
> 主题来源：2026-06-01 daily-plan 文件缺少明确日计划，按历史未覆盖主题 fallback 到 `GRG` 的个性化关闭开关与 `fake_id` 兼容链路；KU/业务背景需人工补充。  
> 范围：只分析 `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp`、`baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp`、`baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp`，关注基础请求信息、个性化开关注入、散度合并入口与关闭策略的出网边界。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #cbd5e1;border-radius:8px;padding:14px;background:#f8fafc;color:#1f2937;">
  <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;align-items:stretch;">
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">请求入口</div>
      <div>grg_service.cpp</div>
      <div>解析 uid / cuid / vip</div>
      <div>注入基础上下文</div>
    </div>
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">策略边界</div>
      <div>fake_id / close-personalization</div>
      <div>业务策略分支</div>
      <div>散度合并前的拦截或降级</div>
    </div>
    <div style="border:1px solid #94a3b8;border-radius:6px;padding:10px;background:#ffffff;">
      <div style="font-weight:700;margin-bottom:6px;">合并与响应</div>
      <div>diversity_merge.cpp</div>
      <div>scatter_context.cpp</div>
      <div>返回可出网结果</div>
    </div>
  </div>
  <div style="margin-top:12px;border-top:1px solid #cbd5e1;padding-top:10px;line-height:1.6;">
    这个边界的核心不是“是否推荐”，而是请求基础信息如何影响后续散度合并和个性化关闭策略；`fake_id` 与关闭开关都属于上下文信号，必须在合并前就稳定下来。
  </div>
</div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "grg_service" as Svc
participant "scatter_context" as Ctx
participant "diversity_merge" as Merge
participant "Response" as Resp
Client -> Svc : request(uid/cuid/vip/fake_id)
Svc -> Svc : normalize base info
Svc -> Ctx : build scatter context
Ctx -> Ctx : apply close-personalization flag
Ctx -> Merge : enter diversity merge
Merge -> Merge : branch by scene / fake_id / open flag
Merge --> Resp : assemble result
Resp --> Client : final response
@enduml
```

## 2. 业务结构信息图
```infographic
compare-binary-horizontal-underline-text-vs
data
  title 个性化开关与普通路径对比
  items
    - label 开启个性化
      desc 走散度合并与策略增强，结果更依赖上下文
    - label 关闭个性化
      desc 降到兼容路径，强调稳定出网与基础召回
```

```infographic
list-grid-badge-card
data
  title 请求上下文检查点
  items
    - label uid / cuid
      desc 用户基础标识
    - label vip
      desc 等级或用户分层信号
    - label fake_id
      desc 个性化关闭兼容入口
    - label scene
      desc 决定后续 merge 分支
    - label response budget
      desc 合并后的结果预算
```

## 3. 关键证据
- `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:1-16`：服务入口依赖与上下文相关头文件。
- `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:130-160`：请求基础信息、用户标识与扩展字段注入。
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:89-160`：散度合并主入口与策略分支。
- `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp:1646-1695`：上下文读取与 merge 入口前准备。
- `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp:2174-2208`：解析与字段传递边界。

## 4. Pitfalls
<div style="border:1px solid #cbd5e1;border-left:4px solid #f59e0b;border-radius:6px;padding:10px;background:#fffdf7;">
  <div style="font-weight:700;margin-bottom:4px;">fake_id 只在后段处理</div>
  <div>如果关闭开关在 merge 之后才生效，会出现策略已经分叉、结果却被回收的错位。</div>
</div>
<div style="border:1px solid #cbd5e1;border-left:4px solid #ef4444;border-radius:6px;padding:10px;background:#fffdf7;margin-top:8px;">
  <div style="font-weight:700;margin-bottom:4px;">基础信息缺失导致兼容路径误判</div>
  <div>uid/cuid/vip 任一缺失都可能让关闭逻辑和普通用户路径混在一起，排查时要先看请求上下文是否完整。</div>
</div>
<div style="border:1px solid #cbd5e1;border-left:4px solid #6366f1;border-radius:6px;padding:10px;background:#fffdf7;margin-top:8px;">
  <div style="font-weight:700;margin-bottom:4px;">散度合并与开关配置耦合</div>
  <div>如果 merge 代码同时负责策略和开关判定，后续会很难单独验证关闭开关是否真的生效。</div>
</div>

## 5. 调试 Checklist
```infographic
list-column-done-list
data
  title 调试检查项
  items
    - label 检查请求基础字段
      done true
      desc uid / cuid / vip / fake_id 是否都到位
    - label 检查关闭开关
      done true
      desc 是否在 scatter_context 之前完成
    - label 检查 merge 分支日志
      done true
      desc diversity_merge 是否按预期走兼容路径
    - label 检查结果预算
      done true
      desc 合并后的条数是否被错误截断
    - label 检查场景映射
      done true
      desc scene 是否把关闭分支带偏
```

## 6. 证据来源
- `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:1-16`
- `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp:130-160`
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:89-160`
- `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp:1646-1695`
- `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp:2174-2208`

> 内网文档背景需人工补充；这里先用代码库证据固定个性化关闭与 `fake_id` 兼容边界。

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-29T19:09:23.105556
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 从现有扫描结果看，`feeda-mv-grg` 与 `feeda-mv-grc` 都已经存在与“请求上下文、策略分支、结果合并”相关的代码基础，尤其是 `grg` 侧已经明确出现了 `fake_id`、个性化关闭开关、`scatter_context`、`diversity_merge` 这条链路，说明该业务边界并不是“从零引入”，而是更适合做**局部收敛与显式化改造**。
- 从使用规模看，两个代码库中 `std::vector / std::string / std::unordered_map` 的使用量都很大，说明底层工程风格已经偏向标准容器与上下文传递模型；因此如果要强化“开关前置、上下文统一、合并前判定”的适配方式，**迁移阻力主要不在语法层，而在业务边界重构与调用链一致性**。整体迁移潜力较高，但需要按链路分层推进，避免策略逻辑和兼容逻辑耦合。

---

## 2. 代码库详情

### `feeda-mv-grg`：序列生成服务

- 扫描到的目标相关文件较少，仅有 1 个直接命中：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 结合你提供的代码证据，`grg` 侧的关键链路非常清晰：
  - `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp`
  - `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp`
  - `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp`
- 这说明 `grg` 的适配更偏向**核心链路治理**：请求入口做基础信息归一化，`scatter_context` 处理开关注入，`diversity_merge` 负责合并与出网。
- 该库中标准容器使用非常广：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 典型代码还显示接口普遍通过 `std::vector` 承载候选集，如：
  - `model/model.h`
  - `model/paddle_model.h`
- 结论：`grg` 已经具备承载“上下文驱动型策略”的工程基础，但当前目标能力只在少量文件中出现，适合先做**局部试点**，再向散度规则链路扩散。

### `feeda-mv-grc`：召回汇聚服务

- 扫描到的目标相关文件更多，共 10 个文件命中：
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/new_adjust/precise_score_init.cpp`
  - 以及其他 5 个相关文件
- 这说明 `grc` 侧更像是**多处理器、多阶段规则汇聚**的场景，适配需求更分散，通常需要在多个 processor / adjuster / service 层同步考虑。
- `grc` 中标准容器使用更大规模：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 典型代码示例集中在：
  - `service/grc_http_service.cpp`
- 这类代码本身就有较强的请求解析和结构化参数承载能力，因此如果要引入统一的开关/上下文传递机制，`grc_http_service.cpp` 是很适合作为入口参考的文件。
- 结论：`grc` 的适配面更宽，收益也更大，但需要考虑多模块一致性，避免不同 processor 对同一开关理解不一致。

---

## 3. 💡 适用性评估与建议

- **建议 1：把个性化关闭与 `fake_id` 判定前移到请求入口，避免在 merge 之后补判定**
  - 适用文件：
    - `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp`
    - `baidu/feed-gr/feeda-mv-grg/src/operator/diversity/scatter_context.cpp`
  - 建议做法：
    - 在 `grg_service.cpp` 中完成 `uid / cuid / vip / fake_id` 的统一归一化；
    - 生成一个稳定的上下文对象后，再传入 `scatter_context`；
    - 不要让 `diversity_merge.cpp` 再去反推是否关闭个性化。
  - 价值：
    - 减少“策略已分叉，但结果又被回收”的错位问题；
    - 提升日志可解释性。

- **建议 2：把 `diversity_merge.cpp` 改成纯合并层，只消费开关结果，不再承载策略决策**
  - 适用文件：
    - `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp`
  - 建议做法：
    - 将“是否启用个性化”“是否走兼容路径”“是否受 `fake_id` 影响”封装成上游布尔/枚举状态；
    - `diversity_merge.cpp` 只负责按照状态进行结果组装和预算控制。
  - 价值：
    - 降低 merge 层复杂度；
    - 便于单测和线上排查；
    - 更容易验证关闭开关是否真正生效。

- **建议 3：以 `low_clarity_diversity_rule.cpp` 作为 `grg` 的试点改造点**
  - 适用文件：
    - `baidu/feed-gr/feeda-mv-grg/src/strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 建议做法：
    - 如果该规则依赖个性化状态，改成显式接收上下文参数；
    - 避免直接读取全局开关或隐式状态；
    - 先在这一条规则上验证“关闭个性化 + fake_id 兼容”的最小闭环。
  - 价值：
    - 这个文件是当前扫描中 `grg` 侧唯一明确命中的业务点，适合做低风险试点。

- **建议 4：`grc` 侧优先在 HTTP 服务和初始化链路上统一上下文传递**
  - 适用文件：
    - `baidu/feed-gr/feeda-mv-grc/src/service/grc_http_service.cpp`
    - `baidu/feed-gr/feeda-mv-grc/src/processor/new_adjust/precise_score_init.cpp`
    - `baidu/feed-gr/feeda-mv-grc/src/processor/multi_factor/ltr_factor_gen_scene.cpp`
  - 建议做法：
    - 如果 `grc` 也需要兼容关闭开关或 fake_id 类信号，优先放在 `grc_http_service.cpp` 入参解析层；
    - 再由 `precise_score_init.cpp` 统一初始化上下文；
    - 最后传播到后续 processor。
  - 价值：
    - 减少多 processor 之间的重复判定；
    - 降低同一请求在不同模块里语义不一致的风险。

- **建议 5：针对容器使用密集的路径，优先做“上下文对象传递”而不是“多次字符串/Map 拷贝”**
  - 适用文件：
    - `baidu/feed-gr/feeda-mv-grg/src/service/grg_service.cpp`
    - `baidu/feed-gr/feeda-mv-grc/src/service/grc_http_service.cpp`
  - 建议做法：
    - 对 `uid / cuid / vip / fake_id / scene` 这类字段，集中封装为轻量上下文结构；
    - 尽量以引用或指针传递，避免在多层 pipeline 中反复构造 `std::unordered_map<std::string, std::string>`。
  - 价值：
    - 在容器高频使用的工程里，能减少额外拷贝和隐式分配；
    - 更容易统一字段命名和默认值策略。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：开关生效时机过晚**
  - 如果 `fake_id` 或个性化关闭逻辑在 `diversity_merge` 之后才生效，会导致策略已经执行、结果才被回收，出现“逻辑上关闭了，业务上却已分叉”的错位。

- **风险 2：基础请求字段缺失会导致兼容路径误判**
  - `uid / cuid / vip / fake_id` 任一字段缺失，都可能让关闭逻辑与普通路径混淆；
  - 排查时必须先确认入口上下文是否完整，而不是先看 merge 结果。

- **风险 3：策略与开关耦合过深会降低可验证性**
  - 如果 `diversity_merge.cpp` 同时负责策略分支和开关判断，后续很难证明“到底是策略没命中，还是开关把它关掉了”；
  - 这会显著增加线上排查成本。

- **风险 4：`grc` 侧多文件扩散会带来一致性成本**
  - `grc` 的命中面更广，涉及 `processor`、`adjuster`、`service` 多层；
  - 一旦上下文定义不统一，很容易出现不同模块对 `scene`、`fake_id`、关闭标志的解释不一致。

---

如果你愿意，我可以继续把这份内容整理成你学习笔记里可直接粘贴的 **“业务代码库适配分析”标准模板**，包括“结论版”和“详细版”两个版本。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
