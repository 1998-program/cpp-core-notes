# 2026-08-29 周六代码理解：GR 请求解析中的关闭个性化与 fake_id 注入边界
<!-- topic-nav:start -->
> 主题：`business/personalization-id` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](2026-07-04-close-individual-fakeid-cross-service-boundary.md)
<!-- topic-nav:end -->


> 日期：2026-08-29  
> 主题来源：当前没有可用的当日 daily-plan，回退到 `notes/weekly-topic-selection/daily-plan-20260529.json` 中的 fake_id / 关闭个性化候选主题；KU/业务背景需人工补充。  
> 范围：`src/processor/request.cpp` 的请求字段解析、`is_close_individual` 覆盖、`fake_user.fake_cuid` 写入、`from_scene` 分支与 `SceneInfo` 地理字段回填。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求字段层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`request_doc` → `CommonInfo`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">从 JSON 中解析 `is_close_individual`、`cmode`、`pd`、`from_scene`、定位字段和实验开关。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">隐私兼容层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`is_only_browse` → `is_close_individual` → `fake_id`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">只浏览模式会强制关闭个性化；非零时计算 fake_id 并写入 fake_user。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">场景信息层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`SceneInfo` reset + sema 合并</div><div style="margin-top:8px;font-size:12px;color:#52606d;">定位字段会同步写回 `sema`，避免后续 processor 从两个容器读到不同值。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">解析请求字段</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">判定关闭个性化</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;padding:10px;text-align:center;color:#a16207;">计算 fake_id</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">SIA + SceneInfo 输出</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title request.cpp close personalization and fake_id boundary
actor "GR Request" as REQ
participant "RequestProcessor" as P
participant "CommonInfo" as C
participant "SidInfo / abtest" as SID
participant "FakeUser" as F
participant "SceneInfo" as SC
participant "SIA" as SIA
REQ -> P : parse request_doc
P -> SID : hit_abtest(zero_to_n_recall_switch)
P -> C : read pd / outside_invoke_type / autoplay
P -> C : read is_close_individual
P -> C : derive is_only_browse from cmode
alt is_only_browse
  P -> C : force is_close_individual = 1
end
alt is_close_individual != 0
  P -> C : Util::caculate_fake_id(uid,cuid,baiduid,logid)
  P -> F : fake_cuid = common_info.fake_id
end
P -> SIA : add is_close_individual / fake_id
P -> C : read from_scene
P -> SC : reset and copy sema
P -> SC : merge loc_province / loc_city / loc_district
P --> REQ : CommonInfo + FakeUser + SceneInfo ready
@enduml
```

## 2. 字段结构信息图
```infographic
infographic list-grid-badge-card
data
  title request.cpp 里的关闭个性化关键字段
  desc 这些字段共同决定下游看到真实用户标识、fake_id，还是场景化定位信息
  items
    - label is_close_individual
      desc `src/processor/request.cpp:302-304` 从请求 JSON 直接读取，是显式关闭个性化入口
      icon mdi/account-cancel
    - label is_only_browse
      desc `src/processor/request.cpp:348-358` 可由 `is_only_browse` 或 `cmode` 派生，并强制关闭个性化
      icon mdi/eye-off-outline
    - label fake_id
      desc `src/processor/request.cpp:361-365` 非零关闭个性化时由 uid/cuid/baiduid/logid 计算
      icon mdi/incognito
    - label fake_cuid
      desc `fake_user->fake_cuid` 承接 fake_id，给下游使用兼容标识
      icon mdi/account-arrow-right
    - label from_scene
      desc `src/processor/request.cpp:712-716` 识别 `feed_after_video`，影响后续 after-read 推荐分支
      icon mdi/movie-open-play
    - label SceneInfo
      desc `src/processor/request.cpp:974-1005` reset 后复制 sema，并把定位字段同步写回 sema
      icon mdi/map-marker-radius
