# 2026-09-02 周三业务库理解：dapper-collector v2 请求路由、权限校验与 bucket 分发边界

> 日期：2026-09-02  
> 主题来源：当前可用 daily-plan 未找到 2026-09-02 当日文件，按历史候选回退到 `dapper-collector` 的请求路由与业务准入边界。KU 正文未读取，业务背景需人工补充。  
> 范围：`src/service/collector_v2.cc`、`src/service/collector_service.cc`、`include/common/request_filter.h`、`conf/request_filter.conf`、`src/manager/sid_sender_receiver_whitelist_mgr.cc`，聚焦 BNS 准入、pd_ch 过滤、channel 分桶和 v1/v2 请求语义差异。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d8e1ea;border-radius:8px;padding:14px;background:#f8fafc;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.08fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">v2 入口</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`src/service/collector_v2.cc:110-229`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">先做 BNS permission，再按 req_info / channel_id / key 对请求记录做过滤与分桶。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">过滤语义</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`RequestFilter` + `request_filter.conf`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">pd_ch 由 sender/receiver/module/prefix_module 共同决定，属于配置驱动的业务门禁。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:.04em;">v1 兼容面</div><div style="margin-top:8px;font-size:14px;color:#102a43;">`CollectorServiceImpl::upload`</div><div style="margin-top:8px;font-size:12px;color:#52606d;">对旧版请求做 protobuf + base64 + CompressData 封装，并在 debug 模式回放 JSON。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 70px 1fr 70px 1fr 70px 1fr;gap:10px;align-items:center;"><div style="background:#eef2ff;border:1px solid #c7d2fe;border-radius:8px;padding:10px;text-align:center;color:#3730a3;">BNS 准入</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#155e75;">字段过滤</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">bucket 分流</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:10px;text-align:center;color:#9a3412;">下游 ReportMgr</div></div></div>

## 1. 核心流程图
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title dapper-collector v2 request routing boundary
participant "RPC Controller" as CNTL
participant "CltServiceV2Impl::upload" as UP
participant "BnsPermissionController" as BNS
participant "RequestFilter" as RF
participant "request_filter.conf" as CFG
participant "tmp_vec buckets" as BUCKET
participant "ReportMgr pool1" as P1
participant "CollectorConverge" as C2
CNTL -> UP : log_id / remote_side / request
UP -> BNS : is_allow_write(request_ip)
UP -> RF : load() was already performed at process start
RF -> CFG : parse pd_ch / sender / receiver / module
UP -> UP : inspect dapper_record_size()
UP -> UP : normalize key high 32 bits by record_type
UP -> UP : keep req_info fields for valid channel ids
UP -> BUCKET : item.key % FLAGS_buffer_pool1_size
BUCKET -> P1 : receive_data(vector<Item>)
P1 -> C2 : later process() consumes bucketed data
@enduml
```

## 2. 结构信息图
```infographic
infographic list-grid-badge-card
data
  title v2 请求链的 6 个分叉点
  desc 这些分叉点决定请求是被拒绝、被过滤、被采样还是进入下游处理
  items
    - label BNS 写权限
      desc `src/service/collector_v2.cc:120-127` 先做 `is_allow_write(request_ip)`，拒绝即返回
      icon mdi-shield-alert-outline
    - label accept switch
      desc `src/service/collector_v2.cc:129-132` 打开后才真正接收数据
      icon mdi-toggle-switch
    - label pd_ch 配置
      desc `include/common/request_filter.h:62-137` 由 `request_filter.conf` 装载所有允许项
      icon mdi-file-cog-outline
    - label channel 判定
      desc `src/service/collector_v2.cc:180-187` 对 `channel_id == 1 || 178` 之外的请求做过滤计数
      icon mdi-swap-horizontal
    - label bucket 切分
      desc `src/service/collector_v2.cc:210-229` 通过 `item.key % FLAGS_buffer_pool1_size` 分桶
      icon mdi-grid-large
    - label 旧版信封
      desc `src/service/collector_service.cc:34-64` 把 CollectorRequest 包成 CompressData + base64 便于日志回放
      icon mdi-package-variant
