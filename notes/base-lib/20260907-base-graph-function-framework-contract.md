# 2026-09-07 周一基础库理解：BaseGraphFunction 框架契约

> 日期：2026-09-07  
> 主题来源：当前 canonical 笔记仓库未找到 2026-09-07 当日 daily-plan；旧计划 `notes/weekly-topic-selection/daily-plan-20260529.json` 中 Monday 槽位主题为 GraphPool/GraphData 生命周期（20260601、20260827 已覆盖），按 `references/daily-dual-markdown-visual-docs-20260821.md` 邻近主题回退规则改写为 `BaseGraphFunction setup / operator() / process_before_hook / process_after_hook / vertex_log / dapper_span` 框架契约。KU 正文未读取，业务背景需人工补充。  
> 范围：`baidu/feed-general/framework/include/graph_function.h`、`baidu/feed-general/framework/src/graph_function.cpp`、`baidu/feed-general/framework/include/base.h`（VertexContext / TraceLog / TupleLog）、`feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/base_function.h`（GRCGraphFunction）、`feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/base/pipeline_function.h`（PipelineGraphFunction），聚焦所有 GraphFunction 子类共同遵守的运行契约。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">引擎入口</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`GraphFunction::setup(Any)` → `operator()` → `reset()`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">图执行引擎拿到 vertex 后调 setup；run 阶段调 operator；回收阶段调 reset。这些 final 接口只在 BaseGraphFunction 里实现一次。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">框架层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`BaseGraphFunction` / `GRCGraphFunction` / `PipelineGraphFunction`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">在用户子类外面包一层钩子（hook + 上下文 + 观测），子类只需要实现 setup(config) / init / process / processor。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">业务子类</div><div style="margin-top:8px;font-size:14px;color:#102a43;">~215 个 `REGISTER_GRC_FUNCTION` 注册点</div><div style="margin-top:8px;font-size:12px;color:#52606d;">覆盖 LTR/多因子/diversity/fillmeta/recall/quota/pipeline 等全量算子，按相同契约填 process 函数。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">setup 注入 context</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">init 读取 conf</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">process 业务计算</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">reset 释放状态</div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title BaseGraphFunction 框架契约：setup / operator() / process hooks
participant "Graph Engine" as ENG
participant "GraphFunction (final)" as GF
participant "BaseGraphFunction" as BASE
participant "VertexContext" as VC
participant "Business Subclass" as SUB
participant "Graph Engine reset" as RST

ENG -> GF : setup(Any data)
GF -> BASE : setup(Any) (final)
BASE -> VC : vertex.context<VertexContext>()
BASE -> VC : tag = vertex.name() + "_vertex"
BASE -> VC : init_trace_info(vertex)
BASE -> VC : framework_context / m_framework_context
BASE -> SUB : setup(comcfg::ConfigGroup*) (override)
SUB -> SUB : read named_dependency & init subgraph config
ENG -> GF : operator()  (final, called per run)
GF -> BASE : operator()
BASE -> VC : begin_us = gettimeofday_us()
BASE -> BASE : process_before_hook()
BASE -> VC : dapper_span.performance_span.start_us
BASE -> SUB : processor()  (final override, BaseGraphFunction::processor 缺省 0)
SUB -> SUB : process() / pre_process / parallel_consume / post_process
BASE -> BASE : process_after_hook(status)
BASE -> VC : dapper_span.cost_us = end - start
BASE -> VC : dapper_span.error_no = status
BASE -> VC : end_us = gettimeofday_us()
ENG -> RST : reset()
RST -> BASE : reset() (default empty)
BASE -> SUB : reset() (optional override)
@enduml
```

## 2. 结构信息图

```infographic
infographic list-grid-badge-card
data
  title BaseGraphFunction 的 7 个强制契约点
  desc 这些契约决定了所有 GraphFunction 子类如何被引擎调用、记录日志和上报耗时；任一破坏都会让整张图失稳。
  items
    - label 引擎只调 final 接口
      desc setup(Any) / operator() / reset() 在 BaseGraphFunction 中标记 final，引擎无需关心子类实现细节
      icon mdi/lock-outline
    - label setup 双层分发
      desc 框架层取 VertexContext、打 tag、做 init_trace_info，业务子类只覆写 setup(comcfg::ConfigGroup*)
      icon mdi/source-branch
    - label operator 包夹钩子
      desc operator() 在 processor 前后固定执行 process_before_hook / process_after_hook，记 begin/end_us 与 dapper_span
      icon mdi/timer-sand
    - label VertexContext 共享
      desc vertex_log.tag = vertex.name() + "_vertex" 统一命名；后续 SIA_START/SIA_END 命中同一 tag
      icon mdi/tag-multiple
    - label process_before_hook 起表
      desc 只把 start_us 写进 dapper_span.performance_span，cost_us 留给 process_after_hook 填
      icon mdi/play-circle-outline
    - label process_after_hook 收表
      desc 用 begin_time 反推 cost_us，并把 status 写入 dapper_span.error_no，便于 dapper 上层聚合
      icon mdi/stop-circle-outline
    - label reset 默认空实现
      desc BaseGraphFunction::reset 留空，需要释放资源的子类自行覆写；filter 链路的 clear() 与该 reset 同义不同层级
      icon mdi/refresh
