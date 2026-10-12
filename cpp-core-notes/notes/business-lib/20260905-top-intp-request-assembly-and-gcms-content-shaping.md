# 2026-09-05 周六业务库理解：TopIntp 请求装配与 GCMS 内容成形

> 日期：2026-09-05  
> 主题来源：当前 canonical 笔记仓库未找到 2026-09-05 当日 daily-plan；按历史 Friday/Saturday 邻近主题回退到 `TopIntp 请求装配与 GCMS 内容成形`。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/service/top_service.h`、`src/service/top_service_pb.h`、`src/common/session_context.h`、`src/component/top_data_component.h`、`src/component/gcms_component.h`、`src/common/gcms_data.h`，聚焦请求解析、置顶数量、展示策略、双缓冲内容读取和 GCMS 响应项结构。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.08fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求语义</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`SessionContext` / `FeedReq`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">把 protocol、是否需要 top data、目标条数、uid/cuid/baiduid 和 show/his list 统一放到请求态里。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">内容供给</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`TopData` / `GCMS` / `RespItem`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">双缓冲置顶内容加上 GCMS 结构体，决定最终可被转换成什么样的前台结果。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">输出约束</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`TopIntpClosure`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">负责把中间态落成 `TopResponse`，并将 display_strategy、ext、feature_ext 等附加信息装配到返回对象。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 64px 1fr 64px 1fr 64px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">parse request</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">fetch shared data</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">fill response</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">log and return</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title TopIntp request assembly and content shaping
participant "TopServicePbImpl" as SVC
participant "SessionContext" as CTX
participant "TopDataComponent" as TDC
participant "GcmsComponent" as GCMS
participant "TopIntpClosure" as CLS
participant "TopResponse" as RESP
SVC -> CTX : parse_param(request) + fill uid/cuid/baiduid
SVC -> SVC : check_params(feed_req)
SVC -> CTX : set is_need_top_data / required count
CLS -> TDC : get_top_date(top_data, doc)
CLS -> GCMS : get_gcms_data(nids, gcms_resp_map)
GCMS --> CLS : GcmsRespItem map
TDC --> CLS : first/second/third/fourth_top data
CLS -> CLS : pb2json_of_contentitem() / add_display_strategy()
CLS -> RESP : fill_resp_pb()
CLS -> RESP : add_ext() / add_feature_ext()
CLS -> CLS : print_log()
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title TopIntp 请求装配的 6 个约束点
  desc 这些约束点决定请求态如何转成可返回的内容态，以及展示策略是否能在输出中保留。
  items
    - label 请求上下文
      desc `src/common/session_context.h:82-112` 把 protocol、top 条数和结果容器放到 `SessionContext`
      icon mdi/clipboard-text-outline
    - label 入口校验
      desc `src/service/top_service.h:28-40` 的 `parse_param()` / `check_params()` 先拦住非法请求
      icon mdi/shield-check-outline
    - label PB 装配
      desc `src/service/top_service_pb.h:21-41` 把请求转给 pb handler，再进入 closure 路径
      icon mdi/file-code-outline
    - label 双缓冲读取
      desc `src/component/top_data_component.h:56-75` 从前台 buffer 拷贝 top_vec 和 map，避免直接读后台生产对象
      icon mdi/view-column
    - label GCMS 响应项
      desc `src/common/gcms_data.h:42-103` 的 `GcmsRespItem` 保存类目、敏感标记和多路 category 结构
      icon mdi/database-search
    - label 输出富化
      desc `TopIntpClosure` 的 `add_display_strategy()`、`add_ext()`、`add_feature_ext()` 负责把前台展示字段补齐
      icon mdi/palette-outline
```

## 3. 代码链路拆解
### 3.1 请求先落到 SessionContext，再决定是否需要 top data
- `src/common/session_context.h:82-112`：`SessionContext` 里既有 `protocol_type`，也有 `is_need_top_data`、`num_of_top_data_required` 和 `num_of_top_data_issued`，这说明“是否返回置顶”和“返回几条”是显式请求态，不是靠后端猜。
- `src/common/session_context.h:30-60`：`FeedReq` 保存 `uid`、`cuid`、`baiduid`、`channel_id`、`product`、`refresh_state` 和 `app_version`，后续请求分流都依赖这些字段。
- `src/service/top_service.h:28-40`：`parse_param()`、`check_params()`、`make_error()` 把请求接入层和业务层隔开，非法输入会在进入内容装配前被截住。

