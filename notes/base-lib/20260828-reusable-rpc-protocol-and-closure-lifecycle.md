# 2026-08-28 周五代码理解：ReusableRPCProtocol 与响应 Closure 生命周期

> 日期：2026-08-28  
> 主题来源：`notes/weekly-topic-selection/daily-plan-20260529.json` 的历史候选项；本次没有独立的当日 daily-plan 可直接读取，KU/业务背景需人工补充。  
> 范围：只看 `src/main.cpp` 的服务启动、`src/service/grc_service.cpp` 的 `GenericGRCService::query()` / `run()` / `print_log()`，以及 `ReusableRPCProtocol`、`closure_done`、`graph->reset()` 这一条请求闭环。

---

## 0. 架构全景图
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;border:1px solid #d0d7de;border-radius:8px;padding:14px;background:#f8fafc;line-height:1.45;">
  <div style="display:grid;grid-template-columns:1.08fr 1.2fr 1fr;gap:12px;align-items:stretch;">
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">启动装配</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`src/main.cpp:73-136`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">注册 reusable protocol、服务、插件和线程配置，给后续请求闭环提供运行时外壳。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">请求闭环</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`query()` → `try_get()` → `run()` → `send_response()`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">核心不是“跑完就结束”，而是要让 response、graph 和 done 的生命周期对齐。</div>
    </div>
    <div style="background:#ffffff;border:1px solid #cbd5e1;border-radius:8px;padding:12px;">
      <div style="font-size:12px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:.04em;">回收边界</div>
      <div style="margin-top:8px;font-size:14px;color:#1f2937;">`graph->reset()` + `closure_done->release()`</div>
      <div style="margin-top:8px;font-size:12px;color:#475569;">复用成立的前提是每次请求结束时都能把图对象和协议对象归位。</div>
    </div>
  </div>
  <div style="margin-top:12px;display:grid;grid-template-columns:1fr 80px 1fr 80px 1fr 80px 1fr;gap:10px;align-items:center;">
    <div style="background:#eef6ff;border:1px solid #bfdbfe;border-radius:8px;padding:10px;text-align:center;color:#1d4ed8;">main.cpp 装配</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:8px;padding:10px;text-align:center;color:#0f766e;">brpc 服务入口</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#fef9c3;border:1px solid #fde68a;border-radius:8px;padding:10px;text-align:center;color:#a16207;">graph run</div>
    <div style="text-align:center;color:#64748b;font-weight:700;">→</div>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:10px;text-align:center;color:#166534;">response / reset</div>
  </div>
</div>

## 1. 核心流程
```plantuml
@startuml
skinparam handwritten false
skinparam backgroundColor #f8fafc
left to right direction
actor Client
participant "main.cpp" as M
participant "GenericGRCService::query()" as Q
participant "GraphEngine" as E
participant "Graph" as G
participant "ReusableRPCProtocol::Closure" as C
Client -> M : start server
M -> M : register protocol / services
Client -> Q : RPC request
Q -> E : try_get(graph_name)
E --> Q : pooled graph
Q -> G : emit request data
Q -> G : run(end)
G --> Q : closure
Q -> Q : copy response if needed
Q -> C : send_response()
Q -> G : reset()
Q -> C : release()
Q --> Client : response
@enduml
```

## 2. 启动与请求闭环拆解
```infographic
sequence-ascending-steps
  data
    title ReusableRPCProtocol 请求闭环
    desc 启动阶段先完成协议和服务装配，运行阶段再把响应、图对象和 closure 串起来
    items
      - label 协议注册
        desc `ReusableRPCProtocol::register_protocol()` 在 `src/main.cpp:84-88` 完成
      - label 服务装配
        desc brpc server 挂载 `GenericGRCService` 和 HTTP service
      - label 图对象获取
        desc `GraphEngine::try_get(graph_name)` 取出可复用图实例
      - label 响应发送
        desc `closure_done->send_response()` 先把结果发出去，再释放协议闭包
      - label 状态归位
        desc `graph->reset()` 清空图态，`closure_done->release()` 释放请求态
```

