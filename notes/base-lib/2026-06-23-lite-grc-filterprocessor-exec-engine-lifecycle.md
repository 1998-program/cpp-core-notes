# lite-grc FilterProcessor：ExecEngine 过滤链装载、运行与状态边界（2026-06-23）
<!-- topic-nav:start -->
> 主题：`base/filter-lifecycle` · 主文 · [主题目录](README.md)
<!-- topic-nav:end -->


> 今日主题来源：本机未发现可用 `daily-plan` 文件；按周二 fallback 选择 lite-grc 过滤基础库主题，聚焦 `FilterProcessor` 如何把图依赖、`filter_main*.conf`、ExecEngine FilterOperator 和 `MultiQueueResult` 串成可观测过滤链。已按要求设置 `BAIDU_CC_USERNAME=hujiyang`；当前环境 `ku` 不在 PATH 且未提供 KU URL/doc-id，内网背景需人工补充；本文以代码库检索结果为准。

<style>.arch230623{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #dbe4ef;border-radius:18px;padding:18px;margin:16px 0;color:#1f2937}.arch230623 .title{font-size:22px;font-weight:850;margin-bottom:5px}.arch230623 .sub{font-size:13px;color:#64748b;margin-bottom:14px}.arch230623 .grid{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px}.arch230623 .layer{background:#fff;border:1px solid #dbe4ef;border-radius:14px;padding:14px}.arch230623 h3{margin:0 0 10px;font-size:15px;color:#315072}.arch230623 .box{background:#eef6ff;border:1px solid #bfdbfe;border-radius:10px;padding:9px;margin:8px 0;font-size:13px;line-height:1.45}.arch230623 .hot{background:#fff7ed;border-color:#f59e0b}.arch230623 .warn{background:#fff1f2;border-color:#fda4af}.arch230623 .arrow{text-align:center;color:#64748b;font-size:12px;font-weight:800;margin:8px 0}</style><div class="arch230623"><div class="title">架构全景：FilterProcessor 是 Graph 与 ExecEngine FilterOperator 的适配层</div><div class="sub">图节点只看到 RecallResultWithMeta → FilterResult；真正的规则编排由 filter 配置与 ExecEngine operator 工厂完成。</div><div class="grid"><div class="layer"><h3>Graph 绑定层</h3><div class="box">recall_fill_filter_tpl</div><div class="box hot">FilterProcessor vertex</div><div class="box">CommonInfo / SidInfo / RequestType / readlists</div></div><div class="layer"><h3>FilterProcessor 适配层</h3><div class="box hot">setup(): 读取 handle_name 与 filter_conf_name</div><div class="arrow">↓ get_filter_engine_pool</div><div class="box">主 engine: filter_main.conf 或显式配置</div><div class="box">func_rule engine: filter_for_func_rule.conf</div><div class="box warn">process(): mutable MultiQueueResult 原地过滤后 ref emit</div></div><div class="layer"><h3>ExecEngine 热路径</h3><div class="box">operator real_prepare()</div><div class="box hot">item_list/effect_list/non_effect_list</div><div class="box">engine->run()</div><div class="box">ES_Break → queue.erase()</div></div></div></div>

## 核心结论

- `FilterProcessor` 的入口不是随机 C++ 类，而是图模板节点：`conf/plugins/graph/conf_template/recall_fill_filter.conf:61-168` 把 `RecallResultWithMeta`、`SidInfo`、`DislikeInfo`、`MsvReadListInfo`、`RequestType`、`CommonInfo`、`SecurityScenes` 等依赖绑定到过滤节点。
- `setup()` 读取 `filter_conf_name`，默认是 `filter_main.conf`，并同时装载固定的 `filter_for_func_rule.conf`：`src/processor/filter.cpp:13-89`。
- `process()` 是请求级执行边界：先把所有候选包装成 `DynamicStruct*`，再按 effect/non-effect 分流，最后根据 `ES_Break` 从队列中删除：`src/processor/filter.cpp:92-190`、`src/processor/filter.cpp:207-274`。
- 基类 `GrcFilterOperator` 规定“已过滤 item 不再进入后续业务算子”，并在 Break 时统一写 `filter_log`：`src/processor/filter/grc_filter_base.h:72-101`。

## 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title lite-grc FilterProcessor 执行生命周期
participant "Graph vertex" as graph
participant "FilterProcessor.setup" as setup
participant "EngineMgr" as mgr
participant "FilterProcessor.process" as proc
participant "FilterEngine" as engine
participant "GrcFilterOperator" as op
database "MultiQueueResult" as mqr
graph -> setup : option(handle_name, filter_conf_name)
setup -> graph : bind named_dependency(...)
setup -> mgr : get_filter_engine_pool(filter_conf_name)
setup -> mgr : get_filter_engine_pool(filter_for_func_rule.conf)
proc -> mqr : mutable_value<MultiQueueResult>()
proc -> proc : build item_list / effect_list / non_effect_list
proc -> engine : init_filter_engine + set context
alt hit func-rule abtest
  proc -> engine : run(effect_list)
  proc -> engine : func_filter_engine.run(non_effect_list)
else normal
  proc -> engine : run(item_list)
end
engine -> op : real_prepare + real_process per item
op --> engine : ES_Continue / ES_Break
proc -> mqr : erase ES_Break items + count filter_num
proc -> graph : emit_data.ref(*fill_meta_result)
@enduml
```

## 配置/结构信息图

```infographic
infographic hierarchy-structure
data
  title FilterProcessor 三层契约
  desc 图依赖、引擎配置、算子状态必须同时对齐
  items
    - label 图节点契约
      desc conf_template/recall_fill_filter.conf 声明依赖与输出
      children
        - label 输入
          desc RecallResultWithMeta / SidInfo / CommonInfo / RequestType
        - label 输出
          desc Filter<data_prefix>Result 匿名输出
    - label 引擎契约
      desc filter_conf_name 指向 filter_main*.conf，func_rule 固定加载
      children
        - label 主过滤
          desc filter_engine_pool(filter_conf_name)
        - label 功能规则
          desc func_filter_engine_pool(filter_for_func_rule.conf)
    - label 算子契约
      desc real_prepare 缓存请求状态，real_process 处理候选，clear 重置成员
      children
        - label 短路
          desc 已 ES_Break 的 item 不再调用后序 real_process
        - label 归因
          desc Break 时 filter_log=operator.to_string()
theme
  palette #3D5A80 #F59E0B #2D6A4F
```

## 关键机制拆解

### 1. `filter_conf_name` 是运行期配置入口，默认值容易被忽略

`src/processor/filter.cpp:16-23` 中默认 `filter_conf_name = "filter_main.conf"`，只有 vertex option 明确配置时才替换。排查“为什么某个算子没生效”时，先确认图节点是否传入了期望的配置文件，而不是直接跳到 operator 代码。

### 2. FilterProcessor 对 `MultiQueueResult` 是原地过滤语义

`src/processor/filter.cpp:96-99` 取 `mutable_value<MultiQueueResult>()`，`src/processor/filter.cpp:216-235` 在队列遍历时对 `ES_Break` 做 `queue.erase()`，最后 `src/processor/filter.cpp:254-255` 用 `emit_data_p.ref(*fill_meta_result)` 输出。这意味着后续节点看到的是被过滤后的同一批队列视图；调试前后条数必须在该节点边界打点。

### 3. effect/non-effect 分流会改变实际执行的规则集合

`src/processor/filter.cpp:173-188` 根据 `is_effect()` 把候选拆成 `effect_list` 与 `non_effect_list`，命中 `hit_func_rule_abtest()` 时主 engine 只跑 effect，func-rule engine 跑 non-effect。表现上可能像“部分候选没有经过某规则”，实际是被分流到另一套 filter 配置。

### 4. `GrcFilterOperator` 基类隐藏了两个关键默认行为

`src/processor/filter/grc_filter_base.h:83-99`：若 `rid_info->filter_result` 已是 `ES_Break`，基类不会再调用业务 `real_process()`；若本次返回 Break，会写 `filter_log = to_string()`。因此定位误杀时必须看首个写入 Break 的算子，而不是最后一个配置在链上的算子。

<style>.card230623{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#F5F7FA;border-radius:18px;padding:20px;margin:18px 0;border-left:8px solid #3D5A80;color:#1f2937;box-shadow:0 10px 26px rgba(31,41,55,.08)}.card230623 .meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:800}.card230623 h3{font-size:24px;line-height:1.18;margin:8px 0 12px}.card230623 .grid{display:grid;grid-template-columns:2fr 1fr;gap:14px}.card230623 li{font-size:14px;line-height:1.65}.card230623 ul{margin:0 0 0 18px}.card230623 .side{border-left:3px solid #F59E0B;padding-left:12px;color:#374151;font-size:14px;line-height:1.65}.card230623 code{background:#e8edf3;border-radius:5px;padding:1px 5px}</style><div class="card230623"><div class="meta">Pitfalls / Filter Lifecycle</div><h3>过滤链问题通常不是单点 bug，而是“图依赖、配置文件、算子状态”三者错位。</h3><div class="grid"><div><ul><li><b>配置文件错位：</b>默认加载 <code>filter_main.conf</code>，不要假设一定是 auto_play 或 redpoint 配置。</li><li><b>依赖顺序错位：</b><code>get_dependency(context, n)</code> 依赖配置中的 <code>[..@depend]</code> 顺序。</li><li><b>短路误判：</b>已 Break 的 item 会跳过后续算子，后面的规则“没打日志”不代表未配置。</li><li><b>分流误判：</b>func-rule abtest 会拆 effect/non-effect，需同时看主 engine 与 func_filter_engine。</li></ul></div><div class="side">评审建议：新增 filter 配置时同步写明 handle_name、filter_conf_name、依赖顺序、是否进入 effect 分流，以及 filter_log 期望值。</div></div></div>

## 调试 checklist

```infographic
infographic list-column-done-list
data
  title FilterProcessor 调试 checklist
  desc 从图节点到过滤日志逐层收敛
  items
    - label 确认入口 main.cpp 与服务图
      desc src/main.cpp 注册 GenericGRCService，图配置再决定具体 FilterProcessor 节点
      done true
    - label 确认 vertex option
      desc handle_name 是否存在，filter_conf_name 是否为目标配置
      done true
    - label 确认 engine pool
      desc get_filter_engine_pool 返回非空，func_rule 配置也必须存在
      done true
    - label 确认候选分流
      desc item_list/effect_list/non_effect_list 的数量是否符合预期
      done true
    - label 确认首个 Break
      desc filter_log 指向首个过滤算子；已 Break item 不会再跑后序 real_process
      done true
    - label 确认输出条数
      desc filter_num 与 emit 后 MultiQueueResult 各队列 size 是否匹配
      done true
theme
  palette #3D5A80 #F59E0B #2D6A4F
```

## 证据来源

- `src/main.cpp:72-146`：lite-grc 服务启动、`GenericGRCService` 注册与 Dapper worker 启动。
- `conf/plugins/graph/conf_template/recall_fill_filter.conf:61-168`：FilterProcessor 图模板、依赖与输出。
- `src/processor/filter.cpp:13-89`：`setup()` 读取配置、绑定依赖、获取 filter engine pool。
- `src/processor/filter.cpp:92-190`：`process()` 构建 item/effect/non-effect 列表并运行 engine。
- `src/processor/filter.cpp:207-274`：根据 `ES_Break` 删除候选、统计 filter_num、输出 SIA。
- `src/processor/filter/grc_filter_base.h:60-69`：带状态算子必须实现 `clear()` 的约束。
- `src/processor/filter/grc_filter_base.h:72-101`：短路、`real_process()` 调用与 `filter_log` 写入。
