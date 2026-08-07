# 2026-08-06 周四代码理解：History Service `gr_state` 双通道状态读写

> 日期：2026-08-06  
> 主题来源：2026-08-06 daily-plan 缺失，按历史未覆盖主题 fallback 到 History Service `gr_state` 双通道状态 I/O  
> 范围：只分析 History Service 侧 `gr_state` 的客户端配置、请求构造、以及 put/get 通道拆分；本文不展开上层业务语义。  
> 内网文档：当前环境未提供可用 KU 文档 URL/doc-id，需人工补充。

---

## 0. 架构全景图

<div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f8fafc;border:1px solid #d9e2ec;border-radius:12px;padding:16px;margin:16px 0;color:#1f2937"><style>.arch-wrap{display:grid;grid-template-columns:1.1fr 1.2fr 1fr;gap:12px}.arch-col{background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:12px;min-height:150px}.arch-col h3{margin:0 0 8px;font-size:14px;color:#102a43}.arch-box{background:#f1f5f9;border:1px solid #cbd5e1;border-radius:8px;padding:8px 10px;margin:8px 0;font-size:13px;line-height:1.45}.arch-box strong{display:block;color:#102a43;margin-bottom:2px}.arch-arrow{color:#64748b;font-size:12px;text-align:center;margin:4px 0}.arch-note{font-size:12px;color:#52606d;line-height:1.5;margin-top:8px}</style><div class="arch-wrap"><div class="arch-col"><h3>入口与配置</h3><div class="arch-box"><strong>HistoryServiceDao::gen_request</strong><span>从 node 配置读取 `channel`，映射到运行时 channel 对象。</span></div><div class="arch-box"><strong>HistoryServiceIO</strong><span>承载 request / channel / logid / emerger 等上下文。</span></div><div class="arch-note">请求侧只负责把场景参数装配进 I/O 对象，再交给后续 RPC/IO 层。</div></div><div class="arch-col"><h3>双通道</h3><div class="arch-box"><strong>put 通道</strong><span>写入状态，配置在 `conf/common_component/dalton_client/sndb_gr_state_client.conf.template` 的 `[.put]`。</span></div><div class="arch-box"><strong>get 通道</strong><span>读取状态，配置在同一模板的 `[.get]`，与写路径分开调优。</span></div><div class="arch-arrow">write path / read path</div><div class="arch-note">`put` 和 `get` 分别设置 timeout、retry、backup request，避免一组参数同时约束两种延迟模型。</div></div><div class="arch-col"><h3>状态存储</h3><div class="arch-box"><strong>SNDB `shoubai_gr_layout_hs`</strong><span>表名由 `@table_name` 固定，client 初始化依赖这一约束。</span></div><div class="arch-box"><strong>state key</strong><span>业务状态通过 `ShowStateInfo` / `CardShowStateInfo` 透传到读写流程。</span></div><div class="arch-note">这层不解释“为什么这样下发”，只保证“写得进去、读得回来、配置可控”。</div></div></div></div>

## 1. 核心流程图

```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Caller
participant "HistoryServiceDao" as Dao
participant "HistoryServiceIO" as IO
participant "SNDB gr_state client" as Sndb
participant "History Service state table" as Table
Caller -> Dao : gen_request()
Dao -> IO : set_channel()
Dao -> IO : set_logid()
Dao -> IO : set_emerger(false)
Dao -> IO : get_request()
Dao -> IO : fill HSRequest
Dao -> Sndb : put/get by route
Sndb -> Table : write_state / read_state
Table --> Sndb : result payload
Sndb --> IO : response
IO --> Dao : transform_response()
@enduml
```

## 2. 配置结构信息图

infographic sequence-ascending-steps
 data
  title `gr_state` 客户端拆分
  desc 写入与读取分离配置，避免把同一组重试/超时参数绑死在同一条链路上
  items
    - label 1. 表名锚定
      desc `@table_name: shoubai_gr_layout_hs`，client 初始化依赖表名正确。
      icon mdi/database
    - label 2. 写通道参数
      desc `[.put]` 使用 `timeout_ms: 300`、`max_retry: 3`、`backup_request_ms: 1000`。
      icon mdi/upload
    - label 3. 读通道参数
      desc `[.get]` 使用 `timeout_ms: 2000`、`max_retry: 3`、`backup_request_ms: 1000`。
      icon mdi/download
    - label 4. 本地测试模板
      desc `sndb_gr_state_client.conf` 仅保留 `127.0.0.1:8094` 的本地连接样例。
      icon mdi/flask-outline
 theme
  palette #3d5a80 #64748b #2d6a4f

