# GRC 基础库：IFCS/GCMS FillMeta 正排补全链路

> 日期：2026-06-08  
> 主题来源：未发现本机 `daily-plan` 文件；按双 Markdown 工作流在 GRC 基础链路中选取“FillMeta → GcmsComponent → IFCS 正排补全”作为今日主题。  
> 范围：`FillMetaBaseProcessor` 的依赖声明、nid 去重、IFCS 请求上下文、场景切换、GCMS pb 写回、缺失正排过滤。  
> 内网文档：已按要求设置 `BAIDU_CC_USERNAME=hujiyang`；本次未提供 KU URL/doc-id，未调用 KU 检索。背景口径需人工补充，本文以代码库证据为准。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div style="font-size:22px;font-weight:800;margin-bottom:6px;">FillMeta 正排补全：RecallResult → IFCS DocInfoMap → RidTmpInfo</div>
<div style="font-size:13px;color:#64748b;margin-bottom:16px;">基础库职责是把图引擎中多个召回分支的 rid 集合合并、批量查询正排，再把 MicroVideoInfo 挂回每个候选 item；业务规则只在挂回后读取字段。</div>
<div style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">Graph 依赖层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>anonymous deps</b><br><span style="font-size:12px;color:#64748b;">多个 RecallResult 输入</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>SidInfo</b><br><span style="font-size:12px;color:#64748b;">AB/小流量透传</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>Context</b><br><span style="font-size:12px;color:#64748b;">ua / flow_loc / logid</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>emit</b><br><span style="font-size:12px;color:#64748b;">RecallResultWithMeta</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">IFCS 查询层</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>merge_rids</b><br><span style="font-size:12px;color:#64748b;">unordered_set 去重</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>gcms_context</b><br><span style="font-size:12px;color:#64748b;">ua / flow_loc 条件</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>sids</b><br><span style="font-size:12px;color:#64748b;">小流量命中写入</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>scene</b><br><span style="font-size:12px;color:#64748b;">RECALL / SEARCH</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>DocInfoMap</b><br><span style="font-size:12px;color:#64748b;">rid → MicroVideoInfo</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">写回与过滤层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>gcms hit</b><br><span style="font-size:12px;color:#64748b;">挂 _video_info/gcms_data</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>copy-on-write</b><br><span style="font-size:12px;color:#64748b;">实验字段改写</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>no gcms</b><br><span style="font-size:12px;color:#64748b;">部分 mark 允许跳过</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>SIA/log</b><br><span style="font-size:12px;color:#64748b;">prepare / rpc / fill 观测</span></div>
</div>
</div>
</div>

## 2. 核心结论

- `FillMetaBaseProcessor::setup()` 会把所有匿名依赖声明为 `RecallResult`，并记录 `SidInfo`、`RecallResultWithMeta`、关注作者/作者 id 集合作为后续处理上下文；证据见 `src/processor/fill_meta.cpp:126-153`。
- `process()` 首先清空输出与临时集合，然后遍历全部匿名依赖，把每个 `RecallResult.rids` 合入 `merge_rids`，实现跨召回分支的批量去重；证据见 `src/processor/fill_meta.cpp:176-201`。
- IFCS 请求前会构造 `gcms_context`，写入 `logid`、`ua`、`flow_loc`，并把命中的小流量 sid 字符串透传给 IFCS；证据见 `src/processor/fill_meta.cpp:203-235`。
- 真正的正排查询由 `GcmsComponent::query_common()` 发起：默认 `server_cache_only=true`，但特定 UA/flow_loc/search sid 会切到 `GCMS_SECNE_SEARCH` 并关闭 server-cache-only；证据见 `src/plugin/gcms_component.cpp:40-72`。
- 查询成功后，FillMeta 逐 item 查 `gcms_common_pb_meta_map`，命中则挂 `tmp->_video_info` 与 `tmp->gcms_data`；若返回空指针则记录 GCMS 日志并跳过该 item；证据见 `src/processor/fill_meta.cpp:285-321`。
- 部分业务改写采用 copy-on-write：例如合集、IP 识别等实验会复制 `MicroVideoInfo` 后改字段，再重建 `gcms_data`，避免直接改 IFCS 返回对象；证据见 `src/processor/fill_meta.cpp:324-463`。

