# 2026-08-16 周日代码理解：feeda-pcs 启动引导与请求分发边界

> 日期：2026-08-16  
> 主题来源：2026-06-01 daily-plan 文件未发现，按历史未覆盖主题 fallback 到 `feeda-pcs` 的启动引导 + 请求分发链路；KU/业务背景需人工补充。  
> 范围：只分析 `main.cpp`、`common_base/service/dispatch.cpp`、`common_base/processor/request_processor.cpp`，关注服务启动、请求解析、图依赖注入与基础特征透传，不展开完整业务策略。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937">
  <div style="font-size:20px;font-weight:800;margin-bottom:10px;">feeda-pcs 启动与分发全景</div>
  <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">1. main.cpp</div>
      <div style="font-size:13px;line-height:1.5;">完成全局初始化、RPC 服务注册、配置装载与进程启动。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">2. Dispatch</div>
      <div style="font-size:13px;line-height:1.5;">解析 `PcsRequest`，补齐 `cuid`、实验参数与 sid 相关上下文。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">3. RequestContext</div>
      <div style="font-size:13px;line-height:1.5;">把 request、common_info、refresh_info、feature 节点挂到 Graph 依赖上。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;">
      <div style="font-weight:700;color:#0f172a;">4. AOPProcessor</div>
      <div style="font-size:13px;line-height:1.5;">`RequestProcessor` 负责把 protobuf 字段写入 `intelligence_feature_info`。</div>
    </div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:#334155;">
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">RPC 入口</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">sid 缓存</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">GraphDependency</span>
    <span style="background:#e2e8f0;border-radius:999px;padding:4px 8px;">特征透传</span>
  </div>
</div>

## 1. 入口链路
`main.cpp` 的角色是把进程级依赖先装好，再把 brpc 服务拉起来。文件头部直接引入 `pcs_service`、`global_initializer`、`reusable_rpc_protocol`、`async_file_appender`、`server`、`gflags` 和 `exp_manager`，说明它不是纯 launcher，而是把日志、实验参数和 RPC 框架一起编排起来。证据见 `main.cpp:1-16`。

`Dispatch::parser_request()` 是请求进入业务图之前的第一道整理层。它先检查 `common_info` 是否存在，然后把 `cuid` 写回上下文，并读取 `need_sample_data`。微视频分支里还会先加载 `exp_manager`，这意味着实验参数比后续 processor 更早进入图上下文。证据见 `common_base/service/dispatch.cpp:36-46`。

## 2. 调用链时序图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "main.cpp" as Main
participant "PcsServiceImpl" as Svc
participant "Dispatch" as D
participant "GraphDependency" as G
participant "RequestProcessor" as RP
Client -> Main : start process
Main -> Svc : register service / init runtime
Client -> Svc : RPC request
Svc -> D : parser_request(request)
D -> D : check common_info
D -> D : set_cuid / need_sample_data
D -> G : bind request / graph data
G -> RP : AOPProcessor::run()
RP -> G : named_emit("IntelligenceFeatureInfo")
RP -> G : write floats from request
@enduml
```

## 3. 请求上下文结构信息图
```infographic
infographic hierarchy-tree-tech-style-capsule-item
data
  title RequestContext and graph dependencies
  desc Dispatch builds a small request context, then AOP processors read/write graph nodes.
  items
    - label request
      desc GraphDependency* input request
      children
        - label common_info
          desc Required for cuid / product / log_id / experiment parsing
        - label is_trigger_all
          desc GraphData used by downstream toggles
        - label refresh_info
          desc GraphData for refresh path
        - label intelligence_feature_info
          desc GraphData carrying float feature vector
    - label parser_request
      desc Validates PcsRequest and prepares runtime state
    - label sample_data flag
      desc Drives sampling behavior early in dispatch
theme
  palette #3b82f6 #64748b #0f766e