### 3.2 内容供给来自 TopData 双缓冲与 GCMS 响应结构
- `src/component/top_data_component.h:46-75`：`TopDataComponent` 从 foreground buffer 拷贝 `first_top_vec`、`second_top_vec`、`third_top_vec` 和 `fourth_more_top_map`，避免请求线程直接碰后台更新中的对象。
- `src/component/top_data_component.h:101-118`：`switch_index()` 和 `update_background_data()` 说明内容是通过后台写入、前台切换的方式刷新，不是每个请求都实时拉全量源。
- `src/common/gcms_data.h:42-103`：`GcmsRespItem` 保存 `vertical_type`、`is_microvideo`、new/old category、`shortv_cate`、`shortv_subcate`、`politic_sensitive_scene_details_*`，因此它既是分类载体，也是敏感治理的载体。

### 3.3 输出装配把业务策略变成前端可消费字段
- `src/service/top_service_pb.h:23-40`：`TopServicePbImpl::handle()` 负责把 `TopRequest`、`TopResponse` 和 `done` 连接起来，再借助 `SessionContext` 维护请求生命周期。
- `src/component/top_data_component.h:56-72`：拿到的 `TopData` 会被复制到当前请求的 `rapidjson::Document` 里，说明最终返回不直接暴露共享缓存，而是走一次局部化转写。
- `src/service/top_service.h:34-40` 与 `src/service/top_service_pb.h:28-35`：错误回填和请求日志都在 handler 一侧收束，说明“展示策略是否命中”和“为什么没命中”都应该在这里可观测。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">business pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">这个链路最容易把“共享内容”误当成“请求专属结果”</div><div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`TopDataComponent` 返回的是前台快照，不是每个请求单独生成的内容。若 `switch_index()` 或后台刷新失败，所有请求都会同时看到旧内容，而不是某个用户独有的异常。</div><div style="background:#f8fafc;border-top:3px solid #2563eb;border-radius:8px;padding:12px;font-size:14px;">`GcmsRespItem` 同时承载类别和敏感信息，做展示策略时不能只看 `vertical_type`。如果敏感字段没同步，前端能看到的内容仍可能被治理层卡掉。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 排查顺序：request parse → SessionContext → TopData snapshot → GCMS map → display_strategy → pb/json fill → error log</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title TopIntp 请求装配排查清单
  desc 适用于返回条数不对、展示策略缺失、GCMS 类目异常、快照过旧和错误回填不完整
  items
    - label 检查请求解析
      desc `parse_param()` 是否把 uid/cuid/baiduid 和刷新态正确写入 `SessionContext`
      done true
    - label 检查参数校验
      desc `check_params()` 是否在进入装配前拦住非法请求
      done true
    - label 检查 top data 快照
      desc `get_top_date()` 是否从当前前台 buffer 拷贝到了足够新的数据
      done true
    - label 检查 GCMS 映射
      desc `get_gcms_data()` 是否拿到完整的 `GcmsRespItem` 集合
      done true
    - label 检查展示策略
      desc `display_strategy`、`ext`、`feature_ext` 是否都被填充
      done true
    - label 检查错误回填
      desc `make_error()` 是否在失败路径保留了 source 和错误文本
      done true
```

## 6. 证据来源
- `src/service/top_service.h:23-41`
- `src/service/top_service_pb.h:21-40`
- `src/common/session_context.h:18-112`
- `src/component/top_data_component.h:46-125`
- `src/component/gcms_component.h:25-34`
- `src/common/gcms_data.h:42-103`
- `src/main.cpp:48-66`

## 7. 说明
当前运行环境未找到 2026-09-05 的 daily-plan 文件；本笔记基于相邻历史主题和本地代码回退生成。KU 正文未读取，业务背景需人工补充。
