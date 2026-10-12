# 2026-08-25 周二代码理解：GraphPool 复用与 reset 生命周期

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

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-26T19:03:09.891842
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- `GraphPool + reset` 这一套机制，适合**“构图/执行成本高、但对象结构可复用、且生命周期边界清晰”**的业务场景。它的核心价值不在于“减少一次 `run()` 的成本”，而在于**避免每次请求重复构造图对象**，并通过 `reset()` 保证复用后不携带脏状态。
- 结合本次扫描结果，两个业务代码库中**没有发现直接的 `GraphPool` 复用实现**，但 `feeda-mv-grc` 中存在更接近“图选择 + 请求执行”的代码形态，迁移潜力明显高于 `feeda-mv-grg`。  
  另一方面，两库都大量使用 `std::vector`、`std::string`、`std::unordered_map`，说明**内存分配和临时对象创建很多**，但这类热点更适合做容器优化、对象池分层、局部复用，不一定适合直接套用 GraphPool。

---

## 2. 代码库详情

### feeda-mv-grg（序列生成服务）

- 扫描命中目标库的文件较少，仅发现 1 个相关文件：
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 代码形态更偏向**规则计算、候选筛选、模型推理接口**，例如：
  - `model/model.h`
  - `model/paddle_model.h`
- 现有 `std` 等价物使用规模：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 结论：
  - 该库的热点更多是**算法逻辑和模型推理参数流转**，不是图池式对象复用。
  - 若要迁移 `GraphPool` 思路，优先考虑**推理图/规则链对象**是否存在重复构造场景，而不是对普通容器做池化。
- 可参考文件：
  - `model/model.h`：接口较轻，说明模型层更适合保持 stateless 或用独立实例管理
  - `model/paddle_model.h`：存在 `predict` / `predict_with_tensor_input` 这类执行入口，若未来有重对象复用需求，可作为生命周期边界参考

### feeda-mv-grc（召回汇聚服务）

- 扫描发现 10 个相关文件，覆盖过滤、因子生成、调整器和服务层：
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/multi_factor/subcate_future_factor_gen.cpp`
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `service/grc_http_service.cpp`
  - 以及其他同类处理文件
- 这里的业务形态更接近“**按场景选择执行链路**”，尤其是：
  - `service/grc_http_service.cpp` 中存在 `graph_name` 读取、依赖图获取、请求参数拼装等逻辑
  - 这类结构与 `GraphPool::try_get(graph_name)` 的适配度较高
- 现有 `std` 等价物使用规模更大：
  - `std::vector`：8520 次，1290 个文件
  - `std::string`：7267 次，1247 个文件
  - `std::unordered_map`：2860 次，646 个文件
- 结论：
  - 该库存在明显的**请求级上下文 + 图/场景选择**特征，适合优先评估 `GraphPool` 或类似对象池机制。
  - 如果 `grc_http_service.cpp`、`ltr_factor_gen_scene.cpp` 一类文件中存在重复构图/重复初始化，迁移收益会比较实在。

---

## 3. 💡 适用性评估与建议

- `feeda-mv-grc/service/grc_http_service.cpp`
  - 如果这里的请求处理每次都会根据 `graph_name` 构造或重建执行图，建议引入**按图名缓存的 `GraphPool`**。
  - 推荐做法是：
    - 请求入口先解析场景和图名
    - 再通过 `try_get(graph_name)` 获取图实例
    - 执行后统一 `run()` → `reset()`
  - 这是最接近你技术笔记中 `GraphPool` 生命周期的落地点。

- `feeda-mv-grc/processor/multi_factor/ltr_factor_gen_scene.cpp`
  - 如果这里存在“场景对象持有临时状态、请求结束后未彻底清理”的情况，建议把场景拆成：
    - **静态配置部分**：可长期复用
    - **请求上下文部分**：每次请求单独注入
  - 这样可以为后续引入池化做准备，避免把请求态塞进复用对象里。

- `feeda-mv-grc/processor/multi_factor/subcate_future_factor_gen.cpp`
  - 这里更像因子生成链路，通常会有大量中间 `vector/string/map` 临时对象。
  - 不建议直接做 GraphPool 化；更适合做：
    - `reserve()` 预分配
    - 局部对象复用
    - 线程内 scratch buffer
  - 也就是说，这类文件优先做**容器级优化**，不要强行套“图池 + reset”。

- `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 如果该规则实现是纯函数式、无状态的，建议保持现状，不必上对象池。
  - 如果内部反复构建辅助上下文或规则缓存，可抽象成一个可复用的 `RuleContext`，并明确提供 `reset()`。
  - 这比直接池化规则对象更稳妥。

- `feeda-mv-grg/model/model.h`、`feeda-mv-grg/model/paddle_model.h`
  - 模型推理接口更适合保持“**输入驱动、状态外置**”。
  - 若存在重型模型实例初始化成本，可考虑**模型实例池**，但不建议把 `PredictSample`、tensor 输入等请求态缓存进池对象。
  - 这类场景应优先考虑“模型对象复用”，而不是“图池复用”。

---

## 4. ⚠️ 引入风险与限制

- **reset 覆盖不全会导致跨请求串值**
  - `GraphPool` 最容易出问题的不是 `try_get()`，而是 `reset()` 漏清理字段。
  - 需要重点检查：临时上下文、输出缓存、插件状态、错误标志、重复字段。

- **异常路径和超时路径也必须归还一致状态**
  - 一旦 `run()` 中途报错、超时或提前返回，图对象仍然必须回到可复用状态。
  - 如果异常分支没走 `reset()`，池化就会积累脏对象。

- **请求级配置不能被缓存进复用对象**
  - 技术笔记里提到的 `DynamicTimeOutPlugin` 说明超时策略可能是请求相关的。
  - 这类配置不应作为对象内部长期状态保存，否则不同请求会互相污染。

- **线程安全和外部引用生命周期要额外验证**
  - 如果图实例内部还持有外部上下文指针、引用或借用对象，单纯清字段不够。
  - 需要确认对象复用期间不会发生并发重入，也不会悬挂旧请求的资源句柄。

---

## 总体判断

- `feeda-mv-grc`：**适合优先试点**，尤其是 `service/grc_http_service.cpp` 这一类图选择/执行入口。
- `feeda-mv-grg`：**更适合局部优化**，例如减少临时对象、优化模型/规则上下文复用，不建议直接大范围引入 GraphPool。
- 如果要做迁移验证，建议先选一个**图名明确、执行链稳定、reset 边界清晰**的链路做小流量灰度。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
