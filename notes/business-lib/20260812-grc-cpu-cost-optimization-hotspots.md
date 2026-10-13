# 2026-08-12 周三代码理解：GRC 成本/CPU 性能优化热点复盘
<!-- topic-nav:start -->
> 主题：`business/cpu-cost` · 同题补充 / 历史版本 · [主题目录](README.md) · [先读主文](20260707-grc-cpu-cost-optimization-hotspots.md)
<!-- topic-nav:end -->


> 日期：2026-08-12  
> 主题来源：2026-08-12 daily-plan 缺失，按历史未覆盖主题 fallback 到 GRC 成本/CPU 热点；KU 正文未逐篇读取，需人工补充实验口径与收益归因。  
> 服务：`feeda-mv-grc`  
> 范围：分析调权、过滤、响应组装、序列化和回写阶段的 CPU 热点，重点看 `Set2Set`、`cache_queue`、响应出口和业务解释边界。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1fr 1.15fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.arch-layer h3{margin:0 0 8px;font-size:15px;color:#102a43}.arch-box{border:1px solid #bcccdc;border-radius:8px;padding:8px 10px;margin:6px 0;background:#f8fafc;font-size:13px;line-height:1.4}.arch-box.key{background:#eefcf3;border-color:#8fd19e}.arch-arrow{font-size:18px;text-align:center;color:#627d98;margin:4px 0}.arch-title{font-size:20px;font-weight:800;margin:0 0 12px;color:#102a43}</style><div class="arch-title">GRC CPU 热点从请求到响应的放大链路</div><div class="arch-wrap"><div class="arch-layer"><h3>业务入口</h3><div class="arch-box">`src/service/grc_service.cpp`<br>服务入口与请求接入</div><div class="arch-arrow">↓</div><div class="arch-box">`src/request/grc_request.cpp`<br>请求构建、字段补齐与过滤条件计算</div><div class="arch-arrow">↓</div><div class="arch-box key">调权 / 策略判定 / 场景分流</div></div><div class="arch-layer"><h3>热点区</h3><div class="arch-box key">`SerializeToString()` / `ParseFromString()`</div><div class="arch-box">`std::vector`、`push_back`、临时拷贝</div><div class="arch-box">`cache_queue`、响应聚合、重复组包</div><div class="arch-arrow">↓</div><div class="arch-box">CPU 抖动、P99 拉长、带宽放大</div></div><div class="arch-layer"><h3>业务出口</h3><div class="arch-box">`src/process/response_function.cpp`</div><div class="arch-arrow">↓</div><div class="arch-box">结果回写、extmsg 透传、最终响应出口</div><div class="arch-arrow">↓</div><div class="arch-box">用户侧体验与实验指标归因</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "grc_service" as SVC
participant "filter / rank" as FILT
participant "response_function" as RESP
participant "protobuf encoder" as PB
Client -> SVC : request
SVC -> FILT : route + filter + score
FILT -> FILT : build response candidates
FILT -> PB : SerializeToString()
PB -> RESP : encoded payload
RESP -> RESP : merge extmsg / re-pack
RESP -> Client : final response
note over FILT,PB
high cost usually comes from
rebuild + copy + encode
end note
@enduml
```

## 2. 热点结构信息图

```infographic
infographic list-grid-badge-card
data
  title GRC 成本热点拆分
  desc 从请求入口到响应出口的高频成本点
  items
    - label 请求构建
      desc 字段补齐、条件计算、场景分流
      value 1
      icon mdi:clipboard-text
    - label 策略/调权
      desc 过滤链路和候选重排
      value 2
      icon mdi:tune
    - label 响应组装
      desc extmsg 合并、候选回写
      value 3
      icon mdi:application-import
    - label 序列化出口
      desc 消息编码与重复拷贝
      value 4
      icon mdi:serialize
```

## 3. 关键结论

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#fff;border:1px solid #d9e2ec;border-left:4px solid #2d6a4f;border-radius:10px;padding:12px 14px;margin:14px 0;color:#1f2937"><div style="font-size:14px;font-weight:800;color:#102a43;margin-bottom:6px">结论</div><div style="font-size:13px;line-height:1.6">业务侧看到的 CPU 热点通常不是单点策略函数，而是请求构建、候选聚合、响应回写和序列化一起叠加出来的。排查时要把“业务意义上的慢”拆成“构建慢”“聚合慢”“编码慢”三段看。</div></div>

## 4. Pitfalls

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px"><div style="font-weight:800;color:#102a43;margin-bottom:6px">Pitfall 1</div><div style="font-size:13px;line-height:1.6">把所有 CPU 增长都归因给策略模型是错误的。响应拼装和序列化往往更容易放大尾延迟。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px"><div style="font-weight:800;color:#102a43;margin-bottom:6px">Pitfall 2</div><div style="font-size:13px;line-height:1.6">只看单条链路的耗时也不够。要同时看消息大小、候选数和重组次数。</div></div></div>

## 5. 调试 Checklist

```infographic
infographic list-column-done-list
data
  title 调试 Checklist
  items
    - label 核对 request -> filter -> response 的阶段耗时
      done true
    - label 检查是否存在重复组包
      done true
    - label 检查候选列表是否频繁扩容
      done true
    - label 核对 extmsg 与响应体的放大比
      done true
    - label 比较优化前后的 P99 与消息大小
      done true
```

## 6. 证据来源

- `notes/base-lib/20260707-protobuf-serialize-flatbuffers-hotspots.md`
- `notes/business-lib/20260707-grc-cpu-cost-optimization-hotspots.md`
- `notes/business-lib/20260724-grc-video-launch-response-contract.md`
- `src/service/grc_service.cpp`
- `src/request/grc_request.cpp`
- `src/process/response_function.cpp`

> 内容整理：重复扫描统计驱动的历史适配附录；不作为已核实的迁移结论，已移出正文。[查看原始版本及附录](https://github.com/1998-program/cpp-core-notes/blob/5738832bab4b13ba26356af0a37a0a32e477b675/notes/business-lib/20260812-grc-cpu-cost-optimization-hotspots.md)。
