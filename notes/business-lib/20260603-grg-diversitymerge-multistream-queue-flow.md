# GRG DiversityMerge：多队列输入、MultiStreamEngine 调权与结果归并
<!-- topic-nav:start -->
> 主题：`business/diversity-merge` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260619-grg-new-diversity-merge-multistream-engine.md)
<!-- topic-nav:end -->


> 日期：2026-06-03  
> 主题来源：未发现本机 daily-plan 文件；按双 Markdown 工作流补齐业务库主题，选择 `feeda-mv-grg` 中 `DiversityMergeFunction` 如何接入基础库 `MultiStreamEngine` 完成多队列多样性合并。  
> 服务：`feeda-mv-grg`  
> 范围：`loads/rule/function/effect` 四类输入队列读取、AB 条件注入、位置无关软打散预处理、`MultiStreamEngine::run()` 执行、结果写回、topn 端重排辅助输出与多维日志指标。  
> 内网文档：当前环境未发现可用 `ku` 命令；本文以代码库检索结果替代，业务策略口径需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div style="font-size:22px;font-weight:850;margin-bottom:6px;">DiversityMerge 业务全景：队列 → 上下文 → 引擎 → 结果</div>
<div style="font-size:13px;color:#64748b;margin-bottom:16px;">DiversityMergeFunction 是业务桥：把 GRG GraphData 中的候选、历史、AB、模型特征转换成 MultiStreamEngine 可消费的动态结构与条件。</div>
<div style="display:grid;grid-template-columns:1.1fr 1.6fr 1.1fr;gap:12px;align-items:stretch;">
<div style="background:#fff;border-radius:14px;border-top:5px solid #3d5a80;padding:14px;">
<div style="font-weight:800;margin-bottom:10px;">输入队列</div>
<div style="display:grid;gap:8px;font-size:13px;">
<div style="background:#eef2f7;border-radius:10px;padding:9px;"><b>loads</b><br>强插/兜底资源，按槽位组织</div>
<div style="background:#eef2f7;border-radius:10px;padding:9px;"><b>rule + news_rule</b><br>规则队列，rec_num 阈值后启用</div>
<div style="background:#eef2f7;border-radius:10px;padding:9px;"><b>function</b><br>功能队列，一组全局候选</div>
<div style="background:#eef2f7;border-radius:10px;padding:9px;"><b>effect</b><br>效果队列，软打散与 PK 主体</div>
</div>
</div>
<div style="background:#fff;border-radius:14px;border-top:5px solid #2d6a4f;padding:14px;">
<div style="font-weight:800;margin-bottom:10px;">DiversityMergeFunction 主流程</div>
<div style="display:grid;grid-template-columns:repeat(2,1fr);gap:8px;font-size:13px;">
<div style="background:#edf7f2;border-radius:10px;padding:9px;"><b>init()</b><br>取 engine pool，bind graph dependency</div>
<div style="background:#edf7f2;border-radius:10px;padding:9px;"><b>prepare context</b><br>logid/cuid/ua/sid/condition keys</div>
<div style="background:#edf7f2;border-radius:10px;padding:9px;"><b>read_queue()</b><br>构造 input_map 与输入统计</div>
<div style="background:#edf7f2;border-radius:10px;padding:9px;"><b>general adjust</b><br>bthread 并发预跑位置无关软规则</div>
<div style="background:#edf7f2;border-radius:10px;padding:9px;"><b>engine run</b><br>result_num loop，多 stream 合并</div>
<div style="background:#edf7f2;border-radius:10px;padding:9px;"><b>generate result</b><br>DynamicStruct 转 RidTmpInfo 写出</div>
</div>
</div>
<div style="background:#fff;border-radius:14px;border-top:5px solid #b45309;padding:14px;">
<div style="font-weight:800;margin-bottom:10px;">输出与观测</div>
<div style="display:grid;gap:8px;font-size:13px;">
<div style="background:#fff7ed;border-radius:10px;padding:9px;"><b>div_result</b><br>最终多样性合并结果</div>
<div style="background:#fff7ed;border-radius:10px;padding:9px;"><b>client_rerank_topn_vec</b><br>端重排 topn 辅助结果</div>
<div style="background:#fff7ed;border-radius:10px;padding:9px;"><b>SIA / GRG_DETAIL_LOG</b><br>输入率、输出率、队列分布</div>
<div style="background:#fff7ed;border-radius:10px;padding:9px;"><b>Dapper cost</b><br>由基础引擎采样上报</div>
</div>
</div>
</div>
</div>

