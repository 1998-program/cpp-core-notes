# 2026-09-21 业务库理解：MV-GRG NewResponseFunction 的 Content 组装与 ExtMsg 回写合同

> 日期：2026-09-21（周一）  
> 主题选择：未发现目标日期的有效 daily-plan；最近业务笔记已覆盖 video_launch 合并响应、dup/title-dup 和 gr-rank ContextDNN，因此按相邻未覆盖的下游边界回退到 **MV-GRG `DiversityMergeResult` → `NewResponseFunction` → `GRCResponse` 的最终响应组装，以及策略装饰和 `predictor_extmsg` 回写**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：`NewResponseFunction` 的输入依赖、`reqnum` 截断、`ResponseDecorateStrategy` 两类装饰、压缩/普通 ExtMsg 解码、sample DNN Q 合并、not-show 信息回写与 `response.conf` 配置。

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f7faf5;border:1px solid #d9e2d0;border-radius:8px;padding:16px;margin:16px 0;color:#243126;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">上游结果</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">DiversityMergeResult</div><div style="margin-top:8px;font-size:12px;color:#52615a;">按 merge 后顺序提供 RidTmpInfo；`reqnum` 决定最终 ContentItem 数量上限。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">响应构造</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">NewResponseFunction</div><div style="margin-top:8px;font-size:12px;color:#52615a;">创建 content、复制 item、解析 ExtMsg，再执行策略装饰和 sample 字段合并。</div></div><div style="background:#ffffff;border:1px solid #d9ead3;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#5f6f52;text-transform:uppercase;letter-spacing:.04em;">输出协议</div><div style="margin-top:8px;font-size:16px;color:#23412b;font-weight:700;">GRCResponse + predictor_extmsg</div><div style="margin-top:8px;font-size:12px;color:#52615a;">ExtMsg 重新序列化并 base64 写回 item ext，最终 response ref 到 passthrough response。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 40px 1fr 40px 1fr 40px 1fr;gap:8px;align-items:center;"><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#166534;font-size:13px;">merge result</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:6px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">ContentItem</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:6px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">ExtMsg decorate</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;padding:10px;text-align:center;color:#991b1b;font-size:13px;">response emit</div></div></div>

**一句话结论：** MV-GRG 的最终响应不是简单复制 merge vector；它在 `NewResponseFunction` 内完成数量截断、item 统计、ExtMsg 多编码路径兼容、策略装饰和 response ownership 转移，任何一步遗漏都可能造成“数量正确但字段缺失”。

## 1. 核心时序

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title MV-GRG NewResponseFunction 响应组装
participant "DiversityMergeResult" as MERGE
participant "NewResponseFunction" as RESP
participant "GRCResponse" as PB
participant "ResponseDecorateStrategy" as STRAT
participant "ExtMsg" as EXT
participant "Funnel / SIA" as OBS
MERGE -> RESP : provide ordered RidTmpInfo*
RESP -> PB : add_content()
loop while items < reqnum
  RESP -> RESP : skip null / record rid
  RESP -> PB : add_items() and copy ContentItem
  RESP -> EXT : decode predictor_extmsg
  alt compressed bytes
    EXT -> EXT : SnappyDecompress
  else base64 string
    EXT -> EXT : base64_decode + ParseFromString
  end
  RESP -> EXT : merge sample_dnn_q
  RESP -> STRAT : decorate_ext_msg
  RESP -> STRAT : decorate_content_item
  RESP -> EXT : SerializeToString + base64_encode
  RESP -> PB : set predictor_extmsg
end
RESP -> PB : response.ref(passthrough)
RESP -> PB : response.release()
RESP -> OBS : SIA final counts / funnel
@enduml
```

## 2. 响应组装信息图

```infographic
infographic sequence-snake-steps-simple
data
  title NewResponseFunction 的六个步骤
  desc 组装顺序决定响应字段是否与 merge 结果保持一致
  items
    - label 取结果
      desc 从 DiversityMergeResult 按顺序遍历，跳过空指针并用 reqnum 限制输出数量
      icon mdi:format-list-numbered
    - label 建 item
      desc add_items 后复制 RidTmpInfo 内的 ContentItem，并记录 response nid 集合
      icon mdi:playlist-plus
    - label 解码 ExtMsg
      desc 优先处理 predictor_extmsg_bytes 的 Snappy 路径，否则走 base64 字符串路径
      icon mdi:lock-open-outline
    - label 合并特征
      desc 将 sample_dnn_q 的 protobuf 字段映射到 ExtMsg 的 dnn_q，支持 repeated 与 scalar
      icon mdi:database-cog-outline
    - label 策略装饰
      desc 依次调用 decorate_ext_msg 和 decorate_content_item，注入业务策略字段
      icon mdi:brush-variant
    - label 回写收口
      desc ExtMsg 序列化、base64 回写 item ext，response ref/release 后记录 SIA
      icon mdi:check-decagram-outline
