# 2026-10-08 业务库代码理解：GCMS / UMS / Redis 业务侧的 parser 配置装配与热路径写入

> 日期：2026-10-08（周四）
> 配对说明：本文与同日基础库笔记 `feed-gr/parser` 解析器框架合同配对。基础库侧讲的是 **AnyParser / Parser / AssignDsParser 三级合同**；本文讲的是**业务插件如何用配置字符串把这些抽象实例化，并在请求热路径上驱动写入**。两侧的真实代码边界是：框架定义 `initialize/parse` 契约，业务插件持有 `ConfigUnit → parser 名字 → Schema → FieldAccessor` 的装配链。
> 回退说明：本次运行**未找到 daily-plan 文件**，主题由历史笔记语料 + 同快照采纳计数（74 处注册）推得。**KU / 业务上下文需人工补充**。

---

## 一、架构全景：三类业务插件共享同一条装配链

<div class="arch-wrapper" style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:1100px;margin:0 auto;padding:18px;background:#f8fafc;border-radius:10px;">
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:13px;">
<div style="background:#fff;border-top:4px solid #3d5a80;padding:15px;border-radius:8px;box-shadow:0 1px 4px rgba(15,23,42,.07);">
<div style="font-size:11px;letter-spacing:.14em;color:#64748b;font-weight:700;">GCMS PLUGIN</div>
<div style="font-size:15px;font-weight:700;margin:8px 0;color:#1a1a1a;">GCMS 内容特征抽取</div>
<div style="font-size:12.5px;line-height:1.8;color:#475569;">
输入：GCMS JSON 串<br>
parser：<code>json_int32_parser</code><br>
<code>json_string_parser</code><br>
字段：<code>quality_score.final_score</code> → <code>final_score</code><br>
语义：内容质量分 / 标题 / 反作弊分
</div>
</div>
<div style="background:#fff;border-top:4px solid #0f766e;padding:15px;border-radius:8px;box-shadow:0 1px 4px rgba(15,23,42,.07);">
<div style="font-size:11px;letter-spacing:.14em;color:#64748b;font-weight:700;">UMS PLUGIN</div>
<div style="font-size:15px;font-weight:700;margin:8px 0;color:#1a1a1a;">用户特征装配</div>
<div style="font-size:12.5px;line-height:1.8;color:#475569;">
输入：UMS 返回体<br>
parser：按 feature 配置项可选<br>
可选 <code>field_schema</code> 覆盖<br>
可选 <code>post_handler</code><br>
可选 <code>origin_field</code>
</div>
</div>
<div style="background:#fff;border-top:4px solid #b45309;padding:15px;border-radius:8px;box-shadow:0 1px 4px rgba(15,23,42,.07);">
<div style="font-size:11px;letter-spacing:.14em;color:#64748b;font-weight:700;">REDIS PLUGIN</div>
<div style="font-size:15px;font-weight:700;margin:8px 0;color:#1a1a1a;">Redis 特征直取</div>
<div style="font-size:12.5px;line-height:1.8;color:#475569;">
输入：<code>RedisReply</code><br>
parser：Redis 系列专用<br>
可选 <code>schema</code> 覆盖<br>
按 group 维度装配<br>
通道与 parser 一一对应
</div>
</div>
</div>
<div style="margin-top:14px;background:#0f172a;color:#e2e8f0;padding:13px 16px;border-radius:8px;font-size:13px;line-height:1.7;">
<b style="color:#7dd3fc;">共享装配链：</b>读配置项 <code>parser</code> → <code>ApplicationContext::get_or_create&lt;Base&gt;(parser_name)</code> → 解析 schema（配置指定或插件默认）→ <code>parser-&gt;initialize(conf, schema)</code> → 存入 <code>_parser_map</code> → 请求期 <code>parser-&gt;parse(...)</code>。
</div>
</div>

## 二、装配链时序：配置 → 工厂 → schema → 字段句柄

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
actor Conf as 配置文件 (.conf)
participant "BusinessPlugin\n(init)" as P
participant "ApplicationContext" as AC
participant "AnyParser 实现\n(StringDsParser 等)" as Parser
participant "SchemaManager" as SM
participant "DynamicStruct::Schema" as Schema
participant "FieldAccessor" as FA

Conf -> P : feature_conf["parser"] = "json_int32_parser"
P -> P : 判断 selfType() == CONFIG_ERROR_TYPE
P -> AC : get_or_create<StringDsParser>(parser_name)
AC --> P : 工厂返回实例（由 BABYLON 注册表命中）
alt factory 返回 nullptr
  P -> P : LOG(FATAL) + return -1
else
  P -> SM : get_schema(feature_conf["field_schema"])
  SM --> P : Schema*
  P -> Parser : initialize(feature_conf, schema)
  Parser -> Schema : get_field("struct_field")
  Schema --> Parser : FieldAccessor
  Parser -> Parser : 缓存 _struct_field_ptr
  Parser --> P : 0
  P -> P : _parser_map.emplace(feature_name, parser)
