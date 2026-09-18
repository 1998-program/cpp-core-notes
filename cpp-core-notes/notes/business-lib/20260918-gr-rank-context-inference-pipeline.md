# 2026-09-18 业务库理解：gr-rank 从 RPC 到 ContextData 与 ContextDNN 并行推理的业务边界

> 日期：2026-09-18  
> 主题选择：未发现本日有效 daily-plan；按配对基础库的 fork/framework 主题，业务侧回退到 `gr-rank` **从 GRRankRequest 到 InferReq、ContextData 上下文特征、Embedding 获取、InferProcessor 分批并行推理与响应汇总的端到端业务边界**。KU 正文未读取，业务背景需人工补充；本文只依据本地代码证据。  
> 范围：`main.cc` 启动、`GRRService::predict`、`GRRServiceImpl::predict` 的请求路由/图选择、ContextDataProcessor 上下文特征抽取与 embedding、InferProcessor 的 InputChannel + bthread_async 并行、`GRRServiceImpl::generate_log` 的响应汇总。

---

## 0. 架构全景图

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;border:1px solid #d8e1ea;border-radius:8px;padding:16px;margin:16px 0;color:#243b53;line-height:1.45;"><div style="display:grid;grid-template-columns:1fr 1.2fr 1fr;gap:12px;align-items:stretch;"><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">RPC 入口</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">GRRankRequest + GRRService</div><div style="margin-top:8px;font-size:12px;color:#52606d;">brpc server 启用 baidu_std_reuse；Cntl 失败直接 ERROR_NOTMATCH 返回。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#52606d;text-transform:uppercase;letter-spacing:.04em;">图与特征</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">InferReq + ContextData</div><div style="margin-top:8px;font-size:12px;color:#52606d;">request_type 决定 str_graph；AEF 抽取 feature_list→Example；EbdComponent 取 embedding。</div></div><div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;"><div style="font-size:12px;font-weight:800;color:#102a43;font-weight:700;">推理与汇总</div><div style="margin-top:8px;font-size:15px;color:#102a43;font-weight:700;">InferProcessor + generate_log</div><div style="margin-top:8px;font-size:12px;color:#52606d;">InputChannel 批消费 + bthread_async 并行；响应按 pos×model_result 汇总。</div></div></div><div style="margin-top:12px;display:grid;grid-template-columns:1fr 42px 1fr 42px 1fr 42px 1fr;gap:8px;align-items:center;"><div style="background:#e0f2fe;border:1px solid #bae6fd;border-radius:6px;padding:10px;text-align:center;color:#075985;font-size:13px;">GRRankRequest</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ecfdf5;border:1px solid #bbf7d0;border-radius:6px;padding:10px;text-align:center;color:#065f46;font-size:13px;">InferReq + str_graph</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#fef3c7;border:1px solid #fde68a;border-radius:6px;padding:10px;text-align:center;color:#92400e;font-size:13px;">ContextDataProcessor</div><div style="text-align:center;color:#64748b;font-weight:800;">→</div><div style="background:#ede9fe;border:1px solid #ddd6fe;border-radius:6px;padding:10px;text-align:center;color:#5b21b6;font-size:13px;">InferProcessor 分批</div></div></div>

**一句话结论：** 业务侧的稳定边界是“RPC 类型→图选择→特征/embedding→并行推理→响应汇总”五段流水线；线上业务效果要先看 `InferReq` 的 `request_type` 是否被正确识别，再看 `ContextData` 的 `feature_list`、`ebd` 与 `InferProcessor` 的 `num_per_batch` 是否匹配 `pos_num`。

