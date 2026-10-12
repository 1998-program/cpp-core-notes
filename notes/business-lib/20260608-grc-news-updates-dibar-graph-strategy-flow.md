# GRC 业务库：news_updates_dibar 图文速读融合链路

> 日期：2026-06-08  
> 主题来源：未发现本机 `daily-plan` 文件；按双 Markdown 工作流选择 `news_updates_dibar` 场景作为业务库主题，覆盖策略配置、图 include、召回/保量/多样性/响应链路。  
> 范围：`news_updates_dibar` strategy、graph include、queue/baoliang/diversity/response 配置、与基础 FillMeta/IFCS 正排链路的衔接点。  
> 内网文档：已按要求设置 `BAIDU_CC_USERNAME=hujiyang`；本次未提供 KU URL/doc-id，产品背景与线上实验需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div style="font-size:22px;font-weight:800;margin-bottom:6px;">news_updates_dibar：图文速读场景的召回 → 排序 → 保量 → 多样性 → 响应</div>
<div style="font-size:13px;color:#64748b;margin-bottom:16px;">业务配置把 news 与 video 队列混合进同一场景，核心风险是策略层 quota、图执行层依赖和 diversity phase_name 必须一致。</div>
<div style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">策略入口层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>scene</b><br><span style="font-size:12px;color:#64748b;">news_updates_dibar</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>fork_type</b><br><span style="font-size:12px;color:#64748b;">video/news recall</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>queue_group</b><br><span style="font-size:12px;color:#64748b;">marks + quota_ratio</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>converge</b><br><span style="font-size:12px;color:#64748b;">quota / baoliang / merge</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">Graph 执行层</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>global deps</b><br><span style="font-size:12px;color:#64748b;">ua / flow_loc / ExpInfo</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>merge</b><br><span style="font-size:12px;color:#64748b;">MergePipelineFunction</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>sketchy/rank</b><br><span style="font-size:12px;color:#64748b;">adjust + ctr rank</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>baoliang/vfs</b><br><span style="font-size:12px;color:#64748b;">NewsBaoliang → VFS</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>precise</b><br><span style="font-size:12px;color:#64748b;">精排调权</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">多样性与响应层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>split</b><br><span style="font-size:12px;color:#64748b;">Rule/Function/Effect queue</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>adjust</b><br><span style="font-size:12px;color:#64748b;">FunctionQueueAdjust</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>slot</b><br><span style="font-size:12px;color:#64748b;">SlotPreAssign</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>response</b><br><span style="font-size:12px;color:#64748b;">NewsResponseFunction</span></div>
</div>
</div>
</div>

## 2. 核心结论

- `news_updates_dibar` 策略入口定义了多个召回 fork：`short_micro_default_uniq_ucf_icf`、`video_micro_rec_new`、`news_recall`、`guanzhu_recent_ertiao`，其中 video/news 共享 `queue_group: video` 但 max_len 与 quota_ratio 不同；证据见 `conf/plugins/strategies/news_updates_dibar.conf:1-69`。
- converge 阶段静态 `num: 1400`，并启用 `peak_quota_limit` 动态 quota；保量桶显式配置 marks `421/34/662/301`，前 3 个使用 `sort_by_event_score_and_cp_score`；证据见 `conf/plugins/strategies/news_updates_dibar.conf:70-125`。
- graph 顶层通过 `$include` 拼装公共图、dynamic recall、队列、baoliang、diversity、response、billing 等子图；这意味着排障不能只看一个 conf 文件，必须从 graph include 链展开；证据见 `conf/plugins/graph/news_updates_dibar/news_updates_dibar_graph.conf:42-57`。
- `MergePipelineFunction_news_updates_dibar` 输入 `NewsSketchyRpcConfigResult`，输出 `NewsMergeResult` 与 `NewsMergeForAttractResult`，pipe_name 为 `news`、batch_size 为 2000；证据见 `conf/plugins/graph/news_updates_dibar/news_updates_dibar_vertex.conf:6-23`。
- 多样性阶段使用统一 `phase_name: diversity_list_generator`，从 `NewsPreciseAdjustResult` 分出 rule/function/effect 队列，并经过 `FunctionQueueAdjustFunction`、`TrafficPriceFunction`、`SlotPreAssignFunction`；证据见 `conf/plugins/graph/news_updates_dibar/diversity.conf:1-138`。
- 响应阶段 `NewsResponseFunction` 依赖 effect queue、function queue with price、slot pre assign、session showlist、rec_num、SidInfo 等，说明最终出图不仅依赖排序结果，还依赖多样性槽位与 session 去重状态；证据见 `conf/plugins/graph/news_updates_dibar/response.conf:1-29`。

