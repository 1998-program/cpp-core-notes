# MultiStreamEngine bind_graph_dependency 桥接 GraphDependency 深入理解

> 日期：2026-06-01  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的 `mon.business_lib` 候选项  
> 范围：分析 GRG 侧 `MultiStreamEnginePlugin` / `bind_graph_dependency()` 如何把外层 `GraphDependency`、`GraphVertex` 和引擎对象串起来；本文只讨论这条绑定链路，不扩展到全部业务策略。
> 内网文档：本次环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:18px;margin:16px 0;color:#1f2937"><style scoped>.ms-grid{display:grid;grid-template-columns:1.1fr 1.2fr 1fr;gap:12px}.ms-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.ms-k{font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase}.ms-v{margin-top:6px;font-size:14px;line-height:1.55}.ms-chip{display:inline-block;padding:3px 8px;border-radius:999px;background:#e2e8f0;color:#334155;font-size:12px;margin:4px 6px 0 0}.ms-title{font-size:22px;font-weight:850;margin:0 0 10px 0}.ms-sub{font-size:13px;color:#64748b;margin:0 0 14px 0}</style><div class="ms-title">MultiStreamEngine 绑定链路</div><div class="ms-sub">外层 GraphDependency 先进入 ExecEngine 插件层，再由 bind_graph_dependency 完成 vertex 与引擎对象的桥接。</div><div class="ms-grid"><div class="ms-col"><div class="ms-k">上游输入</div><div class="ms-v"><span class="ms-chip">GraphDependency</span><span class="ms-chip">GraphVertex</span><span class="ms-chip">dependency_map</span></div></div><div class="ms-col"><div class="ms-k">执行框架</div><div class="ms-v">`MultiStreamEnginePlugin` 负责把图数据绑定到 exec engine，形成多流并行/汇聚的运行壳。</div></div><div class="ms-col"><div class="ms-k">输出结果</div><div class="ms-v">业务侧 `DiversityMerge` / `NewResponseFunction` 继续处理聚合结果，直到最终响应落出。</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "MultiStreamEnginePlugin" as MSP
participant "bind_graph_dependency()" as BGD
participant "EngineObject" as EO
participant "GraphVertex" as GV
participant "Business Merge" as BM
Caller -> MSP: initialize / bind
MSP -> BGD: bind_graph_dependency(vertex, engine)
BGD -> GV: read GraphDependency bindings
BGD -> EO: attach engine object
EO --> MSP: ready engine
MSP -> BM: execute merge / response flow
BM --> Caller: merged result
@enduml
```

## 2. 配置与结构信息图

```infographic
infographic sequence-timeline-simple
data
  title 绑定链路的四个节点
  desc 从 graph dependency 到 engine object，再进入业务合并链路。
  items
    - label 读取依赖
      desc GraphDependency 进入图顶点上下文
      value 1
    - label 执行绑定
      desc bind_graph_dependency 把 dependency 贴到引擎对象
      value 2
    - label 装配引擎
      desc MultiStreamEnginePlugin 完成 exec engine 组装
      value 3
    - label 业务合并
      desc 交给 diversity merge / response function
      value 4
