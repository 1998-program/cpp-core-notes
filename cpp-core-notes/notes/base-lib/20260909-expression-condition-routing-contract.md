# 2026-09-09 基础库理解：Expression 条件路由与图输入契约

> 日期：2026-09-09  
> 主题选择：未发现当日有效 daily-plan；旧计划文件已过期，且候选中的 GraphPool、MultiStreamEngine、PipelineGraphFunction、BaseGraphFunction 等主题已有覆盖。本次按“邻近未覆盖主题”回退到 `expression.conf` 条件表达式与图输入路由契约。KU 正文未读取，企业业务上下文需人工补充；以下结论以代码和配置证据为准。  
> 范围：GRC 图配置中的表达式定义、表达式依赖的数据向量以及业务处理器消费这些命名结果的边界。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">输入上下文</div><div style="margin-top:8px;font-size:14px;color:#102a43;">UA / sid / scene / experiment flags</div><div style="margin-top:8px;font-size:12px;color:#52606d;">运行时把请求基础信息注册为表达式可读变量，表达式只组合这些已注册值。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">条件路由层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">expression.conf → named expression graph</div><div style="margin-top:8px;font-size:12px;color:#52606d;">用布尔条件、三元选择和表达式引用，把一个稳定的逻辑名映射为实际数据源。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">消费节点</div><div style="margin-top:8px;font-size:14px;color:#102a43;">Select / rerank / PCS / merge processors</div><div style="margin-top:8px;font-size:12px;color:#52606d;">处理器依赖逻辑名，不必复制所有场景分支；真正的数据向量由表达式在图配置层选择。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 62px 1fr 62px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">请求变量</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">逻辑名 / 三元表达式</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">实际 GraphData 输入</div></div></div>

## 1. 核心调用与数据流

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title Expression 条件路由：从请求变量到处理器输入
participant "Request context" as CTX
participant "Expression registry" as REG
participant "expression.conf" as CONF
participant "GraphData dependency" as DATA
participant "Processor" as PROC

CTX -> REG : 注册 UA / sid / scene flags
REG -> CONF : 解析 named expression
CONF -> CONF : 计算布尔条件与三元分支
CONF -> DATA : 选择实际数据向量
DATA -> PROC : 注入逻辑名对应依赖
PROC -> PROC : 读取 Res_Dep / CtrInput / merge input

alt Push 个性化请求
  CONF -> DATA : Res_Dep = Push_Rerank
  CONF -> DATA : Push_Rerank = CtrRerankResult 或 AdjustRankResult
else 普通请求
  CONF -> DATA : Res_Dep = SearchVideoResult
end

note over CONF
表达式层负责选择，业务处理器负责消费。
修改逻辑名时必须同时检查生产者和消费者。
end note
@enduml
```

## 2. 配置结构信息图

```infographic
infographic sequence-snake-steps-simple
data
  title 条件表达式的四步解析链
  desc 逻辑名把请求场景与实际 GraphData 输入隔离开
  items
    - label 注册上下文
      desc UA、sid、IsSearchRelated、IsFeedShortMicro 等变量先进入表达式环境
      icon mdi:database-arrow-right
    - label 计算条件
      desc 布尔与非运算决定当前请求走哪个业务分支
      icon mdi:filter-variant
    - label 解析三元分支
      desc Res_Dep、Push_Rerank、CtrInput 等逻辑名选择具体结果向量
      icon mdi:source-branch
    - label 注入处理器
      desc 下游节点依赖逻辑名，沿 GraphData 读取最终选中的 vector
      icon mdi:arrow-right-bold
    - label 输出闭环
      desc rerank、PCS、merge 节点继续消费选择后的结果
      icon mdi:check-circle-outline
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 逻辑名是配置层的稳定接口

`conf/plugins/graph/expression.conf:1-7` 定义了两级路由：`Res_Dep` 先判断 `IsAppPushIndividuation`，命中后转到 `Push_Rerank`；`Push_Rerank` 再根据 `is_push_rerank` 选择 `CtrRerankResult` 或 `AdjustRankResult`。这等价于把“请求场景选择”和“结果向量选择”拆成两层，处理器只需依赖 `Res_Dep`。

`conf/plugins/graph/expression.conf:72-78` 的 `CtrInput` 采用同样的稳定命名方式：搜索相关请求消费 `DupResultItemsVec`，否则消费 `VfsResult`。新增场景时优先扩展表达式，而不是在每个处理器中复制 if/else。

### 3.2 表达式引用形成隐式依赖图

`conf/plugins/graph/expression.conf:80-110` 展示了从 `IsRkcjMuteTag`、`PcsRkcjRelevantResult` 到 `IsListMergeFrame` 的连续派生关系。一个表达式的名字既是配置变量，也是图依赖契约；删除、改名或改变类型都会影响所有引用它的节点。

