# MV-GRG ResponseForGrg：请求准备、响应拼装与业务边界

> 运行说明：2026-08-23 未发现 daily-plan 文件，按附近未覆盖主题 fallback 生成。当前 cron 环境未验证 KU 可用性，KU/业务背景需人工补充；本文结论基于 `feeda-mv-grc` 代码与配置证据。

<div class="arch-wrapper" style="display:flex;gap:12px;align-items:stretch;background:#f8fafc;border:1px solid #d8e2ef;border-radius:8px;padding:14px;color:#1f2937;font-family:Inter,Arial,sans-serif;">
  <div class="arch-main" style="flex:1;min-width:0;">
    <div style="font-size:20px;font-weight:800;margin-bottom:10px;">业务响应链的角色分工</div>
    <div class="arch-layer application" style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;margin-bottom:10px;">
      <div style="font-weight:700;margin-bottom:6px;">请求前处理</div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;">
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`src/strategy/short_micro/prepare_request_cb.cpp`<br/>整理请求上下文和特征入口</div>
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`src/processor/video_launch/pcs_after_request.cpp`<br/>把 PCS 结果转成后续处理语义</div>
      </div>
    </div>
    <div class="arch-layer ai" style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;margin-bottom:10px;">
      <div style="font-weight:700;margin-bottom:6px;">核心响应函数</div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;">
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`src/processor/video_launch/response_for_grg.cpp:102-130`<br/>`ResponseForGrgFunction` 初始化 Q 映射</div>
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`conf/plugins/graph/video_launch/grg_response.conf:1-40`<br/>把函数绑定到 `_response_for_grg` 节点</div>
      </div>
    </div>
    <div class="arch-layer data" style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;">
      <div style="font-weight:700;margin-bottom:6px;">数据语义</div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;">
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`src/processor/video_launch/news_response.cpp:24-80`<br/>新闻侧 Q 值注入和阈值控制</div>
        <div style="border:1px solid #e2e8f0;border-radius:6px;padding:8px;background:#f8fafc;">`conf/plugins/scenes.conf:5-27`<br/>scene/strategy 条件分发</div>
      </div>
    </div>
  </div>
  <div class="arch-sidebar" style="width:260px;flex:0 0 260px;">
    <div style="background:#0f172a;color:#e2e8f0;border-radius:8px;padding:10px;margin-bottom:10px;">
      <div style="font-weight:800;margin-bottom:6px;">结论</div>
      <div style="font-size:13px;line-height:1.55;">业务侧不是单一函数，而是“请求准备 → PCS 后处理 → 响应函数 → scene 配置”的链式拼装。`ResponseForGrgFunction` 负责输出侧的 Q 映射，`prepare_request_cb` 负责输入侧约束。</div>
    </div>
    <div style="background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:10px;">
      <div style="font-weight:800;margin-bottom:6px;">相关处理器</div>
      <div style="font-size:13px;line-height:1.6;">`src/processor/video_launch/response_for_grg.cpp`<br/>`src/processor/video_launch/news_response.cpp`<br/>`src/processor/video_launch/pcs_after_request.cpp`<br/>`src/strategy/short_micro/prepare_request_cb.cpp`</div>
    </div>
  </div>
</div>

## 核心流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
participant "prepare_request_cb" as Prep
participant "pcs_after_request" as Pcs
participant "ResponseForGrgFunction" as Resp
participant "NewsResponseFunction" as News
participant "scenes.conf" as Scene
Prep -> Pcs : 请求特征/上下文就绪
Pcs -> Resp : 输入转成 graph data
Resp -> Resp : 初始化 q_cnt 映射
Resp -> News : 新闻/视频侧评分与阈值语义
News -> Scene : scene/strategy 条件命中
Scene --> Resp : 选择最终输出路径
@enduml
```

```infographic
sequence-ascending-steps
data
  title 业务响应链的四个阶段
  desc 请求前处理、PCS 后处理、响应函数初始化、scene 条件落点
  items
    - label 请求准备
      desc `prepare_request_cb.cpp` 把上下文和特征整理成可消费状态
    - label PCS 后处理
      desc `pcs_after_request.cpp` 将 PCS 结果映射到后续处理语义
    - label 响应函数
      desc `response_for_grg.cpp` 初始化 `_q_cnt_vec` 等输出映射
    - label Scene 绑定
      desc `grg_response.conf` 和 `scenes.conf` 决定最终走哪条策略
