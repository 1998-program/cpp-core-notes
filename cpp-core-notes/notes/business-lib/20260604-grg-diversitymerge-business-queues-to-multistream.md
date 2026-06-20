# GRG DiversityMerge：业务多队列如何接入 MultiStreamEngine

> 日期：2026-06-04  
> 主题来源：未发现本机 daily-plan 文件；按双 Markdown 工作流延续 2026-06-03 主题，今天聚焦 `feeda-mv-grg` 的 `DiversityMergeFunction` 如何准备 loads/rule/function/effect 四类输入队列、绑定 GraphDependency，并调用基础库 `MultiStreamEngine` 完成多队列合并。  
> 范围：`DiversityMergeFunction::init()`、`process()` 中 exec_context 准备、输入队列构造、general_adjust 预处理、`run_prepare()`/`run()`、trace 与 reset。  
> 内网文档：当前环境未发现可用 `ku` 命令；已按要求先设置 `BAIDU_CC_USERNAME=hujiyang` 检查，本文以代码库检索结果替代，线上策略口径需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div class="arch-title" style="font-size:22px;font-weight:800;margin-bottom:6px;">DiversityMergeFunction：业务队列 → 通用多流引擎</div>
<div class="arch-subtitle" style="font-size:13px;color:#64748b;margin-bottom:16px;">业务侧负责上下文、队列和策略开关；基础库负责按 stream 配置执行 loads/rule/function/effect/PK。</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">初始化层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>EngineMgr pool</b><br><span style="font-size:12px;color:#64748b;">按 conf 获取 MultiStreamEngine</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>custom_context</b><br><span style="font-size:12px;color:#64748b;">绑定 DiversityScatterContext</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>GraphDependency</b><br><span style="font-size:12px;color:#64748b;">插件绑定 vertex 依赖</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>trace phase</b><br><span style="font-size:12px;color:#64748b;">phase_name / phase_no</span></div>
</div>
</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">业务输入层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>loads</b><br><span style="font-size:12px;color:#64748b;">loads 资源强插/豁免</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>rule</b><br><span style="font-size:12px;color:#64748b;">规则队列 + news_rule</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>function</b><br><span style="font-size:12px;color:#64748b;">功能队列</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>effect</b><br><span style="font-size:12px;color:#64748b;">效果队列 + 软打散</span></div>
</div>
</div>
<div class="arch-layer" style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">执行与输出层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>run_prepare</b><br><span style="font-size:12px;color:#64748b;">绑定输入容器</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>general_adjust</b><br><span style="font-size:12px;color:#64748b;">位置无关软打散并发预处理</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>engine.run</b><br><span style="font-size:12px;color:#64748b;">按 result_num 循环选槽</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>trace/reset</b><br><span style="font-size:12px;color:#64748b;">队列 trace、baiplus log、clear</span></div>
</div>
</div>
</div>

## 2. 核心结论

- `init()` 从配置读取 `plugin/conf`，通过 `EngineMgr::get_multi_stream_engine_pool(conf)` 获取引擎对象，并把业务 `DiversityScatterContext` ref 到 `exec_context->custom_context`；证据见 `src/process/diversity_merge.cpp:65-85`。
- `init()` 还会把 GraphEngine vertex 的依赖绑定到 MultiStreamEngine，使配置中的 operator depend 能从图上下文取值；证据见 `src/process/diversity_merge.cpp:86-92`。
- `process()` 先填充 exec_context 的 logid/cuid/ua/sid、字段 accessor 与条件变量，例如 `is_open_dnn_soft_rule`、`is_searchc_immersive_request`、`enable_general_adjust`；证据见 `src/process/diversity_merge.cpp:130-209`。
- 输入队列先拆成 `loads/rule/function/effect` 四类：loads/rule 按 rec_num 分槽，function/effect 单容器；`read_queue()` 后再转成 `SelectStreamContainer` 放入 `input_container_map`；证据见 `src/process/diversity_merge.cpp:251-270`、`src/process/diversity_merge.cpp:700-705`。
- 若命中 `is_open_dnn_soft_rule && enable_general_adjust`，业务侧在 MultiStreamEngine 主循环前用 8 个 bthread 对 effect 队列做位置无关软打散调权，再把 `ori_score_key` 切到 `general_adjust_score`；证据见 `src/process/diversity_merge.cpp:727-794`。
- 最终通过 `_diversity_engine->set_loop_num(result_num)` 和 `_diversity_engine->run(input_container_map, &output)` 进入基础库多流执行；证据见 `src/process/diversity_merge.cpp:796-806`。

## 3. 核心时序图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "GRG request" as req
participant "DiversityMergeFunction" as div
participant "ExecContext" as ctx
participant "DiversityScatterContext" as scatter
database "input_container_map" as map
participant "MultiStreamEngine" as engine
participant "Rule/Soft Operators" as ops
req -> div : process()
div -> ctx : set logid/cuid/ua/sids/field keys
div -> ctx : add_condition_value(...)
div -> scatter : set insert limits / ignore indexes / function context
div -> div : read_queue(loads/rule/function/effect)
div -> map : wrap RidInfoVec as SelectStreamContainer
alt general_adjust enabled
  div -> ops : get_runable_operators(batch_rule[*].valid)
  loop 8 bthreads
    div -> ops : run_soft_rule(effect shard)
  end
  div -> ctx : _ori_score_key = general_adjust_score
