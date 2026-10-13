# 2026-09-19 业务库理解：Dup Service 与 title-dup 信号跨层流转
<!-- topic-nav:start -->
> 主题：`business/dup-title` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](2026-06-10-dup-service-title-dup-flow.md)
<!-- topic-nav:end -->


> 日期：2026-09-19  
> 主题选择：未发现本日有效 daily-plan；历史计划中的 `Dup Service 去重与 title dup 信号跨层流转` 尚未在当前 2026-09 系列形成对应双笔记，因此按邻近未覆盖主题回退。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：GRC `DupServicePlugin` 的 DupClient 初始化/对象池、GRC `response_for_grg` 对 `item_is_title_dup` 的条件写出、GRG `grc_recall_function` 的字段读入、`RkcjDiversityHardRule` 与 `TitleDiversityRule` 的两条消费路径。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7faf5;border:1px solid #d9e2d0;border-radius:8px;padding:16px;margin:16px 0;color:#243126;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">GRC 信号侧</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">DupServicePlugin</div><div style="margin-top:8px;font-size:12px;color:#52615a;">读取 graph pool_size，初始化 DupClient，创建并暴露 dup_client 对象池。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">响应字段边界</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">ContentItem.ext</div><div style="margin-top:8px;font-size:12px;color:#52615a;">GRC 仅在 UA=85 且命中 `same_title_filter_*` 时把 `RidTmpInfo::is_title_dup` 写成 `item_is_title_dup`。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">GRG 打散侧</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">recall → hard rule</div><div style="margin-top:8px;font-size:12px;color:#52615a;">召回把 ext 字段复制到 `RidTmpInfo`；RKCJ 规则命中实验且标记为 1 时返回 false。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">DupClient pool</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">GRC response ext</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">GRG RidTmpInfo</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;padding:10px;text-align:center;color:#334155;font-size:13px;">Rkcj hard filter</div></div></div>

**一句话结论：** `DupServicePlugin` 负责客户端资源与请求基础信息，不等于 GRG 每次实时调用 Dup；当前可见闭环是 GRC 响应条件写出 `item_is_title_dup`，GRG 召回复制为 `is_title_dup`，再由受实验与场景约束的硬规则消费。

## 1. title-dup 信号跨层流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title Dup Service 与 title-dup 信号跨层流转
participant "GRC main.cpp" as MAIN
participant "DupServicePlugin" as DUP
participant "GRC response_for_grg" as RESP
participant "ContentItem.ext" as EXT
participant "GRG grc_recall_function" as RECALL
participant "RidTmpInfo" as RID
participant "RkcjDiversityHardRule" as HARD
participant "TitleDiversityRule" as TITLE
MAIN -> DUP : component initialize()
DUP -> DUP : load graph pool_size
DUP -> DUP : DupClient::global_init()
DUP -> DUP : create ObjectPool + expose dup_client
RESP -> RESP : check ua == 85
RESP -> RESP : hit same_title_filter_1..4
alt both conditions true
  RESP -> EXT : set_item_is_title_dup(ridinfo.is_title_dup)
else condition false
  RESP -> EXT : omit this conditional write
end
EXT -> RECALL : ContentItem.ext().item_is_title_dup()
RECALL -> RID : ridinfo->is_title_dup = ext field
RID -> HARD : run_hard_rule(ridinfo)
alt same_title_filter_* hit and is_title_dup == 1
  HARD --> RID : false, candidate filtered
else not matched
  HARD --> RID : continue other rules
