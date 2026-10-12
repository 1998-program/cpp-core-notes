# brpc::bvar —— 用户态零竞争计数器：在线服务监控的隐形基石

> 2026-06-27 · C++ 核心模块笔记 · brpc / ng-framework 实战
>
> 关联模块：[34-brpc-butex](34-brpc-butex.md) · [35-brpc-iobuf](35-brpc-iobuf.md) · [37-protobuf-arena](37-protobuf-arena.md)

## 0. 为什么先看 bvar？

线上推荐服务的一个真实场景：

```
ng-framework Predictor 服务，QPS 30w，单机 80 个 bthread worker。
某天凌晨开始 p99 抖动到 80ms（平时 12ms），监控曲线却看不出明显异常。
```

排查到最后，根因是某个业务同学在热点路径上写了：

```cpp
std::atomic<int64_t> g_request_count{0};
// ...
g_request_count.fetch_add(1, std::memory_order_relaxed);  // 每次请求都执行
```

看似无害的 `fetch_add`，在 80 核机器上 30w QPS 下，把这一个 cache line 打成了 **每秒上千万次跨核心 MESI 失效广播**，
直接吃掉 ~6% CPU 并把 L3 带宽搞炸。

bvar 存在的意义就是：**让"统计"这件事在多核机器上不要成为瓶颈本身**。

## 1. 核心思想：把"写"分散，把"读"集中

bvar 用一句话概括：

> **Write to thread-local, read by aggregating across all threads.**

| 维度          | std::atomic 计数器             | bvar::Adder<T>                 |
| ------------- | ------------------------------ | ------------------------------ |
| 写路径        | 全局 cache line CAS / RMW      | 线程私有 slot，**零原子操作**  |
| 读路径        | 单次 load                      | O(线程数) 累加                 |
| 多核扩展性    | 随核数指数级劣化               | **基本线性**                   |
| 适用场景      | 极低频统计                     | 任意热点路径                   |

读路径稍贵——但监控数据是 1s/次拉取，**写路径才是 30w QPS**。这就是 bvar 的核心 trade-off。

## 2. 关键源码切片

```cpp
// brpc/src/bvar/reducer.h（简化）
template <typename T, typename Op>
class Reducer : public Variable {
public:
    void operator<<(T value) {
        agent_type* agent = _combiner.get_or_create_tls_agent();
        // 关键：写入线程私有 agent，无锁、无原子（弱内存序）
        agent->element.modify(_combiner.op(), value);
    }
    T get_value() const {
        T sum = _combiner.reset_all_agents();  // 遍历所有线程聚合
        return sum;
    }
private:
    detail::AgentCombiner<T, T, Op> _combiner;
};

using Adder = Reducer<int64_t, AddTo<int64_t> >;
```

`AgentCombiner` 内部维护一个 **per-thread linked list of agents**，
通过 `pthread_key_create` 的 TLS 机制让每个线程拿到自己的 slot。
线程退出时由 TLS 析构回调把 slot 归还给 combiner，combiner 在读取时遍历所有 agent 累加。

**这正是 jemalloc per-arena、tcmalloc ThreadCache、folly::ThreadLocal 的同一类思想**——
"把竞争的粒度切到 per-thread"。

## 3. bvar 的四种基础原子积木

| 类型           | 语义                          | 典型用法                                 |
| -------------- | ----------------------------- | ---------------------------------------- |
| `Adder<T>`     | 求和（计数器）                | QPS、错误数、字节流量                    |
| `Maxer<T>`     | 求最大值                      | 单 batch 最大耗时、最大 payload          |
| `Miner<T>`     | 求最小值                      | 历史最小响应时间                         |
| `Window<Var>`  | 在另一个 bvar 上加滑动窗口    | "最近 60s 的 QPS"                        |

**Window 是最常用的派生**：