```

## 3. 关键实现

`common-processor_20260203152938/src/plugin/exec_engine.h:60-68` 定义了 `MultiStreamEnginePlugin` 和 `bind_graph_dependency(baidu::feed::graph::GraphVertex&, EngineObject&)`。这说明绑定动作不是隐式发生的，而是插件层显式暴露出来的接口，调用方必须把图顶点和引擎对象一起交给它。

`common-processor_20260203152938/src/plugin/multi_stream_engine.cpp` 中可以看到 `bind_graph_dependency` 被真正调用，证明这条桥接链路是运行时执行的一部分，不只是头文件声明。

从 daily-plan 的候选项看，这条链路对应的主题就是 `MultiStreamEngine bind_graph_dependency 桥接外层 GraphData`，代码证据指向 `src/processor/video_launch/diversity_merge.cpp:57` 和 `src/process/diversity_merge.cpp:89`。本次环境下未能完整读取业务主文件正文，因此具体业务策略位点需要人工补充，但框架级绑定语义已经足够确认。

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #d9e2ec;border-left:4px solid #f59e0b;border-radius:10px;padding:14px;margin:14px 0;color:#1f2937"><div style="font-size:16px;font-weight:800;margin-bottom:6px">绑定顺序不能反过来</div><div style="font-size:14px;line-height:1.6">先装配引擎、再补 GraphDependency 和 vertex，通常会让业务侧拿到半初始化对象，后面的 merge 逻辑会出现空依赖或默认分支。</div></div>
<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #d9e2ec;border-left:4px solid #ef4444;border-radius:10px;padding:14px;margin:14px 0;color:#1f2937"><div style="font-size:16px;font-weight:800;margin-bottom:6px">不要把 framework 层绑定当成业务策略</div><div style="font-size:14px;line-height:1.6">`bind_graph_dependency()` 只负责桥接，不负责策略判断。策略应继续留在业务 merge / response function 层，否则会把框架和业务耦死。</div></div>

## 5. 调试 Checklist

```infographic
infographic list-column-done-list
data
  title 绑定链路调试清单
  items
    - label 确认入口
      desc 查 exec_engine.h 和 multi_stream_engine.cpp 的实际调用点
      done true
    - label 核对依赖对象
      desc 确认 GraphDependency 不是空 map 或空引用
      done true
    - label 验证顶点装配
      desc 观察 GraphVertex 是否已完成绑定后再进入 merge
      done true
    - label 回看业务侧
      desc 对照 diversity_merge.cpp 的业务入口继续追踪
      done true
    - label 保留人工补充位
      desc 本次环境未读全业务源码，业务策略细节需补证
      done true