end
@enduml
```

### 2.1 UMS 插件：四段可选装配

`ums_plugin.cpp:302-345` 是三插件中装配语义最完整的一段，四类配置项全部可选：

1. **`parser`（:303-323）**：缺失则 `continue` 跳过整个 feature；存在则工厂取实例，失败 `LOG(FATAL)` 并整体 init 返回 -1。可选的 `field_schema`（:313-318）允许**逐 feature 覆盖**插件默认 `_schema`，这是同一进程内不同 feature 落到不同 struct 的唯一手段。
2. **`post_handler`（:326-331）**：独立工厂 `UmsFeaturePostHandler`，与 parser 是两条正交的生命周期，解析完成后再跑。
3. **`origin_field`（:334-343）**：在**插件默认 schema** 上取 `FieldAccessor`，注意这里用的是 `_schema->get_field(...)` 而非 per-feature schema，与第 1 步存在 schema 选择上的不对称——排查 origin_field 找不到时要先确认该 feature 是否覆盖过 schema。
4. **`prefix`（:296-300）**：字符串前缀表，与解析无关但同属装配期。

### 2.2 Redis 插件：类型参数化的工厂取实例

`single_redis_plugin.cpp:53-73` 的工厂调用把完整类型写进了模板参数：

```
get_or_create<Parser<RedisReply, DynamicStruct>>(parser_name)
```

而不是先取基类再转型。这意味着**Redis 系 parser 必须精确派生自 `Parser<RedisReply, DynamicStruct>`**，配置里写一个 JSON parser 名字不会编译期报错，但运行时工厂查找会 miss 并返回 nullptr（:58-61 `LOG(FATAL)`）。schema 也是 per-group 可选覆盖（:62-71），且覆盖时 `schema_ptr` 为空会直接失败（:66-69）——与 UMS 的静默回落相反。

`:78` 有一条显式性能约束注释：调用 fetch 之前 `mid_results` 中每一项的 status 必须保持初始化时的 0。这说明 parser 的输出对象**会被复用/预分配**，与基础库侧 `unique_ptr` 失败即置空的语义需要在业务侧对齐理解。

### 2.3 GCMS 插件：配置即字段映射表

GCMS 侧的配置直接把 JSON 路径与 struct 字段并列：

```infographic
infographic list-grid-badge-card
data
  title GCMS 插件字段映射（节选自 gcms_plugin.conf）
  desc json_field 决定取值路径，struct_field 决定落点，parser 决定类型转换
  items
    - label url_sign ← json_uint64_parser
      desc json_field: url_sign → struct_field: url_sign
      icon mdi/link-variant
    - label is_excellent_hot ← json_int32_parser
      desc json_field: excellent_hot_info.is_excellent_hot → struct_field: is_excellent_hot
      icon mdi/flash
    - label final_score_tmp ← json_int32_parser
      desc json_field: anti_spam.final_score_tmp → struct_field: final_score_tmp
      icon mdi/shield-alert
    - label title ← json_string_parser
      desc json_field: title → struct_field: title
      icon mdi/text
    - label final_score ← json_int32_parser
      desc json_field: quality_score.final_score → struct_field: final_score
      icon mdi/chart-line
    - label prev_score ← json_int32_parser
      desc json_field: quality_score.prev_score → struct_field: prev_score
      icon mdi/chart-bell-curve
    - label copy_level ← json_int32_parser
      desc json_field: content_quality.copy_level → struct_field: copy_level
      icon mdi/content-copy
    - label disgust_level ← json_int32_parser
      desc json_field: content_quality.disgust_level → struct_field: disgust_level
      icon mdi/emoticon-angry
theme
  palette #3D5A80 #0F766E #B45309 #64748B
