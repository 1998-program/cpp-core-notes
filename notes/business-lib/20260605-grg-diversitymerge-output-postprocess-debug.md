# GRG DiversityMerge：结果生成、业务后处理与回退排障
<!-- topic-nav:start -->
> 主题：`business/diversity-merge` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260619-grg-new-diversity-merge-multistream-engine.md)
<!-- topic-nav:end -->


> 日期：2026-06-05  
> 主题来源：未发现本机 `daily-plan` 文件；按双 Markdown 工作流延续 2026-06-04 的 GRG DiversityMerge 主题，今天补齐 MultiStreamEngine 执行后的结果生成、业务实验后处理、trace/reset 与回退排障口径。  
> 范围：`DiversityMergeFunction::process()` 中输入容器转换、`run_prepare()`、general_adjust、`set_loop_num()`/`run()`、`output.next_all()`、结果 push、后处理实验、trace/reset。  
> 内网文档：已按要求设置 `BAIDU_CC_USERNAME=hujiyang` 并确认 `ku` 可用；但本次任务未提供 KU URL/doc-id，本文以代码库检索结果为主，业务策略口径需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div style="font-size:22px;font-weight:800;margin-bottom:6px;">DiversityMerge 运行闭环：业务队列 → 引擎输出 → 结果后处理</div>
<div style="font-size:13px;color:#64748b;margin-bottom:16px;">业务侧决定输入队列、实验条件和结果落盘；基础库负责多流选槽、回退和上下文更新。</div>
<div style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">输入与上下文层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>exec_context</b><br><span style="font-size:12px;color:#64748b;">logid/cuid/ua/sids/field keys</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>conditions</b><br><span style="font-size:12px;color:#64748b;">soft rule / searchc / set2set</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>DiversityContext</b><br><span style="font-size:12px;color:#64748b;">insert limit / ignore indexes</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>queue map</b><br><span style="font-size:12px;color:#64748b;">loads/rule/function/effect</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">引擎执行层</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>wrap</b><br><span style="font-size:12px;color:#64748b;">RidInfoVec → SelectStreamContainer</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>run_prepare</b><br><span style="font-size:12px;color:#64748b;">创建 stream unit</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>general_adjust</b><br><span style="font-size:12px;color:#64748b;">8 bthreads 预打分</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>run</b><br><span style="font-size:12px;color:#64748b;">按 result_num 选槽</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>output</b><br><span style="font-size:12px;color:#64748b;">SelectStreamContainer next_all</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">结果与观测层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>push result</b><br><span style="font-size:12px;color:#64748b;">push_item_to_result</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>business exp</b><br><span style="font-size:12px;color:#64748b;">attention / loads_next / client rerank</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>trace</b><br><span style="font-size:12px;color:#64748b;">phase + queue trace + SIA</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>reset</b><br><span style="font-size:12px;color:#64748b;">engine/context/container clear</span></div>
</div>
</div>
</div>

## 2. 核心结论

- 业务输入在进入引擎前会把 `_input_map` 中的 `RidInfoVec` reinterpret 为 `std::vector<DynamicStruct*>`，再包装为 `SelectStreamContainer`；证据见 `src/process/diversity_merge.cpp:696-705`。
- `run_prepare(input_container_map)` 是业务队列与基础库配置对齐的关键检查点：pip 输入键缺失会在基础库 create 阶段失败；业务侧此处只记录 fatal；证据见 `src/process/diversity_merge.cpp:725-729` 与 `exec-engine/src/multi_stream_engine.h:68-80`。
- general_adjust 在主引擎 run 前执行，命中 `is_open_dnn_soft_rule && !is_searchc_immersive_request && ua != 87 && enable_general_adjust` 时，对 effect 队列分 8 片并发跑软规则；证据见 `src/process/diversity_merge.cpp:730-774`。
- general_adjust 完成后，effect 使用 `div_res_score` 写入 `general_adjust_score`，function/loads/rule 使用 `offer_score` 写入，并把 `_ori_score_key` 切到 `_general_adjust_score_key`；证据见 `src/process/diversity_merge.cpp:776-793`。
- 业务侧通过 `result_num = max(reqnum, _result_num)` 创建输出容器，并调用 `_diversity_engine->set_loop_num(result_num)` 决定最多选槽数；证据见 `src/process/diversity_merge.cpp:796-803`。
- 引擎返回后，业务侧从 `output.next_all()` 取出被选中的 `DynamicStruct`，逐条转回 `RidTmpInfo` 并 `push_item_to_result()`；证据见 `src/process/diversity_merge.cpp:808-850`。
- 多个后处理实验会在引擎输出后继续改写结果或补充旁路输出，例如 `client_rerank_topn_vec` 与 effect 随机交换实验；证据见 `src/process/diversity_merge.cpp:853-898`。

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
database "_input_map" as imap
database "input_container_map" as cmap
participant "SoftRule Operators" as soft
participant "MultiStreamEngine" as engine
database "output" as output
database "div_result" as result
req -> div : process()
div -> ctx : set logid/cuid/ua/sids/field keys
div -> ctx : add_condition_value(...)
div -> scatter : set insert/ignore/function context
div -> imap : read_queue(loads/rule/function/effect)
div -> cmap : RidInfoVec -> SelectStreamContainer
alt loads_next_tq exemption
  div -> scatter : collect skip_hard_rule_rids
end
div -> engine : run_prepare(cmap)
alt general_adjust enabled
  div -> soft : get_runable_operators(batch_rule[*].valid)
  loop 8 bthreads
    div -> soft : run_soft_rule(effect shard)
  end
  div -> ctx : _ori_score_key = general_adjust_score
