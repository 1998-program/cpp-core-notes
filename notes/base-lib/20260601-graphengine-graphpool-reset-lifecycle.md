# GraphEngine GraphPool 对象池复用与 reset 生命周期

> 日期：2026-06-01  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 `mon.base_lib` 计划项  
> 范围：GRC/GRG 入口 `GraphEngine::get()`、`GraphEngine::try_get()`、`GraphPool::run()`、`GraphPool::reset()` 的对象池生命周期；本文只讨论对象池复用、重置和返回，不泛化到整个 graph engine。
> 内网文档：本次环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:18px;margin:16px 0;color:#1f2937"><style scoped>.graphpool-grid{display:grid;grid-template-columns:1.2fr 1.4fr 1fr;gap:12px}.graphpool-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.graphpool-k{font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0}.graphpool-v{margin-top:6px;font-size:14px;line-height:1.55}.graphpool-chip{display:inline-block;padding:3px 8px;border-radius:999px;background:#e2e8f0;color:#334155;font-size:12px;margin:4px 6px 0 0}.graphpool-title{font-size:22px;font-weight:850;margin:0 0 10px 0}.graphpool-sub{font-size:13px;color:#64748b;margin:0 0 14px 0}</style><div class="graphpool-title">GraphEngine 对象池生命周期</div><div class="graphpool-sub">GraphPool 负责图对象的获取、运行、复位和回收，核心是让服务入口复用已构建的 Graph 实例。</div><div class="graphpool-grid"><div class="graphpool-col"><div class="graphpool-k">入口层</div><div class="graphpool-v"><span class="graphpool-chip">GraphEngine::get()</span><span class="graphpool-chip">GraphEngine::try_get()</span><span class="graphpool-chip">GraphUnit::get()</span><span class="graphpool-chip">GraphUnit::try_get()</span></div></div><div class="graphpool-col"><div class="graphpool-k">执行层</div><div class="graphpool-v">GraphPool 将对象交给运行时执行链，调用方完成一次请求的 graph run、trace flush、尾部清理。</div></div><div class="graphpool-col"><div class="graphpool-k">回收层</div><div class="graphpool-v">对象被 reset 后回到池中，供下一次请求复用；如果配置了 life cycle / reconstruct，则影响返回池前的重建行为。</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "GraphEngine" as GE
participant "GraphPool" as GP
participant "Graph" as G
participant "Closure" as C
Client -> GE: get(graph_name) / try_get(product_id)
GE -> GP: obtain pooled object
GP --> GE: PooledObject
GE -> G: run(request context)
G -> C: get() / continue processing
G -> G: trace flush / cleanup
G -> GP: reset() / return to pool
GP --> GE: reusable object
@enduml
```

## 2. 配置与结构信息图

```infographic
infographic sequence-timeline-simple
data
  title GraphPool 生命周期关键节点
  desc 对象池复用依赖获取、执行、重置和回收四个步骤。
  items
    - label 获取对象
      desc GraphEngine::get / try_get 选择可复用对象
      value 1
    - label 进入执行
      desc Graph::run 进入图执行链
      value 2
    - label 尾部收尾
      desc trace flush 与上下文清理
      value 3
    - label 归还对象
      desc GraphPool::reset 后回到池中
      value 4
