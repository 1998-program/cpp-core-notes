# ExecEngine MultiStreamEngine：按槽位循环、stream 链路与算子成本可观测

> 日期：2026-06-03  
> 主题来源：未发现本机 daily-plan 文件；按双 Markdown 工作流补齐基础库主题，选择 `exec-engine` 中可复用的 `MultiStreamEngine` 按槽位循环执行与 Dapper 成本采样链路。  
> 范围：`MultiStreamEngineInfoLoader` 配置加载、`MultiStreamEngineInfo::create()` 构造 stream、`MultiStreamEngine::run_prepare()` 绑定依赖与 prepare、`MultiStreamEngine::run()` 按 loop/stream 执行、rollback、结果输出与 Dapper 成本上报。  
> 内网文档：当前环境未发现可用 `ku` 命令；本文以代码库检索结果替代，线上运维口径需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div class="arch-title" style="font-size:22px;font-weight:800;margin-bottom:6px;">MultiStreamEngine 从配置到按槽位执行</div>
<div class="arch-subtitle" style="font-size:13px;color:#64748b;margin-bottom:16px;">核心不是“一个 operator 跑到底”，而是 config → stream/pip/executor/operator 元信息 → 每个推荐槽位逐 stream 流转。</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">配置与元信息层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>conf_file/default</b><br><span style="font-size:12px;color:#64748b;">加载 ExecContextConf</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>dependency_schema</b><br><span style="font-size:12px;color:#64748b;">依赖字段 schema</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>stream[]</b><br><span style="font-size:12px;color:#64748b;">loads/rule/function/effect/pk</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>operator conditions</b><br><span style="font-size:12px;color:#64748b;">合并为 OR 表达式</span></div>
</div>
</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">请求生命周期层</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>run_prepare</b><br><span style="font-size:12px;color:#64748b;">bind dependency</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>create streams</b><br><span style="font-size:12px;color:#64748b;">计算输入容量</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>loop_prepare</b><br><span style="font-size:12px;color:#64748b;">每槽位重置状态</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>stream run</b><br><span style="font-size:12px;color:#64748b;">逐 stream 执行</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>output push</b><br><span style="font-size:12px;color:#64748b;">写入最终容器</span></div>
</div>
</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">观测与清理层</div>
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>TraceLogContext</b><br><span style="font-size:12px;color:#64748b;">merge_trace_pair_vec / print_trace_log</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>DapperRecord</b><br><span style="font-size:12px;color:#64748b;">MULTISTREAM_ENGINE_NODE_COST</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>clear()</b><br><span style="font-size:12px;color:#64748b;">streams/operators/context/record</span></div>
</div>
</div>
</div>

## 2. 核心结论

- `MultiStreamEngineInfoLoader::add_engine_info()` 先读 `multi_stream_engine` 配置，再把 `stream/pip/executor/operator` 展开为 `MultiStreamEngineInfo`，并收集 operator 的依赖条件；证据见 `multi_stream_engine.cc:60-113`、`multi_stream_engine.cc:115-205`。
- `MultiStreamEngineInfo::create()` 是请求级动态 stream 构造点：为每个 stream 绑定输入容器、估算输出容量、把 stream 输出注册回 `item_container_map`，让后续 stream 可以消费前序 stream 的结果；证据见 `multi_stream_engine.h:48-110`。
- `run_prepare()` 做依赖绑定、stream 创建、`request_init()` 与 executor `prepare()`；真正按槽位执行发生在 `run()` 的 `while loop_num < _loop_num` 中；证据见 `multi_stream_engine.h:286-315`、`multi_stream_engine.h:319-423`。
- rollback 不是异常退出，而是 `check_output()` 返回 false 后清空当前输出并重试同一 loop；证据见 `multi_stream_engine.h:386-395` 与业务侧 `multi_stream_diversity_operator.cc:221-288`。
- 成本采样由 `collect_multi_stream_op_cost_ratio` 控制，命中后写 Dapper 与本地 NOTICE 成本日志；证据见 `multi_stream_engine.h:424-476`、`multi_stream_engine.h:481-527`、`multi_stream_engine.h:556-562`。

## 3. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Request as req
participant "DiversityMergeFunction" as biz
participant "MultiStreamEngine" as engine
participant "BaseStreamContext" as ctx
participant "ExecutorStream" as stream
participant "BatchOperator" as op
database "item_container_map" as map
req -> biz : process()
biz -> engine : run_prepare(map)
engine -> op : bind_dependency_data()
engine -> map : create streams and outputs
engine -> ctx : request_init()
engine -> stream : prepare(context)
biz -> engine : set_loop_num(result_num)
biz -> engine : run(map, output)
loop per slot loop_num
  engine -> ctx : loop_prepare()
  engine -> op : loop_prepare()
  loop per stream
    engine -> ctx : generator_input(map, stream.input)
    ctx --> engine : true / skip
    engine -> stream : run(input, output)
    stream -> op : real_process()
    engine -> ctx : check_output(input, output)
    alt rollback
      ctx --> engine : false
      engine -> stream : clear output
    else success
      ctx --> engine : true
    end
  end
  engine -> op : update_context(result slice)
  engine -> ctx : update_context(result slice)
  engine -> biz : output.push(result slice)