theme
  palette #3D5A80 #F59E0B #2D6A4F
```

## 3. 关键机制拆解

### 3.1 引擎只与 final 接口对话

`baidu/feed-general/framework/include/graph_function.h:14-22` 中 `setup(const Any&)`、`operator()` 都标记 `final`，`reset()` 标记 `noexcept`。这是“框架定义稳定边界，业务只填可扩展点”的标准做法：图引擎拿到一个 vertex 后只需按这套接口 dispatch，无需知道具体业务子类。子类只能覆写 `setup(comcfg::ConfigGroup*)`、`processor()`、`reset()`。

### 3.2 setup 阶段是“观测基线”的建立时机

`baidu/feed-general/framework/src/graph_function.cpp:7-18` 的 `BaseGraphFunction::setup(Any)` 在调用子类 setup 之前做了 5 件事：拿 `VertexContext`、把 `vertex_log.tag` 拼成 `vertex.name() + "_vertex"`、调用 `init_trace_info(vertex)` 初始化 trace 采样、缓存 `framework_context` 和 `m_framework_context` 两个图级上下文。这意味着子类的 setup 一开始就能在 vertex_log 上写 tag，任何 SIA/SIB/SIC 宏都会自动归到这个 vertex 上。

### 3.3 operator() 的钩子是 dapper 上报的唯一入口

`graph_function.cpp:23-32` 中 `operator()` 在调用 `processor()` 前后固定执行两个钩子：
- `process_before_hook`（`graph_function.cpp:38-46`）只把 `start_us` 写到 `dapper_span.performance_span().set_start_us(...)`。
- `process_after_hook`（`graph_function.cpp:48-58`）反推 `cost_us`，把 `error_no` 设为 `processor()` 的返回值。

最终 `cost_us` 和 `end_us` 同时进入 `vertex_log`，前者喂给 dapper，后者喂给 RPC/timer 日志聚合。注意 `BaseGraphFunction::processor()` 默认返回 0（`graph_function.cpp:34-36`），业务子类必须覆写才能生效。

### 3.4 GRC 侧把 GraphFunction 进一步收紧为 GRCGraphFunction

`feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/base_function.h:34-44` 中 `GRCGraphFunction` 把 `setup(const comcfg::ConfigGroup*)` 和 `processor()` 都标记 `final override`，并暴露两个纯虚：`init(...)` 和 `process()`。同时引入 `Context context` 字段供业务算子存放请求级状态。也就是说 GRC 业务的“主战场”其实是 `process()`，不是 `processor()`；`processor()` 只在 `GRCGraphFunction::processor` 中转发到 `process()`。这一层包装把框架契约继续细化成“一次 request 一次 process”的语义。

### 3.5 PipelineGraphFunction 把 process 拆成 pre / parallel_consume / post

`feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/base/pipeline_function.h`（20260904 笔记已覆盖并行批消费框架）让 `process()` 进一步收敛为三个生命周期点：`pre_process()`、`parallel_consume()`、`post_process()`。这意味着 BaseGraphFunction 的契约在下游被逐步模板化：业务算子决定“批内做什么”，框架决定“批怎么调度、怎么观测、怎么回收”。

### 3.6 215 个 REGISTER_GRC_FUNCTION 共同验证契约稳定性

`feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/` 全量搜索得到 215 处 `REGISTER_GRC_FUNCTION(name)` 调用，覆盖 doc_feature、adjust_param、auto_play、set2set、beauty_consumer、newhot_ip、jieti_div_select、sa_info_shortplay 等全部算子族。这意味着只要契约设计变更一次，就要重新编译并同步发布所有 215 个子类；反过来，只要 215 个子类都在稳定运行，说明当前契约（setup/operator/hooks/reset）就是事实标准。

### 3.7 reset 与 clear 是不同层级

`BaseGraphFunction::reset()`（`graph_function.cpp:60`）默认空实现，主要在 GraphPool 复用 vertex 实例时把图函数整体状态复位到下一轮 run。而 `GrcFilterOperator::clear()`（20260617 笔记）针对的是单个算子的成员变量。理解这两个 reset/clear 的层级关系，是排查“跨请求污染”时定位第一现场的关键。

## 4. Pitfalls 卡片

<style>.flt-card{font-family:Inter,Arial,sans-serif;background:#F5F7FA;border-radius:18px;padding:20px;margin:18px 0;border-left:8px solid #3D5A80;color:#1f2937;box-shadow:0 10px 26px rgba(31,41,55,.08)}.flt-card .meta{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:800}.flt-card h3{font-size:25px;line-height:1.15;margin:8px 0 12px}.flt-card .grid{display:grid;grid-template-columns:2fr 1fr;gap:14px}.flt-card p,.flt-card li{font-size:14px;line-height:1.65}.flt-card ul{margin:0 0 0 18px}.flt-card .side{border-left:3px solid #F59E0B;padding-left:12px;color:#374151}.flt-card code{background:#e8edf3;border-radius:5px;padding:1px 5px}</style><div class="flt-card"><div class="meta">Pitfalls / Framework Contract</div><h3>BaseGraphFunction 是“最终接口”，子类只能在 framework 包装过的钩子里填实现；覆写 final 接口会让整张图无法 dispatch。</h3><div class="grid"><div><ul><li><b>final 接口覆写：</b>不要重新覆写 <code>setup(Any)</code>、<code>operator()</code>，它们在图引擎里是唯一定向点。</li><li><b>setup 顺序假设：</b>框架层先建 VertexContext、再调子类 setup；子类不能假设依赖已经 emit。</li><li><b>dapper_span 错位：</b><code>process_before_hook</code> 与 <code>process_after_hook</code> 必须配对，否则 cost_us 全为 0。</li><li><b>vertex_log.tag 污染：</b>子类覆写 setup 时不要再覆盖 <code>vertex_log.tag</code>，否则 SIA_START 会落到别的桶。</li><li><b>reset 漏写：</b>基类 reset 默认空实现，引用外部句柄（字典/UMS/GCMS）时必须显式覆写。</li></ul></div><div class="side">评审口径：新增一个 GraphFunction 子类，先回答“它覆写哪个钩子？”、“它要不要覆写 reset？”、“它要不要覆写 clear？”三问。</div></div></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title BaseGraphFunction 子类调试 checklist
  desc 从钩子覆写、上下文、观测、回收四个维度排查
  items
    - label 钩子覆写正确
      desc 仅覆写 init / process / processor，不触碰 setup(Any) / operator()
      done true
    - label VertexContext 已建立
      desc setup 中 vertex.context<VertexContext>() 已经被框架层填好 tag / framework_context
      done true
    - label dapper_span 起停配对
      desc process_before_hook 写 start_us，process_after_hook 写 cost_us 与 error_no
      done true
    - label vertex_log tag 一致
      desc 业务 SIA_START(name) 与 vertex_log.tag 对齐，避免落到错误 vertex 桶
      done true
    - label reset 与 clear 分清
      desc GraphPool 复用级别用 reset()，算子成员变量用 clear()，不要全部塞 reset
      done true
    - label REGISTER 名称唯一
      desc REGISTER_GRC_FUNCTION(name) 的 name 必须全局唯一，命名冲突会让 graph 解析阶段 FATAL
      done true
theme
  palette #3D5A80 #F59E0B #2D6A4F
```