```

## 3. 代码链路拆解
### 3.1 BNS 权限决定了 v2 入口是否有资格继续处理
- `src/service/collector_v2.cc:116-127`：`remote_side().ip` 被转成字符串后传给 `BnsPermissionController::is_allow_write()`。拒绝时直接回写 `REPORT_PERMISSION_DENIED`。
- `src/service/collector_v2.cc:120-125`：拒绝分支只抽样打印 10% 日志，说明这条边界是高频而且有噪声控制的。
- `main.cc:83-97`：`BnsPermissionController::instance().init()` 和 `SidSenderReceiverWhitelistMgr::instance().load(...)` 都在 server Start 之前执行，说明业务准入是启动前配置，不是运行时临时查表。

### 3.2 `RequestFilter` 不是单字段黑白名单，而是三层业务语义
- `include/common/request_filter.h:35-61`：`pd_ch` 可以单独判定，也可以拿到 `ProductInfo`，还可以进一步结合 `module_name` 进行精确放行。
- `include/common/request_filter.h:70-135`：每个 `request.item` 都会读 `pd_ch`、`sender`、`receiver`、`module`、`prefix_module`，并把 trace 串在日志里。这个实现直接暴露了配置内容的业务意图。
- `conf/request_filter.conf:1-180`：允许项里同时存在 `microvideo-grf`、`feeda-mv-grf`、`qimao_grf`、`smart-grf`、`tieba-yuelaou` 等 sender/receiver 组合，说明这是跨业务线的统一过滤表。

### 3.3 v2 请求在进入业务池前就被切成两类流量
- `src/service/collector_v2.cc:144-187`：`has_req_info()` 的记录会读取 cuid/log_id/channel_id/request_time；其中 channel_id 为 1 或 178 才记为 valid，否则计入 filter_count。
- `src/service/collector_v2.cc:188-199`：`FLAGS_is_trace` 打开时会记录更细粒度的字段，说明这条链路本身就是为调试和线上治理留的观测点。
- `src/service/collector_v2.cc:205-229`：对 `path_info` 进行额外统计后，把记录塞入 `tmp_vec[slice]`，最后按 bucket 下发给 `ReportMgr`。这一步确保同一个 key 在同一个池里处理，减少乱序。

### 3.4 v1 旧接口保留了更强的兼容壳
- `src/service/collector_service.cc:34-64`：`CollectorRequest` 先 `SerializeToString`，再包进 `CompressData`，最后 base64；这不是压缩优化，而是兼容旧日志格式和监控采样的信封层。
- `src/service/collector_service.cc:67-79`：debug 模式下先 base64 decode，再 ParseFromString，再转 JSON 打印。这个路径更像回放工具，不是主业务链路。

## 4. Pitfalls 卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:5px solid #0f766e;border-radius:8px;padding:16px;margin:16px 0;color:#1f2937;line-height:1.65;"><div style="font-size:12px;font-weight:800;color:#0f766e;text-transform:uppercase;letter-spacing:.06em;">debug pitfalls</div><div style="font-size:22px;font-weight:900;margin:6px 0 10px;color:#172033;">入口问题通常表现成“请求进来了，但被门禁切掉”</div><div style="display:grid;grid-template-columns:1.22fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`channel_id` 只有 1 和 178 会走 valid 分支，其余请求默认计入 filter_count。若上游变更了 channel 编码而配置没同步，看起来像“业务沉默”，其实是入口策略没对齐。</div><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:8px;padding:12px;font-size:14px;">`FLAGS_is_filter_channel` 一旦打开，非目标 channel 直接 `continue`。这类问题不报错，只改变进入池子的数量，必须用计数器和 trace 还原。</div></div><div style="margin-top:10px;font-weight:900;color:#0f766e;">∎ 排查顺序：BNS 权限 → accept switch → pd_ch 配置 → channel_id → bucket 切分 → ReportMgr 接收</div></div>

## 5. 调试 checklist
```infographic
infographic list-column-done-list
data
  title dapper-collector v2 路由排查清单
  desc 适用于权限拒绝、过滤过严、bucket 抖动、v1/v2 语义混淆和调试日志缺失
  items
    - label 检查写权限
      desc 确认请求 IP 在 `BnsPermissionController::is_allow_write()` 白名单内
      done true
    - label 检查 accept 开关
      desc `accept_switch=true` 才会真正入池
      done true
    - label 检查 request_filter.conf
      desc 目标 pd_ch 是否存在，sender/receiver/module 是否齐全
      done true
    - label 检查 channel_id
      desc 1 / 178 才是 valid 入口，其余会被计入 filter_count
      done true
    - label 检查 key 分桶
      desc `item.key % FLAGS_buffer_pool1_size` 是否导致热点集中
      done true
    - label 检查 v1 回放路径
      desc debug 下 `CollectorServiceImpl::decompress()` 是否能回放原始 JSON
      done true
```

## 6. 证据来源
- `src/service/collector_v2.cc:110-229`
- `src/service/collector_service.cc:34-79`
- `include/common/request_filter.h:35-137`
- `conf/request_filter.conf:1-180`
- `src/manager/sid_sender_receiver_whitelist_mgr.cc:15-41`
- `main.cc:83-97`
- `include/service/collector_v2.h:32-88`
- `include/service/collector_service.h:26-39`

## 7. 说明
当前运行环境未找到 2026-09-02 的 daily-plan 文件；本笔记基于历史候选与本地代码回退生成，KU 正文未读取，业务背景需人工补充。