## 2. 核心结论

- `init()` 中通过配置字段拿到 `plugin/conf`，从 `EngineMgr` 获取 `MultiStreamEngine` 实例，并调用 `bind_graph_dependency()` 把 graph 依赖绑定到引擎算子；证据见 `diversity_merge.cpp:65-92`。
- `process()` 先将 logid、cuid、ua、sid、结果字段 key、trace 上下文写入 `ExecContext`，这是后续 operator 条件与日志的共同上下文；证据见 `diversity_merge.cpp:130-168`、`diversity_merge.cpp:178-205`。
- 输入不是一个候选列表，而是四类队列：`loads`、`rule`、`function`、`effect`；其中 `loads/rule` 按 `rec_num` 个槽位建 vector，`function/effect` 只有一个全局队列；证据见 `diversity_merge.cpp:251-270`。
- 业务侧可在引擎前做“位置无关软打散”预处理：把 effect 队列分 8 份，用 `bthread_async` 并发跑 `run_soft_rule()`，再把 `div_res_score` 写成 `general_adjust_score`，作为后续 PK 基础分；证据见 `diversity_merge.cpp:730-794`。
- 真正合并由 `_diversity_engine->run(input_container_map, &output)` 完成，业务侧只负责把 `output.next_all()` 转成 `RidTmpInfo` 并 `push_item_to_result()`；证据见 `diversity_merge.cpp:796-851`。
- 输出后继续做业务实验分支，例如端重排 topn 辅助结果、effect 随机扰动/advantage 排序、输入输出比例日志；证据见 `diversity_merge.cpp:853-923`、`diversity_merge.cpp:925-981`。

## 3. 调用链时序图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor GRG_Request as req
participant "DiversityMergeFunction" as dm
participant "ExecContext" as ec
participant "InputMap" as im
participant "GeneralAdjustRules" as rules
participant "MultiStreamEngine" as mse
participant "Output Vector" as out
req -> dm : process()
dm -> out : emit diversity result channels
dm -> ec : set logid/cuid/ua/sids/field keys
dm -> ec : add_condition_value(ua, AB flags, feature flags)
dm -> im : create loads/rule/function/effect containers
dm -> im : read_queue(loads/rule/news_rule/function/effect)
alt open DNN soft rule and enable_general_adjust
  dm -> rules : get runnable batch soft rules
  loop 8 bthreads
    dm -> rules : run_soft_rule(effect sublist)
  end
  dm -> ec : _ori_score_key = general_adjust_score
end
dm -> mse : run_prepare(input_container_map)
dm -> mse : set_loop_num(max(rec_num, result_num))
dm -> mse : run(input_container_map, output)
mse --> dm : SelectStreamContainer output
dm -> out : for each ds push_item_to_result(RidTmpInfo)
dm -> out : optional client_rerank_topn_vec
dm -> dm : input/output rate logs and SIA metrics
@enduml
```

## 4. 队列与配置结构信息图

```infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title DiversityMerge 四类队列
  desc 四类输入的组织方式不同，决定了后续 stream 的选择优先级与槽位行为。
  items
    - label 插入类队列
      desc 按槽位组织，决定是否抢占当前推荐位
      children
        - label loads
          desc 强插/兜底，支持 loads_select 与 ignore loads slot
        - label rule
          desc 规则插入，包含 rule_queue 与 news_rule_queue
    - label 排序类队列
      desc 全局候选列表，主要用于功能选择和效果 PK
      children
        - label function
          desc 功能队列，rec_num 阈值满足后读取
        - label effect
          desc 效果队列，支持软打散预调权和最终 PK
