# ExecEngine MultiStreamEngine：槽位循环、回退语义与可观测性
<!-- topic-nav:start -->
> 主题：`base/multistream-binding` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260529-exec-engine-multistreamengine-graphdependency-deep-dive.md)
<!-- topic-nav:end -->


> 日期：2026-06-05  
> 主题来源：未发现本机 `daily-plan` 文件；按双 Markdown 工作流延续 2026-06-04 的 MultiStreamEngine 主题，今天补齐运行期 `run()` 的槽位循环、`check_output()` 回退、结果上下文更新与 Dapper 成本采样。  
> 范围：`run_prepare()`、`run()`、`generator_input()`、`check_input()`、`check_output()`、`update_context()`、Dapper operator cost 上报。  
> 内网文档：已按要求设置 `BAIDU_CC_USERNAME=hujiyang` 并确认 `ku` 可用；但本次任务未提供 KU URL/doc-id，本文以代码库检索结果为主，线上策略口径需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div style="font-size:22px;font-weight:800;margin-bottom:6px;">MultiStreamEngine 运行期：从请求准备到槽位提交</div>
<div style="font-size:13px;color:#64748b;margin-bottom:16px;">静态图由配置加载阶段提供；请求期关注输入容器绑定、每槽流式执行、失败回退和成本上报。</div>
<div style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">请求准备层 · run_prepare</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>bind dependency</b><br><span style="font-size:12px;color:#64748b;">非 GraphEngine 场景绑定 dependency data</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>custom_context</b><br><span style="font-size:12px;color:#64748b;">BaseStreamContext request_init</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>create streams</b><br><span style="font-size:12px;color:#64748b;">pip name → input container</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>executor prepare</b><br><span style="font-size:12px;color:#64748b;">operator request_init / real_prepare</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">槽位循环层 · run loop</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>loop_prepare</b><br><span style="font-size:12px;color:#64748b;">设置 loop_num 并清理中间态</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>generator_input</b><br><span style="font-size:12px;color:#64748b;">业务决定本 stream 是否可运行</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>executor.run</b><br><span style="font-size:12px;color:#64748b;">按 stream/pip/operator 执行</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>check_output</b><br><span style="font-size:12px;color:#64748b;">失败可清空输出并触发 rollback</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>commit</b><br><span style="font-size:12px;color:#64748b;">末级输出写入 out_item_list</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">观测层 · trace + dapper</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>trace merge</b><br><span style="font-size:12px;color:#64748b;">每槽合并 trace pair</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>operator cost</b><br><span style="font-size:12px;color:#64748b;">rule/batch rule process cost</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>prepare cost</b><br><span style="font-size:12px;color:#64748b;">单算子 prepare 耗时</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>tag kv</b><br><span style="font-size:12px;color:#64748b;">module / parent file / idc</span></div>
</div>
</div>
</div>

## 2. 核心结论

- `run_prepare()` 不是轻量 no-op：它会收集所有 operator、绑定依赖数据、通过 `MultiStreamEngineInfo::create()` 创建本请求 stream 容器，并逐个 `stream.executor->prepare()`；证据见 `exec-engine/src/multi_stream_engine.h:286-315`。
- `MultiStreamEngineInfo::create()` 会按 pip name 从 `item_container_map` 找输入，并把每个 stream 的输出以 stream name 注册回 map；因此后续 stream 可以消费前序 stream 输出；证据见 `exec-engine/src/multi_stream_engine.h:48-110`。
- `run()` 的 while 条件是 `!_context.is_done() && loop_num < _loop_num`，业务侧通过 `set_loop_num(result_num)` 控制最多提交多少槽；证据见 `exec-engine/src/multi_stream_engine.h:335-337`。
- 每个槽位开始会 reset 输入游标、clear stream 输出，并调用 `BaseStreamContext::loop_prepare()` 与所有 stream operator 的 `loop_prepare()`；证据见 `exec-engine/src/multi_stream_engine.h:344-367`。
- `generator_input()` 返回 false 时仅跳过当前 stream；`check_input()` 输入为空则直接失败返回；`check_output()` 返回 false 会清空当前 stream 输出并 break，让业务 context 决定是否 rollback；证据见 `exec-engine/src/multi_stream_engine.h:370-390`。
- 若 `stream_context->is_rollback()` 为 true，当前槽不递增 `loop_num`，直接重试；否则取最后一个 stream 输出，更新 operator/context 并 push 到 `out_item_list`；证据见 `exec-engine/src/multi_stream_engine.h:393-421`。
- 成本上报依赖 `FLAGS_open_multi_stream_engine_cost_dapper && _context._collect_op_cost`，并对 `RuleSelectOperator` 下的 rule/batch-rule 分别记录 process 与 prepare 耗时；证据见 `exec-engine/src/multi_stream_engine.h:424-468`。

