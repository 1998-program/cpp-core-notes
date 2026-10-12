# GRG news_updates_dibar：NewDiversityMerge 到 NewResponseFunction 的最终出流链路（2026-06-22）

> 今日主题来源：本机未发现可用 `daily-plan` 文件；按周一 fallback 选择 `news_updates_dibar` 业务链路，聚焦 GRG 如何把 GRC 返回的 loads/rule/function/effect 队列，经 MultiStreamEngine 多流打散后，由 `NewResponseFunction` 输出最终 `GRGResponse`。已按要求设置 `BAIDU_CC_USERNAME=hujiyang`；未提供 KU URL/doc-id，产品策略口径需人工补充；本篇以代码库检索结果为准。

<style>.arch{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;border:1px solid #dbe3ef;border-radius:16px;padding:18px;background:#f8fafc;color:#172033}.arch h3{margin:0 0 12px;font-size:20px}.arch .layers{display:grid;gap:10px}.arch .layer{border-radius:12px;padding:12px;border:1px solid #cbd5e1;background:white}.arch .title{font-weight:700;color:#1f4e79;margin-bottom:8px}.arch .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.arch .box{background:#eef6ff;border:1px solid #bfdbfe;border-radius:10px;padding:8px;font-size:13px}.arch .box.hot{background:#fff7ed;border-color:#fed7aa}.card{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;border-radius:18px;padding:20px;margin:18px 0;background:#fffaf3;border:1px solid #f1d8b8;color:#2b2118}.card .meta{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#9a6b34;font-weight:800}.card h3{margin:8px 0 10px;font-size:24px}.card p{line-height:1.65;margin:8px 0}.card .bar{height:5px;width:86px;background:#c67a2d;border-radius:999px;margin:12px 0}.card ul{margin:8px 0 0 18px;line-height:1.65}</style>
<div class="arch"><h3>架构全景：news_updates_dibar 的“打散 → 响应”闭环</h3><div class="layers"><div class="layer"><div class="title">召回与特征准备</div><div class="grid"><div class="box">GrcRecallFunction<br/>PassThoughResponse / Reqnum</div><div class="box">RequestFeatureFunction</div><div class="box">UserFeatureFunction</div><div class="box">ReadlistFillMetaDone</div></div></div><div class="layer"><div class="title">多流打散</div><div class="grid"><div class="box hot">NewDiversityMergeFunction</div><div class="box">loads / rule / function / effect</div><div class="box">news_diversity.conf</div><div class="box">DiversityMergeResult</div></div></div><div class="layer"><div class="title">最终响应</div><div class="grid"><div class="box hot">NewResponseFunction</div><div class="box">Swap ContentItem</div><div class="box">decorate_ext_msg</div><div class="box">GRGResponse.ref(PassThoughResponse)</div></div></div></div></div>

## 核心结论

- `news_updates_dibar/vertex.conf:49-175` 的 `GrcRecallFunction` 一次性产出 `PassThoughResponse`、`Reqnum`、四类队列和多种用户/内容偏好数据，是后续打散和响应的输入根。
- `news_updates_dibar/vertex.conf:221-275` 把 `NewDiversityMergeFunction` 作为 `@engine_vertex`，绑定 `multi_stream_engine_plugin` 与 `news_diversity.conf`，产出 `DiversityMergeResult`。
- `news_diversity.conf:10-65` 定义 5 条 stream：`loads_select`、`rule_select`、`function_select`、`final_select`、`effect_pk`，再由 `merge_pk` 做最终 PK。
- `new_response_function.cpp:52-130` 从 `DiversityMergeResult` 按 reqnum 截断输出，`Swap` 内容项、解析/重写 predictor extmsg，并应用 decorate 策略。

## 业务调用链

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "GrcRecallFunction" as grc
participant "NewDiversityMergeFunction" as div
participant "MultiStreamEngine" as mse
participant "NewResponseFunction" as resp
database "PassThoughResponse" as ptr
grc -> grc : emit loads/rule/function/effect queues
grc -> ptr : emit PassThoughResponse + Reqnum
div -> div : read_queue() build input_map
div -> mse : run_prepare(input_container_map)
div -> mse : run(input_container_map, output)
mse --> div : SelectStreamContainer output
div -> div : push_item_to_result => DiversityMergeResult
resp -> ptr : add_content key SofaMicroVrIONode-microvideo_grc
resp -> resp : Swap ContentItem + rewrite predictor_extmsg
resp -> ptr : response.ref(PassThoughResponse)
@enduml
```

## 配置结构信息图

```infographic sequence-timeline-simple
data
  title news_updates_dibar 关键配置路径
  items
    - time vertex 1
      label GrcRecallFunction
      desc 产出 PassThoughResponse、Reqnum、loads/rule/news/function/effect 队列
    - time vertex 2
      label NewDiversityMergeFunction
      desc plugin=multi_stream_engine_plugin conf=news_diversity.conf result_num=4
    - time exec
      label news_diversity.conf
      desc loads_select/rule_select/function_select/final_select/effect_pk/merge_pk
    - time response
      label NewResponseFunction
      desc 依赖 DiversityMergeResult 与 PassThoughResponse，最终 emit GRGResponse
```

## 关键链路拆解

### 1. GRC 召回输出不只是候选，还定义响应壳

`vertex.conf:49-175` 中 `GrcRecallFunction` 同时 emit `_pass_though_response`、`_reqnum`、`_loads_queue`、`_rule_queue`、`_function_queue`、`_effect_queue`。这说明 GRG 的最终响应不是从空对象构造，而是在 GRC pass-through response 上继续添加 content 和 extmsg。

### 2. MultiStreamEngine 的业务形态由配置决定

`news_diversity.conf:10-65` 把 loads/rule/function/effect 分为不同 stream，其中 `function_select` 和 `final_select` 都走分值选择，`merge_pk` 再把不同 stream 的输出合并。`news_diversity.conf:70-180` 中硬规则依赖 `ShowItemListWithMeta`、`NewsReadlist`、`SidInfo`，因此“展现去重/历史阅读/实验 sid”是 news_updates_dibar 打散的基础约束。

### 3. NewDiversityMerge 的输入维度与回退边界

`new_diversity_merge.cpp:203-220` 先为 `loads/rule/function/effect` 建立 `_input_map`；当 `rec_num <= insert_recnum_threshold` 时，不读 rule/function 队列，只保留 loads/effect 主链路。`new_diversity_merge.cpp:725-803` 先 `run_prepare()`，再运行位置无关软打散，最后 `run()` 引擎。

### 4. NewResponseFunction 重写 predictor_extmsg

`new_response_function.cpp:66-121` 对每个输出 item：`Swap` 原始 `ContentItem`、解析 `predictor_extmsg_bytes` 或 base64 `predictor_extmsg`、叠加 `sample_dnn_q`、执行 decorate 策略，最后 `SerializeToString` + base64 写回 `mutable_ext()->set_predictor_extmsg()`。这一步是“打散结果”转为“可被下游解释的响应”的关键。

<div class="card"><div class="meta">Pitfalls</div><h3>news_updates_dibar 响应链路的排障重点</h3><div class="bar"></div><ul><li>如果最终条数不足，不要只看响应函数；先看 `DivSuccSize`、`loads_input_size`、`effect_input_size` 与 `div_succ_size`。</li><li>`insert_recnum_threshold` 会让 rule/function 插入在小 reqnum 下直接关闭，表现像“配置不生效”。</li><li>`predictor_extmsg` 有 bytes/snappy 与 base64 两条路径，解析失败只打 warning，容易被忽略。</li><li>`ContentItem::Swap` 会搬走原对象内容；响应后继续读取原 `RidTmpInfo._content_item` 时要确认是否仍需要字段。</li></ul></div>

## 调试 checklist

```infographic list-column-done-list
data
  title GRG news_updates_dibar 出流排障清单
  items
    - label 看 GrcRecallFunction 输出
      desc PassThoughResponse、Reqnum、四类队列是否都被 emit
      done true
    - label 看 MultiStreamEngine 配置
      desc news_diversity.conf 的 stream/pip/executor 是否与 vertex conf 一致
      done true
    - label 看打散输入输出 SIA
      desc loads_input_size/effect_input_size/div_succ_size/output_news_rate
      done true
    - label 看 extmsg 解析 warning
      desc predictor_extmsg_bytes snappy 或 predictor_extmsg base64 是否解析失败
      done true
    - label 看响应 items_size
      desc content_ptr->items_size() < reqnum 时回查 DiversityMergeResult
      done true
```

## 证据来源

- `conf/plugins/graph/news_updates_dibar/vertex.conf:49-175`：`GrcRecallFunction` 输出响应壳、reqnum 与队列。
- `conf/plugins/graph/news_updates_dibar/vertex.conf:221-275`：`NewDiversityMergeFunction` engine vertex 配置。
- `conf/plugins/exec_engine/news_diversity.conf:10-65`：多 stream 与 merge_pk 拓扑。
- `conf/plugins/exec_engine/news_diversity.conf:70-180`：select executor、硬规则依赖与条件。
- `src/process/new_diversity_merge.cpp:43-76`：引擎池获取、上下文绑定、GraphDependency 绑定。
- `src/process/new_diversity_merge.cpp:203-220`：四类队列读入与插入阈值控制。
- `src/process/new_diversity_merge.cpp:725-803`：run_prepare、软打散、engine run。
- `src/process/new_response_function.cpp:52-130`：输出 items、extmsg 解析/重写与 decorate。
- `src/process/new_response_function.cpp:151-189`：`GRGResponse` ref、SIA 输出与最终返回。