```

## 4. 关键实现细节
`Dispatch` 里最值得注意的是 sid 缓存和实验参数加载这两类前置动作。代码显式写了“sid cache实验，缓存sid解析结果”，并用 `SidCacheKey(product, log_id, cuid)` 去命中缓存；这意味着 `cuid`、`log_id` 和 `product` 共同决定了 sid 解析复用。证据见 `common_base/service/dispatch.cpp:64-68`。

`RequestProcessor` 则很薄，它只做一个明确的事情：把 `request_p->intelligence_feature_info()` 的 float 列表写入 `custom_context->intelligence_feature_info`。这条路径说明基础库层更像图节点装配器，而不是策略决策器。证据见 `common_base/processor/request_processor.cpp:21-26`、`common_base/processor/request_processor.cpp:35-61`。

## 5. Pitfalls
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0;">
  <div style="border:1px solid #cbd5e1;border-left:4px solid #f59e0b;border-radius:10px;background:#fff;padding:12px;">
    <div style="font-weight:800;margin-bottom:6px;">上下文缺失会直接短路</div>
    <div style="font-size:13px;line-height:1.6;">`common_info` 为空时 `Dispatch::parser_request()` 直接返回 -1，后续任何图节点装配都不会发生。</div>
  </div>
  <div style="border:1px solid #cbd5e1;border-left:4px solid #ef4444;border-radius:10px;background:#fff;padding:12px;">
    <div style="font-weight:800;margin-bottom:6px;">透传字段不是自动填充</div>
    <div style="font-size:13px;line-height:1.6;">`intelligence_feature_info` 只有在 `RequestProcessor` 显式 `named_emit` 后才可见，调试时不要默认它一定存在。</div>
  </div>
</div>

## 6. 调试 Checklist
```infographic
infographic list-column-done-list
data
  title Debug checklist
  items
    - label Confirm main.cpp initializes RPC, logging, and exp manager
      desc Check startup includes and service bootstrap path
      done true
    - label Verify Dispatch rejects requests without common_info
      desc Inspect parser_request early return branch
      done true
    - label Trace sid cache key composition
      desc product + log_id + cuid decide cache reuse
      done true
    - label Check RequestProcessor writes intelligence_feature_info
      desc Ensure float vector is emitted into graph data
      done true
    - label Keep all evidence paths relative
      desc Use src/... or conf/... paths only
      done true
theme
  palette #16a34a #2563eb #64748b
```

## 7. 证据来源
- `src/main.cpp:1-16`
- `common_base/service/dispatch.cpp:36-46`
- `common_base/service/dispatch.cpp:64-68`
- `common_base/processor/request_processor.cpp:21-26`
- `common_base/processor/request_processor.cpp:35-61`

## 8. 结论
`feeda-pcs` 的基础层边界很清晰：`main.cpp` 管启动，`Dispatch` 管请求上下文整理和 sid/实验前置，`RequestProcessor` 只负责把 protobuf 字段落到图里。基础库真正的职责不是做业务决策，而是把后续 processor 可以依赖的数据形态稳定地准备出来。

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-17T19:02:20.613587
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 这次技术点的核心，不是引入复杂业务策略，而是把 `feeda-pcs` 里的**请求入口整理、上下文装配、图依赖注入、基础特征透传**这套边界能力，迁移到业务代码库的入口层和公共处理层。
- 从扫描结果看，`feeda-mv-grg` 只有 **1 个文件**出现目标库使用，说明当前更像是**局部试点**；`feeda-mv-grc` 已有 **10 个文件**使用，说明该库对这类模式的接受度更高，具备进一步扩展的基础。结合两边大量 `std::vector` / `std::string` / `std::unordered_map` 的使用规模，迁移潜力主要集中在**公共上下文、参数解析、规则/算子装配**这些高复用边界，而不是单个业务算法内部。

## 2. 代码库详情

- `feeda-mv-grg`：
  - 已发现目标库使用：`strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 说明：
    - 目前只有一个落点，适合作为**参考样板**，但不适合直接判断全库兼容性。
    - 该库的标准容器使用规模较大：
      - `std::vector`：1969 次，356 个文件
      - `std::string`：2443 次，425 个文件
      - `std::unordered_map`：734 次，205 个文件
    - 这意味着如果要把“请求上下文 + 图依赖注入”模式推广开，适合优先落在**规则层、模型输入层、公共工具层**，避免直接触碰大量计算核心代码。
  - 可作为参考的现有代码：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
    - 可重点观察它是否已经有类似“上下文传递、规则输入整理、依赖读取”的封装方式。

