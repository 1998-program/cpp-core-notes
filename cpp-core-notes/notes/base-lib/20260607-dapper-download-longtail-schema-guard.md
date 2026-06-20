# Dapper Collector 基础库：download_longtail 列契约与行级防护

> 日期：2026-06-07  
> 主题来源：未发现本机 `daily-plan` 文件；按双 Markdown 工作流在已有 Dapper 长尾汇聚排障线索上继续展开。本篇聚焦基础库下载层 `Download::download_longtail()` 的列契约、字段防护与 common / 非 common 分支差异。  
> 范围：MySQL 拉取、`columns_num` 校验、长尾 path 行解析、common 分支解析、异常行跳过策略。  
> 内网文档：已按要求设置 `BAIDU_CC_USERNAME=hujiyang` 并确认 `ku` 可用；但本次任务未提供 KU URL/doc-id，本文以代码库检索结果为主，线上口径需人工补充。

## 1. 架构全景图

<div class="arch-wrapper" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8dee9;border-radius:16px;padding:18px;margin:16px 0;color:#1f2937;">
<div style="font-size:22px;font-weight:800;margin-bottom:6px;">download_longtail：SQL 行 → AggregateData → 计算层输入</div>
<div style="font-size:13px;color:#64748b;margin-bottom:16px;">基础库下载层的关键不是业务语义，而是把配置声明的列顺序稳定映射为后续 LongTailConv 可消费的 key/value 结构。</div>
<div style="background:#ffffff;border-left:5px solid #3d5a80;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">配置契约层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>table block</b><br><span style="font-size:12px;color:#64748b;">table_name / table_type / duration</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>columns</b><br><span style="font-size:12px;color:#64748b;">字段顺序即解析 ABI</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>columns_num</b><br><span style="font-size:12px;color:#64748b;">仅检查数量，不检查语义</span></div>
<div style="background:#eef2f7;border-radius:10px;padding:10px;"><b>module flag</b><br><span style="font-size:12px;color:#64748b;">决定 common / 非 common 解析分支</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #2d6a4f;border-radius:12px;padding:14px;margin-bottom:12px;">
<div style="font-weight:700;margin-bottom:10px;">下载执行层</div>
<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;">
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>connect</b><br><span style="font-size:12px;color:#64748b;">mysql_real_connect</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>query</b><br><span style="font-size:12px;color:#64748b;">mysql_query + cost log</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>fetch</b><br><span style="font-size:12px;color:#64748b;">mysql_use_result / fetch_row</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>shape check</b><br><span style="font-size:12px;color:#64748b;">fields_num == columns_num</span></div>
<div style="background:#edf7f2;border-radius:10px;padding:10px;"><b>null guard</b><br><span style="font-size:12px;color:#64748b;">row[0/1/2/5] 非空</span></div>
</div>
</div>
<div style="background:#ffffff;border-left:5px solid #b45309;border-radius:12px;padding:14px;">
<div style="font-weight:700;margin-bottom:10px;">解析输出层</div>
<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>non-common key</b><br><span style="font-size:12px;color:#64748b;">channel#version#module</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>receiver</b><br><span style="font-size:12px;color:#64748b;">path_info base64 pb</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>common key</b><br><span style="font-size:12px;color:#64748b;">module → vector</span></div>
<div style="background:#fff7ed;border-radius:10px;padding:10px;"><b>sid_item</b><br><span style="font-size:12px;color:#64748b;">DownloadResult 中间结构</span></div>
</div>
</div>
</div>

## 2. 核心结论

- `download_longtail()` 的第一层保护是 `mysql_num_fields(result) == sql_info.columns_num`，它能挡住“实际返回列数”和“配置列数”不一致，但挡不住“配置列顺序与代码读取语义不一致”；证据见 `src/component/download.cc:209-217`。
- 非 common 分支当前显式要求至少 6 列，并检查 `row[0]`、`row[1]`、`row[2]`、`row[5]` 非空，再读取 `module/channel/version/fuwtype/refresh_state/path_info`；证据见 `src/component/download.cc:222-249`。
- 非 common 分支的 `sid_item` key 是 `channel#version#module`，而真正的 path protobuf 字符串放在 `AggregateData.receiver`；后续计算层会按这个 key 拆出 channel/version/module；证据见 `src/component/download.cc:236-250` 与 `src/conv/long_tail_conv.cc:71-90`。
- common 分支直接走 `common_download_longtail()`，期望列顺序是 `module,fuwtype,refresh_state,path_info,product,channel`，key 只使用 `sender/module`；证据见 `src/component/download.cc:218-220` 与 `src/component/download.cc:261-279`。
- 因为 common 分支读取 `row[4]`、`row[5]`，配置侧同样必须保证 6 列；如果只给 4 列，基础层字段数校验会通过或失败取决于 `columns_num`，但语义都不安全。
- 本地工作副本中的非 common 分支已加入 `fields_num < 6` 和关键字段空指针防护，能把历史上 `row[5]` 越界/空指针构造 `std::string` 的 core 转为 WARNING + continue；证据见 `src/component/download.cc:222-235`。