## 3. 关键结论

<div style="display:grid;grid-template-columns:1.2fr 1fr;gap:12px;margin:16px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-left:4px solid #3d5a80;border-radius:10px;padding:14px"><div style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:#486581;margin-bottom:6px">结论</div><div style="font-size:14px;line-height:1.7;color:#1f2937">`HistoryServiceDao` 的职责很窄：把 node 配置、会话上下文和 `HistoryServiceIO` 组装成一次可路由请求。真正的状态存取细节被下沉到 `gr_state` 的 SNDB 客户端配置，写和读由不同参数面控制。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-left:4px solid #2d6a4f;border-radius:10px;padding:14px"><div style="font-size:12px;font-weight:700;letter-spacing:0.04em;color:#486581;margin-bottom:6px">边界</div><div style="font-size:14px;line-height:1.7;color:#1f2937">这篇只看存储与 I/O，不解释 `NewsHistory` 里的状态语义，也不推导前端展现策略。</div></div></div>

## 4. Pitfalls

<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0"><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px"><div style="font-size:13px;font-weight:700;color:#102a43;margin-bottom:6px">配置陷阱</div><div style="font-size:14px;line-height:1.7;color:#243b53">`@table_name` 错了会直接让 client 初始化失败；如果把读写参数混成一组，超时和 backup_request 的含义会互相污染。</div></div><div style="background:#fff;border:1px solid #d9e2ec;border-radius:10px;padding:14px"><div style="font-size:13px;font-weight:700;color:#102a43;margin-bottom:6px">请求陷阱</div><div style="font-size:14px;line-height:1.7;color:#243b53">`HistoryServiceDao::gen_request` 依赖 `channel` 节点配置存在且可解析；缺失时直接返回错误，后续链路不会补救。</div></div></div>

## 5. 调试 Checklist

infographic list-column-done-list
 data
  title `gr_state` 链路检查项
  items
    - label 确认 channel 配置
      desc 检查 node 配置里 `channel` 是否存在且能映射到运行时 channel。
      done true
    - label 确认表名
      desc 校验 `@table_name: shoubai_gr_layout_hs` 是否与线上表一致。
      done true
    - label 区分 put/get 参数
      desc 读写超时、重试、backup_request 不要混用同一组假设。
      done true
    - label 本地模板验证
      desc 先看 `sndb_gr_state_client.conf`，再看 `.template` 是否一致。
      done true

## 证据来源

- `gr/dao/history_service_dao.cc:23-53`
- `history-service/conf/common_component/dalton_client/sndb_gr_state_client.conf.template:1-29`
- `history-service/conf/common_component/dalton_client/sndb_gr_state_client.conf:1-20`
- `gr/dao/history_service_dao.cc:104-214`

---

## 七、业务代码库适配分析
> **分析时间**：2026-08-07T19:02:57.654393
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`gr_state` / History Service 双通道状态读写能力在两个业务代码库中的直接使用规模并不大：`feeda-mv-grg` 仅发现 1 个相关文件，`feeda-mv-grc` 发现 10 个相关文件。说明该能力当前更偏向局部业务场景使用，而不是全局基础设施级依赖。

- 但两个代码库都存在大量状态组织、候选集处理、召回/排序/过滤链路代码，尤其是 `feeda-mv-grc` 中 `std::vector`、`std::string`、`std::unordered_map` 使用规模很高，说明业务侧有较多“临时状态构造、上下文传递、结果缓存、过滤标记”的需求。若这些状态需要跨请求、跨服务或跨阶段复用，`gr_state` 的 put/get 双通道模型具备一定迁移潜力，尤其适合将“读路径”和“写路径”的超时、重试、backup request 参数拆开调优。

---

### 2. 代码库详情

#### feeda-mv-grg

- 已发现目标能力相关使用：1 个文件
  - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`

- 当前代码库中已有大量标准容器使用：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型业务形态：
  - `model/model.h`
    ```cpp
    class Model {
    public:
        virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    };
    ```
  - `model/paddle_model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) {
        return 0;
    }
    ```
  - `model/paddle_model.h`
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```