theme
  palette #2d6a4f #3d5a80 #64748b
```

## 关键实现

`src/processor/video_launch/response_for_grg.cpp:102-130` 定义 `ResponseForGrgFunction`，初始化时把业务 Q 值名映射到输出字段，例如 `average_q -> res_score`、`average_ctr_q -> dnn_score_ctr`、`average_follow_q -> refine_multi_follow`。这说明输出不是原始分数直出，而是经过统一的字段对齐。

`conf/plugins/graph/video_launch/grg_response.conf:1-40` 把 `ResponseForGrgFunction` 挂在 `_response_for_grg` vertex 上，同时声明了多个依赖节点。业务边界在这里被显式固定：响应函数只消费配置中声明的数据，不自行推导上游依赖。

`src/processor/video_launch/news_response.cpp:24-80` 体现了新闻/视频侧的数值注入习惯，偏向于把多路打分写入 `sample_dnn_q` 一类结构，并用阈值或开关控制是否落盘到某些字段。它更像“输出字段组装器”，不是策略本体。

`src/strategy/short_micro/prepare_request_cb.cpp:1-40` 与 `src/processor/video_launch/pcs_after_request.cpp:1-40` 分别处在请求进入和 PCS 后的两个边界：前者偏输入准备，后者偏中间态收敛。把这两段和 `ResponseForGrgFunction` 一起看，能看出整条链是“输入清洗 → 中间态转换 → 输出映射”。

`conf/plugins/scenes.conf:5-27` 说明最终场景选择依赖 `ua`、`product`、`flow_loc`、`IsSlightUser` 等条件，属于配置驱动分流。业务理解时不要把这些条件散落到代码里读，先看 scene 配置再看 processor。

<div style="border:1px solid #dbe3ef;border-radius:8px;padding:12px;background:#fff;">
  <div style="font-weight:800;margin-bottom:6px;">Pitfalls</div>
  <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;font-size:13px;line-height:1.55;">
    <div style="border-left:4px solid #94a3b8;padding-left:10px;">`ResponseForGrgFunction` 不是 scene 分发器，它只做响应字段映射。</div>
    <div style="border-left:4px solid #94a3b8;padding-left:10px;">`prepare_request_cb` 解决的是输入准备，不要把输出逻辑塞进去。</div>
    <div style="border-left:4px solid #94a3b8;padding-left:10px;">`pcs_after_request` 的职责是后处理语义转换，不是最终响应生成。</div>
    <div style="border-left:4px solid #94a3b8;padding-left:10px;">scene 条件在配置里，别在 processor 里硬编码 `ua` / `flow_loc` 分支。</div>
  </div>
</div>

```infographic
list-column-done-list
data
  title 调试 checklist
  desc 检查业务函数、依赖和 scene 配置是否一致
  items
    - label 响应函数是否绑定到 vertex
      desc 对照 `grg_response.conf`
      done true
    - label Q 值字段是否对齐
      desc 核查 `average_q`、`average_ctr_q` 等映射
      done true
    - label 请求准备链是否先于响应链
      desc `prepare_request_cb` 和 `pcs_after_request` 是否在前置阶段完成
      done true
    - label scene 是否命中预期分支
      desc 对照 `scenes.conf` 条件
      done true
    - label 输出是否由配置驱动
      desc 不要把策略分流写死在代码里
      done true
theme
  palette #2d6a4f #3d5a80 #64748b
```

## 证据来源

- `src/processor/video_launch/response_for_grg.cpp:102-130`
- `src/processor/video_launch/news_response.cpp:24-80`
- `src/processor/video_launch/pcs_after_request.cpp:1-40`
- `src/strategy/short_micro/prepare_request_cb.cpp:1-40`
- `conf/plugins/graph/video_launch/grg_response.conf:1-40`
- `conf/plugins/scenes.conf:5-27`
- `conf/plugins/graph/vertex.conf:1-38`