## 1. 端到端请求流程

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
title gr-rank 端到端业务流水线
participant "brpc Server" as SRV
participant "GRRService::predict" as RPC
participant "GRRServiceImpl::predict" as CORE
participant "DeepEsGraph" as GRAPH
participant "ContextDataProcessor" as CDP
participant "EbdComponent" as EBD
participant "InferProcessor" as INF
participant "GRRServiceImpl::generate_log" as LOG
SRV -> RPC : predict(cntl, request, response, done)
RPC -> RPC : cntl->Failed() ? set_error : next
RPC -> CORE : predict(request, response, err_msg)
CORE -> CORE : request_type -> str_graph + extractor_name
CORE -> GRAPH : get_deep_es_instance(str_graph)
alt table/model not ready
  CORE --> RPC : set_error(ERROR_NOTMATCH)
else ready
  CORE -> GRAPH : instance.call(infer_req, response, ...)
  GRAPH -> CDP : run GraphFunction
  CDP -> CDP : extract_context_feature -> feature_list -> Example
  CDP -> EBD : get_embedding(feature_ids, ebd, cube_table_name)
  CDP -> INF : publish ContextEbdData
  INF -> INF : subscribe item_ebd_channel + bthread_async(num_per_batch)
  INF -> INF : feed_forward per pos per esins
  GRAPH -> CORE : response filled
  CORE -> LOG : generate_log(response, model_result_str)
  CORE --> RPC : return true
end
RPC -> SRV : ReusableRPCProtocol::Closure::send_response + release
@enduml
```

## 2. 业务边界信息图

```infographic
infographic list-grid-badge-card
data
  title gr-rank 业务边界图
  desc 五段流水线共同决定 gr-rank 的线上行为
  items
    - label RPC 入口
      desc baidu_std_reuse + ReusableRPCProtocol；Cntl 失败 ERROR_NOTMATCH；brpc 重用协议可重发请求并释放 closure
      icon mdi:server-network
    - label 请求路由
      desc request_type 决定 str_graph 与 extractor_name，model_name 决定 layout_predict_net2 / paddle 分支
      icon mdi:routes-clock
    - label 特征抽取
      desc FeatureExtractorManager::create_feature_list("context") + parent user_feature_list_info_ptr；feature_list 复用而非每次新建
      icon mdi:list-box-outline
    - label Embedding
      desc slot_fields + feature_ids 通过 EbdComponent::get_embedding 写到 ebd map；cube_table_name 决定 SparseTable
      icon mdi:table-multiple
    - label 推理并行
      desc InputChannel 批消费 + bthread_async；最后一批不满 num_per_batch 不开线程，本地同步执行
      icon mdi:call-split
    - label 响应汇总
      desc 按 pos × model_name 聚合 result_vec，做 q_avg；release closure 之前的 send_response 决定是否真发回包
      icon mdi:archive-search-outline
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 3. 关键机制拆解

### 3.1 启动顺序保证 SparseTable、DeepEs 与 ChannelManager 先就绪
`src/main.cc:60-88` 的 `main()` 先 `google::ParseCommandLineFlags` 与 `com_loadlog`，随后通过 `CHECK_RET(GRRServiceImpl::init(), "init failed!")` 触发 `init`。`src/service/gr_rank_service.cc:123-153` 依次 `init` `FeedLocaltimeManager`、`DeepEsGraph`、`ChannelsManager`，注册 `ReusableRPCProtocol::register_protocol()`，最后 `init_expired_cache`。任一 FATAL 都会 `_exit(1)`。

### 3.2 RPC 入口判断 Cntl 失败并直接释放 closure
`src/service/gr_rank_service.cc:88-121` 的 `predict` 入口先 `baidu::rpc::ClosureGuard done_guard(done)`，然后判 `cntl->Failed()`，否则调用 `GRRServiceImpl::predict`。失败路径通过 `set_error(logid, cntl, response, -1, "cntl failed", done_guard.release())` 显式释放 closure，避免 double-release。正常路径会 `dynamic_cast<ReusableRPCProtocol::Closure*>(done_guard.release())` 并 `send_response() + release()`，这是 `baidu_std_reuse` 的释放约定。

