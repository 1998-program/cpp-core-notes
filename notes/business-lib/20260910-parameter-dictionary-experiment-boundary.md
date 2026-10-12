# 2026-09-10 业务库理解：字典与实验参数读取边界

> 日期：2026-09-10  
> 主题选择：未发现当日有效 daily-plan；历史计划与最近笔记已覆盖主要 quota、策略、互动 v5 和表达式主题。本次按邻近未覆盖候选回退到 GRC 业务代码中的 `GET_COMMON_DICT` / `EXPERIMENT_GET_PARAM` 参数读取边界，重点观察请求级配置、vector 长度校验、候选级计算和高频读取风险。KU 正文未读取，业务背景需人工补充；本文只陈述代码可验证的行为。  
> 范围：Set2set predictor/function、fan-cate downrank adjuster、predictor plugin，以及 GRC/GRG 中参数访问的静态分布。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">输入层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">SidInfo / CommonInfo / PCS / graph context</div><div style="margin-top:8px;font-size:12px;color:#52606d;">请求上下文提供实验管理器、sid 开关和业务数据；候选字段从 PCS、GCMS 或其他 GraphData 进入。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">参数读取层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">GET_COMMON_DICT + EXPERIMENT_GET_PARAM</div><div style="margin-top:8px;font-size:12px;color:#52606d;">字典读取返回状态与 typed value，实验参数按声明类型取值；调用方负责命名、长度与默认值。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">业务输出层</div><div style="margin-top:8px;font-size:14px;color:#102a43;">predictor input / factor / score write-back</div><div style="margin-top:8px;font-size:12px;color:#52606d;">参数进入预测请求、调权阈值或 factor 公式；结果写回候选分数并由 SIA/日志记录。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 54px 1fr 54px 1fr 54px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">上下文</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">读取</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">校验</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">计算 / 写回</div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title 业务参数读取：从请求上下文到候选分数
participant "GraphFunction" as FN
participant "SidInfo / CommonInfo" as CTX
participant "ExpManager" as EXP
participant "CommonDict" as DICT
participant "PCS / GCMS data" as DATA
participant "factor / score" as OUT
FN -> CTX : validate context pointers
FN -> EXP : EXPERIMENT_GET_PARAM
EXP --> FN : abtest flags / typed vectors
FN -> DICT : GET_COMMON_DICT(key, typed value)
DICT --> FN : status + vector/map
FN -> FN : check status, size, index and defaults
loop each candidate
  FN -> DATA : read feature / category / score
  FN -> FN : apply threshold or formula
  FN -> OUT : write factor or grg_new_score
end
FN -> FN : SIA_END and sampled logging
note over FN,DICT
参数宏只提供访问通道；
业务代码拥有默认值和 shape 校验责任。
end note
@enduml
```

## 2. 参数读取信息图

```infographic
infographic sequence-ascending-steps
data
  title 一次可靠参数读取的四道闸门
  desc 名称、状态、形状和数值范围必须在进入公式前完成校验
  items
    - label 名称命中
      desc key、实验参数名和 sid 开关名称与配置协议一致
      icon mdi:key-variant
    - label 状态成功
      desc 检查 GET_COMMON_DICT 返回码与 typed value 指针，再继续读取
      icon mdi:check-network
    - label shape 足够
      desc vector 按使用下标检查 size，避免旧配置或空结果越界
      icon mdi:format-list-checks
    - label 数值可用
      desc 阈值、指数、上下界和默认值满足公式的定义域
      icon mdi:calculator-variant
    - label 写回可追踪
      desc factor、score、SIA 和采样日志能关联同一 logid/nid
      icon mdi:chart-timeline-variant
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 参数读取发生在不同业务边界

`processor/set2set_predict_function.cpp:78-93` 先从 `SidInfo` 计算多个 abtest 开关，再用 `EXPERIMENT_GET_PARAM` 读取 `router_gr_set2set_mv`，并把字符串路由映射到不同 predictor 版本。这里实验参数改变的是执行分支和模型选择，不是单纯的数值调权。

同一文件 `:153-178` 使用 `GET_COMMON_DICT` 读取 `set_fr_smfw` 与 `set_fz_2` 的 double vector，并要求至少 6 个值后写入动态 factor 数组。状态码或 vector 长度不满足时只记录 warning，数组保留预设默认值。这是“失败可降级”的业务契约。

### 3.2 typed value 与 key 拼接构成隐式协议

Set2set 代码 `:150-178` 先从 sid 变量拼出 `set_fr_lh_`、`set_fz_2_` 等 key，再访问 `d_grc_param_dict`。key 后缀由实验开关决定，字典数据的 vector 下标又被公式解释为不同 factor 槽位。修改 flag 命名、后缀或 vector 排布，都会改变业务含义，即使 C++ 类型仍然是 `VectorValStrDouble`。

### 3.3 调权器把请求级读取与候选级公式分开

`operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:35-112` 在 `calculate_list_conditions` 中读取 PCS 比例、实验向量和请求 logid；` :115-181` 再按候选的 cate/subcate、曝光量、互动完成度和时长执行公式，最后把 factor 乘入 `item->grg_new_score`。请求级数据不应在每个候选循环里重复获取，否则会放大字典/实验访问成本。

该实现还要求 `fancate_ratio_vec.size() >= 5`、`cocoon_downrank_vec.size() >= 13`，并检查阈值、指数和输入范围。公式有指数和幂函数，范围校验不是装饰，而是防止 NaN、极端 factor 和排序扰动的数值边界。

### 3.4 默认值与回退行为必须写在调用方