因此排查条件路由时要沿两端检查：一端确认变量是否被请求上下文注册，另一端确认逻辑名是否被 vertex 配置或处理器声明为依赖。只读 expression 文件而不看生产者和消费者，无法证明路由在运行时成立。

### 3.3 场景分支的优先级由表达式嵌套决定

`Res_Dep → Push_Rerank → is_push_rerank` 是显式嵌套；`SearchVideoResult` 在 `conf/plugins/graph/expression.conf:178-180` 又根据搜索场景在 `CtrRankResult` 和 `ReplaceRankResult` 间选择。嵌套越深，越需要先画出决策树再改配置，否则一个上游条件变化可能改变多个下游结果。

### 3.4 常量表达式也是有效的开关

`conf/plugins/graph/expression.conf:84-90` 将 `IsRkcjMuteTag` 固定为 `false`，使 `PcsRkcjRelevantResult` 固定选择 `PcsAfterMergeRet`。`conf/plugins/graph/expression.conf:96-110` 也包含固定值和 UA 条件组合。常量不是无效配置，它可能是灰度开关、临时旁路或已关闭实验的保留契约；修改前应确认是否有外部配置或监控依赖。

### 3.5 运行时输入注册必须覆盖表达式变量

`src/processor/video_launch/diversity_merge.cpp:114-118` 展示了处理器向执行上下文加入 `ua` 条件值的边界。表达式中出现 `UA`、`sid_xxx`、`IsSearchRelated` 等变量时，必须找到其注册或派生来源。出现“表达式始终走 else”时，优先检查变量名大小写、注册时机和类型，而不是先怀疑三元表达式语法。

## 4. 评审与排查要点

<div style="font-family:Inter,Arial,sans-serif;background:#F5F7FA;border-radius:12px;padding:20px;margin:18px 0;border-left:8px solid #3D5A80;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:800;">Pitfalls / Expression Contract</div><h3 style="font-size:24px;line-height:1.2;margin:8px 0 12px;">条件表达式不是孤立配置，它同时约束变量生产者、逻辑名和数据消费者。</h3><div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;"><div><ul style="font-size:14px;line-height:1.65;margin:0 0 0 18px;"><li><b>变量未注册：</b>表达式能解析但运行时拿不到值时，分支可能持续落入默认路径。</li><li><b>逻辑名改名：</b>改动 <code>Res_Dep</code> 或 <code>CtrInput</code> 时必须同步检查 vertex 的 depend/emit 声明。</li><li><b>嵌套分支误读：</b>先画外层条件，再展开内层条件，避免把 <code>Push_Rerank</code> 当成独立入口。</li><li><b>常量开关误删：</b><code>false</code> 可能是有意旁路，删除会改变现有流量。</li><li><b>大小写与类型：</b><code>UA</code>、<code>ua</code> 或 sid 标识不一致会造成路由与预期不符。</li></ul></div><div style="border-left:3px solid #F59E0B;padding-left:12px;color:#374151;font-size:14px;line-height:1.65;">评审顺序：变量来源 → 表达式决策树 → 逻辑名消费者 → 默认分支 → 线上观测。</div></div></div>

## 5. 调试 checklist

```infographic
infographic list-column-done-list
data
  title Expression 路由调试 checklist
  desc 从配置解析、上下文注册、依赖声明和结果消费四个层面闭环
  items
    - label 找到变量生产者
      desc 确认 UA、sid 和场景变量在执行上下文中有注册或派生来源
      done true
    - label 展开决策树
      desc 对每个三元表达式列出 true、false 和嵌套分支的最终数据源
      done true
    - label 核对逻辑名引用
      desc 搜索 Res_Dep、CtrInput、PcsRkcjRelevantResult 等名字的所有消费者
      done true
    - label 检查默认路径
      desc 记录变量缺失、条件不命中和常量 false 时的实际输入向量
      done true
    - label 校验数据类型
      desc 确认两个分支返回同类 GraphData，避免 vector 类型或生命周期不一致
      done true
    - label 做场景回放
      desc 至少覆盖搜索、push、slide 和普通请求四类场景并记录最终依赖
      done true
    - label 检查改名影响
      desc 配置改动后同步更新 vertex depend/emit、处理器读取和监控字段
      done true
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 6. 证据来源

- `conf/plugins/graph/expression.conf:1-7`：Res_Dep / Push_Rerank 两级结果路由。
- `conf/plugins/graph/expression.conf:72-78`：CtrInput 根据搜索相关性选择输入。
- `conf/plugins/graph/expression.conf:80-110`：RKCJ、play-list 与 list-merge 条件表达式。
- `conf/plugins/graph/expression.conf:178-220`：SearchVideoResult、RerankData、SketchyRankResult 派生链。
- `src/processor/video_launch/diversity_merge.cpp:114-118`：执行上下文加入 `ua` 条件值。
- `src/operator/adjuster/base/base_adjuster.h:21-65`：业务 adjuster 的框架扩展边界与注册宏。