```

## 3. 业务链路拆解
### 3.1 显式请求字段不是唯一入口
- `src/processor/request.cpp:302-304`：如果请求 JSON 中带 `is_close_individual`，直接写入 `common_info->is_close_individual`。
- `src/processor/request.cpp:348-355`：`is_only_browse` 既能从同名字段读取，也能从 `cmode` 第一段是否为 `2` 推导。这个推导发生在 fake_id 计算前。
- `src/processor/request.cpp:357-358`：一旦 `is_only_browse` 为真，会强制把 `is_close_individual` 置为 1。排查时不能只看原始请求是否显式带了关闭个性化字段。

### 3.2 fake_id 是下游兼容标识，不只是日志字段
- `src/processor/request.cpp:361-365`：当 `is_close_individual != 0` 时，代码调用 `Util::caculate_fake_id()`，输入包含 `uid`、`cuid`、`baiduid` 和 logid，然后把结果写到 `common_info->fake_id` 与 `fake_user->fake_cuid`。
- `src/processor/request.cpp:367-369`：同一段落立即写 SIA 指标 `is_close_individual`、`doublecolumn_status` 和 `fake_id`，说明这里也是定位线上请求行为的观测点。

### 3.3 场景字段与定位字段在同一个请求 processor 中收束
- `src/processor/request.cpp:712-716`：`from_scene == "feed_after_video"` 会打开 `is_recommend_after_read`，这是和 fake_id 相邻但语义不同的场景分支。
- `src/processor/request.cpp:974-980`：`SceneInfo` 每次先 reset，再把 `sema` 对象复制进场景信息，避免复用对象残留。
- `src/processor/request.cpp:981-1004`：`loc_province`、`loc_city`、`loc_district` 会同时写入 `SceneInfo` 字段和 `sema` 对象。后续从结构化字段或 sema 读取定位时，应该得到一致值。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">关闭个性化不是一个字段的开关，而是一段请求归一化链</div><div style="display:grid;grid-template-columns:1.25fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">如果只检查请求里的 `is_close_individual`，会漏掉 `cmode` 推导出的 `is_only_browse`。线上出现 fake_id 时，先同时打印原始字段、cmode、is_only_browse 和最终 common_info 值。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`fake_user->fake_cuid` 是下游兼容边界。排查“个性化仍生效”时，要确认下游 processor 读的是真实 cuid、fake_cuid，还是 common_info.fake_id。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 判断顺序：显式字段 → cmode 派生 → 强制关闭 → fake_id 写入 → 下游读取点</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title fake_id 与关闭个性化排查清单
  desc 适用于关闭个性化不生效、fake_id 缺失、下游仍用真实 cuid、定位字段不一致
  items
    - label 打印原始请求字段
      desc 同时看 `is_close_individual`、`is_only_browse`、`cmode`，不要只看一个字段
      done true
    - label 检查派生覆盖
      desc `cmode` 第一段为 2 会把 `is_only_browse` 置真，并强制关闭个性化
      done true
    - label 验证 fake_id 写入
      desc 关闭个性化后检查 `common_info->fake_id` 与 `fake_user->fake_cuid` 是否同时有值
      done true
    - label 对齐 SIA
      desc 用 SIA 中的 `is_close_individual` 和 `fake_id` 确认线上真实路径
      done true
    - label 检查下游读取点
      desc 确认 rank、recall、UFS 或特征服务读取的是 fake_cuid 还是真实 cuid
      done true
    - label 检查 SceneInfo 同步
      desc 定位字段需要同时存在于 SceneInfo 字段和 sema，避免条件图读到旧值
      done true
```

## 6. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/processor/request.cpp:279-369`
- `src/processor/request.cpp:712-716`
- `src/processor/request.cpp:974-1005`
- `notes/business-lib/20260819-grg-close-personalization-fake-id-boundary.md`

## 7. 说明
当前运行环境未发现 2026-08-29 的 daily-plan 文件，也没有读取 KU 正文；本笔记使用本地代码包与历史候选主题回退生成，业务背景需人工补充。

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260829-gr-request-close-personalization-fake-id.md)。