### 3.3 `request_type` 是图选择与特征抽取的双向开关
`src/service/gr_rank_service.cc:155-280` 把 `request_type` 分到 `ITEM_REQUEST`/`REFRESH_REQUEST`/`CLICK_FOLLOW_REQUEST`/`BUFFER_RANK_REQUEST`/`ABACUS_CONTEXT_DNN_COMMON`/`ABACUS_CONTEXT_DNN_CONTEXT`。其中 `ABACUS_CONTEXT_DNN_CONTEXT` 会进一步按 `deepes_model_name` 选 `layout_predict_net2`、`paddle_ctxdnn_predict_net` 等分支，并通过 `s_layout_vec` 或 `s_layout_vec_add_dc_card` 切 layout。没有 `request_type` 时退化为 `ITEM_REQUEST`。

### 3.4 SparseTable 与 DeepEs 双闸门失败直接 ERROR_NOTMATCH
`src/service/gr_rank_service.cc:183-205` 先用 `SparseTable::get_instance().is_table_ready(infer_req.cube_table_name)` 校验表，没准备好就把 `error_code` 置为 `ERROR_NOTMATCH` 并把 `cube_table_name + " table is not ok!"` 写回 `error_msg`。`DeepEsComponent::get_instance().check_model_ok(infer_req.deepes_model_name)` 是第二道闸门，模型未就绪时同样 `ERROR_NOTMATCH`。`AEF_EXTRACTOR` 路径还会再校验 `FeatureExtractorManager::get_instance().get_extractor(infer_req.extractor_name)`。

### 3.5 `ContextDataProcessor` 把上下文特征装进 `ContextData` 并发布
`src/es/processor/context_dnn_context_data_processor.cc:30-95` 定义 `ContextDataProcessor` 与 `ContextDataContext`，`setup` 通过 `vertex.named_dependency("infer_req")` 与 `vertex.named_emit("context_ebd_data")` 拿到依赖/输出；`process` 中先 `data.clear()`，再调用 `extract_context_feature` 和 `get_context_ebd`。`extract_context_feature` 通过 `extractor->create_feature_list("context")` 创建并把 `ctx_feature_list_info_ptr` 复用，遍历 `sample->info().selected_index`，将 `arg(1)/(2)/(3)` 填入当前/前两个 index，最后用 `to_example` 把 `feature_list` 转成 `Example`。`get_context_ebd` 用 `EbdComponent::get_embedding(SparseTable, feature_ids, ebd, cube_table_name)` 写 ebd。

### 3.6 `ContextData` 既是 feature 工具又是 embedding 容器
`src/es/base/context_data.h:16-79` 的 `ContextData` 持有 `feature_list`、`example`、`slot_fields`、`layout_slot_fields`、`feature_ids`、`ebd`，`clear()` 全部复位。`ContextEbdDataInterface` 暴露 `get_ebds/get_example/get_fields/get_layout_fields`，下游 layer/InferProcessor 通过该接口访问 ContextData 而不是直接读字段。

### 3.7 `InferProcessor` 用 InputChannel + bthread_async 把推理切成 num_per_batch
`src/es/processor/infer_processor.cc:57-105` 的 `setup` 从 `comcfg::ConfigGroup` 读 `num_per_batch` 与 `item_ebd_channel_name`，并 `declare_channel<AllEbdInterface>()` 把输入拉进来。`process` 计算 `num_of_batch = ceil(item_size / num_per_batch)` 与 `res_num = num_of_batch * pos_num`，先 reserve `results` 与 `item_contextss`。`process:259-272` 把每批用 `bthread_async(consume_data_func, range, cur_index*pos_num, &item_contextss[cur_index])` 调度；最后一批不满 `num_per_batch` 不开线程，本地同步执行。