end
div -> engine : set_loop_num(max(reqnum, result_num_conf))
div -> engine : run(cmap, output)
engine --> div : selected slices
output -> div : next_all()
loop each selected DynamicStruct
  div -> result : push_item_to_result(RidTmpInfo)
end
alt business post experiments
  div -> result : client rerank topn / effect swap / logs
end
@enduml
```

## 4. 队列到结果结构信息图

```infographic hierarchy-structure
data
  title DiversityMerge 结果链路对象树
  desc 从业务队列到引擎输出，再到最终 DiversityMergeResult。
  items
    - label _input_map
      desc loads/rule/function/effect 四类业务候选
      children
        - label loads
          desc rec_num 个槽位队列，支持 loads 豁免/强插
        - label rule
          desc hard rule 与 news rule 候选
        - label function
          desc 功能队列，单容器
        - label effect
          desc 效果队列，general_adjust 主要处理对象
    - label input_container_map
      desc key 与 multi_stream_diversity_new.conf 中 pip name 对齐
      children
        - label SelectStreamContainer
          desc 指向 RidInfoVec 的 DynamicStruct 视图
    - label output
      desc 引擎每槽提交的最终 stream 输出
      children
        - label div_result
          desc push_item_to_result 写业务结果
```

## 5. 配置 stream 与业务输入对齐

```infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title 输入键与配置 stream 的对应关系
  desc 排查 run_prepare 失败时，先看左侧 key 是否能被右侧 pip 消费。
  items
    - label 业务输入 key
      desc DiversityMergeFunction 构造
      children
        - label loads
          desc _input_map[loads]，rec_num 个容器
        - label rule
          desc _input_map[rule]，rec_num 个容器
        - label function
          desc _input_map[function]，单容器
        - label effect
          desc _input_map[effect]，单容器
    - label 配置 pip / stream
      desc multi_stream_diversity_new.conf
      children
        - label loads_select
          desc pip name loads，输出 stream loads_select
        - label rule_select
          desc pip name rule，输出 stream rule_select
        - label function_select
          desc pip name function，输出 stream function_select
        - label final_select → effect_pk → merge_pk
          desc effect 先软规则/PK，最后 merge_pk 本地聚合
```

## 6. Pitfalls 卡片

<div class="card-frame" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:18px 0;max-width:980px;">
<div class="card" style="background:#f4f8f6;border:1px solid #d7e5dd;border-radius:18px;padding:22px;color:#1f332b;">
<div style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#2d6a4f;margin-bottom:8px;">Pitfall · DiversityMerge Output</div>
<div style="font-size:30px;font-weight:850;letter-spacing:-.02em;margin-bottom:10px;">引擎输出不是最终业务结果</div>
<div style="height:5px;width:88px;background:#2d6a4f;border-radius:4px;margin:10px 0 16px 0;"></div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:16px;align-items:start;">
<div style="font-size:15px;line-height:1.65;color:#28443a;">`MultiStreamEngine::run()` 只把选中的 DynamicStruct 切片推入 `output`。DiversityMerge 后续还会执行 `push_item_to_result()`、attention/load-next 日志、client rerank topn 输出、effect 随机交换等业务逻辑；定位线上排序差异时不能只看 stream 输出。</div>
<div style="border-top:3px solid #2d6a4f;background:#fbfefc;border-radius:12px;padding:12px;font-size:13px;line-height:1.55;color:#355448;"><b>调试抓手</b><br>对比 `output.next_all()`、`div_result`、`client_rerank_topn_vec` 与后处理实验命中 SID。</div>
</div>
</div>
</div>

## 7. 调试 checklist

```infographic list-column-done-list
data
  title DiversityMerge 回退与结果排障 checklist
  items
    - label 检查 input_container_map 键名
      desc loads/rule/function/effect 必须与 multi_stream_diversity_new.conf 中 pip name 对齐
      done false
    - label run_prepare 失败先看配置输入链
      desc 缺 pip 输入或 stream name 重复会在基础库 create 阶段报错
      done false
    - label general_adjust 排查先看四个条件
      desc is_open_dnn_soft_rule、非 searchc immersive、ua != 87、enable_general_adjust
      done false
    - label 分数异常要看 ori_score_key 是否切换
      desc general_adjust 后 effect 用 div_res_score，其他队列用 offer_score
      done false
    - label 结果数异常要看 result_num
      desc max(reqnum, _result_num) 决定 output 容量与 engine loop_num
      done false
    - label 排序差异要纳入后处理实验
      desc client rerank topn 与 effect swap 都发生在 engine.run 之后
      done false
```

## 8. 证据来源

- `src/process/diversity_merge.cpp:65-105`：初始化引擎、业务 context 与 GraphDependency 绑定。
- `src/process/diversity_merge.cpp:130-209`：exec_context 字段与 condition 注入。
- `src/process/diversity_merge.cpp:251-270`：loads/rule/function/effect 输入队列结构。
- `src/process/diversity_merge.cpp:696-705`：`RidInfoVec` 转 `SelectStreamContainer`。
- `src/process/diversity_merge.cpp:725-803`：`run_prepare()`、general_adjust、`set_loop_num()` 与 `run()`。
- `src/process/diversity_merge.cpp:808-898`：引擎输出转业务结果及后处理实验。
- `conf/plugins/exec_engine/multi_stream_diversity_new.conf:8-65`：stream/pip 拓扑。
