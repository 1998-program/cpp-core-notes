# 2026-08-28 周五代码理解：GRC fill_meta 的 GCMS 场景路由与小流量筛选
<!-- topic-nav:start -->
> 主题：`business/gcms-fillmeta` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260601-grc-gcms-ifcs-fill-meta-chain.md)
<!-- topic-nav:end -->


> 日期：2026-08-28  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的历史候选项；本次没有独立的当日 daily-plan 可直接读取，KU/业务背景需人工补充。  
> 范围：只看 `src/processor/fill_meta.cpp` 中的 `FillMetaBaseProcessor::setup()`、`process()`、`small_flow_list`、`GCMS_SECNE_RECALL`、`card_no_gcms_mark` 和 `RidTmpInfo` 填充路径，不展开其它策略树。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:14px;background:#f8fafc;line-height:1.45;">
  <div style="display:grid;grid-template-columns:1.1fr 1.18fr 1fr;gap:12px;align-items:stretch;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">上下文装配</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`FillMetaBaseProcessor::setup()`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">把匿名依赖、命名依赖和输出占位装进 `FillMetaBaseContext`。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">场景路由</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`small_flow_list` + `GCMS_SECNE_RECALL`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">把作者等级小流量、UA/flow_loc 条件和 GCMS 查询场景拼成一次请求策略。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">结果落点</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`gcms_common_pb_meta_map` / `RidTmpInfo`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">查回来的通用元数据再挂回到各个 rid，决定后续 fill 阶段能拿到什么字段。</div>
    </div>
  </div>
  <div style="margin-top:12px;display:grid;grid-template-columns:1fr 80px 1fr 80px 1fr 80px 1fr;gap:10px;align-items:center;">
    <div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#4338ca;">匿名依赖收集</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">GCMS 请求准备</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;padding:10px;text-align:center;color:#a16207;">字段回填</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">fill meta 输出</div>
  </div>
</div>

## 1. 核心流程
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "FillMetaBaseProcessor" as P
participant "FillMetaBaseContext" as C
participant "GcmsComponent" as G
participant "GCMS query_common()" as Q
participant "RidTmpInfo / GcmsData" as R
Caller -> P : setup(vertex, context)
P -> C : collect dependencies / config
Caller -> P : process(context)
P -> P : build small_flow_list
P -> G : get_instance()
P -> Q : query_common(merge_rids,...,GCMS_SECNE_RECALL)
Q --> P : gcms_common_pb_meta_map
P -> R : attach GcmsData to RidTmpInfo
P --> Caller : fill meta result
@enduml
```

## 2. 场景路由拆解
```infographic
sequence-ascending-steps
  data
    title fill_meta 场景路由
    desc 从依赖收集到 GCMS 查询，再到 rid 回填，主线分成四段
    items
      - label 上下文装配
        desc `setup()` 绑定 handle_name、匿名依赖和命名依赖
      - label 小流量筛选
        desc 通过 `autor_level_sm` 和 abtest 结果生成 `small_flow_list`
      - label GCMS 查询
        desc 用 `ua`、`flow_loc` 和场景常量发起 `query_common()`
      - label 元数据回填
        desc 把 `gcms_common_pb_meta_map` 里的结果贴回 `RidTmpInfo`
```

## 3. 关键判断
- `setup()` 的职责是把依赖关系和上下文对象准备好，不是做真正的业务筛选。
- `small_flow_list` 说明这个链路不是简单“查全量再过滤”，而是先按作者等级和 abtest 生成窄流量集合。
- `GCMS_SECNE_RECALL` 把这条查询明确落在召回场景，场景常量本身就是业务语义的一部分。
- `card_no_gcms_mark` 告诉我们并不是所有 rid 都走同一条回填路由，存在跳过正排的显式分支。

## 4. 结构卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:4px solid #0ea5e9;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#0369a1;text-transform:uppercase;letter-spacing:.04em;">关键结论</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">fill_meta 的关键不是“有没有拿到 GCMS 数据”，而是“哪些 rid 进入查询、哪些场景被降到小流量、哪些字段允许跳过正排”。真正的业务边界就在这三个路口。</div>
</div>

<div style="height:10px"></div>

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fefce8;border:1px solid #fde68a;border-left:4px solid #eab308;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#a16207;text-transform:uppercase;letter-spacing:.04em;">调试提示</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">如果某些 rid 的 meta 缺失，不要先怀疑 `query_common()` 一定失败，先拆开看是小流量名单没命中、`card_no_gcms_mark` 跳过了，还是 `gcms_common_pb_meta_map` 里本来就没有该键。</div>
</div>

## 5. Pitfalls
- `small_flow_list` 依赖手工维护的作者等级集合，配置变化会直接改变查询集合，不是纯代码逻辑。
- `merge_rids` 与 `gcms_common_pb_meta_map` 是两个不同阶段的数据容器，混用它们会让问题定位失真。
- `RidTmpInfo` 既承载业务字段又承载 GCMS 回填结果，生命周期不清时很容易把临时状态带到下一段处理。
- `open_gcms_statistics` 只影响统计和日志，不代表主链路正确，别把观测分支当成业务成功条件。

## 6. 调试 Checklist
```infographic
list-column-done-list
  data
    title fill_meta 调试清单
    items
      - label 检查 setup 依赖
        desc 确认匿名依赖和命名依赖都已绑定
        done true
      - label 检查小流量名单
        desc 确认 `small_flow_list` 按预期命中
        done true
      - label 检查场景常量
        desc 确认 `GCMS_SECNE_RECALL` 没写错或传错场景
        done true
      - label 检查回填键
        desc 确认 `gcms_common_pb_meta_map` 的 key 和 rid 对齐
        done true
```

## 7. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/fill_meta.cpp:126-255`
- `src/processor/fill_meta.cpp:268-352`
- `notes/business-lib/20260825-grc-fill-meta-request-response-boundary.md`

> 说明：当前运行环境没有独立的当日 daily-plan，可用历史候选项回退；KU/业务背景需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260828-grc-fill-meta-gcms-scene-routing.md)。
