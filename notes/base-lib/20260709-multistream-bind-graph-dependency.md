# 2026-07-09 周度代码理解：MultiStreamEngine bind_graph_dependency 桥接外层 GraphData

> 今日未获得可直接读取的 KU doc-id；本文以本地 feeda-mv-grg 代码与配置检索为主，历史设计背景需人工补充。

## 1. 架构全景图：ExecEngine 如何接入 Graph 顶点

<style>.arch-wrap{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:14px;padding:18px;margin:16px 0;color:#243b53}.arch-title{font-size:22px;font-weight:800;margin-bottom:12px;color:#102a43}.arch-layer{border:1px solid #bcccdc;border-radius:10px;background:#fff;margin:10px 0;padding:12px}.arch-layer h3{margin:0 0 10px 0;font-size:15px;color:#334e68}.arch-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.arch-box{border:1px solid #d9e2ec;border-radius:8px;background:#f8fafc;padding:10px;font-size:13px;line-height:1.45}.arch-box b{display:block;color:#102a43;margin-bottom:4px}.arch-box.hot{background:#e6f6ff;border-color:#62b0e8}.arch-flow{font-size:12px;color:#52606d;margin-top:8px}.arch-note{font-size:12px;color:#627d98;margin-top:10px}</style><div class="arch-wrap"><div class="arch-title">MultiStreamEngine 与 GraphData 的桥接关系</div><div class="arch-layer"><h3>Graph 顶点层</h3><div class="arch-grid"><div class="arch-box hot"><b>DiversityMergeFunction</b>Graph 顶点生命周期入口，持有 vertex()</div><div class="arch-box"><b>GraphData</b>SidInfo / Request / CommonInfo 等外部依赖</div><div class="arch-box"><b>Processor Config</b>plugin/conf/result_num 等初始化字段</div><div class="arch-box"><b>Phase</b>多样性合并前后的执行边界</div></div></div><div class="arch-layer"><h3>ExecEngine 插件层</h3><div class="arch-grid"><div class="arch-box hot"><b>MultiStreamEnginePlugin</b>bind_graph_dependency(vertex, engine)</div><div class="arch-box"><b>EngineMgr</b>按 conf 获取 engine pool</div><div class="arch-box"><b>ExecContext</b>custom_stream_context 注入业务上下文</div><div class="arch-box"><b>BaseStreamContext</b>承接 stream 运行期字段读写</div></div></div><div class="arch-layer"><h3>多队列选择层</h3><div class="arch-grid"><div class="arch-box"><b>loads_select</b>负载/规则型队列</div><div class="arch-box"><b>rule_select</b>规则约束队列</div><div class="arch-box"><b>function_select</b>函数队列</div><div class="arch-box"><b>effect/final</b>效果队列与最终选择</div></div><div class="arch-flow">GraphData 由顶点提供，ExecEngine 在初始化阶段绑定依赖，stream 运行时通过 ContextInfo schema 读取与回写。</div></div><div class="arch-note">证据：src/process/diversity_merge.cpp:70-90；conf/plugins/exec_engine/multi_stream_diversity.conf:1-31</div></div>

## 2. 核心调用链：从配置到运行期依赖绑定

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "Graph Scheduler" as Scheduler
participant "DiversityMergeFunction" as Func
participant "EngineMgr" as Mgr
participant "MultiStreamEnginePool" as Pool
participant "MultiStreamEngine" as Engine
participant "ExecContext" as Ctx
participant "MultiStreamEnginePlugin" as Plugin
Scheduler -> Func: init_impl(config)
Func -> Mgr: get_multi_stream_engine_pool(conf)
Mgr --> Func: pool
Func -> Pool: get()
Pool --> Func: _diversity_engine
Func -> Engine: get_context()
Engine --> Func: exec_context
Func -> Ctx: custom_stream_context(...)
Func -> Plugin: bind_graph_dependency(vertex(), _diversity_engine)
Plugin --> Func: true / false
Func -> Func: load multi_stream_select_soft_rule.conf
@enduml
```

关键点不是 engine 单独运行，而是 `DiversityMergeFunction` 在初始化期把 Graph 顶点的依赖域交给 `MultiStreamEnginePlugin`。`src/process/diversity_merge.cpp:73-76` 先从 `EngineMgr` 取 engine pool 并拿到 `_diversity_engine`，`src/process/diversity_merge.cpp:80-86` 再取得 `ExecContext` 并设置业务自定义 context，最后 `src/process/diversity_merge.cpp:88-90` 调 `bind_graph_dependency(this->vertex(), _diversity_engine)`，失败直接 `LOG(FATAL)`。

## 3. 配置结构信息图：ContextInfo 与 stream 队列

```infographic
infographic hierarchy-structure
data
  title MultiStreamEngine 配置骨架
  desc dependency_schema 与 mutable_context_schema 同为 ContextInfo，stream 按队列类型拆分执行器
  items
    - label multi_stream_engine
      desc conf_instance=TestExecContextConf；conf_file=default
      children
        - label dependency_schema
          desc ContextInfo，只读依赖输入
        - label mutable_context_schema
          desc ContextInfo，可变上下文输出
    - label loads_select
      desc loads_select_executor，支持回退
    - label rule_select
      desc select_executor，规则队列
    - label function_select
      desc 函数选择队列
    - label effect_select
      desc 效果队列，常承接打散/收益信号
    - label final_select
      desc 最终选择与兜底出口
theme
  palette #3D5A80 #2D6A4F #B8432F
```

`conf/plugins/exec_engine/multi_stream_diversity.conf:1-7` 明确了 `dependency_schema: ContextInfo` 与 `mutable_context_schema: ContextInfo`。这意味着外层 GraphData 的桥接不是靠每个 operator 自己访问 vertex，而是先归一到 engine context schema，再由不同 stream/pip executor 消费。

## 4. 运行期理解：为什么绑定点是排障优先级 P0

`bind_graph_dependency` 是 Graph 框架与 ExecEngine 框架的边界。如果这里漏绑或 schema 不匹配，后续表现可能不是初始化失败，而是某个 select executor 运行期拿不到依赖、默认值生效或结果队列为空。排查时应先看三个层面：

```infographic
infographic list-column-done-list
data
  title 调试 checklist
  desc MultiStreamEngine 依赖桥接问题优先检查顺序
  items
    - label 确认 engine pool 是否按 conf 命中
      desc diversity_merge.cpp:73-76，pool 或 engine 为空会提前失败
      done true
    - label 确认 ExecContext 已设置 custom_stream_context
      desc diversity_merge.cpp:80-86，业务上下文是 operator 读取字段的入口
      done true
    - label 确认 bind_graph_dependency 返回 true
      desc diversity_merge.cpp:88-90，失败会 FATAL，是最直接信号
      done true
    - label 对照 ContextInfo schema 与 stream 依赖字段
      desc multi_stream_diversity.conf:1-31，schema 与队列配置必须一致
      done false
    - label 检查 final/effect 队列是否为空
      desc 运行期空队列常由上游依赖缺失或规则过严触发
      done false
theme
  palette #3D5A80 #2D6A4F #B8432F
```

## 5. Pitfalls 卡片

<style>.card-frame{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:18px 0}.pit-card{background:#fffaf0;border:1px solid #d9c9a3;border-radius:12px;padding:18px;color:#2d3748}.pit-meta{font-size:12px;font-weight:700;color:#7c6853;text-transform:uppercase;letter-spacing:.04em}.pit-title{font-size:24px;font-weight:850;margin:6px 0 10px;color:#1a202c}.pit-grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}.pit-panel{background:#fff;border-top:4px solid #7c6853;border-radius:8px;padding:12px;font-size:14px;line-height:1.65}.pit-panel b{color:#1a202c}.pit-end{font-size:12px;color:#7c6853;margin-top:10px}</style><div class="card-frame"><div class="pit-card"><div class="pit-meta">Pitfalls / MultiStreamEngine</div><div class="pit-title">不要只看 stream 配置，先确认 Graph 依赖是否进入 engine</div><div class="pit-grid"><div class="pit-panel"><b>常见误判</b><br>看到 `multi_stream_diversity.conf` 中 stream/executor 配置完整，就认为选择逻辑一定能拿到 SidInfo、CommonInfo 等 GraphData。实际入口在 `bind_graph_dependency`，绑定失败或 ContextInfo 字段不一致时，下游 operator 的表现会像业务规则未命中。</div><div class="pit-panel"><b>排查落点</b><br>先从 `diversity_merge.cpp:73-90` 看 engine 获取、context 注入、依赖绑定，再回到 `multi_stream_diversity.conf:1-31` 对照 schema 和 stream 名称。</div></div><div class="pit-end">证据链：init_impl → EngineMgr → ExecContext → bind_graph_dependency → stream executor</div></div></div>

## 6. 证据来源

- `src/process/diversity_merge.cpp:70-90`：读取 plugin/conf、获取 engine pool、设置 custom stream context、绑定 Graph dependency。
- `src/process/diversity_merge.cpp:600-614`：effect 队列运行期读取 `_input_map["effect"]`，说明 stream 结果会被业务逻辑继续消费。
- `conf/plugins/exec_engine/multi_stream_diversity.conf:1-31`：定义 `ContextInfo` schema 与 loads/rule/function stream。
- `conf/plugins/graph/short_micro_video/vertex.conf`：GRG graph 配置入口，需结合 processor 节点确认 DiversityMerge 所在 phase。

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-20T19:09:45.939326
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

# 业务代码库适配分析报告

## 1. 分析摘要

- 这套技术本质上是 **Graph 顶点层与 ExecEngine 运行层的依赖桥接机制**，核心价值不是替代某个算法实现，而是把业务侧的 `GraphData / SidInfo / Request / CommonInfo` 等外部依赖，统一收敛到 `ContextInfo` 体系中，再由多 stream/executor 消费。
- 从扫描结果看，**feeda-mv-grg 仅发现 1 处相关使用**，更像试点或局部引入；**feeda-mv-grc 已发现 9 处相关使用**，说明其业务链路更适合做批量适配。结合两库中大量 `std::vector` / `std::string` / `std::unordered_map` 的使用规模，迁移收益主要体现在 **减少顶点/处理器之间的显式参数传递、降低依赖耦合、提升配置驱动能力**。

## 2. 代码库详情

### feeda-mv-grg

- 已发现目标库使用：**1 个文件**
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
- 迁移判断：
  - 当前更像是 **单点接入** 或 **局部实验性接入**。
  - 如果该文件里已经存在对图依赖、外部上下文、候选集打分的组合逻辑，它可以作为 **后续推广到同类 diversity rule** 的参考模板。
- 现有 std 等价物规模：
  - `std::vector`：1969 次，356 个文件
  - `std::string`：2443 次，425 个文件
  - `std::unordered_map`：734 次，205 个文件
- 结论：
  - 该库中容器使用非常广，但目标技术使用很少，说明 **迁移空间存在，但适配范围应优先限定在 diversity / rule / 多阶段选择类代码**，避免全库铺开。

### feeda-mv-grc

- 已发现目标库使用：**9 个文件**
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
- 迁移判断：
  - 这些文件集中在 **filter / adjuster / multi_factor / scene** 等多阶段业务链路，天然适合把依赖拆成 `dependency_schema` + `mutable_context_schema`。
  - 该库的 std 容器使用规模很大，说明很多逻辑仍依赖手工组织输入输出，若引入依赖桥接机制，可以显著减少“层层传参 + 手动拼接上下文”的成本。
- 现有 std 等价物规模：
  - `std::vector`：8442 次，1279 个文件
  - `std::string`：7170 次，1234 个文件
  - `std::unordered_map`：2834 次，639 个文件
- 结论：
  - 相比 grg，**grc 更适合做体系化迁移**，尤其是多阶段处理、规则选择、打分聚合、场景组装这类流程。

## 3. 💡 适用性评估与建议

- **建议 1：在 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 做首个适配样板**
  - 适用场景：该 rule 如果依赖 `SidInfo / CommonInfo / Request` 等图外信息，建议把这些输入收敛到 engine context，而不是在 rule 内部直接访问 vertex 或全局对象。
  - 具体做法：把 rule 所需的只读信息放入 `dependency_schema`，打分结果或中间态放入 `mutable_context_schema`。
  - 价值：这是最接近 `bind_graph_dependency` 语义的业务点，适合作为 grg 的迁移模板。

- **建议 2：在 `processor/filter/low_agile_goodrate_filter_operator.cc` 与 `processor/filter/user_explore_interest_ugc_filter_operator.cc` 中引入上下文分层**
  - 适用场景：过滤器通常依赖请求参数、用户特征、内容特征和运行期状态，容易出现“参数链太长”的问题。
  - 具体做法：将静态依赖在初始化时绑定到 `dependency_schema`，将过滤结果、命中原因、打分中间值写入 `mutable_context_schema`。
  - 价值：减少 operator 间的隐式耦合，也方便后续对 filter 链路做配置化编排。

- **建议 3：在 `processor/new_adjust/precise_score_init_first_refresh.cpp` 优先迁移“初始化 + 刷新”类状态**
  - 适用场景：这类文件通常有“初始化一次、运行多次”的特点，特别适合将外部依赖和可变状态拆开。
  - 具体做法：把初始化阶段依赖放入 engine context，刷新阶段只读/只写上下文，不直接在函数参数中层层传递。
  - 价值：更利于控制状态生命周期，减少刷新链路中的重复构造和拷贝。

- **建议 4：在 `operator/adjuster/sketchy/ltv_factor_cp_opt.cpp` 中缓存不可变依赖**
  - 适用场景：`ltv_factor` 这类因子计算通常有较强的上下文依赖，且对性能较敏感。
  - 具体做法：将稳定不变的 graph/业务依赖在 `bind_graph_dependency` 阶段完成绑定，运行期仅读取 context 内缓存。
  - 价值：降低每次打分时的查找和组装成本，减少热路径上的动态开销。

- **建议 5：在 `processor/multi_factor/ltr_factor_gen_scene.cpp` 推进多 stream 队列化**
  - 适用场景：scene 组装和多因子融合通常包含 `loads_select / rule_select / function_select / effect / final` 多阶段逻辑。
  - 具体做法：将不同阶段拆成独立 stream 或 executor，把场景输入统一映射到 `ContextInfo`，避免 scene 内部直接拼装所有依赖。
  - 价值：适合做业务流水线化改造，后续可更方便定位哪个阶段产生空队列或弱命中。

## 4. ⚠️ 引入风险与限制

- **风险 1：`ContextInfo` 与实际业务字段不一致会导致运行期失败**
  - `bind_graph_dependency` 是边界绑定点，schema 不匹配时，问题往往不是编译错误，而是运行期缺字段、默认值生效或最终队列为空。
  - 建议在配置和代码侧同步维护字段契约，尤其是 `dependency_schema` 与 `mutable_context_schema`。

- **风险 2：绑定失败通常是“强失败”，排障成本高**
  - 该机制在初始化阶段就可能 `FATAL`，虽然能尽早暴露问题，但一旦配置错位，排查路径会跨越 `init_impl -> EngineMgr -> ExecContext -> bind_graph_dependency` 多层。
  - 建议保留清晰的日志和告警，避免线上只能看到最终崩溃点。

- **风险 3：过度引入上下文桥接会增加一层间接访问**
  - 对高频打分、热路径 filter、极轻量 operator 来说，若每一步都转成 context 读写，可能带来额外封装和潜在拷贝成本。
  - 建议只在 **图依赖明显、生命周期明确、多阶段复用强** 的模块引入，不要全量替换。

- **风险 4：业务调试链路会变长**
  - 以前可能是“函数参数里直接看结果”，引入 engine context 后，问题定位要同时看配置、绑定结果、stream 队列和最终输出。
  - 建议补充一套“上下文打印 / schema 校验 / 队列空检查”工具，尤其是 final/effect 队列为空的场景。

如果你愿意，我可以继续把这份分析整理成更适合放进学习笔记的 **“结论版”**，或者补一版 **“迁移优先级表格”**。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