```

## 3. 关键实现

GraphEngine 的公开入口在 `framework/src/graph_engine.cpp:328` 和 `framework/src/graph_engine.cpp:343`，分别对应 `GraphEngine::get()` 与 `GraphEngine::try_get()`。这两个入口返回 `GraphPool::PooledObject`，说明调用方拿到的不是裸指针，而是带生命周期管理语义的池化对象。

对象池的核心价值不是单纯缓存，而是把“构建成本高”的 Graph 实例生命周期缩短为“请求级别”。只要调用路径保证在尾部完成 reset，池中的对象就能反复服务后续请求。

`framework/src/dynamic_timeout_plugin.cpp:12` 的 `DynamicTimeOutPlugin::initialize()` 是另一条常见入口。它本身不管理对象池，但它和 GraphPool 一样都体现了 framework 层“先初始化、再按场景运行”的风格，后续请求在同一套框架上下文中完成调度。

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #d9e2ec;border-left:4px solid #f59e0b;border-radius:10px;padding:14px;margin:14px 0;color:#1f2937"><div style="font-size:16px;font-weight:800;margin-bottom:6px">reset 不是装饰动作</div><div style="font-size:14px;line-height:1.6">如果调用链在异常分支、早返回或中间插件失败时没有走到 reset，池化对象会携带上一次请求的残留状态，下一次复用会出现难以定位的串线问题。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #d9e2ec;border-left:4px solid #ef4444;border-radius:10px;padding:14px;margin:14px 0;color:#1f2937"><div style="font-size:16px;font-weight:800;margin-bottom:6px">try_get 和 get 的语义要分开看</div><div style="font-size:14px;line-height:1.6">`get()` 更接近强获取路径，`try_get()` 则常用于带产品 ID 或 graph_name 的选择分支。把这两者混在一起看，容易误判对象池耗尽还是路由失败。</div></div>

## 5. 调试 Checklist

```infographic
infographic list-column-done-list
data
  title GraphPool 调试清单
  items
    - label 确认入口
      desc 定位 get / try_get 的实际调用点
      done true
    - label 检查 reset
      desc 逐层确认异常和 early return 是否仍会回收对象
      done true
    - label 核对复用状态
      desc 看 pooled object 是否残留 request 级字段
      done true
    - label 观察配置
      desc 检查 pool reconstruct / life cycle 相关参数
      done true
    - label 对照 trace
      desc 结合尾部 trace flush 判断对象是否完整退出
      done true