## 3. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "Caller" as caller
participant "MultiStreamEngine" as engine
participant "MultiStreamEngineInfo" as info
participant "BaseStreamContext" as ctx
participant "ExecutorStream" as stream
participant "BatchOperator" as op
database "item_container_map" as map
database "out_item_list" as out
caller -> engine : run_prepare(map)
engine -> op : bind_dependency_data()
engine -> info : create(map, cond_params, sids)
info -> map : resolve pip inputs
info -> map : register stream outputs
engine -> ctx : request_init(exec_context)
engine -> stream : prepare(exec_context)
caller -> engine : set_loop_num(result_num)
caller -> engine : run(map, out)
loop while !context.done && loop_num < loop_num_limit
  engine -> ctx : set_loop_num + loop_prepare
  engine -> op : loop_prepare
  engine -> stream : reset inputs + clear outputs
  loop each stream
    engine -> ctx : generator_input(map, stream.input)
    alt generated
      engine -> ctx : check_input(stream.input)
      engine -> stream : executor.run(input, output)
      engine -> ctx : check_output(input, output)
      alt check_output false
        engine -> stream : clear output
        engine -> engine : break stream loop
      end
    else skip
      engine -> engine : continue next stream
    end
  end
  alt rollback
    engine -> ctx : retry same slot
  else commit
    engine -> op : update_context(result_slice)
    engine -> ctx : update_context(result_slice)
    engine -> out : push(result_slice)
    engine -> ctx : merge_trace_pair_vec()
  end
end
@enduml
```

## 4. 运行结构信息图

```infographic hierarchy-structure
data
  title MultiStreamEngine 运行态对象树
  desc 请求期从静态 EngineInfo 派生 stream unit，并在每个槽位重复执行。
  items
    - label MultiStreamEngine
      desc _context、_streams、_operators、_loop_num
      children
        - label run_prepare
          desc bind dependency → create streams → request_init → prepare
          children
            - label StreamUnit
              desc name、executor、input[]、output
            - label BatchOperator
              desc dataframe dependency、loop_prepare、update_context
        - label run loop
          desc generator_input → executor.run → check_output → commit/rollback
          children
            - label rollback path
              desc is_rollback true 时不递增 loop_num
            - label commit path
              desc 末级输出更新上下文并写出
```

## 5. 回退与失败语义

```infographic compare-binary-horizontal-underline-text-vs
data
  title skip / fail / rollback 的区别
  desc 三种路径都发生在单槽执行中，但对槽位推进影响不同。
  items
    - label skip stream
      desc generator_input=false；跳过当前 stream，不代表请求失败
    - label fail request
      desc check_input 空或 executor.run 非 0；run() 返回 -1
    - label retry slot
      desc check_output=false 且 context 标记 rollback；同一 loop_num 重试
```

## 6. Pitfalls 卡片

<div class="card-frame" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:18px 0;max-width:980px;">
<div class="card" style="background:#faf8f4;border:1px solid #e7dfd4;border-radius:18px;padding:22px;color:#2f2a24;">
<div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#7c6853;margin-bottom:8px;">Pitfall · MultiStreamEngine Run</div>
<div style="font-size:30px;font-weight:850;letter-spacing:-.02em;margin-bottom:10px;">不要把 `check_output=false` 等同于失败</div>
<div style="height:5px;width:88px;background:#7c6853;border-radius:4px;margin:10px 0 16px 0;"></div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;align-items:start;">
<div style="font-size:15px;line-height:1.65;color:#3f372f;">`check_output()` 失败后引擎只清空当前 stream 输出并跳出 stream loop。是否重试同一槽由业务 `BaseStreamContext` 的 rollback 标志决定；只有进入 commit 分支才会递增 `loop_num` 并写入结果。</div>
<div style="border-top:3px solid #7c6853;background:#fffaf2;border-radius:12px;padding:12px;font-size:13px;line-height:1.55;color:#5d5044;"><b>调试抓手</b><br>同时看 `stream_name()`、`loop_num`、`is_rollback()`、最后一级 stream output size。</div>
</div>
</div>
</div>

## 7. 调试 checklist

```infographic list-column-done-list
data
  title MultiStreamEngine 运行期排障 checklist
  items
    - label 先确认 run_prepare 是否成功
      desc stream size 为 0 通常是 pip 输入键缺失或重复 stream output 注册失败
      done false
    - label 对齐 input_container_map 键名与配置 pip name
      desc create() 用 pip_info->name 查输入，大小写或新旧 conf 名称不一致会 FATAL
      done false
    - label 区分 generator_input skip 与 check_input fail
      desc 前者可继续后续 stream，后者直接返回 -1
      done false
    - label 回退问题必须看 context rollback 标志
      desc check_output=false 后未必重试，取决于 BaseStreamContext 实现
      done false
    - label Dapper 无数据时检查两个开关
      desc FLAGS_open_multi_stream_engine_cost_dapper 与 _context._collect_op_cost 同时满足才上报
      done false
```

## 8. 证据来源

- `exec-engine/src/multi_stream_engine.h:48-110`：`MultiStreamEngineInfo::create()` 输入绑定与 stream output 注册。
- `exec-engine/src/multi_stream_engine.h:286-315`：`run_prepare()` 请求初始化与 executor prepare。
- `exec-engine/src/multi_stream_engine.h:335-421`：`run()` 主循环、skip/fail/rollback/commit 路径。
- `exec-engine/src/multi_stream_engine.h:424-468`：Dapper operator cost 上报结构。
- `exec-engine/src/multi_stream_engine.cc:115-205`：静态配置展开为 stream/pip/executor/operator 图。