```

映射规律清晰：**反作弊、质量分、内容质量三个子树全部走 int32，少数标识/签名类走 uint64 或 string**。新增一个内容质量字段的标准动作就是复制一个 `@field` 块，而不是写 C++ 代码——这是框架合同带来的直接业务收益。

### 2.4 PB 侧业务 parser：热路径上的反射式取值

业务侧的 protobuf parser 集中在 `generator-service/src/plugin/gcms_pb_parser.cc` / `dc-gr` 同名文件，它们统一继承框架的 `PBTParser<T>`，只实现 `parse(const Message&, DynamicStruct*)` 一个函数体，内部用 `get_field(output)` 下钻取到叶字段再赋值。观测到的派生族包括 `PBQualityEmergentFlagsParser`、`PBDislikeSlotSlotListParser`、`PBStrategyExinfoParser`、`PBIsMicrovideoParser`、`PBUniqueClassParser`、`PBOnlyFirstKeyMapParser`、`PBFeatureMappingParser` 等（`gcms_pb_parser.cc:78-303`）。

这些 parser 的存在意义是：**把 protobuf 的反射能力接入 DynamicStruct 字段体系**，让后续业务算子可以像读 GCMS JSON 字段一样读 pb 字段，而不必各自处理 `Message` 与 `FieldDescriptor`。

## 三、Pitfalls

<div class="card-frame" style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:1100px;margin:0 auto;padding:16px;background:#f5f7fa;border-radius:10px;">
<div class="card" style="background:#fff;padding:18px 20px;border-radius:8px;border-left:4px solid #b45309;">
<div class="card-meta" style="font-size:11px;letter-spacing:.14em;color:#64748b;font-weight:700;">PITFALLS · 业务装配侧</div>
<div class="card-title" style="font-size:24px;font-weight:800;color:#1a1a1a;margin:8px 0 4px;letter-spacing:-.02em;">五个装配期陷阱</div>
<div class="card-bar" style="height:4px;background:#b45309;width:72px;border-radius:2px;margin:10px 0 14px;"></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:13.5px;line-height:1.7;color:#334155;">
<div style="border-top:2px solid #b45309;padding-top:9px;"><b>1. 缺 parser ≠ 报错</b><br>UMS 侧 `parser` 配置缺失直接 `continue`，该 feature 静默消失。Redis 侧则是可选但一旦 schema 覆盖名无效就硬失败。两种策略不一致。</div>
<div style="border-top:2px solid #3d5a80;padding-top:9px;"><b>2. 工厂 miss ≠ 编译错误</b><br>parser 名字是运行期字符串。Redis 侧写成 `<code>get_or_create&lt;Parser&lt;RedisReply, DynamicStruct&gt;&gt;</code>`，名字写错只在 init 时 LOG(FATAL)。</div>
<div style="border-top:2px solid #0f766e;padding-top:9px;"><b>3. schema 覆盖范围不一致</b><br>UMS 的 parser 可用 per-feature schema，但 origin_field 仍走插件默认 `_schema`。跨 schema 组合是隐性故障源。</div>
<div style="border-top:2px solid #64748b;padding-top:9px;"><b>4. json_field 路径写错是静默的</b><br>GCMS 侧路径不存在不会触发 FATAL，只是字段缺失。下游读到的是默认值，业务上表现为「分数突然归零」。</div>
<div style="border-top:2px solid #64748b;padding-top:9px;"><b>5. mid_results 预分配状态约束</b><br>Redis 插件要求 fetch 前每项 status 保持 0。复用输出对象时若提前写过 status，会与 parser 的失败置空语义冲突。</div>
</div>
<div class="card-endmark" style="text-align:right;color:#94a3b8;margin-top:14px;">∎</div>
</div>
</div>

## 四、调试 checklist

```infographic
infographic list-column-done-list
data
  title 业务 parser 装配排障 checklist
  desc 从配置文件到最终字段值的六段定位
  items
    - label 核对 parser 名字与注册表
      done false
      desc 名字必须是 BABYLON_REGISTER_FACTORY_COMPONENT_WITH_TYPE_NAME 第三个参数
    - label 核对 parser 基类是否匹配载体
      done false
      desc RedisReply / rapidjson::Value / FbTable / Message 四类载体各有专属基类
    - label 核对 struct_field 在目标 schema 中存在
      done false
      desc UMS 可 per-feature 覆盖 schema，先确认覆盖范围
    - label 核对 json_field / pb_field 路径可达
      done false
      desc 路径错误为静默字段缺失，需要用原始返回体对照
    - label 核对字段类型与 parser 类型一致
      done false
      desc TypeId 不匹配在 PB 侧是启动期 FATAL，JSON 侧可能表现为转换失败
    - label 区分 100 / -1 / 缺失三类异常
      done false
      desc 100=未配置 parser；-1=解析或取值失败；缺失=字段根本没进 struct
theme
  palette #3D5A80 #0F766E #B45309 #64748B
```

## 五、证据来源

- `common-component/src/plugin/ums_plugin.cpp:296-345`（UMS 四段可选装配：prefix/parser/field_schema/post_handler/origin_field）
- `common-component/src/plugin/single_redis_plugin.cpp:52-76`（Redis 侧类型参数化工厂取实例与 per-group schema 覆盖）
- `common-component/src/plugin/single_redis_plugin.cpp:78-85`（mid_results status 必须保持 0 的性能约束注释）
- `common-component/src/plugin/pcs_common_plugin.cpp:33-41`（PCS 侧按 parser 配置数组循环初始化）
- `common-component/src/on_demand_component/ucs_component.cc:31-44`（ucs_component 的 parser 名字取实例与 initialize）
- `common-component/test/conf/gcms_plugin.conf:20-62`（GCMS 字段映射三元组：json_field / struct_field / parser）
- `common-component/test/conf/gcms_plugin_with_cond.conf:39-63`（带条件分支的 GCMS 字段映射）
- `common-component/test/conf/ucs_component.conf:27-47`（ucs_component 侧 json_string_parser 使用）
- `parser/src/gcms_pb_parser.h:95-152`（PBFieldParser 100 哨兵与 PBTParser 类型校验，框架侧边界）
- `generator-service/src/plugin/gcms_pb_parser.cc:78-303`（业务侧 PB parser 派生族与 get_field 取值写法）
