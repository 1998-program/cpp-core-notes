# 2026-09-13 业务库理解：GRG 缓存代理 Protobuf 解析与 predictor_extmsg 回写边界

> 日期：2026-09-13  
> 主题选择：未发现本日有效 daily-plan；历史候选中的 fake_id 与 TraceLog 主题已有覆盖，因此回退到邻近未覆盖的 **GRG cache proxy Protobuf 解析、批量缓存读取与 predictor_extmsg 响应回写**。KU 正文未读取，业务背景需人工补充；本文只陈述代码可验证的行为。  
> 范围：`src/strategy/cache_proxy/newhot_cache_proxy.cpp`、`src/process/response_function.cpp`，并对照 GRC `src/processor/response.cpp` 的同一 extmsg 透传边界。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8fafc;border:1px solid #d8e1ea;border-radius:12px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">缓存输入层</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">SNDB / DynamicStruct / batch_get</div><div style="margin-top:8px;font-size:12px;color:#52606d;">静态 key 代理读取多组字符串载荷，按 50 个 key 组成批次，再交给 Protobuf 解析。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">解析与业务映射</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">ParseFromString → nid/eid map</div><div style="margin-top:8px;font-size:12px;color:#52606d;">NewHotDict 产生 eid key；NewHotEvent 展开 precise item，写入 nid 到 eid 的反向索引。</div></div><div style="background:#fff;border:1px solid #cbd5e1;border-radius:10px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">响应透传层</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">predictor_extmsg decode → mutate → encode</div><div style="margin-top:8px;font-size:12px;color:#52606d;">GRG/GRC 对 extmsg 解码、修改、序列化和 base64 回写，形成跨服务字段契约。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 48px 1fr 48px 1fr 48px 1fr;gap:8px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;font-size:13px;">SNDB value</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;font-size:13px;">batch parse</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #a7f3d0;border-radius:8px;padding:10px;text-align:center;color:#166534;font-size:13px;">business map</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;font-size:13px;">extmsg response</div></div></div>

**一句话结论：** 这条业务边界同时包含“缓存载荷是否可解析”和“响应 extmsg 是否完成 round-trip”两类契约，不能只看最终 response 是否非空。

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title GRG cache proxy 与 predictor_extmsg round-trip
participant "SNDB Dalton" as SNDB
participant "StaticKeyCache" as CACHE
participant "NewHotDict" as DICT
participant "batch_get" as BATCH
participant "NewHotEvent" as EVENT
participant "nid → eid map" as MAP
participant "GRC/GRG response" as RESP
participant "predictor_extmsg" as EXT

SNDB -> CACHE : get static key payloads
CACHE -> DICT : ParseFromString(dict_pb)
DICT -> BATCH : split eid keys in batches of 50
BATCH --> CACHE : DynamicStruct values
loop each value
  CACHE -> EVENT : ParseFromString(event_pb)
  EVENT -> MAP : expand precise.items
end
CACHE --> RESP : NewHotSndbRes / lookup result
RESP -> EXT : decode bytes or base64 payload
EXT -> EXT : mutate ExtMsg fields
EXT -> EXT : SerializeToString()
EXT -> RESP : base64_encode + set_predictor_extmsg
@enduml
```

## 2. 结构信息图

```infographic
infographic list-grid-badge-card
data
  title 缓存解析与响应回写的六个检查点
  desc 从底层 value 到跨服务 extmsg，每一层都可能产生“空但不崩”的结果
  items
    - label static key
      desc newshot_new_cache_2 通过组件注册绑定 cache proxy 配置
      icon mdi:key-chain
    - label payload parse
      desc NewHotDict、NewHotEvent 和 EventHqRsourceList 都必须检查解析结果或内容规模
      icon mdi:code-json
    - label batch size
      desc eid key 按 50 个一批调用 batch_get，尾批也必须提交
      icon mdi:package-variant-closed
    - label map merge
      desc precise.items 的 nid 追加到已有 eid 集合，重复 nid 不能覆盖既有映射
      icon mdi:map-marker-multiple
    - label encoding mode
      desc extmsg 可能走 predictor_extmsg_bytes + Snappy，也可能走 base64 字符串
      icon mdi:swap-horizontal
    - label response write-back
      desc SerializeToString 后 base64_encode，再写回 predictor_extmsg
      icon mdi:message-arrow-right
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 cache proxy 先取三类静态载荷，再统一产出结果对象