## 3. 关键判断
- `main.cpp` 不是业务逻辑本身，但它决定了 reusable protocol、服务、线程和插件是否以可复用方式启动。
- `query()` 里真正重要的是把 `GraphPool::PooledObject`、`GRCSessionContext` 和 `done` 绑成一个短生命周期单元。
- `run()` 里先 `send_response()` 再 `closure.wait()`，说明发送链路和图执行链路是分开的，不能把它们当成一个同步函数看。
- `graph->reset()` 必须发生在请求末尾，否则下一次 `try_get()` 拿到的就是带脏状态的图实例。

## 4. 架构卡片
<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ffffff;border:1px solid #d0d7de;border-left:4px solid #2563eb;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#2563eb;text-transform:uppercase;letter-spacing:.04em;">关键结论</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">这条链路的核心约束不是 graph 能不能跑，而是 response 什么时候出、graph 什么时候归、done 什么时候释放。三个对象的生命周期只要错开一点，复用就会把问题放大。</div>
</div>

<div style="height:10px"></div>

<div style="font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#fff7ed;border:1px solid #fed7aa;border-left:4px solid #f97316;border-radius:8px;padding:14px;">
  <div style="font-size:12px;font-weight:700;color:#c2410c;text-transform:uppercase;letter-spacing:.04em;">调试提示</div>
  <div style="margin-top:8px;font-size:14px;line-height:1.65;color:#1f2937;">如果客户端已经收到响应但后续状态异常，优先查 `send_response()` 前后的对象归位顺序，再看 `graph->reset()` 是否覆盖了所有临时数据，而不是先怀疑 brpc 本身。</div>
</div>

## 5. Pitfalls
- 只看 `ReusableRPCProtocol::register_protocol()` 成功，会误以为整条链路已经具备复用条件，实际还要看 `closure_done` 的回收时机。
- `graph->reset()` 放得太早，会让后续日志、监控或响应拷贝拿到被清理的状态。
- `closure_done->send_response()` 和 `closure_done->release()` 之间不能插入会修改响应对象生命周期的额外逻辑。
- `graph_name` 的分支映射如果和 `run()` 里的 end data 不一致，复用再好也会把错误图跑进来。

## 6. 调试 Checklist
```infographic
list-column-done-list
  data
    title ReusableRPCProtocol 调试清单
    items
      - label 检查 protocol 注册
        desc 确认 `src/main.cpp:84-88` 没有失败
        done true
      - label 检查服务装配
        desc 确认 `GenericGRCService` 和 HTTP service 都被挂到 server
        done true
      - label 检查 graph 归还
        desc 确认 `graph->reset()` 在请求末尾执行
        done true
      - label 检查 closure 生命周期
        desc 确认 `send_response()` 后再 `release()`，没有提前释放
        done true
```

## 7. 证据来源
- `notes/weekly-topic-selection/daily-plan-20260529.json`
- `src/main.cpp:73-136`
- `src/service/grc_service.cpp:151-220`
- `src/service/grc_service.cpp:226-455`
- `src/service/grc_service.cpp:457-563`

> 说明：当前运行环境没有独立的当日 daily-plan，可用历史候选项回退；KU/业务背景需人工补充。
---

## 七、业务代码库适配分析
> **分析时间**：2026-08-28T19:01:44.624776
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grc` 与本次笔记主题 **ReusableRPCProtocol / response closure 生命周期 / graph reset 复用闭环** 的相关性更高。该代码库本身就是召回汇聚服务，扫描中也出现了 `service/grc_http_service.cpp`，并且技术笔记的核心代码路径位于 `src/service/grc_service.cpp`，说明它具备直接落地和验证该请求闭环优化的业务场景。

- `feeda-mv-grg` 当前只发现 1 个目标库相关使用文件，主要业务形态偏序列生成和模型预测，扫描样例集中在 `model/model.h`、`model/paddle_model.h` 等模型接口层。它对 ReusableRPCProtocol 的直接适配空间相对有限，但由于仓库内 `std::vector`、`std::string`、`std::unordered_map` 使用规模很大，仍然具备在热点请求路径、候选集处理、模型输入构造等场景进行对象复用、减少临时分配的优化潜力。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- **目标库使用情况**
  - 已发现目标库使用：1 个文件
    - `strategy/diversity/rule/low_clarity_diversity_rule.cpp`
  - 说明该仓库中已有少量相关技术或依赖使用经验，但覆盖面较窄，尚未形成通用模式。

- **std 等价物使用规模**
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- **典型代码位置**
  - `model/model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
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