end
TITLE -> TITLE : independent showlist title window
TITLE --> RID : based on title_ts/is_title_show and nid set
@enduml
```

## 2. 字段与规则信息图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title title-dup 的生产、透传与消费
  desc 同名“去重”行为分为外部字段路径和本地 showlist 路径，排查时不能混用
  items
    - label DupClient 资源层
      desc DupServicePlugin 读取 graph.pool_size，global_init 后构造 ObjectPool<DupClient> 并 expose dup_client；get() 只返回池对象
      icon mdi:database-cog-outline
      children
        - label fill_basic_info
          desc MODULE_GRC、PRODUCT_MICRO_VIDEO、log_id/req_id、proxy 标记、cuid/uid/baiduid、REQTYPE_GET_DUP
          icon mdi:form-select
    - label GRC 响应层
      desc UA=85 且 same_title_filter_1..4 任一命中时，才把 ridinfo.is_title_dup 写入 ContentItem.ext.item_is_title_dup
      icon mdi:arrow-right-bold-box-outline
      children
        - label 条件写出
          desc 非满足条件的响应不会在这段代码中写出该 title-dup 字段
          icon mdi:filter-outline
    - label GRG 召回层
      desc grc_recall_function 从 item->ext() 读取字段，复制到 RidTmpInfo::is_title_dup，默认字段初值为 0
      icon mdi:inbox-arrow-down-outline
    - label GRG 打散层
      desc RkcjDiversityHardRule 在 same_title_filter_* 命中且标记为 1 时返回 false；TitleDiversityRule 另行基于 showlist 构造 nid 集合
      icon mdi:shield-filter-outline
      children
        - label local title window
          desc 首刷时回看 title_ts 在最近窗口内且 is_title_show 的 showlist item
          icon mdi:history
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 3. 关键机制拆解

### 3.1 DupServicePlugin 的核心是池化客户端，不是打散判定

`src/plugin/dup_service.cpp:13-43` 从 GRC graph 配置读取 `graph.pool_size`，默认值为 10；随后执行 `feed::dup::DupClient::global_init()`，创建 `ObjectPool<DupClient>`，在构造器中统计 `_real_pool_size`，最后通过 `expose("dup_client")` 暴露池。`src/plugin/dup_service.cpp:60-64` 的 `get()` 返回池化对象，组件由 `BABYLON_REGISTER_COMPONENT` 注册。

`src/plugin/dup_service.cpp:46-57` 的 `fill_basic_info` 只填充 DupRequest 的模块、产品、日志/请求 ID、是否来自 proxy、用户标识和 `REQTYPE_GET_DUP`。因此看到这个插件不能直接推出“每个候选都会同步访问 Dup”；必须继续追踪实际 get/请求调用者和响应字段。

### 3.2 GRC 响应写字段受 UA 与实验双重门控

`src/processor/video_launch/response_for_grg.cpp:3444-3449` 只有在 `_common_info->ua == 85` 且 `same_title_filter_1..4` 任一实验命中时，才执行 `set_item_is_title_dup(ridinfo.is_title_dup)`。代码还打印 `dqz_same_title_filter add title dup` 日志。这个边界意味着“GRG 没读到字段”可能是字段根本没有在当前 GRC 场景写出，而不一定是 GRG 解析丢失。

### 3.3 GRG 召回把 ext 字段落到可复用运行时结构

`src/process/grc_recall_function.cpp:488-502` 在填充 `RidTmpInfo` 的同时执行 `ridinfo->is_title_dup = item->ext().item_is_title_dup()`。字段定义位于 `src/data/rid_tmp_info.h:109-120`，初始值为 0。由此形成明确的字段链：`ContentItem.ext.item_is_title_dup` → `RidTmpInfo.is_title_dup`；后续规则读取的是运行时结构，不再直接访问 protobuf ext。

### 3.4 RkcjDiversityHardRule 是外部 title-dup 标记的直接消费点

`src/operator/diversity/Rkcj_diversity_hard_rule.cpp:37-49` 先限制 UA=85、首刷和 `loop_num()==0`，然后检查 `same_title_filter_1..4` 与 `ridinfo.is_title_dup == 1`。两组条件同时成立时返回 false，候选被硬过滤；否则继续后续 RKCJ 规则。排查时要把“实验未命中”和“字段值为 0”分开记录。

### 3.5 TitleDiversityRule 是另一条独立的本地历史窗口路径

`src/operator/diversity/title_diversity_rule.cpp:26-60` 在首刷场景清空 `_nid_set`，回看 showlist，遇到超过时间窗口的 `title_ts` 即停止，并把近期 `is_title_show` 的 rid 放进集合。` :63-139` 先执行短剧/合集/豁免/场景等 bypass 条件，最后以 `_nid_set.find(rid.rid)` 决定是否过滤。这条规则不依赖 `RidTmpInfo::is_title_dup`，不能把它和 Rkcj 的 ext 字段路径合并解释。

## 4. 两条 title 过滤路径对账

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title 外部 dup 标记 vs 本地 title 展现历史
  desc 两条路径都可能造成候选减少，但输入、门控和调试证据不同
  items
    - label external signal
      desc GRC response ext 写出 item_is_title_dup；GRG recall 复制到 RidTmpInfo；Rkcj 规则检查 same_title_filter_* 与值 1
      icon mdi:transit-connection-variant
    - label local history
      desc TitleDiversityRule 在首刷时读取 showlist 的 title_ts/is_title_show，构造最近窗口 nid 集合
      icon mdi:history
    - label common gate
      desc 外部路径受 UA=85、首刷/loop0 和实验影响；本地路径受首刷、slide/dibar、豁免和资源类型影响
      icon mdi:gate
    - label failure question
      desc 先问字段是否写出，再问召回是否读入，最后问 hard rule 是实验过滤还是 showlist 过滤
      icon mdi:help-circle-outline
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d6d3ce;border-left:6px solid #2d6a4f;border-radius:8px;padding:18px;margin:18px 0;color:#243126;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#2d6a4f;font-weight:900;">Pitfalls · title dup</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">不要把“Dup 服务初始化”误读成“GRG 每次实时查 Dup”</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #2d6a4f;border-radius:6px;padding:12px;font-size:14px;"><b>三个常见误判：</b><br>① 只看 DupClient pool，忽略 GRC 响应字段的 UA/实验门控；② 把 ext 字段未写出误判为 GRG 解析失败；③ 把 Rkcj 外部 dup 标记和 TitleDiversityRule 的 showlist 历史窗口当成同一条规则。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>Dup pool 初始化 → 实际 Dup 调用者 → GRC UA/实验 → response ext → GRG recall → RidTmpInfo → Rkcj hard rule → TitleDiversityRule。</div></div><div style="margin-top:12px;font-weight:900;color:#2d6a4f;">∎ 线上排查第一问应是：字段没生产、没透传，还是已经透传但被哪条 hard rule 消费掉？</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title title-dup 跨层排查清单
  desc 从客户端池、响应字段到 GRG 规则逐层定位候选被过滤的原因
  items
    - label 检查 DupClient 初始化
      desc graph.pool_size、global_init、real_pool_size 和 dup_client expose 是否成功
      done true
    - label 找实际 Dup 调用者
      desc 不要只凭 DupServicePlugin 存在就假设请求已发出，继续追踪 get() 和 DupClient 调用点
      done false
    - label 检查 GRC 条件
      desc UA 是否为 85，same_title_filter_1..4 是否命中，ridinfo.is_title_dup 是否已有值
      done false
    - label 检查 response ext
      desc response_for_grg 是否执行 set_item_is_title_dup，日志是否出现 add title dup
      done false
    - label 检查 GRG recall
      desc item->ext().item_is_title_dup() 是否复制到 RidTmpInfo::is_title_dup
      done false
    - label 检查 Rkcj hard rule
      desc 首刷/loop0、实验命中和 is_title_dup==1 是否共同成立并返回 false
      done false
    - label 区分本地历史规则
      desc 若 ext 字段为 0，继续检查 TitleDiversityRule 的 title_ts、is_title_show 和 _nid_set
      done false
    - label 补齐业务背景
      desc 从 KU/实验平台确认 same_title_filter_* 的灰度、人群、版本与监控口径
      done false
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 7. 证据来源

- `src/main.cpp:39-59`：GRC/GRG 服务入口的 reusable protocol、GlobalInitializer、实验参数和日志 funnel 初始化形态。
- `src/plugin/dup_service.cpp:13-43`：配置读取、DupClient global_init、ObjectPool 创建和 `dup_client` expose。
- `src/plugin/dup_service.cpp:46-64`：DupRequest 基础字段填充、池对象获取和组件注册。
- `src/plugin/dup_service.h:30-45`：DupServicePlugin 接口、对象池和 real pool size 字段。
- `src/processor/video_launch/response_for_grg.cpp:3444-3449`：GRC 在 UA/实验门控下写出 `item_is_title_dup`。
- `src/process/grc_recall_function.cpp:488-502`：GRG 从 ContentItem ext 复制 `is_title_dup`。
- `src/data/rid_tmp_info.h:109-120`：`RidTmpInfo::is_title_dup` 默认值与运行时字段位置。
- `src/operator/diversity/Rkcj_diversity_hard_rule.cpp:37-49`：same-title 实验与 `is_title_dup==1` 的硬过滤。
- `src/operator/diversity/title_diversity_rule.cpp:26-60`：showlist title 时间窗口与 `_nid_set` 构建。
- `src/operator/diversity/title_diversity_rule.cpp:63-145`：本地 title 历史窗口的 bypass 与最终过滤判断。

> KU 正文未读取，需人工补充 Dup 服务真实调用链、`same_title_filter_*` 的实验分桶/灰度、`item_is_title_dup` 的协议兼容范围、Rkcj 与 TitleDiversityRule 的线上优先级，以及过滤结果的监控指标。