`src/strategy/cache_proxy/newhot_cache_proxy.cpp:26-80` 通过 `SndbDaltonPlugin` 读取 `event_nid_eid_map`、`event_hq_resource` 和实验 key；每次读取前清理 `DynamicStruct`，最后把解析结果封装为 `NewHotSndbRes`。这里的 `Context::close_cache()` 明确表示该代理不使用内层 cache，排查时应将 SNDB 返回、DynamicStruct 转换和 Protobuf 解析分开看。

### 3.2 NewHotDict 的 eid 列表被切成固定批次

`src/strategy/cache_proxy/newhot_cache_proxy.cpp:83-108` 先解析 `NewHotDict`，以 50 为 batch_size 拼接 prefix 和 eid；循环结束后若仍有尾部 `cur_batch`，会额外放入 `batch_vecs`。`src/strategy/cache_proxy/newhot_cache_proxy.cpp:110-121` 对每个批次调用 `batch_get()`，单批失败记录 warning 并继续处理后续批次。

因此“部分命中”是代码允许的行为：某一批失败不会让整个函数直接失败。业务监控需要同时记录批次数、失败批次和最终 map size，不能只看函数返回值。

### 3.3 解析成功后按 nid 合并 eid 集合

`src/strategy/cache_proxy/newhot_cache_proxy.cpp:122-154` 将 DynamicStruct 转成 `StringInfo`，再解析 `NewHotEvent`。对每个 precise item，代码以 nid 为 key：首次出现时创建集合，后续出现时向已有集合插入 eid。这个合并语义保证同一 nid 对应多个活动 eid 时不会被后一个事件覆盖。

### 3.4 extmsg 存在两种输入编码路径

`src/process/response_function.cpp:2487-2500` 优先处理 `predictor_extmsg_bytes`，通过 Snappy 解压到 Protobuf；没有 bytes 时再 base64 decode 字符串并 `ParseFromString`。这意味着排查解析失败时必须先确认 payload 使用的编码分支，不能把 bytes 路径的失败误判为 base64 内容损坏。

### 3.5 GRG 修改后必须完成序列化和回写闭环

`src/process/response_function.cpp:4218-4222` 将修改后的 `extmsg` 序列化、base64 编码并写回 `content_item_ptr->mutable_ext()->set_predictor_extmsg()`。后续 `src/process/response_function.cpp:4510-4517` 又对响应中的 predictor_extmsg 做解码和解析日志，形成可用于回放的 round-trip 检查点。

GRC 的对照实现位于 `src/processor/response.cpp:1451-1455` 和 `src/processor/response.cpp:5570-5574`：同样是 SerializeToString → base64_encode → set 字段。因此跨 GRC/GRG 排查时应比较编码格式和字段版本，而不是只比较 payload 长度。

## 4. 观测字段信息图