theme
  palette #2D6A4F #3D5A80 #F59E0B
```

## 3. 关键机制拆解

### 3.1 配置把输入输出合同显式化

`conf/plugins/graph/news_updates_dibar/response.conf:1-22` 将 vertex function 配置为 `NewResponseFunction`，emit `_response: GRGResponse`，并声明 `_request`、`_pass_though_response`、`_reqnum`、`_diversity_result`、`_funnel_obj` 五组依赖，同时启用 `@decorate: common`。

这意味着排查响应为空时，不能只看 `DiversityMergeResult` 是否有数据，还要确认图配置是否把同一批依赖接入了当前 vertex。配置中的 decorate 选项也是业务字段来源的一部分，而不是仅用于日志。

### 3.2 `reqnum` 是输出数量闸门，不是 merge 数量证明

`src/process/new_response_function.cpp:52-75` 先通过 `_response.emit()` 获取 response，创建 content，并在遍历 `_diversity_result` 时同时检查 vector 边界和 `content_ptr->items_size() < *_reqnum`。空的 `RidTmpInfo` 会被跳过。

因此最终 item 数量小于 merge vector 长度可能是正常截断，也可能来自空指针；应同时记录 merge size、reqnum、跳过的 null 数量和最终 `items_size()`，不能只看一个计数器。

### 3.3 predictor_extmsg 同时兼容压缩 bytes 与 base64 string

`src/process/new_response_function.cpp:81-106` 优先检查 `has_predictor_extmsg_bytes()`，命中后用 `SnappyDecompress` 解出 ExtMsg；否则 base64 decode 后调用 `ParseFromString`。两条路径失败只记录 warning，随后流程继续。

这带来一个重要诊断点：warning 不一定导致 item 被丢弃，但会让后续策略装饰基于空的 ExtMsg 运行。客户端字段缺失时，应先确认输入是 bytes 还是 string，再检查对应解码失败日志。

### 3.4 策略装饰发生在重编码之前

`src/process/new_response_function.cpp:108-126` 对每个装饰策略先调用 `decorate_ext_msg`，再调用 `decorate_content_item`；之后将 ExtMsg `SerializeToString`，base64 编码并写回 `content_item_ptr->mutable_ext()->set_predictor_extmsg()`。

`src/strategy/response_decorate/response_decorate.h:54-69` 定义两个可覆写接口。前者影响候选级 ExtMsg，后者直接影响 ContentItem。若策略已经写入 ExtMsg 却在后续使用旧字符串覆盖，就会丢字段；当前实现通过“解码 → 装饰 → 重新序列化”维持单一回写点。

### 3.5 sample_dnn_q 的反射映射要区分 repeated 和 scalar

`src/process/new_response_function.cpp:284-315` 通过 protobuf descriptor/reflection 遍历 sample message 字段。repeated 字段逐项读取并生成 `dnn_q`，scalar 字段只在 `HasField` 为真时生成一项。字段名会作为 q_name，repeated 项还追加下标。

因此 sample schema 新增字段后，是否出现在响应中取决于字段类型和 presence；不能只检查数值是否非零。空 repeated、未设置 scalar、类型不匹配都可能让响应中没有预期 q。

### 3.6 not-show 信息使用已输出 rid 集合做差集

`src/process/new_response_function.cpp:193-251` 的 `add_not_show_info` 先从 item 的 predictor_extmsg 恢复 ExtMsg，再读取 first ext message 的 not-show 信息；只有不在 `_response_nid_set` 中的 rid 才会加入当前 item 的 `dnn_q` 和 `sketchy_not_show_rid_info`，ctr not-show rid 也最多补入 30 个。

所以该逻辑依赖“response nid 集合已在主循环中建立”。如果在主循环前调用，或 reset 未清理集合，就可能出现旧请求 rid 泄漏或差集错误。

### 3.7 response ref/release 是最终 ownership 收口

`src/process/new_response_function.cpp:151-153` 将构造好的 `_pass_though_response` 通过 `response.ref(*_pass_though_response)` 交给 emit response，随后 `response.release()`。这一步表明成功返回的对象所有权转移发生在最后，而不是 `add_content()` 之后。

`src/process/new_response_function.cpp:346-350` 的 reset 会清空 `_response_nid_set`、ExtMsg 和临时字符串。请求复用时若遗漏 reset，最容易表现为跨请求字段污染和 not-show 差集异常。

## 4. 分层对账信息图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title 数量正确但字段缺失的三层定位
  desc 由外到内确认结果、对象和编码三个边界
  items
    - label merge result
      desc DiversityMergeResult 是否有足够的非空 RidTmpInfo，merge 顺序是否符合预期
      value 1
      icon mdi:source-branch
    - label ContentItem
      desc reqnum 截断、add_items、item copy、strategy decorate_content_item 是否成功
      value 2
      icon mdi:card-text-outline
    - label ExtMsg
      desc bytes/string 解码、sample reflection、decorate_ext_msg、SerializeToString 和 base64 回写
      value 3
      icon mdi:code-braces
theme
  palette #2D6A4F #3D5A80 #DC2626
```