### 3.8 `consume_data_func` 是推理核心：Pack + feed_forward
`src/es/processor/infer_processor.cc:150-256` 按 `range` 收集 `item_base_field_ebd/item_nopos_field_ebd`，再对每个 pos 收集 `item_pos_field_ebd`。`PackInferData::pack_es_infer_data` 把数值特征 + noise 写到 ES 实例；首次 pos 走 `pack_dnn_infer_data`，后续 pos 走 `update_dnn_pos_infer_data` 节省装配开销。最后 `deep_es_object->feed_forward(deepes_model_name, result)`，结果写入 `results[cur_index++]`。所有 future 通过 `future.get()` 在 `process` 末尾 join。

### 3.9 `generate_log` 按 pos 与 model_name 汇总模型结果
`src/service/gr_rank_service.cc:50-86` 的 `generate_log` 遍历 `response->pos_result()`，对每个 `pos_result.item_result()` 按 `model_result.name()` 聚合 `result_vec`，首次看到时 `resize()`，否则要求 `result_size()` 与现有 `result_vec.size()` 一致，否则置 `error_result = true`。最终把 `result_vec` 做 `q_avg`（除以 `item_result_size()`）并按 `model_name` 输出到 `model_result_str`。

## 4. 业务对账图

```infographic
infographic compare-binary-horizontal-underline-text-vs
data
  title 请求到响应的四类对账点
  desc 这四类问题在 gr-rank 线上故障占绝大多数
  items
    - label rpc contract
      desc baidu_std_reuse 与 ReusableRPCProtocol::Closure 释放顺序；Cntl 失败直接 ERROR_NOTMATCH
      icon mdi:server-security
    - label graph selector
      desc request_type + deepes_model_name 共同决定 str_graph 与 layout_vec
      icon mdi:graph-outline
    - label feature contract
      desc ctx_feature_list_info_ptr 复用、parent user_feature_list_info_ptr；AEF_EXTRACTOR 校验 extractor_name 存在
      icon mdi:format-list-bulleted
    - label inference contract
      desc num_per_batch、pos_num、item_size 三者共同决定 bthread_async fan-out；最后一批不开线程
      icon mdi:call-split
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 5. Pitfalls 卡片

<div style="font-family:Inter,Arial,sans-serif;background:#ffffff;border:1px solid #d7dee8;border-left:6px solid #0f766e;border-radius:8px;padding:18px;margin:18px 0;color:#1f2937;line-height:1.6;"><div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#0f766e;font-weight:800;">Pitfalls · gr-rank 业务边界</div><div style="font-size:24px;line-height:1.2;font-weight:900;margin:7px 0 12px;">图选择对了，不代表推理结果一致</div><div style="display:grid;grid-template-columns:1.35fr 1fr;gap:12px;"><div style="background:#f8fafc;border-top:3px solid #0f766e;border-radius:6px;padding:12px;font-size:14px;"><b>四个常见误判：</b><br>① 把 SparseTable/DeepEs 双闸门失败当空响应；② 不识别 ABACUS_CONTEXT_DNN_CONTEXT 与 model_name 的 layout 分支；③ ctx_feature_list_info_ptr 复用导致父指针被覆盖；④ bthread_async fan-out 后忘记 join future。</div><div style="background:#fff7ed;border-top:3px solid #f59e0b;border-radius:6px;padding:12px;font-size:14px;color:#7c2d12;"><b>核对顺序</b><br>cube_table_name → deepes_model_name → request_type → str_graph → extractor_name → ContextData → Embedding → num_per_batch/pos_num → feed_forward → generate_log → send_response。</div></div><div style="margin-top:12px;font-weight:900;color:#0f766e;">∎ gr-rank 的业务边界在 str_graph 一行，在 num_per_batch 一行，而不是在 brpc 端口一行。</div></div>

## 6. 调试 checklist

```infographic
infographic list-column-done-list
data
  title gr-rank 业务侧排查清单
  desc 适用于线上请求失败、上下文特征缺失、推理结果不一致
  items
    - label 确认 init 顺序
      desc FeedLocaltimeManager → DeepEsGraph → ChannelsManager → ReusableRPCProtocol → ExpiredCacheComponent
      done true
    - label 确认 Cntl 失败路径
      desc cntl->Failed() 时 set_error 并 release closure；不要重复 release
      done true
    - label 确认 SparseTable/DeepEs 闸门
      desc cube_table_name 与 deepes_model_name 任一未就绪即 ERROR_NOTMATCH
      done true
    - label 确认 request_type 分支
      desc 没有 request_type 时退化为 ITEM_REQUEST；ABACUS_CONTEXT_DNN_CONTEXT 会再走 model_name layout 分支
      done true
    - label 确认 ContextData 复用
      desc ctx_feature_list_info_ptr 已存在时直接复用，parent 是 user_feature_list_info_ptr
      done true
    - label 确认 Embedding 抓取
      desc EbdComponent::get_embedding(SparseTable, feature_ids, ebd, cube_table_name) 必须命中 SparseTable
      done true
    - label 确认 fan-out
      desc num_per_batch × pos_num 与 sample->size 匹配；最后一批不满不开线程，本地同步
      done false
    - label 确认 future join
      desc 所有 bthread_async future.valid() 后必须 future.get()，否则结果可能未写入
      done false
    - label 确认 generate_log 长度
      desc model_result.result_size() 与现有 result_vec.size() 不一致时 error_result=true
      done false
