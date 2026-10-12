# Dapper Collector 业务库：天级长尾 path 汇聚的配置契约与 core 复盘

> 日期：2026-06-07  
> 主题来源：未发现本机 `daily-plan` 文件；按双 Markdown 工作流延续 Dapper Collector 天级长尾汇聚问题。本篇从业务配置视角解释为什么“天级 long_tail_path_sid 只配 4 列”会和下载/计算链路的 6 列契约冲突。  
> 范围：`long_tail_path_` / `long_tail_path_sid_` 下载配置、上传配置、`conv_duration_aggregate_path_data()` 合并逻辑、sid 与非 sid 的业务语义差异。  
> 内网文档：已按要求设置 `BAIDU_CC_USERNAME=hujiyang` 并确认 `ku` 可用；但本次任务未提供 KU URL/doc-id，业务发布背景与线上变更单需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div style="font-size:22px;font-weight:800;margin-bottom:6px;">天级长尾 path 汇聚：分钟/小时表 → 天级表</div>
<div style="font-size:13px;color:#64748b;margin-bottom:16px;">业务目标是把低粒度 long_tail_path 数据按 module、channel/version、sid 等维度重新合并；风险点在 sid 表和非 sid 表的列契约不一致。</div>
<div style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">下载配置层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>long_tail_path_</b><br><span style="font-size:12px;color:#64748b;">module,channel,version,...</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>long_tail_path_sid_</b><br><span style="font-size:12px;color:#64748b;">历史示例仅 4 列</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>duration</b><br><span style="font-size:12px;color:#64748b;">10 / 60 / 3600 → upload 60 / 3600 / 86400</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>table_type</b><br><span style="font-size:12px;color:#64748b;">4 非 sid，5 sid</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">业务汇聚层</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>download_result</b><br><span style="font-size:12px;color:#64748b;">table → sid → key → data</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>split key</b><br><span style="font-size:12px;color:#64748b;">channel#version#module</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>parse path</b><br><span style="font-size:12px;color:#64748b;">base64 + gzip + protobuf</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>merge modules</b><br><span style="font-size:12px;color:#64748b;">node/module/module_all</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>build sql</b><br><span style="font-size:12px;color:#64748b;">写入天级目标表</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">上传落表层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>non sid cols</b><br><span style="font-size:12px;color:#64748b;">start_time,channel,version,...</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>sid cols</b><br><span style="font-size:12px;color:#64748b;">sid,start_time,...</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>path_info</b><br><span style="font-size:12px;color:#64748b;">合并后的 KeyPathResponse</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>batch_size</b><br><span style="font-size:12px;color:#64748b;">控制批量写入</span></div>
</div>
</div>
</div>

## 2. 核心结论

- 业务配置中非 sid 的 `long_tail_path_` 下载列是 `module,channel,version,fuwtype,refresh_state,path_info`，与非 common 下载代码的 6 列契约一致；证据见 `conf/0_download_database.conf:103-116`、`conf/0_download_database.conf:133-146`、`conf/0_download_database.conf:163-176`。
- 同一配置中 `long_tail_path_sid_` 下载列是 `module,fuwtype,refresh_state,path_info`，`columns_num: 4`，不包含 channel/version；证据见 `conf/0_download_database.conf:118-131`、`conf/0_download_database.conf:148-161`、`conf/0_download_database.conf:178-191`。
- 但 `conv_duration_aggregate_path_data()` 期望下载阶段提供的 key 能拆成 3 段：`channel#version#module`；如果下载层无法构造这个 key，后续会跳过或丢失 channel/version 维度；证据见 `src/conv/long_tail_conv.cc:71-80`。
- 计算层会把 path_info 作为 `node.receiver` 传入 `longtail_parse_and_construct()`，因此下载层对 `row[5]` 的读取实际上是在读取 path_info；证据见 `src/conv/long_tail_conv.cc:84-90`。
- 上传侧 sid 天级表的列包含 `sid,start_time,fuwtype,refresh_state,idc_index,module,end_time,timestamp,count,path_info`，没有 channel/version；这解释了为什么业务最初可能把 sid 下载配置也简化成 4 列，但下载/计算中间态仍依赖 channel/version key；证据见 `conf/0_upload_database.conf:67-77`。
- 修复方向有两类：要么让 `long_tail_path_sid_` 下载也提供 channel/version 以复用现有非 common 链路；要么显式实现 sid 表专用解析与计算 key，不能让 4 列配置误入 6 列 ABI。

## 3. 业务链路时序图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "daily task" as task
participant "download config" as conf
participant "Download::download_longtail" as dl
database "download_result" as dr
participant "LongTailConv::conv_duration_aggregate_path_data" as conv
participant "longtail_parse_and_construct" as parse
database "upload_result" as upload
participant "Upload" as up
task -> conf : select long_tail_path / long_tail_path_sid blocks
conf -> dl : SQL + columns + columns_num
dl -> dr : sid_item[key].push(AggregateData)
alt key has channel#version#module
  dr -> conv : table_download_result
  conv -> conv : split key into channel/version/module
  conv -> parse : node.receiver as path_info
  parse --> conv : ModulePathMap
  conv -> upload : conv_duration_path_merge(...)
  upload -> up : batched SQL for daily table