## 5. Pitfalls

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;color:#263238;"><div style="border-left:4px solid #d97706;background:#fff7ed;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#9a3412;text-transform:uppercase;">不要只看 vector size</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">最终输出同时受 reqnum、空指针跳过和 ContentItem 建立结果影响，merge size 不等于 response size。</div></div><div style="border-left:4px solid #2563eb;background:#eff6ff;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#1d4ed8;text-transform:uppercase;">区分 bytes 与 string</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">predictor_extmsg_bytes 走 Snappy，predictor_extmsg 走 base64；用错解码器会得到空 ExtMsg。</div></div><div style="border-left:4px solid #dc2626;background:#fef2f2;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#991b1b;text-transform:uppercase;">warning 可能是降级而非失败</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">ExtMsg 解析失败后流程仍可继续，结果可能有 item 但缺少策略字段；要把 warning 与最终字段对账。</div></div><div style="border-left:4px solid #059669;background:#ecfdf5;padding:14px 16px;border-radius:6px;"><div style="font-size:12px;font-weight:800;color:#047857;text-transform:uppercase;">reset 是请求隔离边界</div><div style="margin-top:6px;font-size:14px;line-height:1.55;">response nid 集合、ExtMsg 和临时 base64 字符串都必须在复用前清空，尤其关注 not-show 差集。</div></div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title NewResponseFunction 排查清单
  items
    - label 配置
      desc 核对 response.conf 的 function、emit、depend 和 decorate 选项
      done false
    - label 数量
      desc 同时记录 diversity_result size、null skip、reqnum 和 content items_size
      done false
    - label 编码
      desc 确认输入命中 compressed bytes 或 base64 string，并保留对应 parse warning
      done false
    - label 装饰
      desc 分别检查 decorate_ext_msg 与 decorate_content_item 是否执行、是否被后续覆盖
      done false
    - label 回写
      desc 检查 SerializeToString、base64_encode 和 set_predictor_extmsg 的最终值
      done false
    - label 生命周期
      desc 检查 response ref/release 顺序与 reset 是否清理跨请求状态
      done false
theme
  palette #2D6A4F #0F766E #F59E0B
```

## 7. 证据来源

- `src/process/new_response_function.cpp:23-75`：函数注册、response 创建、reqnum 截断和 item 组装
- `src/process/new_response_function.cpp:81-126`：ExtMsg 解码、sample 合并、策略装饰和 base64 回写
- `src/process/new_response_function.cpp:151-190`：response ref/release 与 SIA 收口
- `src/process/new_response_function.cpp:193-251`：not-show 信息差集与 ExtMsg 回写
- `src/process/new_response_function.cpp:284-315`：protobuf reflection 到 dnn_q 的映射
- `src/process/new_response_function.cpp:346-378`：reset、GRAPH_FUNCTION_INTERFACE 与注册
- `src/strategy/response_decorate/response_decorate.h:54-69`：两类 response decorate 接口
- `conf/plugins/graph/news_updates_dibar/response.conf:1-22`：NewResponseFunction 配置依赖