## 3. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "DownloadContext" as ctx
participant "Download::download_longtail" as dl
database "MySQL" as mysql
participant "MYSQL_RES" as res
participant "common_download_longtail" as common
participant "non-common parser" as parser
database "sid_item" as sid
ctx -> dl : download_table_sql[table][sql_info]
dl -> mysql : mysql_query(sql_info.sql)
mysql --> dl : result stream
dl -> res : mysql_use_result()
loop each row
  dl -> res : mysql_fetch_row()
  dl -> res : mysql_num_fields()
  alt fields_num != columns_num
    dl -> dl : WARNING fetch size error; break
  else module == dapper_collector_common
    dl -> common : row[0..5]
    common -> sid : key=module, value=AggregateData
  else non-common
    dl -> parser : guard fields_num>=6 and row[0/1/2/5]
    alt invalid
      parser -> dl : WARNING longtail row invalid; continue
    else valid
      parser -> sid : key=channel#version#module
    end
  end
end
@enduml
```

## 4. 列契约信息图

```infographic compare-binary-horizontal-underline-text-vs
data
  title download_longtail 两套列 ABI
  desc 两个分支共享函数入口，但 row 下标含义完全不同，配置必须和 module_name 分支一起审查。
  items
    - label 非 common 分支
      desc row[0]=module, row[1]=channel, row[2]=version, row[3]=fuwtype, row[4]=refresh_state, row[5]=path_info；key=channel#version#module
      value 6
    - label common 分支
      desc row[0]=module, row[1]=fuwtype, row[2]=refresh_state, row[3]=path_info, row[4]=product, row[5]=channel；key=module
      value 6
```

## 5. Pitfalls 卡片

<div class="card-frame" style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:980px;margin:18px 0;">
<div class="card" style="background:#fbf9f1;border:1px solid #e7dccb;border-radius:18px;padding:22px;color:#332b24;">
<div class="card-meta" style="font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#7c6853;">Pitfall · schema contract</div>
<div class="card-title" style="font-size:30px;font-weight:850;letter-spacing:-.02em;margin:6px 0 10px;">`columns_num` 不是字段语义校验</div>
<div class="card-subtitle" style="font-size:16px;line-height:1.65;color:#5f5144;margin-bottom:14px;">只要 SQL 返回列数与配置一致，下载层就会继续向下解析；如果列顺序少了 channel/version，代码仍可能按旧 ABI 读取 row[5]。</div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;">
<div style="border-top:3px solid #7c6853;padding-top:10px;font-size:14px;line-height:1.7;">排查 core 时不要只看 `columns_num` 是否相等，要同时对照 `columns:` 字段列表、`FLAGS_module_name` 分支和代码中的 row 下标。长尾 path 的 path_info 是压缩后的 protobuf/base64 字符串，错误读取会在构造 string 或后续解压解析时表现为不稳定崩溃。</div>
<div style="background:#f3eadf;border-radius:12px;padding:12px;font-size:13px;line-height:1.6;"><b>快速判断</b><br>非 common：必须能解释 row[1]=channel、row[2]=version。<br>common：必须能解释 row[4]=product、row[5]=channel。</div>
</div>
</div>
</div>

## 6. 调试 checklist

```infographic list-column-done-list
data
  title download_longtail 排障 checklist
  desc 从配置契约、行形状、字段语义三层定位长尾下载问题。
  items
    - label 确认运行模块
      desc 检查 FLAGS_module_name 是否为 dapper_collector_common，决定解析分支。
      done true
    - label 对齐 columns 与 row 下标
      desc 非 common 看 module/channel/version/fuwtype/refresh_state/path_info；common 看 module/fuwtype/refresh_state/path_info/product/channel。
      done true
    - label 检查 columns_num
      desc 确认 SQL 实际返回列数、配置 columns_num、代码最小读取下标一致。
      done true
    - label 搜索 longtail row invalid
      desc 若出现该 WARNING，优先查看 fields_num、module_null、channel_null、version_null、path_info_null。
      done true
    - label 回放 path_info
      desc 对 receiver/path_info 做 base64+gzip+protobuf 解析，确认不是损坏数据。
      done false
    - label 补充内网口径
      desc 本次未提供 KU URL/doc-id，线上配置发布历史需人工补充。
      done false
```

## 7. 证据来源

- `src/component/download.cc:181-259`：`Download::download_longtail()` 主流程。
- `src/component/download.cc:209-217`：字段数量校验。
- `src/component/download.cc:222-249`：非 common 分支关键字段防护与 key 构造。
- `src/component/download.cc:261-279`：`common_download_longtail()` 字段读取顺序。
- `src/conv/long_tail_conv.cc:71-90`：计算层拆分 `channel#version#module` 并解析 path_info。
- `conf/0_download_database.conf:103-191`：长尾 path 下载配置示例。