- **适配判断**
  - `feeda-mv-grg` 当前没有直接暴露类似 `GenericGRCService::query()` → `graph->run()` → `send_response()` → `graph->reset()` 的服务闭环代码。
  - 其优化重点更可能在：
    - 候选集 `candidate_vec` 的生命周期控制；
    - 模型预测输入对象复用；
    - diversity rule 中临时容器和中间结果复用；
    - 避免在单次请求内频繁构造、拷贝、释放 `std::vector` / `std::string` / `std::unordered_map`。

#### feeda-mv-grc：召回汇聚服务

- **目标库使用情况**
  - 已发现目标库使用：10 个文件，扫描样例包括：
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
    - `processor/new_adjust/precise_score_init_first_refresh.cpp`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`
    - `processor/new_adjust/precise_score_init.cpp`

- **std 等价物使用规模**
  - `std::vector`：8520 次，分布在 1290 个文件
  - `std::string`：7267 次，分布在 1247 个文件
  - `std::unordered_map`：2860 次，分布在 646 个文件

- **典型代码位置**
  - `service/grc_http_service.cpp:62`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    ```
  - `service/grc_http_service.cpp:81`
    ```cpp
    static std::vector<std::string> colors{"#FFB6C1", "#DC143C", "#DB7093", ...};
    ```
  - `service/grc_http_service.cpp:152`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    ```

- **适配判断**
  - `feeda-mv-grc` 是本次技术主题的优先适配对象。
  - 该仓库既有服务入口、HTTP 管理接口、GraphEngine、processor/operator 等模块，也存在大量容器和字符串操作，迁移收益主要来自：
    - RPC 响应对象生命周期明确化；
    - graph 实例复用后的脏状态清理；
    - 请求级临时对象归位；
    - 日志、监控、响应构造与 `graph->reset()` 的顺序治理。

---

### 3. 💡 适用性评估与建议

- **优先在 `src/service/grc_service.cpp` 固化请求闭环模板**
  - 适用场景：
    - `GenericGRCService::query()`
    - `GenericGRCService::run()`
    - `GenericGRCService::print_log()`
  - 建议将请求结束阶段整理成明确顺序：
    - 响应对象构造完成；
    - `closure_done->send_response()`；
    - 等待必要的异步闭包或统计逻辑完成；
    - `graph->reset()`；
    - `closure_done->release()`。
  - 重点是不要让业务逻辑在 `send_response()` 与 `release()` 之间继续修改 response 依赖的数据对象。
  - 该文件本身就是本次技术笔记的核心参考实现，后续新增 RPC 方法时应优先复用这一生命周期模型。

- **在 `service/grc_http_service.cpp` 中区分“管理接口临时对象”和“请求闭环对象”**
  - 该文件中存在较多 `std::unordered_map<std::string, std::vector<int>>`、`std::vector<std::string>`、`std::string resp_str` 等临时结构。
  - 对于 HTTP 管理接口，例如 graph 可视化、依赖关系查询、开关参数解析等，建议先保持普通局部对象，不要过早引入请求级复用。
  - 但如果某些 HTTP 接口被高频调用，例如依赖拓扑查询、运行时调试接口，可以考虑：
    - 对 `depend_map` 做容量预估，提前 `reserve()`；
    - 对 `resp_str` 做 `reserve()`，减少字符串扩容；
    - 对 `sub_access_off_vec` / `sub_access_on_vec` 做请求内复用封装。
  - 注意这类接口通常不是主链路，不建议直接套用 `ReusableRPCProtocol::Closure` 的释放模型。

- **在 `processor/filter/low_agile_goodrate_filter_operator.cc` 等 processor 热点模块中排查请求态残留**
  - 已扫描到该文件使用目标库，可作为 `feeda-mv-grc` 中已有适配经验的参考点。
  - processor/operator 往往会被 graph 节点复用，如果内部保存了请求相关缓存，需要检查：
    - 是否在每次请求结束后清空；
    - 是否依赖 `graph->reset()` 间接清理；
    - 是否存在 static/local cache 引入跨请求污染。
  - 建议对以下文件做同类排查：
    - `processor/filter/low_agile_goodrate_filter_operator.cc`
    - `processor/multi_factor/session_ltr_dibar_factor_gen.cpp`
    - `processor/new_adjust/precise_score_init.cpp`
    - `processor/new_adjust/precise_score_init_first_refresh.cpp`
    - `operator/adjuster/function_queue/youzhi_queue_adjust.cpp`

- **在 `feeda-mv-grg` 的 `model/model.h` 与 `model/paddle_model.h` 中优先做容器复用和拷贝消除**
  - 该仓库没有明显的 ReusableRPCProtocol 请求闭环落点，但模型预测接口大量传递：
    ```cpp
    std::vector<RidTmpInfoPtr>& candidate_vec
    ```
  - 建议重点检查：
    - `candidate_vec` 是否在调用链中发生不必要的复制；
    - 预测前后是否会构造临时 `std::vector`；
    - `PredictSample` 是否可以请求级复用；
    - tensor 输入构造是否存在反复申请内存。
  - 对 `model/paddle_model.h` 的 `predict_with_tensor_input()` 场景，建议优先做 profile，确认热点后再引入对象池或请求上下文复用，避免为了复用增加复杂度。

- **以 `strategy/diversity/rule/low_clarity_diversity_rule.cpp` 作为 feeda-mv-grg 的迁移参考样本**
  - 这是 `feeda-mv-grg` 中扫描到的唯一目标库使用文件。
  - 建议先分析该文件的使用模式：
    - 是仅使用工具类，还是已经涉及对象生命周期复用；
    - 是否位于主请求链路；
    - 是否对候选集排序、过滤、打散产生临时容器。
  - 如果该文件已经稳定使用相关库或复用模式，可以将其作为 grg 内部推广样例；如果只是偶发使用，则不建议直接全仓推广。

---

### 4. ⚠️ 引入风险与限制

- **`graph->reset()` 顺序错误会放大复用风险**
  - 如果 reset 太早，日志、监控、响应拷贝可能读到被清空的数据。
  - 如果 reset 太晚，下一次 `GraphEngine::try_get(graph_name)` 可能拿到带脏状态的 graph。
  - 建议在 `src/service/grc_service.cpp` 中明确约定：所有依赖 graph 状态的响应构造和日志采集完成后，才能执行 `graph->reset()`。

- **`closure_done->send_response()` 与 `closure_done->release()` 之间不能插入复杂业务逻辑**
  - `send_response()` 之后，响应链路已经开始推进。
  - 如果中间继续修改 response、session context 或 graph 中的数据，容易产生时序问题。
  - 建议将这一区间控制为最小，只保留必要的状态归位和资源释放。

- **processor/operator 内部状态可能与 graph 复用产生冲突**
  - `processor/filter/low_agile_goodrate_filter_operator.cc`、`processor/new_adjust/precise_score_init.cpp` 等模块如果持有请求级状态，必须确认其 reset 边界。
  - 不建议默认认为 `graph->reset()` 会覆盖所有 processor 内部缓存。
  - 对有成员变量缓存、静态变量、线程局部变量的 operator，需要逐个检查。

- **大规模替换 std 容器不一定直接带来收益**
  - 两个仓库中 `std::vector`、`std::string`、`std::unordered_map` 使用规模都很大，但不代表都需要迁移。
  - 应优先关注：
    - 主请求链路；
    - 高频 processor；
    - 大候选集处理；
    - response 构造；
    - graph 节点间数据传递。
  - 对低频管理接口、初始化代码、调试页面，例如 `service/grc_http_service.cpp` 中部分 HTTP 查询逻辑，应以可读性和稳定性优先。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
