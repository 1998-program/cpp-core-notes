# 2026-07-24 周五代码理解：GRC video_launch 响应出口与 GRG 返回契约

> 本文基于本地 `feeda-mv-grc` 代码阅读生成；未获得可直接读取的 KU 背景文档，线上实验、指标口径和策略负责人需人工补充。

## 1. 架构全景图：从元数据补齐到 GRG 响应

<style>.arch-wrap{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:14px;padding:18px;margin:16px 0;color:#243b53}.arch-title{font-size:22px;font-weight:850;margin-bottom:12px;color:#102a43}.arch-grid{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px}.arch-layer{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px}.arch-layer h3{margin:0 0 10px;font-size:14px;color:#102a43}.arch-box{border:1px solid #bcccdc;border-left:4px solid #3d5a80;border-radius:8px;padding:9px;margin:8px 0;background:#f8fafc}.arch-box strong{display:block;font-size:13px;color:#102a43}.arch-box span{display:block;font-size:12px;line-height:1.5;color:#52606d;margin-top:4px}.arch-arrow{text-align:center;font-weight:800;color:#486581;margin:8px 0}.arch-note{font-size:12px;color:#627d98;margin-top:10px}.arch-hot{border-left-color:#c2410c;background:#fff7ed}.arch-data{border-left-color:#047857;background:#ecfdf5}@media(max-width:820px){.arch-grid{grid-template-columns:1fr}}</style><div class="arch-wrap"><div class="arch-title">GRC video_launch Response Contract</div><div class="arch-grid"><div class="arch-layer"><h3>Graph Inputs</h3><div class="arch-box"><strong>FillMetaPipelineFunction</strong><span>读取候选内容并通过 GCMS/本地字典补齐卡片元数据。</span></div><div class="arch-box"><strong>LoadsQueue / SlotPreAssign</strong><span>保量、队列和坑位预分配结果作为响应排序与截断依据。</span></div><div class="arch-box"><strong>NewsResponseForGrg</strong><span>新闻沉浸式条件依赖，命中时合入视频响应出口。</span></div></div><div class="arch-layer"><h3>Response Assembly</h3><div class="arch-box arch-hot"><strong>ResponseForGrgFunction</strong><span>创建 `_response_for_grg`，清空旧响应，逐条写入 content、attachment、ext 特征和截断结果。</span></div><div class="arch-arrow">metadata + queue signals -> protobuf response</div><div class="arch-box arch-data"><strong>DNN Q / SIA / PCS</strong><span>把 average_q、队列长度、PCS quota 等诊断特征写入 ext_msg 或 attachment。</span></div></div><div class="arch-layer"><h3>Consumers</h3><div class="arch-box"><strong>GRG / upper graph</strong><span>消费 ResponseForGrg，继续做跨服务合并、展示控制或回传。</span></div><div class="arch-box"><strong>Debug logs</strong><span>通过 log_id 采样、SIA 计数和 funnel 顶点定位响应异常。</span></div><div class="arch-box"><strong>Experiment guards</strong><span>不同 UA、scene、实验条件会改变新闻合入、quota、截断和字段透传。</span></div></div></div><div class="arch-note">证据入口：`src/main.cpp` 注册服务，`grg_response.conf` 绑定响应顶点，`response_for_grg.cpp` 完成出口组装。</div></div>

## 2. 入口与配置绑定

`src/main.cpp:19-21` 引入 `GrcService`、HTTP service 和全局初始化，说明该服务不是单一算法库，而是 RPC 服务进程。video_launch 的图配置把最终响应出口拆成独立顶点：`conf/plugins/graph/video_launch/grg_response.conf:1-8` 声明 `ResponseForGrgFunction`，emit `_response_for_grg` 与 `_truncated_effect_queue_for_pcs`；`grg_response.conf:9-28` 继续声明新闻响应、loads 队列、IP cluster、slot 预分配和历史满意度等依赖。

这意味着排查“GRC 返回少量”“GRG 收到字段为空”“PCS 截断不符合预期”时，不应只看召回或 DataMerge。最终响应顶点本身会二次消费队列信号、实验参数和历史特征，它是跨服务契约的最后一道写口。

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor "GRG caller" as caller
participant "GrcService" as svc
participant "GraphEngine\nvideo_launch" as graph
participant "FillMetaPipelineFunction" as fill
participant "ResponseForGrgFunction" as resp
database "GCMS / dict" as gcms
caller -> svc: query video_launch
svc -> graph: build context and execute graph
graph -> fill: candidates + card ids
fill -> gcms: query_common / metadata lookup
gcms --> fill: title, media, card metadata
fill --> graph: FillMetaPipelineResult
graph -> resp: queues + meta + experiment context
resp -> resp: clear response, add content, write attachment/ext
resp --> graph: ResponseForGrg + TruncatedEffectQueueForPcs
graph --> svc: graph outputs
svc --> caller: protobuf response
@enduml
```

## 3. FillMeta 的职责边界

`src/processor/video_launch/fill_meta_pipeline.cpp:19-23` 定义 `FillMetaPipelineFunction`，初始化时把 `_fill_meta_pipeline_result` 和 `_card_fill_meta_result` 绑定到当前 graph vertex。`fill_meta_pipeline.cpp:27-31` 创建 `UpToCostEmit` 和 `ChannelPublisher<RidTmpInfo*>`，说明这里是 pipeline 风格的批处理节点，不是简单同步函数。

该文件在开头引入 `gcms_plugin.h` 与 `GcmsComponent`，并声明 `open_gcms_statistics` 开关，说明元数据补齐具有外部内容服务依赖和统计开关。`pipeline_card_type` 当前只包含 `241`，排查卡片元数据问题时要先确认 card type 是否进入该管线，否则后续响应出口即使正常也只能拿到不完整内容。

```infographic list-grid-badge-card
data
  title FillMeta 关键输入输出
  desc video_launch 响应前的元数据补齐检查点
  items
    - label 输入候选
      desc RidTmpInfo 与候选内容进入 ChannelPublisher
      icon mdi/file-tree
    - label 外部依赖
      desc GCMS 组件和本地字典共同补齐内容字段
      icon mdi/database-search
    - label 卡片白名单
      desc pipeline_card_type 当前关注 241 类型
      icon mdi/card-bulleted-outline
    - label 输出结果
      desc FillMetaPipelineResult 与 CardFillMetaResult 绑定 graph vertex
      icon mdi/export
    - label 成本观测
      desc UpToCostEmit 记录 fill_meta 前后耗时
      icon mdi/timer-outline
```

## 4. ResponseForGrg 的返回契约

`src/processor/video_launch/response_for_grg.cpp:102` 定义 `ResponseForGrgFunction`，继承 `StrategyFunction<DiversityListGeneratorStrategy>`。`response_for_grg.cpp:178-187` 进入 `process_impl` 后先 emit `_response_for_grg` 和 `_truncated_effect_queue_for_pcs`，随后 `response->Clear()`，这解释了为什么上游顶点留下的旧字段不会自然透传：最终出口会重新组装响应。

`response_for_grg.cpp:181-192` 的早期逻辑会创建响应对象、处理直播内容并进入主内容写入；`response_for_grg.cpp:225-231` 写 attachment，例如 actual_reqnum 和 UA 类型；`response_for_grg.cpp:570-578` 遍历 `_res_content_vec` 并为每个 content 写入平均 Q 特征与 SIA 计数。文件顶部的宏在 `response_for_grg.cpp:69-92` 反复调用 `ext_msg.add_dnn_q()`，把模型分数与缓存标记写成通用 DNN Q 结构。

这条链路的关键不是“把候选列表原样返回”，而是把候选、队列、实验、元数据和诊断字段折叠成 GRG 可消费的 ResponseForGrg。任何字段缺失都要区分三类原因：上游没有生产、配置没有声明 depend、出口顶点没有写入。

```infographic sequence-ascending-steps
data
  title ResponseForGrg 组装步骤
  desc 从 graph 输出到跨服务 protobuf 返回
  items
    - label Emit 输出对象
      desc `_response_for_grg` 与 `_truncated_effect_queue_for_pcs`
      icon mdi/source-branch
    - label Clear 旧响应
      desc 避免复用上下文时旧字段残留
      icon mdi/eraser
    - label 写内容列表
      desc zhibo/news/effect queues 逐类落入 content
      icon mdi/format-list-numbered
    - label 写 attachment
      desc actual_reqnum、ua_str_type、quota 诊断信息
      icon mdi/paperclip
    - label 写 ext_msg
      desc dnn_q、average_q、cache 标记等模型特征
      icon mdi/code-json
    - label 输出给 GRG
      desc 下游基于 ResponseForGrg 继续合并或展示
      icon mdi/export-variant
```

## 5. 调试顺序

当线上现象是“GRG 侧看不到 GRC 字段”时，建议按下面顺序排查：

```infographic list-column-done-list
data
  title Debug Checklist
  desc 响应出口问题优先按依赖链排查
  items
    - label 确认图配置加载了 `grg_response.conf`
      desc `function: ResponseForGrgFunction` 必须在目标 graph 中生效
      done true
    - label 检查 depend 是否声明
      desc 缺依赖时出口函数无法消费上游结果
      done true
    - label 检查 FillMeta 是否命中 card type
      desc 元数据缺失先看 `pipeline_card_type` 与 GCMS 返回
      done true
    - label 检查 `_res_content_vec` 是否为空
      desc DataMerge 有量不代表最终响应列表有量
      done true
    - label 检查 ext_msg 写入分支
      desc 模型分数、cache 标记和平均 Q 可能受实验条件控制
      done true
    - label 用 log_id 采样核对 SIA
      desc 出口顶点已有采样日志和计数字段，优先用现成观测
      done true
```

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff7ed;border:1px solid #fed7aa;border-radius:12px;padding:16px;margin:16px 0;color:#431407"><div style="font-size:12px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:#9a3412">Pitfalls</div><div style="font-size:22px;font-weight:850;margin:6px 0 10px;color:#7c2d12">不要把召回有量等同于响应有量</div><div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px"><div style="font-size:14px;line-height:1.65">video_launch 出口会重新清空并组装 ResponseForGrg。上游召回、DataMerge、FillMeta 任一阶段正常，都不能单独证明 GRG 最终收到完整字段。最终判断点必须落到 `ResponseForGrgFunction` 的 content、attachment 和 ext_msg 写入分支。</div><div style="border-left:4px solid #ea580c;padding-left:10px;font-size:13px;line-height:1.6;background:#fffbeb;border-radius:8px;padding-top:8px;padding-bottom:8px">常见误判：只查候选队列长度，不查 `_truncated_effect_queue_for_pcs`；只查 GCMS 命中，不查 card type；只查 protobuf content，不查 attachment/ext_msg。</div></div></div>

## 6. 证据来源

- `src/main.cpp:19-21`：服务入口引入 `GrcService`、HTTP service 和全局初始化。
- `conf/plugins/graph/video_launch/grg_response.conf:1-8`：`ResponseForGrgFunction` emit `_response_for_grg` 与 `_truncated_effect_queue_for_pcs`。
- `conf/plugins/graph/video_launch/grg_response.conf:9-28`：响应出口依赖新闻响应、loads 队列、IP cluster、slot 预分配和用户历史满意度。
- `src/processor/video_launch/fill_meta_pipeline.cpp:19-31`：FillMeta pipeline 绑定 graph vertex、创建成本记录和 channel publisher。
- `src/processor/video_launch/response_for_grg.cpp:69-92`：DNN Q / cache 标记写入 ext_msg 的宏。
- `src/processor/video_launch/response_for_grg.cpp:102-187`：`ResponseForGrgFunction` 定义、emit 响应对象并清空旧响应。
- `src/processor/video_launch/response_for_grg.cpp:225-231`：写入 attachment 中的请求数量与 UA 类型。
- `src/processor/video_launch/response_for_grg.cpp:570-578`：遍历结果内容并写入 average_q 与 SIA 计数。

## 7. 结论

本周建议把 GRC video_launch 的排查边界从“召回/DataMerge 是否产出候选”后移到“ResponseForGrg 是否完成最终契约写入”。`FillMetaPipelineFunction` 解决内容元数据可用性，`ResponseForGrgFunction` 解决跨服务返回结构可用性；两者中间由 `grg_response.conf` 的 depend/emit 连接。后续如果要做自动化扫描，可以围绕三类规则：graph 配置是否声明依赖、出口函数是否 Clear 后重写关键字段、ext_msg 是否受实验条件遗漏。

---

## 七、业务代码库适配分析
> **分析时间**：2026-07-25T19:01:50.686964
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析报告

### 1. 分析摘要

- 从扫描结果看，目标技术在两个业务代码库中**已经有少量落地**，但整体覆盖率仍然很低。`feeda-mv-grg` 仅发现 1 个文件使用，`feeda-mv-grc` 发现 10 个文件使用；相比之下，两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 的使用规模非常大，说明当前业务主体仍以标准库容器为主，目标技术尚未形成体系化迁移。

- 从迁移潜力看，`feeda-mv-grc` 的收益空间更大：它是召回汇聚服务，存在大量 graph vertex、候选队列、元数据补齐、响应组装和实验特征写入逻辑，`std::vector` 使用达到 8520 次、`std::unordered_map` 使用达到 2860 次。结合本文分析的 `FillMetaPipelineFunction`、`ResponseForGrgFunction` 等热点链路，候选列表、依赖表、ext/attachment 特征缓存等场景都具备进一步优化容器分配、哈希查找和内存复用的潜力。`feeda-mv-grg` 虽然目标技术使用更少，但作为序列生成服务，模型预测、排序、多样性规则中大量使用 `std::vector<RidTmpInfoPtr>`，也适合从低风险的局部热点开始试点。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- **目标技术使用现状**
  - 已发现目标库使用：1 个文件。
  - 参考文件：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 说明该服务中已经存在少量目标技术接入经验，可以作为后续迁移时的代码风格、依赖引入方式、编译兼容性参考。

- **std 等价物使用规模**
  - `std::vector`：1969 次，分布在 356 个文件。
  - `std::string`：2443 次，分布在 425 个文件。
  - `std::unordered_map`：734 次，分布在 205 个文件。
  - 容器使用分布较广，但数量明显小于 GRC，建议优先选择模型预测、多样性规则、候选列表处理等热点路径做点状优化，而不是全局替换。

- **典型业务场景**
  - `model/model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
    - `candidate_vec` 是模型预测入口参数，属于典型高频读写候选列表。
    - 如果目标技术是高性能 vector / small-vector 类容器，需要评估接口兼容性，因为该接口是虚函数，直接改签名会影响所有派生类。

  - `model/paddle_model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```
    - 该路径涉及模型预测与 tensor 输入组装，候选数量较大时，`std::vector` 扩容、拷贝、遍历成本可能成为局部热点。
    - 由于函数链路较长，建议先通过 perf、火焰图或现有耗时日志确认 `candidate_vec` 的构造与扩容是否显著，再决定是否迁移。

#### feeda-mv-grc：召回汇聚服务

- **目标技术使用现状**
  - 已发现目标库使用：10 个文件。
  - 扫描结果中列出的代表文件包括：
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`
    - `processor/new_adjust/precise_score_init.cpp`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
    - `processor/multi_factor/ltr_factor_gen_scene.cpp`
    - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - 这些文件多集中在 adjuster、score init、factor generation 等业务计算路径，说明 GRC 已经有一定目标技术接入基础，可优先复用其封装方式和工程实践。

- **std 等价物使用规模**
  - `std::vector`：8520 次，分布在 1290 个文件。
  - `std::string`：7267 次，分布在 1247 个文件。
  - `std::unordered_map`：2860 次，分布在 646 个文件。
  - 容器使用规模很大，且 GRC 处于请求主链路，候选聚合、graph depend、metadata fill、response assembly 都存在大量临时容器和哈希映射，迁移收益潜力高于 GRG。

- **典型业务场景**
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
    - 该逻辑用于 graph 依赖关系展示或分析，`depend_map` 是字符串到顶点编号列表的映射。
    - 如果请求频繁或 graph 较大，`std::unordered_map<std::string, std::vector<int>>` 会产生较多小对象分配和哈希查找成本。
    - 适合评估替换为目标高性能 hash map / flat map 类容器，同时对 `vector<int>` 做 `reserve`。

  - `service/grc_http_service.cpp`
    ```cpp
    static std::vector<std::string> colors{"#FFB6C1", "#DC143C", "#DB7093", ...};
    ```
    - 这是静态配置型数据，性能问题通常不大。
    - 更适合保持现状，或者仅在代码规范层面改为 `std::array` / `string_view` 类只读结构，没必要作为目标技术迁移优先级。

  - `service/grc_http_service.cpp`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    ```
    - 这是 HTTP 调试或控制参数解析场景，涉及 query string 拆分、临时字符串列表。
    - 如果该接口不是高 QPS 核心链路，迁移优先级不高；如果用于线上频繁访问的 graph debug API，可考虑减少字符串拷贝。

  - `src/processor/video_launch/response_for_grg.cpp`
    - 虽然扫描片段没有列出该文件中的容器使用细节，但根据技术笔记，该文件是 video_launch 最终响应出口，负责：
      - 遍历 `_res_content_vec`
      - 写入 content
      - 写入 attachment
      - 写入 ext_msg / dnn_q / average_q
      - 输出 `_truncated_effect_queue_for_pcs`
    - 这是 GRC 与 GRG 的跨服务契约最终写口，属于比普通调试接口更值得关注的性能敏感路径。
    - 如果该文件中存在大量临时 `std::vector`、`std::string`、`std::unordered_map`，建议作为 GRC 侧重点优化对象。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先在 GRC 的响应出口链路做容器分配优化**
  - 重点文件：
    - `src/processor/video_launch/response_for_grg.cpp`
  - 适用场景：
    - `_res_content_vec` 遍历。
    - content / attachment / ext_msg 组装。
    - DNN Q、average_q、cache 标记等诊断字段写入。
    - `_truncated_effect_queue_for_pcs` 生成。
  - 建议：
    - 对结果列表、截断队列、临时特征列表等容器增加明确 `reserve()`。
    - 对只在函数内部使用、生命周期短的临时 map/list，评估替换为目标高性能容器。
    - 对字符串 key 的 ext/attachment 写入路径，优先排查是否存在重复构造 `std::string`，可考虑使用 `string_view` 或目标库的轻量字符串视图。
  - 原因：
    - `ResponseForGrgFunction` 会 `response->Clear()` 后重新组装响应，是 GRC 到 GRG 的最终契约出口。
    - 该路径直接影响请求尾延迟和响应字段完整性，优化收益更容易体现在主链路指标上。

- **建议 2：在 GRC 的 graph 依赖构建逻辑中评估替换 hash map**
  - 重点文件：
    - `service/grc_http_service.cpp`
  - 典型代码：
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    ```
  - 建议：
    - 如果该接口在生产环境高频调用，可将 `std::unordered_map<std::string, std::vector<int>>` 评估替换为目标高性能 hash map。
    - 在构建 `depend_map` 前，如果可以获取 vertex 数量，建议增加：
      ```cpp
      depend_map.reserve(all_vertex.size());
      ```
    - 对 `std::vector<int>` value，如果单个 depend 对应多个 vertex，可按历史统计或平均依赖数预留容量。
  - 原因：
    - 该代码存在字符串 hash、map 插入、vector 扩容三类成本。
    - GRC 中 `std::unordered_map` 使用达到 2860 次，说明类似模式可能广泛存在，适合作为 hash map 迁移样板。

- **建议 3：GRG 侧不要直接修改模型接口签名，先从函数内部临时容器试点**
  - 重点文件：
    - `model/model.h`
    - `model/paddle_model.h`
  - 典型代码：
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
  - 建议：
    - 不建议第一阶段把虚函数参数从 `std::vector<RidTmpInfoPtr>&` 改成目标容器类型。
    - 可优先优化 `predict` 内部派生出的临时列表、特征数组、过滤结果数组。
    - 如果目标技术支持与 `std::vector` 类似的 view/span，优先使用非 owning view 减少拷贝，而不是修改所有调用方。
  - 原因：
    - `model/model.h` 是抽象接口，改签名会造成大范围 ABI/API 变更。
    - `model/paddle_model.h` 下游调用链较长，涉及模板 `predict<ModelDependInput>`，直接迁移风险高。
    - GRG 目前目标技术仅发现 1 个文件使用，工程经验较少，应以低侵入方式试点。

- **建议 4：复用 GRC 已有目标技术使用文件作为迁移样板**
  - 可参考文件：
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`
    - `processor/new_adjust/precise_score_init.cpp`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
    - `processor/multi_factor/ltr_factor_gen_scene.cpp`
    - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - 建议：
    - 先梳理这些文件中的目标技术引入方式，包括头文件、命名空间、编译依赖、容器初始化方式。
    - 将其中稳定运行的写法沉淀为迁移模板，再推广到 `response_for_grg.cpp`、`fill_meta_pipeline.cpp` 等核心路径。
  - 原因：
    - GRC 已有 10 个文件使用目标技术，说明编译链路和运行环境大概率已经具备基础支持。
    - 直接复用内部已有实践，比从外部文档重新引入更安全。

- **建议 5：FillMeta 链路优先优化批处理容器和字符串元数据**
  - 重点文件：
    - `src/processor/video_launch/fill_meta_pipeline.cpp`
  - 适用场景：
    - `RidTmpInfo*` 批量传递。
    - `ChannelPublisher<RidTmpInfo*>` 发布。
    - GCMS 返回元数据写入。
    - card type 过滤，例如当前 `pipeline_card_type` 仅包含 `241`。
  - 建议：
    - 对批量候选容器预估容量，避免 pipeline 中反复扩容。
    - 对 title、media、card metadata 等字段，排查是否存在不必要的 `std::string` 拷贝。
    - 如果 GCMS 元数据只读透传，可评估使用轻量 string view，避免从外部结果到内部结构的重复构造。
  - 原因：
    - FillMeta 是响应前元数据可用性的关键节点。
    - 元数据字段通常字符串较多，`std::string` 在 GRC 中使用达到 7267 次，是潜在优化重点。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：跨服务 protobuf 契约不能因容器替换改变语义**
  - `src/processor/video_launch/response_for_grg.cpp` 是 GRC 到 GRG 的最终返回出口。
  - 迁移时必须保证：
    - content 顺序不变。
    - attachment key/value 不变。
    - ext_msg / dnn_q 写入条件不变。
    - `_truncated_effect_queue_for_pcs` 截断结果不变。
  - 容器替换不能引入遍历顺序变化，尤其是从 `std::map` 或有序逻辑迁移到 hash 类容器时要特别谨慎。

- **风险 2：不要盲目全量替换 `std::vector`**
  - 两个代码库中 `std::vector` 使用量很大：
    - GRG：1969 次。
    - GRC：8520 次。
  - 但并非所有 vector 都是性能瓶颈。
  - 对静态配置、小规模列表、非主链路调试接口，例如 `service/grc_http_service.cpp` 中的静态颜色数组，迁移收益有限，反而会增加维护成本。

- **风险 3：接口层和虚函数签名迁移成本高**
  - 例如：
    - `model/model.h`
    - `model/paddle_model.h`
  - 这些文件中的 `std::vector<RidTmpInfoPtr>&` 已经成为模型预测接口的一部分。
  - 如果直接替换参数类型，会影响所有实现类、调用方和测试代码。
  - 建议第一阶段只优化函数内部临时容器，接口层保持 `std::vector` 兼容。

- **风险 4：hash 容器替换可能影响内存峰值和调试可读性**
  - 高性能 hash map 通常通过开放寻址、flat storage 或更高装载策略提升性能，但可能改变：
    - 内存占用曲线。
    - rehash 行为。
    - 迭代器失效规则。
    - crash dump 中的数据可读性。
  - 对 `service/grc_http_service.cpp` 这类 graph depend 可视化、debug 相关代码，应优先保证稳定性和可排查性，只有在确认其为高频路径后再迁移。

---

### 5. 结论

- `feeda-mv-grc` 更适合作为目标技术的主要适配代码库，原因是容器使用规模大、主链路中候选聚合和响应组装逻辑重，且已经存在 10 个文件的目标技术使用经验。
- `feeda-mv-grg` 适合做低侵入试点，优先优化模型预测和多样性规则内部的临时容器，不建议一开始修改 `model/model.h` 这类核心接口。
- 推荐迁移顺序：
  - 先参考 `processor/new_adjust/precise_score_init.cpp`、`operator/adjuster/function_queue/youzhi_queue_adjust.cpp` 等已有目标技术使用文件。
  - 再选择 `src/processor/video_launch/response_for_grg.cpp` 和 `src/processor/video_launch/fill_meta_pipeline.cpp` 中的局部热点做 A/B 验证。
  - 最后再评估是否推广到更通用的 graph depend、metadata map、模型特征缓存等场景。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
