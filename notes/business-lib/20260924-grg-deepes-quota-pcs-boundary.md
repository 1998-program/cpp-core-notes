# 业务代码理解：GRG `DeepesQuotaPcsFunction` 配额生成与 Graph 数据边界

> 主题：PCS 返回的 `MicrovideoQuotaResult` 如何被转换成五类内容配额比例，并通过 graph vertex 的 emit/depend 契约交给后续业务节点。
> 主题选择说明：仓库内 daily-plan 文件为 2026-05-29 周计划，已落后于本次执行日 2026-09-24；近期 business-lib 已覆盖 actual_reqnum、response assembly、DiversityMerge、FillMeta、成本热点等主题。本次按 fallback 规则选取计划候选池中的 `DeepesQuotaInfo` 主题，避开已覆盖的 DiversityMerge 细节，聚焦 PCS 适配器与 Graph 数据契约。KU/业务上下文未逐篇读取，需人工补充；以下以源码和配置为主。

## 0. 业务全景图

<style>.quota-arch{font-family:Inter,Arial,sans-serif;border:1px solid #d8dee9;background:#f8fafc;border-radius:8px;padding:18px;margin:18px 0;color:#243041}.quota-arch h3{margin:0 0 12px;font-size:20px}.quota-arch h4{margin:14px 0 8px;font-size:14px;color:#1f3b57}.quota-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.quota-box{border:1px solid #c9d3e3;border-left:4px solid #2d6a4f;background:#fff;border-radius:6px;padding:10px;min-height:90px}.quota-box b{display:block;font-size:13px;color:#1a2f44}.quota-box span{display:block;font-size:12px;line-height:1.5;color:#526071;margin-top:4px}.quota-pcs{border-left-color:#3d5a80}.quota-contract{border-left-color:#9a5b28}.quota-risk{border-left-color:#b8432f}.quota-note{font-size:12px;line-height:1.5;color:#526071;margin-top:10px}@media(max-width:820px){.quota-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.quota-grid{grid-template-columns:1fr}}</style><div class="quota-arch"><h3>DeepES 配额在 GRG graph 中的边界</h3><h4>输入、转换、输出、消费</h4><div class="quota-grid"><div class="quota-box quota-pcs"><b>PCS map</b><span>`PcsResultMap` 按 key 查找 `MicrovideoQuota`，缺失时保持默认值并正常返回。</span></div><div class="quota-box quota-pcs"><b>MicrovideoQuotaResult</b><span>要求 sv/mv/dt/dj/heji 五个 repeated 字段都非空，当前实现各取 index 0。</span></div><div class="quota-box quota-contract"><b>DeepesQuotaInfo</b><span>五个 float ratio 通过 `GRAPH_FUNCTION_EMIT_DATA` 写入 graph 数据流。</span></div><div class="quota-box quota-contract"><b>short_micro_video vertex</b><span>配置把 `_deepes_quota_info` 声明为 `DeepesQuotaInfo`，供后续节点按 data contract 依赖。</span></div></div><h4>缺失数据语义</h4><div class="quota-grid"><div class="quota-box"><b>缺少 PCS key</b><span>返回 `ERR_OK`，不覆盖默认比例。</span></div><div class="quota-box"><b>结构不完整</b><span>五类列表任一为空时不写入任何新比例，保留 reset 后默认值。</span></div><div class="quota-box quota-risk"><b>只取首元素</b><span>重复字段中的 index 1+ 不会参与当前计算，需确认 PCS 约定确实是单值列表。</span></div><div class="quota-box quota-risk"><b>消费边界需继续核对</b><span>当前快照能确认生产和 graph wiring，但没有直接命中后续消费字段的源码位置。</span></div></div><div class="quota-note">这不是“模型直接改排序分数”的函数；它是 PCS 结果到 graph typed data 的适配器，真正的配额消费发生在后续节点或 engine 逻辑中。</div></div>

## 1. 输入契约：PCS 结果按 key 查找

`src/process/deepes_quota_pcs_function.cpp:15-37` 定义 `DeepesQuotaPcsFunction`。函数依赖 `_request`、`_sid_info` 和 `_pcs_result`，其中 `_pcs_result` 是 `std::unordered_map<std::string, const ParameterData*>`。处理开始先做三个输入 null check，再 emit 输出对象并调用 `clear()`，随后查找固定 key `MicrovideoQuota`。

如果 map 中不存在该 key，函数直接返回 `ERR_OK`。这意味着“PCS 没有返回配额”在当前实现中是正常降级，不是错误；由于 `clear()` 之后 output object 已恢复默认比例，后续节点拿到的是默认配额，而不是未初始化内存。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
skinparam sequenceMessageAlign center
title DeepesQuotaPcsFunction PCS 到 Graph 数据流
participant "Graph runtime" as Graph
participant "DeepesQuotaPcsFunction" as Fn
participant "PcsResultMap" as Map
participant "ParameterData" as Data
participant "DeepesQuotaInfo" as Out
participant "short_micro_video graph" as Next
Graph -> Fn : process()
Fn -> Fn : CHECK _request/_sid_info/_pcs_result
Fn -> Out : emit() + clear()
Fn -> Map : find("MicrovideoQuota")
alt key missing
  Map --> Fn : end()
  Fn --> Graph : ERR_OK + defaults
else key exists
  Map --> Fn : ParameterData*
  Fn -> Data : has_microvideo_quota_result()
  alt result missing or five vectors incomplete
    Data --> Fn : false
    Fn --> Graph : ERR_OK + defaults
  else five vectors available
    Data --> Fn : MicrovideoQuotaResult
    Fn -> Out : copy sv/mv/dt/dj/heji [0]
    Fn --> Graph : ERR_OK + typed output
  end
end
Graph -> Next : _deepes_quota_info : DeepesQuotaInfo
@enduml
```

## 2. 输出契约：五类 ratio 与默认值

`src/data/base.h:1248-1261` 的 `DeepesQuotaInfo` 是一个轻量结构，包含 `sv_quota_ratio`、`mv_quota_ratio`、`dt_quota_ratio`、`dj_quota_ratio`、`heji_quota_ratio` 五个 float。默认值分别为 `0.4486`、`0.3640`、`0.0118`、`0.0628`、`0.1128`；`clear()` 会恢复同一组默认值。

这组默认值在当前代码中承担“无 PCS 结果时的稳定 fallback”，不是本函数运行时计算出的比例。文档和调试日志应区分两种来源：

- 默认路径：key 缺失、result 不存在或五个 repeated 字段不完整。
- 模型路径：五个字段都存在时，每个 repeated 字段只取 `[0]`。

`deepes_quota_pcs_function.cpp:39-50` 的完整性判断是一个全有或全无 gate：只要 `sv_t/mv_t/dt_t/dj_t/heji_t` 任意一个为空，就不会部分覆盖。这避免了五类比例来自不同批次或不同完整度的混合状态，但也意味着单类字段异常会让全部五类回退默认值。

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title DeepesQuotaInfo 五类配额
  desc PCS 完整结果到 graph 输出的字段映射
  items
    - label SV
      desc sv_t[0] -> sv_quota_ratio; default 0.4486
      value 44.86
      icon mdi/video-outline
    - label MV
      desc mv_t[0] -> mv_quota_ratio; default 0.3640
      value 36.40
      icon mdi/movie-open-outline
    - label DT
      desc dt_t[0] -> dt_quota_ratio; default 0.0118
      value 1.18
      icon mdi/clock-video-outline
    - label DJ
      desc dj_t[0] -> dj_quota_ratio; default 0.0628
      value 6.28
      icon mdi/playlist-play
    - label Heji
      desc heji_t[0] -> heji_quota_ratio; default 0.1128
      value 11.28
      icon mdi/shape-outline
 theme
  palette #2d6a4f #3d5a80 #9a5b28 #b8432f
```

> 数值展示来自 `DeepesQuotaInfo` 默认初始化常量的百分比换算，仅用于阅读；函数本身不校验五个值的和是否等于 1，也不对 PCS 返回值做范围裁剪。若业务要求 ratio 和、上下界或 NaN 检查，应在 PCS 适配器或消费节点补充显式校验。

## 3. Graph 配置边界：emit/depend 类型必须一致

`conf/plugins/graph/short_micro_video/vertex.conf:1812-1827` 注册了 `DeepesQuotaPcsFunction`，声明输出 `_deepes_quota_info`，类型为 `DeepesQuotaInfo`，同时声明 `_pcs_result`、`_request`、`_sid_info` 作为输入。该配置与 C++ 尾部的 `GRAPH_FUNCTION_INTERFACE` 一一对应：

- `GRAPH_FUNCTION_DEPEND_DATA(PcsResultMap, _pcs_result)` 对应配置中的 `GrgPcsData` 运行时数据。
- `GRAPH_FUNCTION_EMIT_DATA(DeepesQuotaInfo, _deepes_quota_info)` 对应 vertex emit 的 typed output。
- `REGISTER_GRG_FUNCTION(DeepesQuotaPcsFunction)` 让函数名可被配置加载。

因此排查“函数执行了但后续拿不到配额”时，应分别验证注册名、vertex 名称、emit data 名称和 data type；只看 C++ 类名不足以确认 graph wiring 正确。

```infographic
infographic list-grid-badge-card
data
  title Graph wiring 四项契约
  desc 从注册到后续消费需要同时成立的边界
  items
    - label 函数注册名
      desc REGISTER_GRG_FUNCTION(DeepesQuotaPcsFunction) 必须与 vertex function 一致
      icon mdi/identifier
    - label 输入 data 名
      desc _pcs_result、_request、_sid_info 的名字必须和 GRAPH_FUNCTION_INTERFACE 对齐
      icon mdi/input
    - label 输出 data 名与类型
      desc _deepes_quota_info 的类型必须是 DeepesQuotaInfo
      icon mdi/export
    - label 后续节点依赖
      desc 消费者必须依赖同名同类型 output；当前快照需继续追踪具体消费者
      icon mdi/source-branch
 theme
  palette #2d6a4f #3d5a80 #b8432f
```

## 4. 与 DiversityMerge 的边界

`src/process/diversity_merge.cpp:73-105` 展示了另一个 graph 业务节点的装配方式：从 `EngineMgr` 获取 `MultiStreamEngine` pool，取得 pooled engine，把业务 context ref 到 engine context，再通过 `ApplicationContext` 取得 `ExecEnginePlugin` 并调用 `bind_graph_dependency`。这说明 `DeepesQuotaPcsFunction` 的输出并不会自动进入 DiversityMerge；typed data 需要由 graph 配置或 processor interface 明确连接。

`diversity_merge.cpp:797-809` 的当前逻辑以 `rec_num` 与配置 `result_num` 决定输出容量，并设置 engine loop num；`_output_*_num` 在 956-986 行统计各内容类型的实际输出数量。源码中可直接确认“结果数与输出类型计数”边界，但在本次快照内未命中 `DeepesQuotaInfo` 字段在该文件中的直接读取。因此不要把 DeepES 五类 ratio 已经参与 DiversityMerge 的结论写死；应继续从 graph 配置、Context 字段和 rule/operator 读取点验证。

## 5. 缺失、异常与默认回退

```infographic
infographic sequence-timeline-simple
data
  title DeepES 配额异常处理路径
  items
    - label 输入为空
      desc _request、_sid_info 或 _pcs_result 为空，CHECK_IS_NULL 返回 -1
      time Guard
    - label 输出无法 emit
      desc _deepes_quota_info.emit() 得到空指针，返回 -1
      time Emit
    - label PCS key 缺失
      desc 找不到 MicrovideoQuota，保持 clear() 后默认比例并返回 ERR_OK
      time Missing
    - label 五类字段不完整
      desc 任一 repeated 字段为空，不部分覆盖，继续使用默认比例
      time Partial
    - label 五类字段完整
      desc 每类读取 index 0，写入 DeepesQuotaInfo 并返回 ERR_OK
      time Model
 theme
  palette #b8432f #9a5b28 #3d5a80 #2d6a4f
```

需要特别注意：`reset()` 在 `deepes_quota_pcs_function.cpp:55-56` 是空实现。函数每次 `process()` 自己调用 output 的 `clear()`，因此当前默认恢复依赖 process 路径，而不是 graph function 的 reset 回调。如果框架未来复用对象并在 process 之前依赖 reset 清理，必须重新确认这个空 reset 是否仍然安全。

## 6. Pitfalls

<div class="quota-arch"><h3>DeepES 配额排查卡片</h3><div class="quota-grid"><div class="quota-box quota-risk"><b>把 key 缺失当错误</b><span>找不到 MicrovideoQuota 会返回 ERR_OK，并保留默认值。监控上应区分“正常降级”和“函数失败”。</span></div><div class="quota-box quota-risk"><b>只校验部分字段</b><span>当前实现五个 vector 全部非空才写入；不要根据单个 sv/mv 字段判断模型结果有效。</span></div><div class="quota-box quota-risk"><b>误以为会读取全部 repeated 值</b><span>每类只取 index 0。若 PCS 开始返回多阶段结果，当前代码不会自动聚合或选择最佳元素。</span></div><div class="quota-box quota-risk"><b>默认值没有范围校验</b><span>clear() 只恢复常量，PCS 值没有检查 [0,1]、总和或 NaN；异常值可能继续向后传播。</span></div><div class="quota-box quota-contract"><b>类型名漂移</b><span>配置里的 DeepesQuotaInfo、头文件结构名和 GRAPH_FUNCTION_EMIT_DATA 必须同时变更，否则运行时可能加载成功但数据不匹配。</span></div><div class="quota-box quota-contract"><b>消费点未证实</b><span>当前代码快照可证实生产端与 vertex wiring，不能仅凭命名断言后续 DiversityMerge 已消费这些比例。</span></div></div></div>

```infographic
infographic list-column-done-list
data
  title DeepES 配额调试 Checklist
  desc 按输入、转换、输出、消费四层核对
  items
    - label 检查 PCS map key
      desc 确认 _pcs_result 中存在 MicrovideoQuota
      done true
      icon mdi/key-chain
    - label 检查五个 repeated 字段
      desc sv_t/mv_t/dt_t/dj_t/heji_t 都非空且 index 0 值在预期范围
      done true
      icon mdi/format-list-checks
    - label 区分默认与模型路径
      desc 记录 key 缺失、字段不完整和完整覆盖三种状态
      done true
      icon mdi/source-branch-check
    - label 检查 emit 类型
      desc DeepesQuotaInfo 的字段名和类型必须与 graph data contract 一致
      done true
      icon mdi/shape-outline
    - label 检查 vertex wiring
      desc short_micro_video/vertex.conf 中 function、emit、depend 名称对齐
      done true
      icon mdi/file-tree
    - label 继续定位消费点
      desc 从 _deepes_quota_info 或 DeepesQuotaInfo 反向搜索后续 processor/operator
      done true
      icon mdi/magnify-expand
 theme
  palette #2d6a4f #3d5a80 #9a5b28
```

## 证据来源

- `baidu/feed-gr/feeda-mv-grg/src/process/deepes_quota_pcs_function.cpp:15-66`：函数注册、输入依赖、PCS key 查找、五字段转换与 emit contract。
- `baidu/feed-gr/feeda-mv-grg/src/data/base.h:1248-1261`：`DeepesQuotaInfo` 字段、默认值和 clear 语义。
- `baidu/feed-gr/feeda-mv-grg/conf/plugins/graph/short_micro_video/vertex.conf:1812-1827`：`DeepesQuotaPcsFunction` 的 vertex、emit 和 depend 配置。
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:73-105`：MultiStreamEngine pool、业务 context 与 graph dependency 装配边界。
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:797-809`：DiversityMerge 结果容量与 engine loop 设置。
- `baidu/feed-gr/feeda-mv-grg/src/process/diversity_merge.cpp:956-986`：五类内容实际输出计数与日志字段。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260924-grg-deepes-quota-pcs-boundary.md)。
