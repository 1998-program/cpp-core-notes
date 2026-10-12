# GRC ResponseProcessor：最终响应组装与观测依赖全景（2026-06-22）

> 今日主题来源：本机未发现可用 `daily-plan` 文件；按周一 fallback 选择 GRC 响应组装基础库主题，聚焦 `ResponseProcessor` 如何从图引擎依赖收敛、生成 `GRCResponse`、挂载 attachment/content，并输出响应侧 SIA 观测。已按要求设置 `BAIDU_CC_USERNAME=hujiyang`；未提供 KU URL/doc-id，平台规范需人工补充；本篇以代码库检索结果为准。

<style>.arch{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;border:1px solid #dbe3ef;border-radius:16px;padding:18px;background:#f8fafc;color:#172033}.arch h3{margin:0 0 12px;font-size:20px}.arch .layers{display:grid;gap:10px}.arch .layer{border-radius:12px;padding:12px;border:1px solid #cbd5e1;background:white}.arch .title{font-weight:700;color:#1f4e79;margin-bottom:8px}.arch .grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.arch .box{background:#eef6ff;border:1px solid #bfdbfe;border-radius:10px;padding:8px;font-size:13px}.arch .box.hot{background:#fff7ed;border-color:#fed7aa}.card{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;border-radius:18px;padding:20px;margin:18px 0;background:#fffaf3;border:1px solid #f1d8b8;color:#2b2118}.card .meta{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#9a6b34;font-weight:800}.card h3{margin:8px 0 10px;font-size:24px}.card p{line-height:1.65;margin:8px 0}.card .bar{height:5px;width:86px;background:#c67a2d;border-radius:999px;margin:12px 0}.card ul{margin:8px 0 0 18px;line-height:1.65}</style>
<div class="arch"><h3>架构全景：Graph DAG 的最后一跳是“响应聚合器”</h3><div class="layers"><div class="layer"><div class="title">上游数据层</div><div class="grid"><div class="box">Res_Dep<br/>最终候选</div><div class="box">SidInfo / RequestType<br/>场景与实验</div><div class="box">SketchyRankExtInfo<br/>粗排扩展</div><div class="box">Msv/News/Search Readlist<br/>历史行为</div></div></div><div class="layer"><div class="title">ResponseProcessor 核心层</div><div class="grid"><div class="box hot">setup(): named_dependency 绑定</div><div class="box hot">process(): emit GRCResponse</div><div class="box">attachment.actual_reqnum</div><div class="box">content.items[] 逐条填充</div></div></div><div class="layer"><div class="title">观测与下游层</div><div class="grid"><div class="box">SIA: reqnum / final_msv_cnt</div><div class="box">coverage 统计</div><div class="box">predictor_extmsg / features</div><div class="box">GRG pass-through response</div></div></div></div></div>

## 核心结论

- `ResponseProcessor` 是图执行的聚合终点：`conf/plugins/graph/response.conf:1-6` 声明 `processor: ResponseProcessor`，并只 emit `ResData`。
- 依赖面很宽：`response.cpp:28-120` 的 `ResponseContext` 保存 80+ 个图依赖指针；`response.cpp:124-219` 在 `setup()` 中逐个 `named_dependency()` 绑定。
- 响应对象在 `process()` 中创建：`response.cpp:223-246` 获取 `Res_Dep`、`SidInfo`、`CommonInfo`，并 emit `feed::grc::GRCResponse`。
- 真正写入输出 payload 从 content 容器开始：`response.cpp:370-383` 把 `actual_reqnum` 透传到 attachment，然后 `add_content()` 并设置 key 为 `SofaMicroVrIONode-microvideo_grc`。

## 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "GraphEngine" as ge
participant "response.conf" as conf
participant "ResponseProcessor.setup" as setup
participant "ResponseProcessor.process" as proc
database "GraphData ResData" as res
ge -> conf : load @vertex processor=ResponseProcessor
conf -> setup : bind Res_Dep / SidInfo / RequestType / Readlists
setup -> proc : ResponseContext stored in context.custom_context
proc -> proc : validate Res_Dep + SidInfo
proc -> res : emit GRCResponse
proc -> res : attachment.actual_reqnum + content key
proc -> proc : calculate SIA/coverage metrics
@enduml
```

## 配置与依赖信息图

```infographic list-grid-badge-card
data
  title ResponseProcessor 依赖分组
  desc response.conf 与 response.cpp 共同定义最终响应组装所需的图数据面
  items
    - label 必选主干
      desc Res_Dep / CommonInfo / SidInfo / RequestType / ResultCount
      icon mdi/source-branch
    - label 排序扩展
      desc SketchyRankExtInfo / EsCtrResult / EsInterResult / EsDiversityResult
      icon mdi/chart-line
    - label 行为历史
      desc MsvReadListInfo / NewsReadlistInfo / SearchReadlistInfo / History7D*
      icon mdi/history
    - label 场景条件
      desc UA==85、UA==87、IsFeedShortMicro、IsSlideShortDibar 控制依赖启用
      icon mdi/tune
    - label 观测输出
      desc SIA_ADD reqnum、final_msv_cnt、final_sv_cnt、coverage count
      icon mdi/pulse
```

## 关键实现拆解

### 1. 绑定阶段：配置条件与代码字段必须双向一致

`response.conf:31-56` 为 `SketchyRankExtInfo`、`EsCtrResult`、`EsInterResult`、`EsDiversityResult` 设置条件依赖；`response.cpp:131-140` 则无条件拿 named dependency 指针。这里的契约是：配置决定运行期是否有值，代码侧必须用 `value<T>()` 结果判空或只在匹配场景读取。

### 2. 响应阶段：先透传请求规模，再填充 content

`response.cpp:370-383` 的顺序很重要：先把 `common_info->request_ptr->common_info().reqnum()` 写入 `attachment.actual_reqnum` 并打 SIA，再创建 `feed_gr::Content`。这使下游能区分“请求要多少”和“最终返回多少”。

### 3. UA=87 Dibar 特化观测嵌在响应层

`response.cpp:395-415` 在 `req_type->is_dibar` 时统计前 N 个结果中小视频/短视频数量；`response.conf:193-225` 对 UA=87 追加 Interest/Dibar 相关依赖。响应层不仅是序列化终点，也承担“最终结果分布”的可观测口径。

<div class="card"><div class="meta">Pitfalls</div><h3>响应节点最容易踩的坑</h3><div class="bar"></div><ul><li>新增可选依赖时，配置注释要求额外考虑 `&& !IsFromCache`，否则快速反馈机制可能失效（见 `response.conf:36-41`）。</li><li>`ResponseContext` 字段多且有重复语义，例如 `ctr_quota` 与 `ctr_quota_result` 都绑定 `CtrQuota`，排障时要看实际使用点。</li><li>不要只看 `response.cpp`：条件是否生效由 `response.conf` 决定；代码中的空指针保护不能替代配置条件审查。</li><li>响应层加入统计要控制场景条件，否则所有 UA 都承担额外循环或依赖等待。</li></ul></div>

## 调试 checklist

```infographic list-column-done-list
data
  title ResponseProcessor 排障清单
  items
    - label 确认节点是否装载
      desc 检查 graph include 后是否进入 conf/plugins/graph/response.conf
      done true
    - label 检查主依赖是否为空
      desc Res_Dep 为空会记录 response is empty 或直接返回
      done true
    - label 对齐条件依赖
      desc response.conf 的 condition 与代码读取场景必须一致
      done true
    - label 检查 attachment 与 content
      desc actual_reqnum、content key、items_size 是下游定位入口
      done true
    - label 看 SIA 指标
      desc reqnum、final_msv_cnt、final_sv_cnt、coverage 先看响应侧
      done true
```

## 证据来源

- `src/processor/response.cpp:28-120`：`ResponseContext` 依赖字段清单。
- `src/processor/response.cpp:124-219`：`setup()` 绑定 named dependencies。
- `src/processor/response.cpp:223-246`：`process()` 入口、主依赖校验与 `GRCResponse` emit。
- `src/processor/response.cpp:370-383`：`actual_reqnum` 与 content key 写入。
- `src/processor/response.cpp:395-415`：Dibar 最终 msv/sv 统计。
- `conf/plugins/graph/response.conf:1-228`：ResponseProcessor 图配置、条件依赖与可选依赖说明。
