# 2026-09-16 基础库理解：AEF FeatureOperator 的类型发射与 Extractor 编排合同

> 日期：2026-09-16  
> 主题选择：未发现本日有效 daily-plan；近期 9 月笔记已覆盖 ApplicationContext、GraphFunction、ObjectPool、SIA/BVAR 等主题，因此回退到邻近未覆盖的 **feature-extractor / AEF 特征抽取基础合同**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：`Extractor` 初始化与 feature list 编排、`FeatureOperator::Emitter` 的类型发射适配、状态型窗口容器，以及离线 worker 如何消费这些基础合同。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">配置入口</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">extract_offline.conf</div><div style="margin-top:8px;font-size:12px;color:#52606d;">按 tag 声明 user、item、rule、pos_related_context、item_related_context 五组 feature list。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">抽取框架</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">Extractor + FeatureList</div><div style="margin-top:8px;font-size:12px;color:#52606d;">入口初始化 Extractor；worker 通过 get_feature_list 取得可复用列表，并通过 parent/arg 建立上下文链。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#102a43;text-transform:uppercase;letter-spacing:.04em;">类型发射</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">Simple*TypedOp → emitting</div><div style="margin-top:8px;font-size:12px;color:#52606d;">模板适配 Any 值、校验真实类型，并统一转换为 emitter.emit(raw_feature, fea_val)。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 42px 1fr 42px 1fr 42px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">conf tag</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:6px;padding:10px;text-align:center;color:#047857;font-size:13px;">feature list</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">operator</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f1f5f9;border:1px solid #cbd5e1;border-radius:6px;padding:10px;text-align:center;color:#334155;font-size:13px;">Example fields</div></div></div>

**一句话结论：** AEF 的基础合同不是单个算子，而是“配置 tag → FeatureList → typed operator → emitter → Example/Label”的稳定链条；排查特征缺失时要先确认 list 绑定和发射类型，再看具体业务字段。

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title AEF FeatureOperator 类型发射与抽取编排
participant "main.cc" as MAIN
participant "Extractor" as EXT
participant "FeatureList" as LIST
participant "Simple*TypedOp" as OP
participant "babylon::Any" as ANY
participant "Emitter" as EMIT
participant "Example / Label" as OUT
MAIN -> EXT : initialize(conf_dir, conf_file)
MAIN -> MAIN : load gflags + dictionaries + threads
MAIN -> EXT : get_feature_list(tag)
EXT --> LIST : user/item/rule/context lists
LIST -> LIST : clear / parent / arg
LIST -> OP : extract(list, sample)
OP -> ANY : get<RealType>()
alt type mismatch or null
  OP --> LIST : RET_CODE / fatal log
else valid value
  OP -> EMIT : emitting(value)
  EMIT -> OUT : emit(feature_text, feature_val)
end
LIST -> OUT : to_example / to_label
@enduml
```

## 2. 合同结构信息图

```infographic
infographic list-grid-badge-card
data
  title AEF 基础合同的五个固定接口面
  desc 每一面都可能造成“特征缺失”，需要按顺序排查
  items
    - label config tag
      desc extract_offline.conf 用 tag 绑定 feature_conf、label_conf 与 filter_conf
      icon mdi:format-list-bulleted
    - label list lifecycle
      desc worker 每轮 clear、clear_parent、parent，并按上下文设置 arg
      icon mdi:recycle
    - label typed operator
      desc SimpleRef/Ptr/Any/State 模板把 Any 转成真实类型
      icon mdi:code-braces
    - label emitting overload
      desc string、tuple、vector、float、int、bool 等类型统一写成 raw_feature/value
      icon mdi:transit-connection-variant
    - label state container
      desc SessionProfile 与 WindowsState 承载跨 item 的点击、展现和窗口状态
      icon mdi:database-clock
    - label output bridge
      desc FeatureList 最终 to_example / to_label，供业务 worker 输出训练样本
      icon mdi:file-export-outline
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 入口初始化先固定 Extractor 与字典依赖

`src/worker/main.cc:64-83` 会读取 `conf_dir/gflags.conf`，再初始化 `Extractor`；`src/worker/main.cc:84-95` 初始化 feature/label 字典并加载用户时长、品类行为等补充字典。基础库视角下，这些动作确定了 feature list 的配置空间与输出 id 空间；如果字典没有加载成功，后续算子即使抽到文本特征，也无法形成稳定 id。

### 3.2 feature list 是配置 tag 的运行态对象

`conf/feature-extractor/video_shentou_LTR/extract_offline.conf:1-24` 将 `user`、`item`、`rule`、`pos_related_context`、`item_related_context` 绑定到不同配置文件。`src/worker/worker.cc:144-173` 用这些 tag 取出 list；tag 名和配置缺一不可，任一 list 为空都会直接 fatal 返回。

### 3.3 list 的 parent/arg 是跨阶段上下文合同

`src/worker/worker.cc:175-187` 清理 item、pos、item context、rule 的缓存和 parent；`src/worker/worker.cc:181-183` 建立 `user → item → pos_related_context → item_related_context` 的父链。随后 `src/worker/worker.cc:226-279` 对 item、pos、item context、rule 逐段设置 `arg` 并调用 `extract()`。这说明上下文特征不是独立算子输出，而是依赖父 list 和上一个已选 index 的链式状态。