- `feeda-mv-grc`：
  - 已发现目标库使用 10 个文件，覆盖范围明显更广：
    - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - 说明：
    - 该库已经有一定的迁移/接入经验，更适合继续扩展到**公共服务入口、HTTP 请求解析、operator/processor 之间的上下文传递**。
    - 标准容器使用规模也很大：
      - `std::vector`：8520 次，1290 个文件
      - `std::string`：7267 次，1247 个文件
      - `std::unordered_map`：2860 次，646 个文件
    - 这说明此库中“数据组装、映射、上下文传递”的场景非常普遍，迁移收益更可能体现在**减少重复参数传递、统一输入出口、降低跨层耦合**。
  - 可作为参考的现有代码：
    - `service/grc_http_service.cpp`
    - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`

## 3. 💡 适用性评估与建议

- **建议 1：优先在 `feeda-mv-grc/service/grc_http_service.cpp` 做入口层上下文封装**
  - 场景：
    - 该文件已经承担 HTTP 服务入口职责，天然适合承接“请求解析 -> 上下文构建 -> 后续算子分发”的边界逻辑。
  - 建议：
    - 参考 `feeda-pcs` 的 `Dispatch::parser_request()` 思路，在这里把请求公共字段、实验参数、标识信息统一封装成一个轻量上下文对象。
    - 适合把散落在 handler 里的参数读取逻辑收敛到一个入口函数中，减少后续 operator 反复解析 query / header 的成本。
  - 价值：
    - 降低重复解析开销
    - 让后续 processor 只关心业务输入，不关心协议细节

- **建议 2：在 `feeda-mv-grc/processor/multi_factor/session_ltr_dibar_factor_gen.cpp` 引入“特征透传”式结构**
  - 场景：
    - 该类文件通常负责特征生成与拼装，容易出现多层中间变量和重复拷贝。
  - 建议：
    - 参考 `RequestProcessor` 的做法，把 protobuf/请求对象中的基础特征统一写入一个上下文节点，再由后续阶段读取。
    - 如果当前是手工组装多个 `std::vector` / `std::string`，可考虑抽象成统一的 `feature_context` 或 `request_context`。
  - 价值：
    - 减少参数膨胀
    - 让特征流转路径更清晰
    - 便于后续埋点、调试和灰度开关控制

- **建议 3：在 `feeda-mv-grc/processor/filter/user_explore_interest_ugc_filter_operator.cc` 统一依赖读取方式**
  - 场景：
    - 过滤类 operator 往往会读取多个来源的用户兴趣、实验位、上下游状态。
  - 建议：
    - 借鉴 `GraphDependency` 的分层组织方式，把“请求数据 / 公共信息 / 运行时特征 / 过滤结果”拆开管理。
    - 对缺失关键字段的情况采用早返回，而不是在多个分支里容错处理，避免逻辑分散。
  - 价值：
    - 提升过滤链路可维护性
    - 降低隐式依赖导致的线上问题

- **建议 4：在 `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为试点复用现有上下文模型**
  - 场景：
    - `grg` 目前只有这一处目标库使用，适合做“小步验证”。
  - 建议：
    - 先观察该规则是否已经依赖统一输入上下文；若没有，可把其输入改造成与 `feeda-pcs` 类似的“请求上下文 + 规则参数”模式。
    - 如果规则内部已有输入聚合逻辑，可直接抽成公共层供同目录其他 rule 复用。
  - 价值：
    - 风险低
    - 能快速验证是否适合在 `grg` 扩大使用面

- **建议 5：把 `feeda-mv-grc/operator/adjuster/sketchy/ltv_factor_cp_opt.cpp` 和 `operator/adjuster/function_queue/youzhi_queue_adjust.cpp` 作为“上下游解耦”候选点**
  - 场景：
    - 调整器类文件通常包含策略判断、队列调整、特征依赖等混合逻辑。
  - 建议：
    - 参考 `Dispatch` 的前置整理职责，把依赖计算和参数整形前移，调整器只消费规范化后的输入。
    - 对共享字段（如用户标识、实验标识、候选集摘要）建议统一从上下文读取，不再层层传参。
  - 价值：
    - 有助于减少函数签名膨胀
    - 更利于后续做性能分析和链路切分

## 4. ⚠️ 引入风险与限制

- **风险 1：入口层改造会放大回归面**
  - `grc_http_service.cpp`、`session_ltr_dibar_factor_gen.cpp` 这类文件一旦改动，影响的是整条请求链路。
  - 建议先做**兼容式封装**，不要一次性替换所有老参数路径。

- **风险 2：上下文对象过度膨胀**
  - `feeda-pcs` 的模式是“只装配业务后续需要的数据”，不是把所有字段都塞进一个大对象。
  - 如果在 `grg/grc` 中把大量中间态都放进统一上下文，可能导致：
    - 内存占用增加
    - 生命周期复杂化
    - 调试时不知道字段由谁写入

- **风险 3：已有目标库使用点分散，风格可能不统一**
  - `grc` 已有 10 个使用文件，但分布在 `service`、`processor`、`operator` 等多个层次。
  - 这说明虽然有经验，但也可能存在多种接入方式，迁移时需要先统一规范，否则会出现“新旧两套上下文模型并存”。

- **风险 4：性能收益不一定自动成立**
  - 这类改造本质是**结构优化**，不是纯计算加速。
  - 如果当前瓶颈在模型推理或排序算法本身，单纯引入请求上下文/依赖注入，性能收益可能有限；但在参数重复解析、对象复制和跨层耦合较重的场景，收益会更明显。

如果你愿意，我可以继续把这份内容整理成你技术笔记里可直接粘贴的正式小节格式，比如补成：
- `### 业务代码库适配分析`
- `#### 1. 适配结论`
- `#### 2. 文件级建议`
- `#### 3. 风险清单`

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