else sid config only 4 columns
  dl -> dl : cannot safely read channel/version/path_info at expected indexes
  dl -> dr : row invalid warning or historical core
end
@enduml
```

## 4. 配置结构信息图

```infographic hierarchy-structure
data
  title 长尾 path 下载/上传配置结构
  desc 下载侧的 sid 表少 channel/version，上传侧的 sid 表也不保留 channel/version；中间计算却复用非 sid key。
  items
    - label download long_tail_path_
      desc table_type=4，columns_num=6
      children
        - label module
          desc row[0]
        - label channel
          desc row[1]，参与 key
        - label version
          desc row[2]，参与 key
        - label path_info
          desc row[5]，base64 gzip protobuf
    - label download long_tail_path_sid_
      desc table_type=5，columns_num=4
      children
        - label module
          desc row[0]
        - label fuwtype_refresh_state
          desc row[1..2]
        - label path_info
          desc row[3]，但非 common 代码历史读取 row[5]
    - label upload long_tail_path_sid_
      desc sid,start_time,fuwtype,refresh_state,idc_index,module,end_time,timestamp,count,path_info
      children
        - label sid
          desc 分表/切片维度
        - label no channel_version
          desc 目标表不保留，但中间态仍需明确策略
```

## 5. Pitfalls 卡片

<div class="card-frame" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:980px;margin:18px 0;">
<div class="card" style="background:#f4f3f0;border:1px solid #ded8cf;border-radius:18px;padding:22px;color:#2f332d;">
<div class="card-meta" style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#2d6a4f;">Pitfall · business config</div>
<div class="card-title" style="font-size:30px;font-weight:850;letter-spacing:-.02em;margin:6px 0 10px;">目标表不存 channel/version，不代表中间链路不需要</div>
<div class="card-subtitle" style="font-size:16px;line-height:1.65;color:#4d574c;margin-bottom:14px;">`long_tail_path_sid_` 上传列确实只有 sid/module/path_info 等字段，但复用 `conv_duration_aggregate_path_data()` 时，下载结果 key 仍按 `channel#version#module` 组织。</div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;">
<div style="border-top:3px solid #2d6a4f;padding-top:10px;font-size:14px;line-height:1.7;">如果为了贴合目标表把下载列裁成 4 列，会破坏下载层和计算层之间的隐式 ABI。业务修复不能只改 `columns_num`，还要明确 sid 表是否需要继承 channel/version、默认成 `-1`，还是走独立 key 结构。</div>
<div style="background:#e8eee6;border-radius:12px;padding:12px;font-size:13px;line-height:1.6;"><b>复盘关键词</b><br>配置裁剪<br>隐式 ABI<br>sid 专用链路<br>path_info 下标</div>
</div>
</div>
</div>

## 6. 调试 checklist

```infographic list-column-done-list
data
  title 天级长尾汇聚调试 checklist
  desc 用于复盘 core、空产出或天级表数据缺失。
  items
    - label 先定位表块
      desc 区分 long_tail_path_ 与 long_tail_path_sid_，确认 table_type 和 upload_duration_s。
      done true
    - label 对照下载 ABI
      desc 非 common 链路必须检查是否能提供 module/channel/version/fuwtype/refresh_state/path_info 六列。
      done true
    - label 检查计算 key
      desc 在 conv_duration_aggregate_path_data 中确认 split_list.size()==3，否则该条会被跳过。
      done true
    - label 验证 path_info
      desc 对下载出的 path_info 做 base64/gzip/protobuf 解码，确认 KeyPathResponse 可解析。
      done false
    - label 对照上传列
      desc sid 目标表不含 channel/version 时，需要明确默认值或专用汇聚逻辑。
      done true
    - label 补充线上发布记录
      desc 本次未提供 KU URL/doc-id，配置变更背景和影响窗口需人工补充。
      done false
```

## 7. 证据来源

- `conf/0_download_database.conf:103-116`：10s → 60s 非 sid 长尾 path 下载列，6 列。
- `conf/0_download_database.conf:118-131`：10s → 60s sid 长尾 path 下载列，4 列。
- `conf/0_download_database.conf:163-191`：小时 → 天级长尾 path 下载配置。
- `src/component/download.cc:222-249`：非 common 下载分支按 6 列解析并构造 `channel#version#module`。
- `src/conv/long_tail_conv.cc:41-108`：`conv_duration_aggregate_path_data()` 汇聚入口。
- `src/conv/long_tail_conv.cc:71-90`：拆 key 并解析 `node.receiver/path_info`。
- `src/conv/long_tail_conv.cc:201-281`：合并后构造上传 SQL 字段。
- `conf/0_upload_database.conf:67-77`：天级 sid 长尾 path 上传列。