theme
  palette #3D5A80 #0F766E #F59E0B
```

## 7. 证据来源

- `src/main.cc:60-88`：brpc port、`idle_timeout_s`、`baidu_std_reuse`、`AddService(&gr_rank_service, SERVER_DOESNT_OWN_SERVICE)`。
- `src/service/gr_rank_service.h`：声明 `predict` / `set_error`。
- `src/service/gr_rank_service_impl.h:14-32`：`GRRServiceImpl::predict/init` 与 `OK/ERROR_NOTMATCH`。
- `src/service/gr_rank_service.cc:35-86`：`set_error` 与 `generate_log` 按 model_name 汇总。
- `src/service/gr_rank_service.cc:88-121`：`predict` 入口的 cntl 校验、ClosureGuard、Closure::send_response。
- `src/service/gr_rank_service.cc:123-153`：`init` 顺序、ReusableRPCProtocol 与 ExpiredCacheComponent 初始化。
- `src/service/gr_rank_service.cc:155-340`：`request_type`/`deepes_model_name` → `str_graph`/`extractor_name`/`layout_vec` 决策。
- `src/es/base/context_data.h:16-79`：`ContextData` 与 `ContextEbdDataInterface` 的字段/方法定义。
- `src/es/base/context_data.h:28-47`：`clear()`/`to_string()` 用于请求间重置与 TraceLog。
- `src/es/processor/context_dnn_context_data_processor.cc:30-95`：ContextDataProcessor 的 setup/process、依赖/输出获取、clear。
- `src/es/processor/context_dnn_context_data_processor.cc:96-141`：`extract_context_feature` 通过 feature_list + selected_index + parent 写 Example。
- `src/es/processor/context_dnn_context_data_processor.cc:144-163`：`get_context_ebd` 用 `EbdComponent::get_embedding`。
- `src/es/processor/infer_processor.cc:39-105`：InferContext 与 setup 的 comcfg 读取、named_dependency/emit。
- `src/es/processor/infer_processor.cc:107-145`：噪声/版本/ES 实例获取与 noise_map 填充。
- `src/es/processor/infer_processor.cc:150-256`：`consume_data_func` 中 pack_es_infer_data、pack/update_dnn_pos_infer_data、feed_forward。
- `src/es/processor/infer_processor.cc:258-281`：fan-out 与最后一批本地同步，future.join。

> KU 正文未读取，需人工补充 gr-rank 的 request_type 灰度策略、layout_predict_net2/paddle_ctxdnn_predict_net 的 AB 实验分桶、SparseTable/DeepEs 的灰度加载流程、num_per_batch 调优依据以及 generate_log 错误率监控口径。