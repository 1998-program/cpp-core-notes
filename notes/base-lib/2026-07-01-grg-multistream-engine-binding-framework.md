# GRG MultiStreamEngine 绑定框架：从插件加载到 Graph 依赖注入
<!-- topic-nav:start -->
> 主题：`base/multistream-binding` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260529-exec-engine-multistreamengine-graphdependency-deep-dive.md)
<!-- topic-nav:end -->


> 日期：2026-07-01｜类型：基础库笔记｜主题来源：本机未发现可用 `daily-plan` 文件，按周三 fallback 选择 GRG NewDiversityMerge / MultiStreamEngine 框架主题。已按任务要求设置 `BAIDU_CC_USERNAME=hujiyang`；当前未提供 KU URL/doc-id，内网策略背景需人工补充，本文以本地代码库检索为准。

## 1. 架构全景图：三层把配置、Graph 与引擎池接起来
<style>.arch-ms{font-family:Inter,Arial,sans-serif;border:1px solid #d8e0ea;border-radius:16px;background:#f8fafc;padding:18px;margin:16px 0;color:#1f2937}.arch-ms h3{margin:0 0 12px;font-size:20px}.arch-layer{border-radius:12px;padding:12px;margin:10px 0;border:1px solid #cbd5e1}.arch-layer.cfg{background:#eff6ff}.arch-layer.plugin{background:#f0fdf4}.arch-layer.graph{background:#fff7ed}.arch-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.arch-box{background:white;border:1px solid #cbd5e1;border-radius:10px;padding:10px;box-shadow:0 1px 3px rgba(15,23,42,.08)}.arch-box b{display:block;color:#0f172a;margin-bottom:4px}.arch-arrow{text-align:center;font-weight:700;color:#475569;margin:6px 0}.arch-note{font-size:13px;color:#475569;margin-top:8px}</style><div class="arch-ms"><h3>MultiStreamEngine 基础库绑定全景</h3><div class="arch-layer cfg"><b>配置层</b><div class="arch-grid"><div class="arch-box"><b>exec_engine.conf</b>枚举 engine：`multi_stream_diversity.conf`、`multi_stream_diversity_new.conf`、`news_diversity.conf`</div><div class="arch-box"><b>engine conf</b>声明 stream / pip / executor / rule / pk</div><div class="arch-box"><b>vertex.conf</b>在 Graph vertex option 中指定 plugin + conf</div></div></div><div class="arch-arrow">↓ load_multi_stream_engine + get pool</div><div class="arch-layer plugin"><b>插件层</b><div class="arch-grid"><div class="arch-box"><b>MultiStreamEnginePlugin::initialize</b>读取全局 engine 列表并加载 engine pool</div><div class="arch-box"><b>EngineMgr</b>维护 MultiStreamEngine 对象池</div><div class="arch-box"><b>bind_graph_dependency</b>把 engine 依赖名映射为 GraphDependency*</div></div></div><div class="arch-arrow">↓ pooled engine + custom context</div><div class="arch-layer graph"><b>Graph 执行层</b><div class="arch-grid"><div class="arch-box"><b>DiversityMergeFunction</b>传统合并函数，直接持有 ScatterContext</div><div class="arch-box"><b>NewDiversityMergeFunction</b>按 context_name 创建 BaseDiversityScatterContext</div><div class="arch-box"><b>MultiStreamEngine::run</b>消费 SelectStreamContainerMap，输出 SelectStreamContainer</div></div></div><div class="arch-note">关键边界：插件只负责加载/绑定依赖；业务函数负责填充 exec_context、input_map 与结果回写。</div></div>

## 2. 入口链路：服务启动不直接建 engine，而是由 GlobalInitializer / ApplicationContext 间接完成

`main.cpp:39-87` 是服务入口：解析 gflags、加载日志、注册 `ReusableRPCProtocol`，随后执行 `GlobalInitializer::instance().init()` 与 `ExpManager::init()`，最后把 `GenericGRGService` 和 HTTP graph_view 服务注册到 brpc server。这里没有直接 new `MultiStreamEngine`；引擎初始化隐藏在组件初始化阶段，由 `MultiStreamEnginePlugin` 被注册后读取配置。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "brpc Server" as server
participant "main.cpp" as main
participant "GlobalInitializer" as global
participant "ApplicationContext" as app
participant "MultiStreamEnginePlugin" as plugin
participant "EngineMgr" as mgr
participant "Graph Vertex" as vertex
main -> global: init()
global -> app: initialize registered components
app -> plugin: initialize()
plugin -> mgr: load_multi_stream_engine(configure)
plugin -> mgr: get_multi_stream_engine_pool(name)->get()
vertex -> plugin: bind_graph_dependency(vertex, engine)
plugin -> vertex: named_dependency(dep_name)
plugin -> mgr: engine.bind_graph_dependency(map)
@enduml
```

## 3. 配置结构信息图：一个总表，三份 engine conf

```infographic
infographic hierarchy-structure
data
  title MultiStreamEngine 配置装配
  desc exec_engine.conf 只做枚举；每份 engine conf 决定流、执行器和算子依赖
  items
    - label exec_engine.conf
      desc @engine: multi_stream_diversity.conf / multi_stream_diversity_new.conf / news_diversity.conf
      children
        - label multi_stream_diversity.conf
          desc short_micro_video 传统 DiversityMergeFunction 使用
        - label multi_stream_diversity_new.conf
          desc short_micro_video 新 DNN/Paddle 结果分支使用
        - label news_diversity.conf
          desc news_updates_dibar 的 NewDiversityMergeFunction 使用
    - label common stream skeleton
      desc loads_select / rule_select / function_select / final_select / effect_pk / merge_pk
      children
        - label select executor
          desc RuleSelectOperator + hard/soft rule conf
        - label pk executor
          desc DiversityIndexPkOperator 或 DiversityScorePkOperator
```

## 4. 核心机制拆解

### 4.1 插件初始化：先加载，再校验每个 engine pool 可取对象

`multi_stream_engine.cpp:28-48` 中，`MultiStreamEnginePlugin::initialize()` 读取 `FLAGS_engine_conf_path` 与 `FLAGS_multi_stream_engine_conf_file`，再调用：

- `EngineMgr::instance().set_use_state_multi_stream_operator(true)`
- `EngineMgr::instance().load_multi_stream_engine(configure)`
- 遍历 `configure["multi_stream_engine"]["engine"]`，逐个 `get_multi_stream_engine_pool(engine_name)->get()` 校验对象可用

这个阶段是“基础库可用性检查”：如果 `exec_engine.conf` 中多声明了一份不存在或加载失败的 engine conf，会在插件初始化阶段失败，而不是等到业务 vertex process 才失败。

### 4.2 Graph 依赖绑定：engine 依赖名必须能在 vertex 上找到

`multi_stream_engine.cpp:78-91` 是关键绑定点：

1. `multi_stream_engine->get_context()->set_run_in_graph_engine()` 标记运行在 graph engine 内。
2. 从 `multi_stream_engine->get_dependencies_name()` 取依赖名集合。
3. 对每个依赖名调用 `vertex.named_dependency(depenpency)`。
4. 构造 `unordered_map<string, GraphDependency*>` 后交给 `multi_stream_engine->bind_graph_dependency()`。

因此，engine conf 中规则/算子声明的 depend 名，与 `vertex.conf` 的 `[.@depend] data/name` 之间必须对齐；否则后续 operator 读依赖时会出现空依赖或条件不生效。

### 4.3 业务函数 init：拿 pooled engine，再挂 custom_context

`new_diversity_merge.cpp:43-76` 的初始化过程说明了新版封装方式：

- 从 vertex option 读取 `plugin`、`conf`、`context_name`。
- 通过 `EngineMgr::instance().get_multi_stream_engine_pool(conf)->get()` 拿引擎实例。
- 通过 `ApplicationContext::instance().get_or_create<BaseDiversityScatterContext>(context_name)` 创建/获取业务上下文。
- `exec_context->custom_context.ref(static_cast<BaseStreamContext&>(*_diversity_context))` 注入 engine 可访问的 context。
- 动态转型为 `MultiStreamEnginePlugin` 后执行 `bind_graph_dependency()`。

这使得 engine 本体保持通用，场景差异通过 `context_name`、condition strategy 与 queue input map 注入。

## 5. 框架边界：谁负责什么？

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title 基础库 vs 业务函数职责分界
  items
    - label MultiStreamEngine / Plugin
      desc 加载配置、维护 engine pool、解析 dependencies_name、绑定 GraphDependency、执行 stream/pip/executor/pk
      value 50
    - label DiversityMergeFunction / NewDiversityMergeFunction
      desc 填充 logid/ua/sid/condition、读取 loads/rule/function/effect 队列、构造 input container、回写 DiversityMergeResult
      value 50
theme
  palette #3D5A80 #2D6A4F #E07A5F
```

## 6. Pitfalls 卡片

<style>.pit-card{font-family:Inter,Arial,sans-serif;background:#fffdf7;border-left:6px solid #b45309;border-radius:14px;padding:16px 18px;margin:16px 0;box-shadow:0 6px 20px rgba(120,53,15,.08);color:#292524}.pit-card .meta{font-size:12px;font-weight:800;letter-spacing:.08em;color:#92400e;text-transform:uppercase}.pit-card h3{margin:6px 0 10px;font-size:22px;color:#1c1917}.pit-card p{line-height:1.65;margin:8px 0}.pit-card ul{margin:8px 0 0 18px}.pit-card code{background:#fffbeb;border:1px solid #fde68a;border-radius:5px;padding:1px 4px}</style><div class="pit-card"><div class="meta">Pitfall / Engine Binding</div><h3>不要只查业务 vertex，engine 依赖还藏在 conf 里</h3><p>排查 MultiStreamEngine 相关问题时，`vertex.conf` 只能告诉你该 vertex 绑定哪份 engine conf；真正的 stream 顺序、executor 并发模式、RuleSelectOperator 依赖，全部在 `conf/plugins/exec_engine/*.conf` 中。</p><ul><li>如果 graph dependency 缺失，优先核对 engine conf 的 `[..@depend] name` 是否能在 vertex 上解析。</li><li>如果效果队列没有进入结果，优先看 `effect_select_executor`、`effect_pk_executor` 与 condition 是否打开。</li><li>如果新旧链路结果不同，先确认 vertex option 的 `conf` 是 `multi_stream_diversity.conf` 还是 `multi_stream_diversity_new.conf` / `news_diversity.conf`。</li></ul></div>

## 7. 调试 checklist

```infographic
infographic list-column-done-list
data
  title MultiStreamEngine 绑定调试 checklist
  desc 从配置加载到结果回写逐层确认
  items
    - label 检查入口
      desc main.cpp 是否完成 GlobalInitializer 与服务注册
      done true
    - label 检查插件总配置
      desc exec_engine.conf 是否枚举目标 engine conf
      done true
    - label 检查 engine conf
      desc stream / executor / rule / pk 是否声明完整
      done true
    - label 检查 vertex option
      desc plugin、conf、context_name 是否与代码 init 字段匹配
      done true
    - label 检查 Graph 依赖
      desc engine conf 的 depend name 是否能被 vertex.named_dependency 找到
      done true
    - label 检查运行日志
      desc diversity_engine_rpc ret、div_succ_size、queue_cnt_map 是否异常
      done false
```

## 8. 证据来源

- `src/main.cpp:39-87`：GRG 服务入口、初始化与 brpc service 注册。
- `multi_stream_engine.cpp:28-48`：MultiStreamEnginePlugin 初始化与 engine pool 校验。
- `multi_stream_engine.cpp:78-91`：Graph dependency 绑定逻辑。
- `conf/plugins/exec_engine/exec_engine.conf:1-3`：全局 engine conf 枚举。
- `src/process/new_diversity_merge.cpp:43-76`：NewDiversityMergeFunction 初始化、custom_context 注入与插件绑定。
- `conf/plugins/graph/news_updates_dibar/vertex.conf:221-271`：业务 vertex 绑定 `news_diversity.conf` 与 `NewsDiversityScatterContext`。
- `conf/plugins/graph/short_micro_video/vertex.conf:798-934`、`1020-1065`：short_micro_video 传统与新分支的 engine 绑定。