### 3.4 emitting 是所有输出类型的统一出口

`src/base_operator.h:22-45` 给 `tuple<string,float>` 提供基础发射；`src/base_operator.h:64-110` 对 vector 类型逐元素展开；`src/base_operator.h:140-191` 对 float、int、bool 等标量以同一个 emitter name 写出。若新增特征类型没有专门重载，就会落到 `unsupport type` 分支，表现为算子逻辑正常但输出缺失。

### 3.5 TypedOp 模板负责 Any 类型校验

`src/base_operator.h:337-386` 的 `SimpleRefTypedOp` 和 `SimplePtrTypedOp` 从 `babylon::Any` 里读取 `RealType`，读取失败时记录实际类型与目标类型并返回错误。`src/base_operator.h:388-480` 的 AnyRef 与 State 版本复用同样模式。排查类型错误时，要看算子声明的输出类型、Any 内真实类型和 emitting 重载是否三者一致。

### 3.6 状态容器承载跨 item 统计与窗口语义

`src/base_operator.h:489-573` 的 `SessionProfile` 汇总 show/click、类别、子类和 attention 的展现点击，并用置信度修正 CTR；`src/base_operator.h:575-631` 的 `WindowsState` 用固定容量环形覆盖机制维护最近窗口。状态型算子如果没有正确 clear 或容量设置错误，会污染后续 item 的上下文特征。

## 4. 排查路径信息图

```infographic
infographic sequence-snake-steps-simple
data
  title 从特征缺失到根因定位的基础库排查顺序
  desc 先确认配置和 list，再进入具体 operator
  items
    - label tag exists
      desc extract_offline.conf 中存在对应 tag，且 feature_conf/label_conf/filter_conf 路径有效
      icon mdi:tag-check-outline
    - label list acquired
      desc get_feature_list 返回非空，并在每轮样本前清理状态
      icon mdi:playlist-check
    - label context ready
      desc parent 链和 arg 索引与当前 context_index 一致
      icon mdi:link-variant
    - label type valid
      desc Any 内类型、TypedOp RealType 与 emitting 重载一致
      icon mdi:shape-outline
    - label output serialized
      desc to_example/to_label 成功，field_id 与字典 id 空间有效
      icon mdi:database-arrow-right
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d7dee8;border-left:6px solid #3d5a80;border-radius:8px;padding:18px;margin:18px 0;color:#1f2937;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#3d5a80;font-weight:800;">Pitfalls · AEF Contract</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">算子执行了，不代表特征已经进入样本</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #3d5a80;border-radius:6px;padding:12px;font-size:14px;"><b>四个常见误判：</b><br>① 只看 operator 逻辑，没看配置 tag 是否绑定；② 忽略 parent/arg，导致上下文 list 读取错 item；③ 新类型没有 emitting 重载；④ Any 真实类型与模板 RealType 不一致。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>tag → list → parent/arg → Any type → emitting → to_example/to_label → dict id。</div></div><div style="margin-top:12px;font-weight:900;color:#3d5a80;">∎ 特征框架问题要从合同边界排查，不要直接跳到业务字段。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title AEF 特征抽取基础合同 checklist
  desc 适用于特征缺失、field 为空、类型 fatal、上下文特征串线
  items
    - label 固定配置入口
      desc 记录 conf_dir、conf_file、gflags.conf 和实际 tag 列表
      done true
    - label 检查 feature list
      desc 确认 get_feature_list(user/item/rule/context) 均非空
      done true
    - label 核对 parent 链
      desc user、item、pos_related_context、item_related_context 的 parent 顺序不可反
      done false
    - label 核对 arg 索引
      desc item arg(0)、pos arg(1)、item_context arg(0/1)、rule arg(0) 与 context_index 对齐
      done false
    - label 核对类型发射
      desc Any 真实类型、TypedOp 模板类型、emitting 重载三者一致
      done false
    - label 核对输出桥
      desc to_example/to_label 返回 0，field_id 小于字典 field_num
      done false
    - label 清理状态容器
      desc SessionProfile 与 WindowsState 在样本边界正确 clear 或重设 capacity
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/worker/main.cc:64-83`：读取 gflags 并初始化 `Extractor`。
- `src/worker/main.cc:84-103`：初始化特征/标签字典并启动 worker 线程。
- `conf/feature-extractor/video_shentou_LTR/extract_offline.conf:1-24`：五类 feature list 的 tag 与配置文件绑定。
- `src/worker/worker.cc:144-187`：获取 feature list、清理状态并建立 parent 链。
- `src/worker/worker.cc:204-305`：按 user/item/pos/item_context/rule 顺序执行抽取。
- `src/base_operator.h:22-110`：字符串、tuple、vector 类型的 emitting 重载。
- `src/base_operator.h:140-191`：标量类型的 emitting 重载。
- `src/base_operator.h:337-480`：TypedOp 模板从 `babylon::Any` 校验并发射输出。
- `src/base_operator.h:489-631`：`SessionProfile` 和 `WindowsState` 状态容器。

> KU 正文未读取，需人工补充 AEF 配置生成规范、线上 feature list 发布流程、字典版本管理和不同模型任务的特征验收指标。