end
div -> engine : run_prepare(map)
engine -> map : resolve pip inputs and stream outputs
div -> engine : set_loop_num(result_num)
div -> engine : run(map, output)
loop per slot
  engine -> scatter : generator_input / check_output / update_context
  engine -> ops : executor stream run
  engine --> div : selected slice
end
div -> div : generate result + trace queues
div -> engine : clear() in reset()
@enduml
```

## 4. stream / 队列配置图

```infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title 业务队列到 multi_stream_diversity_new.conf
  desc 左侧是 DiversityMergeFunction 构造的输入键；右侧是配置中的 stream 链。
  items
    - label 业务输入
      desc loads、rule、function、effect
      children
        - label loads
          desc rec_num 个槽位队列，loads_select 消费
        - label rule
          desc rule_queue + news_rule_queue，rule_select 消费
        - label function
          desc 功能队列，function_select 消费
        - label effect
          desc 效果队列，final_select/effect_pk 消费
    - label 配置 stream
      desc loads_select → rule_select → function_select → final_select → effect_pk → merge_pk
      children
        - label loads_select
          desc executor loads_select_executor
        - label rule_select/function_select
          desc executor select_executor
        - label final_select/effect_pk
          desc effect_select_executor / effect_pk_executor
        - label merge_pk
          desc trival 本地 PK 聚合
```

## 5. 调试 checklist

```infographic sequence-timeline-simple
data
  title DiversityMerge 排障顺序
  desc 先看上下文，再看队列，再看配置 stream，最后看 trace 与成本。
  items
    - time Step 1
      label init 配置
      desc plugin/conf 是否能拿到 MultiStreamEngine pool，bind_graph_dependency 是否成功
    - time Step 2
      label exec_context 条件
      desc condition value 是否覆盖配置中的 valid/condition 表达式
    - time Step 3
      label 队列规模
      desc SIA loads_input_size/rule_input_size/function_input_size/effect_input_size
    - time Step 4
      label general_adjust
      desc 命中时 effect 使用 general_adjust_score，其他队列回填 offer_score
    - time Step 5
      label engine.run
      desc result_num、rollback、输出为空、trace queue
    - time Step 6
      label reset
      desc _diversity_context、_containers、_input_map、engine.clear 是否清理
```

## 6. Pitfalls 卡片

<div class="card-frame" style="max-width:980px;margin:18px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div class="card" style="background:#fbf9f1;border:1px solid #e7dfd1;border-radius:18px;padding:22px;color:#2f2a24;">
<div class="card-meta" style="font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#7c6853;">Pitfalls · GRG DiversityMerge</div>
<div class="card-title" style="font-size:30px;font-weight:850;letter-spacing:-.02em;margin:8px 0 14px;">业务侧决定输入语义，基础库只按配置消费键。</div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;">
<div style="border-top:4px solid #7c6853;padding-top:12px;line-height:1.65;font-size:14px;">`loads/rule/function/effect` 不是 MultiStreamEngine 内建概念，而是业务侧塞进 `input_container_map` 的 key。配置里的 pip name 必须与这些 key 或前序 stream 输出名对齐，否则症状会表现为 stream skip 或输出为空。</div>
<div style="background:#f1eadf;border-radius:14px;padding:14px;font-size:13px;line-height:1.6;">general_adjust 命中后会改 `ori_score_key`，排查 PK 分值时不要只看原始 offer_score。</div>
</div>
<div style="margin-top:14px;border-left:4px solid #7c6853;padding-left:12px;font-size:14px;line-height:1.6;">`reset()` 不只是清容器，还会在清理前写 baiplus logs；复用对象池引擎时必须确认 `engine.clear()` 已执行。</div>
</div>
</div>

## 7. 证据来源

- `src/process/diversity_merge.cpp:65-105`：初始化 MultiStreamEngine、custom_context、GraphDependency、trace phase。
- `src/process/diversity_merge.cpp:130-209`：请求级 exec_context 字段、sid 与 condition value。
- `src/process/diversity_merge.cpp:210-248`：插入上限、强插/豁免槽位与业务 context。
- `src/process/diversity_merge.cpp:251-270`：loads/rule/function/effect 输入队列构造。
- `src/process/diversity_merge.cpp:700-705`：`RidInfoVec` 转 `SelectStreamContainer` 并放入 input map。
- `src/process/diversity_merge.cpp:727-794`：`run_prepare()` 与 general_adjust 并发软打散预处理。
- `src/process/diversity_merge.cpp:796-806`：设置 loop_num 并运行 MultiStreamEngine。
- `src/process/diversity_merge.cpp:1549-1612`：结果与队列 trace 输出。
- `src/process/diversity_merge.cpp:1714-1777`：reset 清理与对象池引擎 clear。
- `conf/plugins/exec_engine/multi_stream_diversity_new.conf:1-65`：stream 链定义。
- `conf/plugins/exec_engine/multi_stream_diversity_new.conf:70-93`：select_executor 与 RuleSelectOperator。
- `conf/plugins/exec_engine/multi_stream_diversity_new.conf:225-249`：loads_select_executor。
- `conf/plugins/exec_engine/multi_stream_diversity_new.conf:324-430`：effect_select_executor 与条件分支。