```cpp
bvar::Adder<int64_t> g_request_count;
bvar::Window<bvar::Adder<int64_t> > g_qps("predictor_qps", &g_request_count, 60);
// → /vars 页面自动出现 predictor_qps：最近 60s 的累加，等价于 QPS
```

bvar 后台**单独一个采样线程**每秒 dump 一次累计值，差分得到瞬时速率，
业务线程零参与，零额外开销。

## 4. LatencyRecorder —— bvar 在监控里的杀手锏

直方图统计是在线服务最贵的指标之一，传统做法（atomic 数组 + bucket）在热点路径上同样有 cache line 问题。
bvar 把 LatencyRecorder 做成 6 个 reducer 的组合，**写路径全部走 TLS**：

```cpp
bvar::LatencyRecorder g_predict_latency("predictor_predict");

void Predictor::Predict(...) {
    bvar::LatencyRecorderHelper helper(&g_predict_latency);   // RAII 计时
    // ...real work...
}  // 析构时一次 TLS 写入：count++, sum+=elapsed, max=max(max,elapsed)
```

自动暴露：

```
predictor_predict_count          总请求数
predictor_predict_latency        平均延迟（窗口=10s）
predictor_predict_latency_50    p50
predictor_predict_latency_90    p90
predictor_predict_latency_99    p99
predictor_predict_latency_999   p999
predictor_predict_qps            QPS
predictor_predict_max_latency    窗口最大延迟
```

**百分位数怎么做到零锁？**
bvar 用了 **Tdigest + per-thread 局部直方图**：每个线程写自己的 16k bucket 数组，
读时聚合并近似计算分位。误差 < 1%，但写路径 **零原子操作**。

## 5. 和 brpc / ng-framework 的集成

ng-framework 的在线服务（推荐打分、召回、特征抽取）几乎所有 RPC 入口都长这样：

```cpp
// service 注册时
DECLARE_bvar_latency_recorder(rec_predict);
DECLARE_bvar_adder(rec_predict_error);

void RecPredictService::Predict(
    google::protobuf::RpcController* cntl_base,
    const PredictRequest* req,
    PredictResponse* resp,
    google::protobuf::Closure* done) {
    brpc::ClosureGuard done_guard(done);
    bvar::LatencyRecorderHelper helper(&g_rec_predict);

    if (req->user_id() == 0) {
        g_rec_predict_error << 1;
        cntl_base->SetFailed(EINVAL, "empty user_id");
        return;
    }
    // ... protobuf::Arena 分配响应，bthread::butex 协作同步 ...
}
```

brpc 自带的 `/vars` HTTP 端点会把所有 bvar 实时输出，**直接对接 Noah / Prometheus**：

```
$ curl http://localhost:8080/vars/rec_predict_*
rec_predict_count: 12384719
rec_predict_qps : 304182
rec_predict_latency_99 : 13
rec_predict_max_latency: 87
```

ng-framework 在此之上做了两件事：

1. **统一命名规范**：`{service}_{interface}_{metric}`，让 Noah 自动归类聚合。
2. **Sidecar 拉取间隔放宽到 5s**：单机 bvar 数量过万时，读聚合开销会显现，
   适当放宽抓取频率（写路径不变）以保护核心进程。

## 6. 实战陷阱与最佳实践

### 6.1 ❌ 不要在循环里频繁创建 bvar

```cpp
for (auto& item : items) {
    bvar::Adder<int64_t> tmp;          // ❌ 每次构造都注册到全局表
    tmp << item.size;
}
```

bvar 构造会注册到 `bvar::g_vars` 表（带 mutex），高频构造会拖慢全局。
**正确做法**：声明为 `static` 或文件级全局。

### 6.2 ❌ 不要给 bvar 起包含运行期变量的名字

```cpp
bvar::Adder<int64_t> counter("user_" + user_id);  // ❌ 名字泄露 + 内存爆炸
```

bvar 是为**有限维度**的监控设计的；高基数请用 brpc 内置 trace 或日志，**不要用 bvar**。