```

## 6. 证据来源

- `framework/src/graph_engine.cpp:328-343`
- `framework/src/dynamic_timeout_plugin.cpp:12-14`
- `notes/weekly-topic-selection/daily-plan-20260529.json:10-18`

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-23T19:01:43.774171
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析：GraphPool 对象池复用与 reset 生命周期

## 1. 分析摘要

- 这次扫描显示，`GraphEngine::get()` / `GraphEngine::try_get()` 相关对象池能力已经在两个业务代码库中出现，但落点都比较集中：  
  - `feeda-mv-grg`：`service/grg_service.cpp`、`service/grg_http_service.cpp`、`init/global.h`
  - `feeda-mv-grc`：`service/grc_http_service.cpp`、`initializer/global.h`、`service/grc_service.cpp`
- 说明这套能力更像是**服务入口层的生命周期管理能力**，而不是全局铺开的基础设施。对这类代码库来说，迁移价值主要体现在：**复用高成本 Graph 实例、减少请求级构建开销、并通过 `reset()` 保证状态隔离**。

- 从现有 `std` 使用规模看，两个代码库都属于对象和容器使用非常密集的业务系统：  
  - `feeda-mv-grg`：`std::vector` 1969 次、`std::string` 2443 次、`std::unordered_map` 734 次
  - `feeda-mv-grc`：`std::vector` 8442 次、`std::string` 7170 次、`std::unordered_map` 2834 次
- 这意味着如果 Graph 对象内部承载了较重的上下文、缓存、trace、依赖图等状态，那么引入 GraphPool 的收益通常是**明确的**，但前提是：**调用链必须严格保证尾部 `reset()`**，否则复用会放大状态污染风险。

---

## 2. 代码库详情

### `feeda-mv-grg`

- 已发现目标库使用点集中在 3 个文件：
  - `service/grg_service.cpp`
  - `service/grg_http_service.cpp`
  - `init/global.h`
- 这组文件的结构很适合做 GraphPool 适配的“标准入口”：
  - `init/global.h` 适合放全局初始化、池配置、生命周期参数；
  - `service/grg_service.cpp` 适合承接请求级 Graph 获取、执行和返回；
  - `service/grg_http_service.cpp` 适合做 HTTP 层路由、异常处理和收尾回收。
- 现有代码已经有目标技术使用痕迹，可直接作为迁移参考。建议优先把这些文件整理成**“获取-执行-归还”闭环模板**，避免不同入口各自实现一套生命周期逻辑。

### `feeda-mv-grc`

- 已发现目标库使用点同样集中在 3 个文件：
  - `service/grc_http_service.cpp`
  - `initializer/global.h`
  - `service/grc_service.cpp`
- 相比 `grg`，`grc` 的 `std` 使用量明显更高，说明请求处理链更复杂、对象更多、状态更重。对于 GraphPool 这类对象池能力来说，`grc` 更像是**高收益候选库**。
- 现有目标库使用分布说明，`grc` 的入口和初始化层已经具备接入对象池的土壤：
  - `initializer/global.h` 适合统一配置 pool size、reconstruct、life cycle 等参数；
  - `service/grc_service.cpp` 和 `service/grc_http_service.cpp` 适合承接 `GraphEngine::get()` / `try_get()` 的实际调用；
  - 若已有 pool 调用，建议把它们作为“参考实现”，统一成同一种 reset 规范。

---

## 3. 💡 适用性评估与建议

- **建议 1：在服务入口统一包装 Graph 获取与归还，保证 `reset()` 不漏执行**
  - 适用文件：
    - `feeda-mv-grg/service/grg_service.cpp`
    - `feeda-mv-grg/service/grg_http_service.cpp`
    - `feeda-mv-grc/service/grc_service.cpp`
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 建议做法：
    - 将 `GraphEngine::get()` / `GraphEngine::try_get()` 的返回对象封装到 RAII 守卫中；
    - 在析构或统一收尾路径里触发 `GraphPool::reset()`；
    - 避免在多个 return 分支里手写归还逻辑。
  - 价值：
    - 这是对象池适配最关键的一步，能直接避免“上个请求状态泄漏到下个请求”的串线问题。

- **建议 2：`try_get()` 用于路由选择，`get()` 用于强依赖路径，别混用**
  - 适用文件：
    - `feeda-mv-grg/service/grg_http_service.cpp`
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 建议做法：
    - 对于按 `graph_name`、`product_id`、请求参数选择图实例的逻辑，优先使用 `try_get()`；
    - 对于确定存在、必须执行的主链路，再使用 `get()`；
    - 在 HTTP 层把“对象池耗尽”和“路由失败”分开记录日志。
  - 价值：
    - 可以避免把容量不足误判成业务配置错误，也能减少排障成本。

- **建议 3：在 `global.h` / `initializer/global.h` 里集中管理池配置，不要散落到请求代码中**
  - 适用文件：
    - `feeda-mv-grg/init/global.h`
    - `feeda-mv-grc/initializer/global.h`
  - 建议做法：
    - 统一放置 pool size、reconstruct、life cycle 相关配置；
    - 初始化阶段完成对象池创建，不要在每次请求里动态配置；
    - 若存在多环境配置，建议在启动时完成读取和校验。
  - 价值：
    - 能让池行为稳定可控，也更方便压测和回滚。

- **建议 4：在有 trace flush、异常处理、early return 的 HTTP 分支里，优先保证“先收尾，再退出”**
  - 适用文件：
    - `feeda-mv-grg/service/grg_http_service.cpp`
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 建议做法：
    - 任何异常分支、鉴权失败分支、参数校验失败分支，都要确认是否已经完成对象归还；
    - 如果对象后续还要做 trace flush，建议先把必要数据复制到局部变量，再归还对象。
  - 价值：
    - 这类文件通常是最容易漏 `reset()` 的地方，也是池化对象污染最常见的入口。

- **建议 5：优先在 `grc` 做收益验证，再回推到 `grg`**
  - 适用文件：
    - `feeda-mv-grc/service/grc_service.cpp`
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 建议做法：
    - 先在 `grc` 的高频请求链路上验证对象池带来的延迟改善、构建次数下降、异常率变化；
    - 若效果稳定，再把同样的生命周期模板迁移到 `grg`。
  - 价值：
    - `grc` 的容器和字符串使用量更高，更适合做对象池收益验证样本。

---

## 4. ⚠️ 引入风险与限制

- **风险 1：`reset()` 漏调用会导致跨请求状态污染**
  - 这是 GraphPool 最核心的风险。
  - 一旦异常、提前返回、插件失败分支没有走到 `reset()`，下次复用就可能继承上一次请求的残留状态。

- **风险 2：对象不能跨越请求边界或异步边界长期持有**
  - 如果 `GraphPool::PooledObject` 被传到异步回调、跨线程任务或长生命周期缓存里，生命周期就会失控。
  - 这类用法会破坏“请求结束即归还”的前提。

- **风险 3：`try_get()` 语义容易被误解为“返回失败就是业务错误”**
  - 实际上它也可能只是池资源不足或路径选择失败。
  - 需要在日志和指标上区分“取不到对象”和“路由不命中”。

- **风险 4：生命周期 / reconstruct 配置不合理会抵消收益**
  - 如果配置过于激进，可能导致对象频繁重建，失去池化意义；
  - 如果配置过于宽松，又可能让脏状态在池中停留过久。
  - 迁移后必须结合压测和 trace 观察结果做校准。

---

如果你愿意，我还可以继续帮你补一版 **“适配落地改造清单”**，直接按 `grg/grc` 两个仓库拆成可执行的修改项。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