## 6. 证据来源

- `baidu/feed-general/framework/include/graph_function.h:12-27`：BaseGraphFunction final 接口定义。
- `baidu/feed-general/framework/src/graph_function.cpp:7-18`：setup(Any) 注入 VertexContext 与 trace。
- `baidu/feed-general/framework/src/graph_function.cpp:23-32`：operator() 包裹 processor 与两个 hook。
- `baidu/feed-general/framework/src/graph_function.cpp:38-46`：process_before_hook 写 start_us。
- `baidu/feed-general/framework/src/graph_function.cpp:48-58`：process_after_hook 算 cost_us / error_no。
- `baidu/feed-general/framework/src/graph_function.cpp:60`：默认 reset() 空实现。
- `baidu/feed-general/framework/include/base.h:50-63`：TupleLog 结构（tag / error / value / begin_us / end_us）。
- `baidu/feed-general/framework/include/base.h:283-308`：VertexContext（vertex_log / dapper_span / TraceLog）。
- `feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/base_function.h:34-44`：GRCGraphFunction 二次收紧为 init / process。
- `feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/processor/base/pipeline_function.h`：PipelineGraphFunction 拆分 pre / parallel_consume / post。
- `feeda-mv-grc/baidu/feed-gr/feeda-mv-grc/src/service/grc_service.cpp:435`：graph_monitor.print_vertex_log() 收口。