### 6.3 ✅ Window 周期要匹配 Noah 拉取周期

ng-framework 的 Noah 默认 30s 拉取，所以 Window 周期 ≥ 30s 才不会丢点。
推荐统一用 `bvar::Window<...> w(name, &raw, 60)` —— 60s 窗口。

### 6.4 ✅ 配合 jemalloc / Arena 看抖动

p99 抖动排查流程：

1. 看 `rec_predict_latency_99` → 抖动确认
2. 看 `rec_predict_qps` → 是否流量异常
3. 看 `process_memory_resident`（bvar 内置）→ 是否 GC/Arena 抖动
4. 看 `bthread_count` / `bthread_worker_usage`（bvar 内置）→ bthread 是否打满
5. 看 jemalloc 的 `stats.allocated` bvar → 是否大对象抖动

bvar **不是孤立的工具**，它是 brpc/ng-framework 可观测性飞轮的中枢。

## 7. 与同类方案对比

| 方案                  | 写开销     | 读开销   | 适用场景                  |
| --------------------- | ---------- | -------- | ------------------------- |
| `std::atomic`         | 高（MESI） | 低       | 极低频                    |
| `prometheus::Counter` | 中（mutex 或 atomic） | 中       | 跨语言指标                |
| **bvar::Adder**       | **≈0**     | O(N核)   | **C++ 高 QPS 在线服务**   |
| `folly::ThreadCachedInt` | ≈0       | O(N核)   | 类似，但生态更通用        |

bvar 的独特价值在于：**和 brpc 深度耦合**——`/vars`/`/status`/`/flags` 一套全自动暴露，
无需自己写 exporter，无需考虑序列化，无需关心生命周期。

## 8. 一句话总结

> **bvar 是 brpc 给 C++ 在线服务的"免费监控"——零锁、零原子、零侵入。**
> 任何一个上线的 brpc 服务，没用满 `LatencyRecorder + Window` 就等于浪费了平台一半价值。

## 参考