```infographic
infographic compare-hierarchy-left-right-circle-node-pill-badge
data
  title “缓存解析成功”与“响应透传成功”的对账
  desc 两条链路都需要独立证明，最终 response 非空不是充分条件
  items
    - label cache side
      desc 关注 batch_get ret、NewHotDict eids_size、NewHotEvent ParseFromString、nid_map_eids size
      value 1
      icon mdi:database-check
      children
        - label supply
          desc SNDB key 是否存在，尾批是否提交
        - label parse
          desc StringInfo 是否为空，Protobuf 是否可解析
        - label merge
          desc nid/eid 集合是否重复覆盖或异常膨胀
    - label response side
      desc 关注 bytes/base64 分支、ParseFromString、SerializeToString、set_predictor_extmsg
      value 2
      icon mdi:message-processing
      children
        - label decode
          desc 输入编码分支与解码长度
        - label mutate
          desc ExtMsg 字段是否在序列化前完成修改
        - label round-trip
          desc 回写后再次解析能否得到预期字段
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#f4f3f0;border:1px solid #d9d3c8;border-left:8px solid #0f766e;border-radius:16px;padding:20px;margin:18px 0;color:#1f2937;box-shadow:0 8px 20px rgba(31,41,55,.08);"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#0f766e;font-weight:800;">Pitfalls · Cache / ExtMsg Boundary</div><div style="font-size:26px;line-height:1.15;font-weight:900;margin:8px 0 12px;">“能解析”与“能透传”是两件事</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:14px;"><div style="background:#fff;border-top:4px solid #0f766e;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;"><b>常见误读：</b><br>① 只检查 batch_get 返回值，忽略单条 StringInfo 或 Protobuf 解析失败；② 只支持 base64，忽略 predictor_extmsg_bytes + Snappy 分支；③ 修改 ExtMsg 后没有重新 SerializeToString；④ 用最终 response 数量判断缓存映射完整性，却没有对账 nid_map_eids size。</div><div style="background:#fff7ed;border-top:4px solid #f59e0b;border-radius:10px;padding:14px;font-size:14px;line-height:1.65;color:#7c2d12;"><b>排查顺序</b><br>key → batch → value → parse → map merge → decode mode → mutate → serialize → encode → reparse。</div></div><div style="margin-top:12px;font-weight:900;color:#0f766e;">∎ 先确认载荷没有丢，再确认字段没有在编码边界丢。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title Cache proxy / predictor_extmsg 排查清单
  desc 适用于缓存部分命中、解析告警、extmsg 缺字段和跨服务字段漂移
  items
    - label 固定 static key 与 logid
      desc 确认 cache proxy 配置名、SNDB group 和请求 logid
      done true
    - label 对账 eid 批次
      desc 检查 batch_size、尾批和每批 batch_get 返回码
      done true
    - label 检查 StringInfo
      desc DynamicStruct 转换为空时不要继续访问 value
      done false
    - label 检查 Protobuf parse
      desc 分别记录 NewHotDict、NewHotEvent、EventHqRsourceList 的解析结果和规模
      done false
    - label 核对 map merge
      desc 对比 nid_map_eids size、重复 nid 和每个 nid 的 eid 数量
      done false
    - label 识别 extmsg 编码分支
      desc 区分 predictor_extmsg_bytes + Snappy 与 base64 predictor_extmsg
      done true
    - label 做 round-trip
      desc 回写后重新 decode/ParseFromString，确认 retrieval_feature 等关键字段仍存在
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/strategy/cache_proxy/newhot_cache_proxy.cpp:26-80`：SNDB 静态 key 读取与结果封装。
- `src/strategy/cache_proxy/newhot_cache_proxy.cpp:83-121`：NewHotDict 解析、50 条批次和 batch_get。
- `src/strategy/cache_proxy/newhot_cache_proxy.cpp:122-171`：NewHotEvent/EventHqResource 解析与 nid/eid 合并。
- `src/process/response_function.cpp:2487-2500`：bytes/压缩与 base64 两种 extmsg 解码路径。
- `src/process/response_function.cpp:4218-4222`：GRG ExtMsg 序列化、base64 回写。
- `src/process/response_function.cpp:4510-4517`：回写后的 extmsg 再解析与采样日志。
- `src/processor/response.cpp:1451-1455,5570-5574`：GRC 对照的 extmsg 回写路径。

> KU 正文未读取，需人工补充 NewHot/SNDB 线上 key 口径、缓存一致性指标、extmsg 字段版本兼容策略和部分命中降级基线。