```

## 5. 关键路径 checklist

```infographic list-column-done-list
data
  title 排查 DiversityMerge 不出结果 / 结果异常
  desc 先确认输入规模，再确认引擎 loop，再确认结果转换与实验分支。
  items
    - label reqnum 与阈值
      desc rec_num <= recnum_threshold_for_insert 时 rule/function 不读入
      done true
    - label input_container_map
      desc loads/rule/function/effect 四个 key 必须与 multi_stream_diversity.conf 中 pip name 对齐
      done true
    - label AB condition
      desc ua、is_open_dnn_soft_rule、enable_general_adjust 等会影响 operator valid
      done true
    - label general_adjust
      desc 并发软规则会改 div_res_score/general_adjust_score，注意线程内共享 operator 的状态安全
      done false
    - label engine output
      desc output.next_all() 为空会导致业务结果为空或引擎返回异常
      done true
    - label post rerank
      desc topn、随机扰动、advantage 排序属于引擎后处理，需与主引擎区分
      done true
```

## 6. Pitfalls 卡片

<div class="card-frame" style="max-width:980px;margin:18px 0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<div class="card" style="background:#f4f8f6;border:1px solid #d7e6df;border-radius:18px;padding:22px;color:#1f352b;">
<div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#2d6a4f;">Pitfalls · DiversityMerge</div>
<div style="font-size:30px;font-weight:850;letter-spacing:-.02em;margin:8px 0 14px;">不要把“队列没有读入”和“规则没有命中”混为一谈。</div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;">
<div style="border-top:4px solid #2d6a4f;padding-top:12px;line-height:1.65;font-size:14px;">`rec_num` 小于等于阈值时，`rule_queue` 与 `function_queue` 根本不会进入 `_input_map`。此时去查 `RuleSelectOperator` 为什么没命中会走偏，应该先看 `recnum_threshold_for_insert`、`change_req_num` 实验与输入计数日志。</div>
<div style="background:#eaf3ef;border-radius:14px;padding:14px;font-size:13px;line-height:1.6;">`general_adjust_rule_operators` 在 8 个 bthread 中复用同一批 operator 指针；若规则内部维护请求级可变状态，需要特别确认是否线程安全。</div>
</div>
<div style="margin-top:14px;border-left:4px solid #2d6a4f;padding-left:12px;font-size:14px;line-height:1.6;">`loads/rule/function/effect` 是业务队列名，同时也是引擎 pip 输入 key；改配置名或新增 stream 时必须同步业务侧输入 map。</div>
</div>
</div>

## 7. 证据来源

- `src/process/diversity_merge.cpp:65-92`：初始化引擎、绑定 graph dependency。
- `src/process/diversity_merge.cpp:130-168`：写入 logid/cuid/ua/sids 与 trace 上下文。
- `src/process/diversity_merge.cpp:178-205`：注入 AB 与请求条件到 `ExecContext`。
- `src/process/diversity_merge.cpp:251-270`：构造四类输入队列并读取 queue。
- `src/process/diversity_merge.cpp:431-453`：按 `div_index` 还原 GRC response 顺序。
- `src/process/diversity_merge.cpp:455-480`：loads 兜底资源预存与去重。
- `src/process/diversity_merge.cpp:725-729`：调用 `run_prepare()`。
- `src/process/diversity_merge.cpp:730-794`：位置无关软打散并发预处理。
- `src/process/diversity_merge.cpp:796-851`：执行 `MultiStreamEngine::run()` 并生成结果。
- `src/process/diversity_merge.cpp:853-923`：端重排 topn 与后处理实验分支。
- `src/process/diversity_merge.cpp:925-981`：输入/输出比例与队列分布日志。
- `conf/plugins/exec_engine/multi_stream_diversity.conf:8-65`：stream 顺序与 pip/executor 映射。
- `conf/plugins/exec_engine/multi_stream_diversity.conf:70-123`：select executor 与规则配置入口。