## 3. 业务链路时序图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "request" as req
participant "scenes/strategy" as strategy
participant "news_updates_dibar_graph" as graph
participant "MergePipelineFunction" as merge
participant "rank/adjust" as rank
participant "baoliang + VFS" as bao
participant "diversity_list_generator" as div
participant "NewsResponseFunction" as resp
req -> strategy : match scene and strategy
strategy -> strategy : build fork queues + quota
strategy -> graph : select graph=news_updates_dibar
graph -> merge : include queue_vertex, pipe_name=news
merge --> rank : NewsMergeResult / NewsMergeForAttractResult
rank -> bao : NewsSketchyAdjustResult / NewsNewCtrRankResult
bao -> div : NewsPreciseAdjustResult + NewsMoveDownResult
div -> div : split RuleQueue / FunctionQueue / EffectQueue
div -> div : adjust function queue, price, slot pre-assign
div -> resp : FunctionQueueWithPrice + EffectQueue + SlotPreAssign
resp --> req : ResponseForGrg
@enduml
```

## 4. 队列与配置结构信息图

```infographic
infographic hierarchy-structure
data
  title news_updates_dibar 配置骨架
  desc strategy 定义业务队列，graph 定义执行算子，二者通过 scene/phase_name 串联
  items
    - label recall forks
      desc 四组召回入口
      children
        - label short_micro_default_uniq_ucf_icf
          desc marks 50518/50904/50090/50274/50020/50011
        - label video_micro_rec_new
          desc marks 50301, max_len 200
        - label news_recall
          desc marks 421/34/662/490, max_len 1200
        - label guanzhu_recent_ertiao
          desc marks 301, max_len 0
    - label converge
      desc quota 和保量融合
      children
        - label quota num 1400
          desc dynamic_quota peak_quota_limit
        - label baoliang queues
          desc 421/34/662/301 max_len 30
        - label merge_exec_cb
          desc default_merge
    - label graph includes
      desc 子图拼装
      children
        - label queue_vertex
          desc merge/rank/ctr/precise
        - label diversity
          desc queue split and slot assign
        - label response
          desc ResponseForGrg
```

## 5. Pitfalls 卡片

<div class="card-frame" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f3f0;padding:18px;border-radius:16px;margin:16px 0;color:#1f2937;">
<div class="card" style="background:#fffdf8;border:1px solid #ded7c9;border-radius:14px;padding:18px;display:grid;grid-template-columns:2fr 1fr;gap:16px;">
<div>
<div style="font-size:12px;font-weight:800;letter-spacing:.08em;color:#7c6853;text-transform:uppercase;">pitfall</div>
<div style="font-size:28px;font-weight:850;line-height:1.15;margin:8px 0;">只改 strategy quota，可能不会改变最终出图</div>
<div style="font-size:14px;line-height:1.65;color:#4b5563;">`news_updates_dibar` 的最终结果还经过图执行、VFS、精排、多样性拆队列、slot 预分配和响应组装。业务上看到“某队列没出”时，不能只盯 `quota_ratio`，还要检查 graph include、phase_name、依赖 data 类型和 response 输入。</div>
</div>
<div style="border-left:4px solid #7c6853;padding-left:12px;font-size:13px;line-height:1.6;color:#374151;">
<b>常见误判</b><br>1. max_len=0 不等于链路无效<br>2. quota_ratio=0 仍可能通过保量或融合逻辑影响<br>3. response 依赖的是带 price/slot 的队列<br>4. include 链缺一段会表现为下游空依赖
</div>
</div>
</div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title news_updates_dibar 排障 checklist
  items
    - label 从 scenes/strategy 确认请求命中 news_updates_dibar
      desc 先确认 scene/cond_strategy，再看具体队列
      done false
    - label 展开 graph include 链
      desc `news_updates_dibar_graph.conf` 引入公共图、queue、baoliang、diversity、response
      done false
    - label 对齐 strategy marks 与 graph queue 数据类型
      desc marks 在策略里出现，不代表每个下游 data 都有候选
      done false
    - label 检查 phase_name 一致性
      desc diversity 相关 vertex 使用 `diversity_list_generator`
      done false
    - label 检查 response 输入完整性
      desc FunctionQueueWithPrice、EffectQueue、SlotPreAssign、ResultCount、SessionShowItemList 缺一都会影响出图
      done false
```

## 7. 证据来源

- `conf/plugins/strategies/news_updates_dibar.conf:1-125`
- `conf/plugins/graph/news_updates_dibar/news_updates_dibar_graph.conf:42-57`
- `conf/plugins/graph/news_updates_dibar/news_updates_dibar_vertex.conf:6-23`
- `conf/plugins/graph/news_updates_dibar/news_updates_dibar_vertex.conf:70-99`
- `conf/plugins/graph/news_updates_dibar/diversity.conf:1-138`
- `conf/plugins/graph/news_updates_dibar/response.conf:1-29`