- brpc 官方文档：[bvar/intro_cn.md](https://github.com/apache/brpc/blob/master/docs/cn/bvar.md)
- 源码：`brpc/src/bvar/` 目录，重点看 `reducer.h` / `detail/agent.h` / `latency_recorder.h`
- 关联笔记：[34-brpc-butex](34-brpc-butex.md)（同样依赖 TLS+butex 优化）、[24-jemalloc](24-jemalloc.md)（per-arena 同源思想）

---

## 七、业务代码库适配分析
> **分析时间**：2026-06-27T19:04:46.511848
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：brpc::bvar

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 与 `feeda-mv-grc` 两个业务代码库中已经存在一定规模的 bvar 使用痕迹，两个仓库均发现约 10 个相关文件，说明团队并非从零引入，可以优先复用现有封装、命名规范和接入方式进行扩展。其中 `feeda-mv-grg` 中的 `bvar_grc.cc`、`plugin/grc.cpp`、`plugin/predictor.h`，以及 `feeda-mv-grc` 中的 `plugin/user_intent_predictor.cpp`、`plugin/set2set_predictor.h` 等文件可以作为后续改造的参考入口。

- 从业务形态看，`feeda-mv-grg` 偏序列生成与模型打分，`feeda-mv-grc` 偏召回汇聚、HTTP 服务与多插件调用，二者都存在高 QPS、热点 RPC、模型预测、下游调用、召回合并等典型在线服务场景，非常适合用 `bvar::Adder`、`bvar::LatencyRecorder`、`bvar::Window` 对请求量、错误量、耗时分位、下游失败率等指标进行低开销监控。尤其是热点路径中如果仍存在 `std::atomic` 计数、日志式统计或手写耗时聚合，具备较高迁移收益。

---

### 2. 代码库详情

#### feeda-mv-grg

- 已发现 bvar 相关使用：约 10 个文件。
- 代表性文件包括：
  - `plugin/feature_service.h`
  - `plugin/grc.cpp`
  - `bvar_grc.cc`
  - `plugin/predictor.h`
  - `plugin/model_service.cpp`

- 现有代码特征：
  - `model/model.h`、`model/paddle_model.h` 中存在模型预测相关接口，例如：
    - `Model::predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)`
    - `predict_with_tensor_input(...)`
  - 这些函数通常处在推荐链路的核心路径上，适合统计：
    - 单次预测耗时
    - 候选集大小
    - 模型预测失败次数
    - 下游特征服务异常次数
    - batch size 分布与最大值

- 适配判断：
  - 该仓库已经有 `bvar_grc.cc`，说明可能已经存在集中式 bvar 定义或注册文件。
  - 后续建议优先沿用 `bvar_grc.cc` 中已有的命名风格与声明方式，避免每个模块自行创建零散指标。
  - `plugin/grc.cpp`、`plugin/model_service.cpp` 这类服务入口文件，适合作为 `LatencyRecorder` 的第一批改造点。

#### feeda-mv-grc

- 已发现 bvar 相关使用：约 10 个文件。
- 代表性文件包括：
  - `plugin/set2set_predictor.h`
  - `dict/dict_manager.h`
  - `plugin/gcms.h`
  - `plugin/user_intent_predictor.cpp`
  - `plugin/dalton_user_client.h`

- 现有代码特征：
  - `service/grc_http_service.cpp` 中存在 HTTP 服务处理逻辑，并大量使用：
    - `std::unordered_map<std::string, std::vector<int>> depend_map`
    - `std::vector<std::string> sub_access_off_vec`
    - `std::vector<std::string> sub_access_on_vec`
  - 这类文件通常包含请求解析、图依赖遍历、召回开关处理和响应构造逻辑，适合增加：
    - HTTP 请求 QPS
    - 请求失败数
    - 参数非法数
    - 图节点数量、依赖边数量
    - 接口整体耗时与 p99

- 适配判断：
  - `feeda-mv-grc` 的代码规模更大，`std::vector`、`std::string`、`std::unordered_map` 使用分布更广，说明业务逻辑复杂、调用链较长，监控维度也会更丰富。
  - bvar 迁移不应以替换 STL 容器为目标，而应聚焦在热点业务路径中的统计、计数、耗时记录上。
  - `plugin/user_intent_predictor.cpp`、`plugin/dalton_user_client.h`、`plugin/set2set_predictor.h` 这类预测器和下游客户端文件，适合优先补充 `LatencyRecorder` 与错误计数器。

---

### 3. 💡 适用性评估与建议

- **建议一：以 `bvar_grc.cc` 作为 feeda-mv-grg 的统一指标定义入口**
  - 适用文件：
    - `feeda-mv-grg/bvar_grc.cc`
    - `feeda-mv-grg/plugin/grc.cpp`
    - `feeda-mv-grg/plugin/model_service.cpp`
    - `feeda-mv-grg/plugin/predictor.h`
  - 建议做法：
    - 将已有 bvar 定义集中整理到 `bvar_grc.cc`，例如：
      - `grg_predict_latency`
      - `grg_predict_error`
      - `grg_feature_service_latency`
      - `grg_feature_service_error`
      - `grg_candidate_count`
    - 服务入口或核心预测函数中只引用声明，避免在函数内部临时构造 bvar。
  - 示例方向：
    ```cpp
    // bvar_grc.cc
    bvar::LatencyRecorder g_grg_predict_latency("grg_predict");
    bvar::Adder<int64_t> g_grg_predict_error("grg_predict_error");
    bvar::Maxer<int64_t> g_grg_candidate_max("grg_candidate_max");
    ```
    ```cpp
    // plugin/model_service.cpp
    bvar::LatencyRecorderHelper helper(&g_grg_predict_latency);
    if (ret != 0) {
        g_grg_predict_error << 1;
    }
    g_grg_candidate_max << candidate_vec.size();
    ```
  - 预期收益：
    - 减少手写耗时日志和 atomic 计数带来的热点开销。
    - 通过 `/vars` 直接观测预测服务 QPS、平均耗时、p99、错误量。

- **建议二：在 `model/model.h` 与 `model/paddle_model.h` 对模型预测链路增加耗时与候选集指标**
  - 适用文件：
    - `feeda-mv-grg/model/model.h`
    - `feeda-mv-grg/model/paddle_model.h`
  - 适用场景：
    - `Model::predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)`
    - `predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec, ...)`
  - 建议做法：
    - 不建议在纯虚基类 `model/model.h` 中直接埋点，以免污染接口层。
    - 建议在具体实现或统一调用入口中增加 `bvar::LatencyRecorderHelper`。
    - 对 `candidate_vec.size()` 使用 `bvar::Maxer<int64_t>` 或 `bvar::Adder<int64_t>` 配合 `Window` 统计候选规模。
  - 预期收益：
    - 能快速判断 p99 抖动是否来自模型预测阶段。
    - 能关联候选集大小和预测耗时，定位 batch 放大、召回异常等问题。

- **建议三：在 `service/grc_http_service.cpp` 增加 HTTP 入口级 bvar 指标**
  - 适用文件：
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 适用场景：
    - HTTP 请求解析
    - query 参数处理
    - graph 依赖遍历
    - response 构造
  - 建议增加指标：
    - `grc_http_latency`：整体请求耗时
    - `grc_http_qps`：HTTP 请求 QPS，可由 `LatencyRecorder` 自动导出
    - `grc_http_bad_request`：参数错误次数
    - `grc_http_graph_vertex_count`：图节点数量
    - `grc_http_graph_depend_count`：依赖边数量
  - 示例方向：
    ```cpp
    static bvar::LatencyRecorder g_grc_http_latency("grc_http");
    static bvar::Adder<int64_t> g_grc_http_bad_request("grc_http_bad_request");
    static bvar::Maxer<int64_t> g_grc_http_vertex_max("grc_http_vertex_max");

    void GrcHttpService::SomeHandler(...) {
        bvar::LatencyRecorderHelper helper(&g_grc_http_latency);

        const std::string* off = cntl->http_request().uri().GetQuery("off");
        if (invalid_param) {
            g_grc_http_bad_request << 1;
            return;
        }

        auto& all_vertex = graph_engine->get_vertexs_message(graph_name);
        g_grc_http_vertex_max << all_vertex.size();
    }
    ```
  - 预期收益：
    - 快速区分是入口流量异常、参数异常，还是图结构过大导致的耗时上升。
    - 避免通过高频日志统计请求量，降低热点路径 I/O 和锁竞争。

- **建议四：在 `plugin/user_intent_predictor.cpp`、`plugin/set2set_predictor.h` 中补齐预测器级别指标**
  - 适用文件：
    - `feeda-mv-grc/plugin/user_intent_predictor.cpp`
    - `feeda-mv-grc/plugin/set2set_predictor.h`
  - 建议做法：
    - 每个核心 predictor 建议至少包含：
      - 一个 `bvar::LatencyRecorder`
      - 一个错误计数 `bvar::Adder<int64_t>`
      - 一个空结果计数 `bvar::Adder<int64_t>`
      - 一个候选数量或输出数量 `bvar::Maxer<int64_t>` / `bvar::Adder<int64_t>`
  - 指标命名建议：
    - `grc_user_intent_predict_latency`
    - `grc_user_intent_predict_error`
    - `grc_user_intent_empty_result`
    - `grc_set2set_predict_latency`
    - `grc_set2set_predict_error`
  - 预期收益：
    - 召回汇聚服务通常由多个 predictor 组成，入口总耗时只能说明整体变慢，无法定位具体插件。
    - predictor 级别 bvar 可以直接定位是哪一路召回、意图模型或 set2set 模块导致尾延迟升高。

- **建议五：在 `plugin/dalton_user_client.h`、`plugin/gcms.h` 等下游客户端封装中增加调用耗时和失败统计**
  - 适用文件：
    - `feeda-mv-grc/plugin/dalton_user_client.h`
    - `feeda-mv-grc/plugin/gcms.h`
    - `feeda-mv-grg/plugin/feature_service.h`
  - 适用场景：
    - 访问用户画像服务
    - 访问特征服务
    - 访问外部召回或模型服务
  - 建议增加指标：
    - 下游 RPC 耗时：`LatencyRecorder`
    - 超时次数：`Adder<int64_t>`
    - 非零错误码次数：`Adder<int64_t>`
    - 返回空结果次数：`Adder<int64_t>`
  - 预期收益：
    - p99 抖动排查时，可以快速判断是否由下游服务超时、特征缺失或外部依赖异常引起。
    - bvar 写路径为 TLS 累加，适合放在高频下游调用路径中。

---

### 4. ⚠️ 引入风险与限制

- **避免在请求路径中动态创建 bvar**
  - 风险场景：
    - 在 `service/grc_http_service.cpp` 中根据 `graph_name`、`user_id`、`query` 动态拼接 bvar 名称。
    - 在 `plugin/user_intent_predictor.cpp` 中按模型版本、实验分桶、用户分群动态创建指标。
  - 问题：
    - bvar 构造会注册到全局变量表，涉及锁和全局元数据。
    - 动态高基数指标会导致内存膨胀、`/vars` 输出变大、sidecar 拉取变慢。
  - 建议：
    - bvar 名称必须是有限集合。
    - 高基数维度应使用日志、trace 或采样上报，不应使用 bvar。

- **注意 `/vars` 拉取频率与指标数量**
  - 风险场景：
    - `feeda-mv-grc` 模块多、插件多，如果每个插件、每个下游、每个状态都暴露大量 bvar，单机指标数量可能快速膨胀。
  - 问题：
    - bvar 写路径很轻，但读路径需要聚合所有线程的 TLS slot。
    - 当指标数量过万、sidecar 高频拉取时，读聚合开销会对服务进程产生可见影响。
  - 建议：
    - 对业务核心指标优先接入，避免过度细分。
    - Noah / Prometheus 拉取周期建议不低于 5s，窗口类指标建议使用 30s 或 60s。

- **不要把 bvar 当作精确实时计数器使用**
  - 风险场景：
    - 在业务逻辑中读取 `bvar::Adder::get_value()` 作为强一致判断依据。
    - 例如用 bvar 当前值参与限流、熔断、状态机判断。
  - 问题：
    - bvar 的设计目标是监控观测，不是强一致同步原语。
    - 读路径是聚合 TLS 数据，适合秒级监控，不适合纳秒级控制逻辑。
  - 建议：
    - 限流、熔断仍应使用专门的限流器或原子状态。
    - bvar 仅用于观测 QPS、错误量、耗时分布等监控指标。

- **迁移时需统一命名规范，避免监控面板割裂**
  - 风险场景：
    - `feeda-mv-grg` 使用 `grg_predict_latency`，`feeda-mv-grc` 使用 `predict_user_intent_cost`，不同模块命名风格不一致。
  - 问题：
    - Noah / Prometheus 自动聚合困难。
    - 告警规则和 dashboard 难以复用。
  - 建议：
    - 推荐统一采用：
      - `{service}_{module}_{operation}`
      - `{service}_{module}_{operation}_error`
      - `{service}_{module}_{operation}_empty`
      - `{service}_{module}_{operation}_timeout`
    - 示例：
      - `grg_model_predict`
      - `grg_model_predict_error`
      - `grc_user_intent_predict`
      - `grc_dalton_user_rpc_timeout`

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