end
engine -> engine : merge_trace_pair_vec()
engine -> engine : Dapper cost sample if hit
@enduml
```

## 4. 配置结构信息图

```infographic list-grid-badge-card
data
  title MultiStreamEngine 配置骨架
  desc 一个配置文件描述多个 stream；每个 stream 由 pip 指向 executor；executor 再挂 generator 与 rule/operator。
  items
    - label global
      desc conf_instance、conf_file、dependency_schema、mutable_context_schema
      icon mdi/cog-outline
    - label stream
      desc loads_select、rule_select、function_select、final_select、effect_pk、merge_pk
      icon mdi/source-branch
    - label pip
      desc 每个 pip 的 name 被注册为输入/输出容器键
      icon mdi/pipe
    - label executor
      desc exec_type、is_syncer、stages、is_open 小流量开关
      icon mdi/play-box-outline
    - label operator
      desc RuleSelectOperator、DiversityRuleOperator、PK operator
      icon mdi/vector-polyline
    - label observability
      desc TraceLogContext、op cost NOTICE、DapperRecord
      icon mdi/chart-line
```

## 5. 运行时检查点

```infographic sequence-timeline-rounded-rect-node
data
  title 调试顺序
  desc 先查配置加载，再查输入容器，再查 loop/stream，再查成本采样。
  items
    - time Step 1
      label 配置是否加载
      desc multi_stream_engine.conf 是否包含 global、stream、executor、rule
    - time Step 2
      label 输入容器是否齐全
      desc item_container_map 必须有 pip name 对应的输入，否则 create() 会 FATAL
    - time Step 3
      label loop 是否被 rollback 卡住
      desc check_output 返回 false 会 continue 同一 loop
    - time Step 4
      label 最后一个 stream 是否产出
      desc _streams.back().output 为空会返回 -1
    - time Step 5
      label 成本是否可观测
      desc collect_multi_stream_op_cost_ratio 命中才写 Dapper 与成本日志
```

## 6. Pitfalls 卡片

<div class="card-frame" style="max-width:980px;margin:18px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div class="card" style="background:#fbf9f1;border:1px solid #e7dfd1;border-radius:18px;padding:22px;color:#2f2a24;">
<div class="card-meta" style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#7c6853;">Pitfalls · MultiStreamEngine</div>
<div class="card-title" style="font-size:30px;font-weight:850;letter-spacing:-.02em;margin:8px 0 14px;">多数问题不是算子本身，而是 stream 输入与回退语义。</div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;">
<div style="border-top:4px solid #7c6853;padding-top:12px;line-height:1.65;font-size:14px;">`create()` 会按 pip 名在 `item_container_map` 中找输入。缺任何一个 pip key 都可能 FATAL；如果前序 stream 输出名与后序 pip 名不一致，症状会像“operator 没跑”。排查时先对照配置中的 stream/pip name，而不是先看业务规则。</div>
<div style="background:#f1eadf;border-radius:14px;padding:14px;font-size:13px;line-height:1.6;">`check_output()` 的 false 表示重试，不等于失败。若 rollback 条件一直满足，会在同一 loop 上反复进入 stream 链。</div>
</div>
<div style="margin-top:14px;border-left:4px solid #7c6853;padding-left:12px;font-size:14px;line-height:1.6;">成本 Dapper 依赖采样：没有数据不代表没有执行，先确认 `collect_multi_stream_op_cost_ratio` 是否命中。</div>
</div>
</div>

## 7. 证据来源

- `exec-engine/src/multi_stream_engine.cc:60-113`：配置加载、`conf_file/conf_instance/dependency_schema`。
- `exec-engine/src/multi_stream_engine.cc:115-205`：stream/pip 展开与依赖条件合并。
- `exec-engine/src/multi_stream_engine.h:48-110`：请求级 stream 构造、输入输出容器绑定。
- `exec-engine/src/multi_stream_engine.h:286-315`：`run_prepare()` 依赖绑定与 prepare。
- `exec-engine/src/multi_stream_engine.h:319-423`：按 loop/stream 执行、rollback 与输出 push。
- `exec-engine/src/multi_stream_engine.h:424-476`：Dapper 成本记录。
- `exec-engine/src/multi_stream_engine.h:481-527`：本地 operator 成本日志。
- `exec-engine/src/operator/multi_stream_diversity_operator.cc:42-75`：选择成功后的上下文更新。
- `exec-engine/src/operator/multi_stream_diversity_operator.cc:77-213`：按 stream 生成输入与过滤。
- `exec-engine/src/operator/multi_stream_diversity_operator.cc:221-288`：输出检查、豁免强插与 rollback。