- 适配观察：
  - `feeda-mv-grg` 更像序列生成服务，核心链路围绕候选集 `candidate_vec`、模型预测、规则打散/多样性控制展开。
  - 当前发现的目标能力使用点集中在 `strategy/diversity/rule/low_clarity_diversity_rule.cpp`，可作为后续引入 `gr_state` 状态读写的优先参考点。
  - 如果低清晰度、多样性、候选 item 展现状态需要跨请求记忆，`gr_state` 的 read/write 拆分配置可以避免将生成阶段的写状态开销直接耦合到实时预测链路。

#### feeda-mv-grc

- 已发现目标能力相关使用：10 个文件，扫描样例包括：
  - `operator/adjuster/sketchy/duanju_adjuster.cpp`
  - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - `processor/filter/low_agile_goodrate_filter_operator.cc`
  - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - `processor/new_adjust/precise_score_init.cpp`

- 当前代码库中标准容器使用规模更大：
  - `std::vector`：8520 次，分布在 1290 个文件
  - `std::string`：7267 次，分布在 1247 个文件
  - `std::unordered_map`：2860 次，分布在 646 个文件

- 典型业务形态：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
  - `service/grc_http_service.cpp`
    ```cpp
    static std::vector<std::string> colors{"#FFB6C1", "#DC143C", "#DB7093", "#FF1493", "#FF00FF", "#800080",
                                           "#4B0082", "#7B68EE", "#0000FF", "#4169E1", "#778899", "#4682B4",
    ```
  - `service/grc_http_service.cpp`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    ```

- 适配观察：
  - `feeda-mv-grc` 是召回汇聚服务，链路更复杂，状态使用场景更多，适合将部分用户级、item 级、过滤级状态下沉到统一状态服务。
  - 已发现多个过滤、调权、打分初始化相关文件使用目标能力或处于可适配范围，说明该代码库比 `feeda-mv-grg` 更适合优先做 `gr_state` 双通道状态读写标准化。
  - `processor/filter/*`、`operator/adjuster/*`、`processor/new_adjust/*` 是优先排查目录。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先以已有使用点作为参考样板，统一 `gr_state` 调用封装**
  - 参考文件：
    - `feeda-mv-grg/strategy/diversity/rule/low_clarity_diversity_rule.cpp`
    - `feeda-mv-grc/operator/adjuster/sketchy/duanju_adjuster.cpp`
    - `feeda-mv-grc/processor/filter/low_agile_goodrate_filter_operator.cc`
  - 建议做法：
    - 梳理这些文件中是否已经直接或间接依赖 History Service / `gr_state`。
    - 如果存在分散的状态读写逻辑，建议抽出统一的状态访问 helper，例如：
      - `GrStateReader`
      - `GrStateWriter`
      - `HistoryStateAccessor`
    - 业务代码只表达“读什么状态 / 写什么状态”，不要在过滤器、adjuster、factor generator 中硬编码 channel、timeout、retry 等配置细节。
  - 收益：
    - 降低业务模块对 SNDB client 配置细节的感知。
    - 后续切换表名、调整 put/get 参数时，不需要大面积修改业务逻辑。

- **建议 2：在 `feeda-mv-grc` 的过滤类算子中优先评估 read path 接入**
  - 候选文件：
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
    - `processor/filter/user_explore_interest_ugc_filter_operator.cc`
  - 适用场景：
    - 用户历史探索兴趣
    - 内容低质/低敏捷状态
    - item 是否近期已曝光、已过滤、已触发某类策略
  - 建议做法：
    - 将过滤前需要依赖的用户级或 item 级状态统一通过 `get` 通道读取。
    - 读路径建议使用独立参数，不要复用写路径配置。
    - 对实时过滤链路，重点关注 `timeout_ms` 和降级策略，避免状态服务抖动拖慢主链路。
  - 对应技术笔记中的配置原则：
    - `[.get]` 独立配置，例如较大的 `timeout_ms`、独立 `max_retry`。
    - 读失败时业务侧应明确是“不过滤”“保守过滤”还是“走默认特征”。

- **建议 3：在 `feeda-mv-grc` 的调权和打分初始化阶段评估 write path 接入**
  - 候选文件：
    - `operator/adjuster/sketchy/duanju_adjuster.cpp`
    - `processor/new_adjust/precise_score_init.cpp`
    - `processor/multi_factor/ltr_factor_gen_scene.cpp`
  - 适用场景：
    - 记录本次召回或排序阶段生成的状态
    - 记录用户对某类内容的短期状态
    - 记录某些调权策略命中的中间结果，供后续请求或后续阶段读取
  - 建议做法：
    - 写状态统一走 `put` 通道。
    - 对写失败进行弱依赖处理：通常不建议阻断主召回/排序流程。
    - 对高 QPS 写入场景，需要评估批量写、异步写或采样写，避免把状态写入变成主链路瓶颈。
  - 对应技术笔记中的配置原则：
    - `[.put]` 独立配置，例如更短的 `timeout_ms`。
    - 写路径和读路径不要共用一套超时/backup request 策略。

- **建议 4：`feeda-mv-grg` 可从多样性规则场景小范围试点**
  - 候选文件：
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 适用场景：
    - 低清晰度内容是否近期已被压制
    - 用户维度的多样性控制状态
    - item 或 category 的短期展示状态
  - 建议做法：
    - 不建议一开始在 `model/model.h`、`model/paddle_model.h` 这类模型预测基础接口中引入远程状态读写。
    - 更适合在规则层、策略层做试点，因为这些位置业务语义更明确，失败降级也更容易控制。
  - 原因：
    - 模型预测接口调用频率高、延迟敏感。
    - 在预测接口中直接引入远程 get/put 容易造成尾延迟放大。

- **建议 5：对 `service/grc_http_service.cpp` 这类管理/调试入口保持谨慎，不建议作为首批迁移主战场**
  - 文件：
    - `service/grc_http_service.cpp`
  - 当前观察：
    - 该文件存在大量 `std::unordered_map<std::string, std::vector<int>>`、`std::vector<std::string>`、`std::string` 拼接和 HTTP query 解析逻辑。
  - 建议做法：
    - 如果这里只是图结构展示、调试开关、颜色配置、HTTP 参数解析，不建议引入 `gr_state`。
    - 若后续需要展示用户状态、策略命中状态或调试状态查询，可以封装只读接口调用 `get` 通道。
  - 原则：
    - 管理面、调试面代码可以读取状态，但不要轻易写入线上状态表，避免调试请求污染业务状态。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：远程状态读写可能放大主链路尾延迟**
  - `feeda-mv-grc` 的过滤、召回、调权链路通常处于在线请求路径。
  - 如果在 `processor/filter/low_agile_goodrate_filter_operator.cc`、`processor/filter/user_explore_interest_ugc_filter_operator.cc` 中同步读取状态，需要明确超时和降级策略。
  - 建议：
    - 读失败要有默认行为。
    - 不要在 item 级循环中逐条远程 get。
    - 优先批量读、缓存读或请求级复用。

- **风险 2：put/get 配置混用会导致读写路径互相污染**
  - 技术笔记中已经明确 `gr_state` 使用双通道：
    - `[.put]`：写状态
    - `[.get]`：读状态
  - 迁移时不要为了方便在业务侧复用同一个 channel。
  - 写路径更关注不阻塞主流程，读路径更关注可用性和准确性，两者的 timeout、retry、backup request 不应强行一致。

- **风险 3：状态语义需要先收敛，否则容易形成“隐式业务依赖”**
  - 例如在 `operator/adjuster/sketchy/duanju_adjuster.cpp` 写入的状态，如果又被 `processor/filter/user_explore_interest_ugc_filter_operator.cc` 读取，需要明确：
    - key 的组成
    - value 的版本
    - TTL 或过期语义
    - 写失败后的读取行为
    - 老版本状态兼容策略
  - 否则后续排查问题时，很难判断是策略问题、状态缺失问题还是状态版本不兼容问题。

- **风险 4：不要把标准容器的大规模使用简单等同于可迁移收益**
  - 两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 使用非常多，但它们大部分只是本地数据结构，不一定需要迁移到远程状态服务。
  - 迁移判断标准应是：
    - 是否需要跨请求复用？
    - 是否需要跨服务共享？
    - 是否需要持久化或短期记忆？
    - 是否能接受远程 I/O 延迟？
  - 对 `model/model.h`、`model/paddle_model.h` 这类核心预测接口，应优先保持纯计算和内存数据访问，不建议直接耦合 `gr_state`。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