## 3. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "GraphVertex" as vertex
participant "FillMetaBaseProcessor" as fill
participant "GcmsComponent" as gcms
participant "IFCS SDK" as ifcs
database "DocInfoMap<MicroVideoInfo>" as meta
database "RecallResultWithMeta" as out
vertex -> fill : anonymous RecallResult deps + SidInfo
fill -> fill : clear output, merge rids
fill -> fill : build gcms_context(logid, ua, flow_loc, sids)
fill -> gcms : query_common(merge_rids, context, scene=RECALL)
gcms -> gcms : maybe switch scene=SEARCH
note right of gcms
UA/flow_loc/search sid 命中时
server_cache_only=false
end note
gcms -> ifcs : get(ifcs_ctx, nids)
ifcs --> gcms : DocInfoMap
gcms --> fill : ret + result map
alt ret != 0
  fill -> fill : WARNING and return -1
else success
  loop each queue item
    fill -> meta : find(tmp->rid)
    alt found and pointer valid
      fill -> out : set _video_info + gcms_data
      fill -> fill : optional copy-on-write field edits
    else missing/null
      fill -> fill : log/skip
    end
  end
end
@enduml
```

## 4. 配置/结构信息图

```infographic
infographic hierarchy-structure
data
  title FillMeta 数据结构契约
  desc setup 阶段声明依赖，process 阶段批量查询并写回
  items
    - label FillMetaBaseContext
      desc handle_name, merge_rids, complete_result, gcms map
      children
        - label merge_rids
          desc unordered_set<uint64_t>，跨队列去重
        - label gcms_common_pb_meta_map
          desc rid 到 MicroVideoInfo 的 IFCS 返回结果
        - label complete_result
          desc RecallResultWithMeta 输出槽
    - label GcmsComponent
      desc IFCS SDK 单例封装
      children
        - label query_common
          desc MicroVideoInfo 正排查询
        - label query_news
          desc NewsInfo 正排查询
    - label Context
      desc logid, ua, flow_loc, sids 条件
      children
        - label server_cache_only
          desc 默认 true，搜索场景会关闭
        - label scene
          desc RECALL 或 SEARCH
```

## 5. Pitfalls 卡片

<div class="card-frame" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f4f3f0;padding:18px;border-radius:16px;margin:16px 0;color:#1f2937;">
<div class="card" style="background:#fffdf8;border:1px solid #ded7c9;border-radius:14px;padding:18px;display:grid;grid-template-columns:2fr 1fr;gap:16px;">
<div>
<div style="font-size:12px;font-weight:800;letter-spacing:.08em;color:#7c6853;text-transform:uppercase;">pitfall</div>
<div style="font-size:28px;font-weight:850;line-height:1.15;margin:8px 0;">FillMeta 失败不是“单 item 缺字段”那么简单</div>
<div style="font-size:14px;line-height:1.65;color:#4b5563;">`query_common()` 失败会让整个 processor 返回 -1；而单个 rid 缺正排只是跳过或不挂 meta。排障时必须先区分 RPC 级失败、map miss、返回空指针、以及 copy-on-write 后字段被实验覆盖。</div>
</div>
<div style="border-left:4px solid #7c6853;padding-left:12px;font-size:13px;line-height:1.6;color:#374151;">
<b>定位优先级</b><br>1. IFCS ret / `call_ifcs_recall fail`<br>2. `after gcms rpc nid size` 与 total size 差距<br>3. `gcms data is null` 日志<br>4. 实验分支是否改写 `_video_info_copy`
</div>
</div>
</div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title FillMeta 排障 checklist
  items
    - label 确认图配置是否真的包含 FillMeta 类节点
      desc 检查当前 scene graph include 与 vertex 依赖，而不是只看 processor 源码
      done false
    - label 检查 merge_rids 规模
      desc `fill_meta_prepare` SIA 值过低说明上游召回或依赖为空
      done false
    - label 检查 IFCS scene 与 server_cache_only
      desc 搜索场景会切 `GCMS_SECNE_SEARCH` 并关闭 server cache only
      done false
    - label 对比 total nid size 与 after gcms rpc nid size
      desc 差距大时优先查 IFCS 覆盖率/缓存/场景
      done false
    - label 检查 copy-on-write 实验
      desc 合集、IP 等分支会改写 `_video_info_copy` 后重新挂 `gcms_data`
      done false
```

## 7. 证据来源

- `src/processor/fill_meta.h:22-70`
- `src/processor/fill_meta.cpp:126-153`
- `src/processor/fill_meta.cpp:176-252`
- `src/processor/fill_meta.cpp:285-321`
- `src/processor/fill_meta.cpp:324-463`
- `src/plugin/gcms_component.cpp:40-79`