Set2set 的动态数组先用 `{0,0,0,1,1,1}` 或全 1 初始化，字典读取成功且长度足够时才覆盖。Fan-cate 调权器则在 factor 小于下界或大于上界时 clamp 到 `factor_min/factor_max`，若配置缺失再使用 `1e-6` 或 `1e6` 的保护边界。

因此排查“参数没生效”时，需要区分四类情况：key 不命中、读取失败、shape 不足、读取成功但数值未通过业务条件。只看最终 factor 无法定位是哪道闸门短路。

### 3.5 统计热点说明读取应前移和缓存

静态扫描显示 GRC 源码中 `GET_COMMON_DICT` 有 1689 次、`EXPERIMENT_GET_PARAM` 有 219 次命中，GRG 中对应为 408 次和 149 次；这些是源码文本命中次数，不等同于线上调用次数，但足以说明参数访问是广泛的横切模式。大型处理器如 `set2set_predict_function.cpp` 包含大量读取与分支，新增参数时应明确它属于请求级初始化还是候选级计算。

### 3.6 clear 是参数状态隔离的一部分

`microvideo_fancate_downrank_adjuster.cpp:217-221` 清空 `fancate_ratio_vec`、`cocoon_downrank_vec` 并重置 logid。对于会被对象池或 graph 复用的算子，clear 与参数读取同等重要；遗漏清理会让本请求在读取失败时继承上一次请求的数据。

## 4. 读取模式对比

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title 参数读取模式对比
  desc 请求级一次读取优先，候选级只消费已经校验的局部值
  items
    - label 推荐模式
      desc list condition 阶段读取实验与字典；校验 status、size、范围；candidate 阶段只计算和写回
      icon mdi:check-decagram
    - label 高风险模式
      desc 在候选循环内反复 GET_COMMON_DICT/EXPERIMENT_GET_PARAM，未校验 vector 下标或复用旧状态
      icon mdi:alert-outline
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#F5F7FA;border-radius:12px;padding:20px;margin:18px 0;border-left:8px solid #3D5A80;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#64748b;font-weight:800;">Pitfalls / Parameter Access</div><h3 style="font-size:24px;line-height:1.2;margin:8px 0 12px;">参数宏降低了访问样板代码，但 key、shape、默认值和数值边界仍由业务实现负责。</h3><div style="display:grid;grid-template-columns:2fr 1fr;gap:14px;"><div><ul style="font-size:14px;line-height:1.65;margin:0 0 0 18px;"><li><b>vector 下标协议：</b>读取成功不代表长度足够，使用第 n 个槽位前必须检查 size。</li><li><b>key 拼接漂移：</b>sid flag 参与 key 后缀时，实验名变化可能导致字典永远 miss。</li><li><b>读取层级错误：</b>请求级参数放在候选循环内会重复访问并放大延迟。</li><li><b>默认值误改：</b>全 1、全 0、factor clamp 和空结果回退都可能是既有流量保护契约。</li><li><b>数值定义域：</b>指数、幂函数、阈值和 pre-score 范围必须同时验证，避免 NaN 或极端排序。</li><li><b>状态残留：</b>对象复用时必须 clear 参数 vector 与 logid，读取失败不能继续使用旧请求数据。</li></ul></div><div style="border-left:3px solid #F59E0B;padding-left:12px;color:#374151;font-size:14px;line-height:1.65;">评审顺序：key → return code → vector shape → 默认值 → 公式范围 → score 写回 → clear。</div></div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title 字典与实验参数调试 checklist
  desc 让参数读取失败、降级和生效结果都能被定位
  items
    - label 固定请求上下文
      desc 记录 logid、sid、UA、graph 和当前 adjuster/function 名称
      done true
    - label 打印最终 key
      desc 对拼接出来的 dictionary key 和 experiment name 做采样记录
      done true
    - label 检查返回状态
      desc 区分 key 不存在、typed value 为空和底层读取失败
      done true
    - label 检查 vector shape
      desc 将每个下标访问映射到最小 size 要求，覆盖旧配置版本
      done true
    - label 核对默认值
      desc 记录读取失败时保留的数组、factor 和 clamp 边界
      done true
    - label 核对数值范围
      desc 验证 threshold、exponent、pre_dur、pre_finish 和 factor 定义域
      done true
    - label 检查读取位置
      desc 确认请求级读取不在候选循环中，候选级只消费已准备的数据
      done true
    - label 检查复用清理
      desc 请求结束清空 vector、临时状态和 logid，再回放读取失败场景
      done true
    - label 关联输出
      desc 用采样日志核对 factor、grg_new_score、SIA 状态和候选 nid
      done true
 theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/processor/set2set_predict_function.cpp:78-93`：实验开关与 predictor 路由参数。
- `src/processor/set2set_predict_function.cpp:144-178`：动态 factor 字典读取、状态码和 vector 长度校验。
- `src/processor/set2set_predict_function.cpp:180-235`：cluster ES 参数读取与 shape 校验。
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:35-112`：请求级 PCS、实验参数和采样日志。
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:115-181`：候选条件、公式、clamp 和分数写回。
- `src/operator/adjuster/precise/microvideo_fancate_downrank_adjuster.cpp:217-221`：对象复用清理。
- `src/plugin/predictor.cpp:42-65`：预测请求调用、响应状态与耗时边界。
- `src/operator/adjuster/base/base_adjuster.h:21-56`：请求级与候选级 adjuster 扩展契约。
- `src/service/grc_service.cpp:51-61,178-221`：请求上下文、graph 与实验依赖获取边界。