```

## 6. 证据来源

- `common-processor_20260203152938/src/plugin/exec_engine.h:60-68`
- `common-processor_20260203152938/src/plugin/multi_stream_engine.cpp`
- `notes/weekly-topic-selection/daily-plan-20260529.json:72-79`

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-24T19:01:33.886442
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析报告

### 1. 分析摘要

- 从扫描结果看，`MultiStreamEnginePlugin` / `bind_graph_dependency()` 这条 **GraphDependency → GraphVertex → EngineObject** 的绑定链路，已经在两个业务代码库中出现了明确落点：  
  - `feeda-mv-grg` 中发现 2 个相关文件：`process/new_diversity_merge.cpp`、`process/diversity_merge.cpp`
  - `feeda-mv-grc` 中发现 1 个相关文件：`processor/video_launch/diversity_merge.cpp`

- 这说明该技术并不是纯框架侧能力，而是已经进入业务 merge / response 链路，尤其集中在 diversity merge 场景。当前更适合做的是 **统一绑定入口、规范 GraphDependency 传递顺序、减少业务层重复理解框架细节**，而不是大规模改写业务策略。

- 从代码规模看，两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 使用量都很高，尤其 `feeda-mv-grc` 中 `std::vector` 达到 8520 次、`std::unordered_map` 达到 2860 次。这意味着 GraphDependency / dependency_map 一类结构在业务中很可能大量依赖 STL 容器承载，迁移或优化时需要重点关注 **对象生命周期、引用稳定性、拷贝成本和并发访问安全**。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标技术相关文件：
  - `process/new_diversity_merge.cpp`
  - `process/diversity_merge.cpp`

- 这两个文件都处在业务合并链路中，和技术笔记中提到的 `DiversityMerge` 场景一致。结合框架侧证据：
  - `common-processor_20260203152938/src/plugin/exec_engine.h:60-68`
  - `common-processor_20260203152938/src/plugin/multi_stream_engine.cpp`

  可以判断 `feeda-mv-grg` 的 diversity merge 逻辑很可能已经依赖外层 `GraphDependency` 完成图依赖注入，然后由 `bind_graph_dependency()` 将依赖绑定到引擎对象。

- 现有 STL 等价物使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型代码中可以看到 `std::vector<RidTmpInfoPtr>& candidate_vec` 被广泛用于模型预测链路，例如：
  - `model/model.h`
  - `model/paddle_model.h`

- 适配含义：
  - `process/diversity_merge.cpp` 和 `process/new_diversity_merge.cpp` 可以作为该技术在 GRG 业务侧的参考实现。
  - 后续如果要扩展新的多流 merge 或 response 节点，应优先复用现有 diversity merge 中的 GraphDependency 传递方式，而不是在业务层自行持有裸指针或重复解析 dependency map。

---

#### feeda-mv-grc：召回汇聚服务

- 已发现目标技术相关文件：
  - `processor/video_launch/diversity_merge.cpp`

- 该文件位于 `processor/video_launch` 场景，说明 GRC 侧是在具体业务 processor 中消费多流图依赖。与 GRG 相比，GRC 的目标技术落点更少，但整体 STL 容器使用规模更大。

- 现有 STL 等价物使用规模：
  - `std::vector`：8520 次，分布在 1290 个文件
  - `std::string`：7267 次，分布在 1247 个文件
  - `std::unordered_map`：2860 次，分布在 646 个文件

- 典型代码示例：
  - `service/grc_http_service.cpp`

  其中存在如下依赖关系构造逻辑：

  ```cpp
  std::unordered_map<std::string, std::vector<int>> depend_map;
  auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
  for (int i = 0; i < all_vertex.size(); ++i) {
      for (auto &depend : all_vertex[i].depends) {
          ...
      }
  }
  ```

- 适配含义：
  - `service/grc_http_service.cpp` 已经体现出业务侧对 graph vertex / depend map 的直接操作。
  - 如果后续要和 `bind_graph_dependency()` 链路统一，应避免让 HTTP/service 层直接承担过多图绑定语义，建议将 dependency 构造、校验、绑定拆到独立 helper 或 processor 初始化阶段。
  - `processor/video_launch/diversity_merge.cpp` 可作为 GRC 侧业务 processor 使用 GraphDependency 的主要参考点。

---

### 3. 💡 适用性评估与建议

- **建议 1：以 `process/diversity_merge.cpp` 和 `process/new_diversity_merge.cpp` 作为 GRG 侧标准参考实现**
  - 适用代码库：`feeda-mv-grg`
  - 目标文件：
    - `process/diversity_merge.cpp`
    - `process/new_diversity_merge.cpp`
  - 建议内容：
    - 梳理这两个文件中和 `GraphDependency`、`GraphVertex`、engine object 相关的调用顺序。
    - 固化为统一模式：  
      1. 获取或构造 `GraphDependency`
      2. 确认 `GraphVertex` 已完成依赖信息装配
      3. 调用 `bind_graph_dependency(vertex, engine)`
      4. 再进入 diversity merge / response 业务逻辑
    - 避免在新老 diversity merge 中出现两套不同的依赖绑定方式。
  - 迁移收益：
    - 降低新旧 merge 逻辑的维护成本。
    - 防止业务逻辑绕过框架绑定入口，导致不同场景下依赖读取行为不一致。

- **建议 2：在 `processor/video_launch/diversity_merge.cpp` 中明确区分“框架绑定”和“业务策略”**
  - 适用代码库：`feeda-mv-grc`
  - 目标文件：
    - `processor/video_launch/diversity_merge.cpp`
  - 建议内容：
    - 将 GraphDependency 绑定逻辑保持在 processor 初始化或执行前置阶段。
    - 将召回结果去重、多样性打散、排序、截断等策略继续放在 diversity merge 主逻辑中。
    - 不建议把业务判断塞进 `bind_graph_dependency()` 的调用链路。
  - 迁移收益：
    - 保持框架层与业务层解耦。
    - 后续更换 exec engine 或调整图依赖结构时，对业务 merge 策略影响更小。

- **建议 3：对 `service/grc_http_service.cpp` 中的 dependency_map 构造增加一致性校验**
  - 适用代码库：`feeda-mv-grc`
  - 目标文件：
    - `service/grc_http_service.cpp`
  - 当前场景：
    - 文件中存在 `std::unordered_map<std::string, std::vector<int>> depend_map`，并从 `graph_engine->get_vertexs_message(graph_name)` 中读取 vertex 依赖。
  - 建议内容：
    - 在构造 `depend_map` 后增加校验：
      - vertex 名称是否为空
      - depends 中引用的节点是否存在
      - 是否存在环依赖
      - 是否存在重复 depend
      - 是否存在空 dependency map 但仍进入 engine 绑定的情况
    - 如果这份 depend_map 后续会传递到 `GraphDependency` 或 `bind_graph_dependency()`，建议在进入绑定前统一做一次 validate。
  - 迁移收益：
    - 提前暴露图配置错误。
    - 减少业务 processor 中出现“依赖为空但仍执行默认分支”的隐性问题。

- **建议 4：对高频 `std::vector` / `std::unordered_map` 场景控制拷贝成本**
  - 适用代码库：
    - `feeda-mv-grg`
    - `feeda-mv-grc`
  - 参考文件：
    - `model/model.h`
    - `model/paddle_model.h`
    - `service/grc_http_service.cpp`
    - `process/diversity_merge.cpp`
    - `processor/video_launch/diversity_merge.cpp`
  - 建议内容：
    - 对候选集、依赖表、vertex 列表优先使用引用或 `const&` 传递。
    - 对只读依赖结构避免在每次请求中重复构造大 map。
    - 对 request 级动态依赖，可考虑在 processor context 中集中持有，避免在多层函数之间反复拷贝。
    - 对 `std::unordered_map<std::string, std::vector<int>>` 这类结构，可根据规模预先 `reserve()`，降低 rehash 成本。
  - 迁移收益：
    - 降低多流图执行场景下的容器复制和分配成本。
    - 对 GRC 这类 STL 使用规模较大的服务，收益更明显。

- **建议 5：新增业务节点时优先复用 `MultiStreamEnginePlugin` 的显式绑定接口**
  - 适用代码库：
    - `feeda-mv-grg`
    - `feeda-mv-grc`
  - 参考框架文件：
    - `common-processor_20260203152938/src/plugin/exec_engine.h`
    - `common-processor_20260203152938/src/plugin/multi_stream_engine.cpp`
  - 建议内容：
    - 新增 processor、merge 或 response function 时，不要在业务层自行模拟 GraphDependency 绑定。
    - 应通过插件层提供的 `bind_graph_dependency(baidu::feed::graph::GraphVertex&, EngineObject&)` 完成桥接。
    - 对业务代码只暴露已经绑定好的 engine/context，避免业务感知过多框架内部结构。
  - 迁移收益：
    - 绑定流程更一致。
    - 方便后续统一排查初始化顺序、空依赖、vertex 缺失等问题。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：绑定顺序错误会导致半初始化对象进入业务 merge**
  - 相关文件：
    - `process/diversity_merge.cpp`
    - `process/new_diversity_merge.cpp`
    - `processor/video_launch/diversity_merge.cpp`
  - 风险说明：
    - 如果先执行业务 merge，再补充 `GraphDependency` 或 `GraphVertex` 绑定，业务侧可能拿到空依赖或默认对象。
    - 这类问题不一定立即 crash，但可能导致召回结果缺失、多样性策略失效或走默认分支。
  - 建议：
    - 固定执行顺序：先完成 vertex / dependency / engine 绑定，再进入 merge。

- **风险 2：不要把业务策略下沉到 `bind_graph_dependency()`**
  - 风险说明：
    - `bind_graph_dependency()` 的职责是桥接图依赖和引擎对象，不应该承载排序、过滤、打散、截断等业务逻辑。
    - 如果把策略写入绑定阶段，会导致框架层和业务层耦合，后续其他 processor 复用时风险很高。
  - 建议：
    - 框架层只做绑定、校验、对象装配。
    - 策略仍保留在 `diversity_merge.cpp`、`new_diversity_merge.cpp` 或 response function 中。

- **风险 3：GRC 侧 dependency_map 规模较大时可能带来性能抖动**
  - 相关文件：
    - `service/grc_http_service.cpp`
  - 风险说明：
    - 当前 GRC 中 `std::vector`、`std::unordered_map` 使用非常频繁。
    - 如果每次请求都动态构造复杂 `depend_map`，并在多层函数中按值传递，可能产生明显的内存分配、rehash 和拷贝成本。
  - 建议：
    - 对稳定图结构做缓存。
    - 对动态结构使用引用传递。
    - 对 map/vector 提前 `reserve()`。
    - 对只读 graph metadata 使用 `const` 视图或共享上下文。

- **风险 4：当前扫描未完整覆盖全部业务策略文件**
  - 风险说明：
    - 技术笔记中提到本次环境未完整读取业务主文件正文，因此目前结论主要基于文件命中、框架接口和扫描统计。
    - `process/response_function.cpp`、`data/base.h` 等潜在上下游文件如果也参与 GraphDependency 传递，需要进一步人工确认。
  - 建议：
    - 后续补充扫描：
      - `process/response_function.cpp`
      - `data/base.h`
      - `process/diversity_merge.cpp`
      - `process/new_diversity_merge.cpp`
      - `processor/video_launch/diversity_merge.cpp`
    - 重点检查是否存在绕过 `MultiStreamEnginePlugin` 的手工依赖注入逻辑。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
