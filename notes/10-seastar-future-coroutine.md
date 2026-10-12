# 10 · seastar::future — 用户态协程调度

> 源仓库：[scylladb/seastar](https://github.com/scylladb/seastar)  
> 核心方向：用户态 Cooperative 协程调度 · 无锁线程模型 · C++20 协程集成

---

## 模块概述

Seastar 是 ScyllaDB 和 Redpanda 底层使用的高性能异步 C++ 框架，其核心抽象 `seastar::future<T>` 代表一个尚未完成的计算结果。与 OS 线程调度不同，Seastar 完全运行在用户态，采用 **Shared-Nothing** 架构：每个 CPU 核绑定一个线程，线程内部通过协程切换实现并发，彻底消除了锁竞争。

### 与 std::future 的关键区别

| 特性 | `std::future` | `seastar::future` |
|------|-------------|-------------------|
| 阻塞语义 | `.get()` 阻塞线程 | 永不阻塞，通过 continuation 传递 |
| 调度控制 | OS 内核调度 | 用户态事件循环 |
| 跨线程 | 默认支持 | 明确禁止（Shared-Nothing） |
| 性能 | 受 syscall/切换开销影响 | 极低延迟，可达 ns 级 |

---

## 设计思想：Shared-Nothing + Continuation-Passing

```
CPU 0          CPU 1          CPU 2          CPU 3
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ reactor  │  │ reactor  │  │ reactor  │  │ reactor  │
│ task_q   │  │ task_q   │  │ task_q   │  │ task_q   │
│ io_q     │  │ io_q     │  │ io_q     │  │ io_q     │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │ poll epoll  │              │              │
     └─────────────┴──────────────┴──────────────┘
              无锁，无共享状态，无 mutex
```

每个 shard 独立运行 `reactor` 事件循环：
1. `epoll_wait` / `io_uring` 等待 I/O 就绪
2. 将就绪事件转换为 `task`，推入本地 `task_queue`
3. 依次执行 task（每个 task 是一个协程帧或 continuation）
4. 回到步骤 1

---

## 核心代码实现

### 1. future / promise 基础结构

```cpp
// seastar/include/seastar/core/future.hh（简化）
template <typename T>
class future {
    internal::future_state<T>* _state;   // 指向共享状态

public:
    // 注册 continuation，future 完成时自动调用
    template <typename Func>
    auto then(Func&& func) && -> future<std::invoke_result_t<Func, T>>;

    // C++20 coroutine 支持
    bool await_ready() const noexcept;
    void await_suspend(std::coroutine_handle<> h) noexcept;
    T    await_resume();
};

template <typename T>
class promise {
    internal::future_state<T>* _state;

public:
    future<T> get_future();
    void set_value(T&&);
    void set_exception(std::exception_ptr);
};
```

### 2. future_state：zero-copy 状态机

```cpp
// future_state 使用 union 避免额外堆分配
template <typename T>
class future_state {
    enum class state { invalid, future, result, exception };
    state _st = state::future;

    union {
        T               _value;
        std::exception_ptr _ex;
        task*           _continuation;  // 等待此 future 的协程
    };

    void make_ready(T&& val) noexcept {
        if (_st == state::future && _continuation) {
            // 直接唤醒等待的协程，无锁
            engine().add_task(_continuation);
        }
        _st = state::result;
        new (&_value) T(std::move(val));
    }
};
```

### 3. C++20 coroutine 集成

```cpp
// Seastar coroutine 使用示例
#include <seastar/core/coroutine.hh>

seastar::future<std::string> fetch_data(std::string url) {
    // co_await 让出控制权，reactor 继续处理其他任务
    auto conn = co_await seastar::connect(url);
    auto data = co_await conn.read();
    co_return data;
}

seastar::future<> process() {
    // 并发发起多个 I/O，不阻塞任何线程
    auto [d1, d2] = co_await seastar::when_all(
        fetch_data("http://a.com"),
        fetch_data("http://b.com")
    );
    // 两个 I/O 都完成后继续
    handle(d1, d2);
}
```

### 4. Reactor 核心调度循环

```cpp
// seastar/src/core/reactor.cc（极简版）
void reactor::run() {
    while (!_stopped) {
        // 1. 执行所有已就绪的 task
        while (!_task_queue.empty()) {
            auto t = _task_queue.pop_front();
            t->run_and_dispose();   // 运行协程帧
        }

        // 2. 等待新的 I/O 事件（epoll/io_uring）
        _backend->wait_and_process_events(&_task_queue);

        // 3. 处理定时器到期
        _timers.expire(_now);
    }
}
```

---

## 性能优化原理

### 1. 协程帧的栈上分配

Seastar 的 `task` 设计确保协程帧尽可能在栈/局部 arena 分配，避免 `new` 调用：

```cpp
// continuation_base 使用侵入式双链表避免额外分配
struct continuation_base : public task {
    future_state<T>* _state;
    Func _func;
    // 直接内联在 task 对象中，无二次分配
};
```

### 2. io_uring 零拷贝 I/O

```cpp
// Seastar 5.x 支持 io_uring，避免 epoll 系统调用
future<size_t> posix_file_impl::read_dma(
    uint64_t pos, void* buf, size_t len) {
    
    io_request req = io_request::make_read(
        _fd, pos, buf, len);
    
    // 提交到 io_uring submission queue（无 syscall！）
    return engine().submit_io(std::move(req));
}
```

### 3. 跨 shard 通信：message passing

```cpp
// 需要跨 CPU 操作时，通过 smp::submit_to 发送消息
// 比锁更高效：只需一次原子写入 + cache line bounce
seastar::future<int> get_remote_value(unsigned shard_id) {
    return seastar::smp::submit_to(shard_id, [] {
        return local_cache.get_value();  // 在目标 shard 执行
    });
}
```

---

## 性能数据

| 场景 | 传统多线程 + mutex | Seastar Shared-Nothing |
|------|-------------------|------------------------|
| 10K conn 并发读 | ~150K req/s | **~1.2M req/s** |
| P99 延迟 | 2–5 ms | **< 200 µs** |
| 上下文切换开销 | ~1–3 µs（内核切换） | **~50 ns**（协程切换） |
| 内存占用（每连接） | ~8 KB（栈） | ~256 B（协程帧） |

> 数据来源：ScyllaDB Benchmarks & Seastar OSS 文档

---

## 推荐在线架构应用

在**推荐服务**场景中，`seastar::future` 的理念可以直接借鉴：

```
Request
  │
  ├─► [shard 0] 特征拉取 future
  ├─► [shard 1] 召回计算 future  
  └─► [shard 2] 排序模型 future
              │
       when_all_succeed()
              │
        最终结果返回
```

- 每个召回源对应一个 `future`，`when_all` 等待最快的 K 个
- 超时通过 `with_timeout` 直接集成，无需额外线程
- Shared-Nothing 保证特征缓存无锁访问，适合高并发特征服务

---

## 源码分析要点

| 文件 | 内容 |
|------|------|
| `include/seastar/core/future.hh` | `future<T>` / `promise<T>` 完整实现，重点看 `then()` 和 `await_suspend()` |
| `include/seastar/core/coroutine.hh` | C++20 `co_await` 适配层 |
| `src/core/reactor.cc` | 事件循环核心，`run()` → `wait_and_process_events()` |
| `src/core/io_queue.cc` | io_uring 提交/收割逻辑 |
| `include/seastar/core/smp.hh` | `smp::submit_to` 跨 shard 消息传递 |

---

*归档时间：2026-05-12 · 系列：C++ 核心组件深度解析*

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-31T19:01:46.454175
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：`seastar::future` / 用户态协程调度

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 与 `feeda-mv-grc` 中均已出现“目标库相关使用”命中，各 10 个文件，可作为后续引入 Seastar 异步模型时的代码参考入口。不过，从当前命中文件分布来看，主要集中在业务规则、过滤、调整器、插件等模块，尚未看到大规模围绕 `seastar::future`、`co_await`、`reactor` 或 shard-local 状态组织的系统性使用方式。

- 两个代码库都大量使用 `std::vector`、`std::string`、`std::unordered_map` 等标准容器，其中 `feeda-mv-grc` 规模更大：`std::vector` 达 8382 次、`std::unordered_map` 达 2828 次。需要注意的是，Seastar 的收益并不来自简单替换 STL 容器，而是来自 **I/O、RPC、召回、聚合、模型预测等链路的非阻塞化与 shard-local 化**。因此，迁移潜力主要集中在高并发请求处理、外部服务调用、召回聚合、图依赖计算、模型预测等场景，而不是普通数据结构替换。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 扫描发现：
  - 已发现目标库相关使用：10 个文件。
  - 典型命中文件包括：
    - `operator/diversity/author_diversity_rule.cpp`
    - `operator/diversity/title_diversity_rule.cpp`
    - `strategy/diversity/rule/transfer_interest1_diversity_rule.cpp`
    - `operator/diversity/duration_interest_soft_rule.cpp`
    - `operator/diversity/unadjacent_rule.cpp`

- 标准库使用规模：
  - `std::vector`：1969 次，分布在 356 个文件。
  - `std::string`：2443 次，分布在 425 个文件。
  - `std::unordered_map`：734 次，分布在 205 个文件。

- 典型业务场景：
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

- 适配判断：
  - `feeda-mv-grg` 的核心路径更偏向候选集处理、规则过滤、多样性控制、模型预测。
  - 如果当前 `predict`、特征拉取、外部模型服务调用存在同步阻塞，适合逐步改造成 `seastar::future` 或 C++20 coroutine 风格。
  - 如果大部分逻辑是纯 CPU 计算，直接引入 Seastar 收益有限，反而需要重点关注 shard 绑定、任务切分和避免长时间占用 reactor。

#### feeda-mv-grc：召回汇聚服务

- 扫描发现：
  - 已发现目标库相关使用：10 个文件。
  - 典型命中文件包括：
    - `plugin/gcms_sndb.cpp`
    - `processor/filter/news_qs_v2_filter_operator.cc`
    - `operator/adjuster/sketchy/newhot_ip_explore_cp.cpp`
    - `processor/multi_factor/gcf_sim_score_gen.cpp`
    - `operator/adjuster/precise/distill_ucf_jp_adjuster.cpp`

- 标准库使用规模：
  - `std::vector`：8382 次，分布在 1266 个文件。
  - `std::string`：7107 次，分布在 1222 个文件。
  - `std::unordered_map`：2828 次，分布在 636 个文件。

- 典型业务场景：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
  - `service/grc_http_service.cpp`
    ```cpp
    static std::vector<std::string> colors{
        "#FFB6C1", "#DC143C", "#DB7093", "#FF1493", "#FF00FF", "#800080",
        "#4B0082", "#7B68EE", "#0000FF", "#4169E1", "#778899", "#4682B4",
        ...
    };
    ```
  - `service/grc_http_service.cpp`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    ```

- 适配判断：
  - `feeda-mv-grc` 是更适合优先评估 Seastar 的代码库。
  - 召回汇聚天然存在多路并发 I/O、多源召回、结果合并、过滤、排序、特征补充等链路。
  - 如果当前实现依赖线程池、同步 RPC、阻塞 HTTP、阻塞 KV/DB 访问，则可以通过 `seastar::future`、`when_all`、`smp::submit_to` 获得更明显收益。
  - `service/grc_http_service.cpp` 中的图依赖查询、HTTP 参数解析、响应构造可以作为 HTTP reactor 化改造的入口，但不建议从静态配置类容器开始迁移。

---

### 3. 💡 适用性评估与建议

- **建议一：优先在 `feeda-mv-grc/service/grc_http_service.cpp` 评估异步 HTTP 请求处理链路**
  - 当前文件中存在 HTTP 请求参数解析、依赖图访问、响应字符串构造等逻辑：
    ```cpp
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    ```
  - 如果 `graph_engine->get_vertexs_message(graph_name)` 涉及共享状态读取、锁、远程访问或阻塞查询，可以改造为：
    ```cpp
    seastar::future<vertex_messages> get_vertexs_message_async(seastar::sstring graph_name);
    ```
  - 请求处理可改为：
    ```cpp
    seastar::future<seastar::sstring> handle_graph_request(...) {
        auto all_vertex = co_await graph_engine->get_vertexs_message_async(graph_name);
        co_return build_response(all_vertex);
    }
    ```
  - 该场景的收益点在于：HTTP reactor 不被单个图查询阻塞，多请求之间通过用户态协程切换并发执行。

- **建议二：在 `feeda-mv-grc/plugin/gcms_sndb.cpp` 中重点排查外部存储或服务调用，适合作为 `seastar::future` 封装试点**
  - `plugin/gcms_sndb.cpp` 属于插件类模块，通常可能包含外部系统访问、召回源访问或存储查询。
  - 如果当前逻辑存在同步 RPC、同步 DB 查询、同步 cache miss 回源，建议封装为：
    ```cpp
    seastar::future<Result> query_sndb_async(Request req);
    ```
  - 多个召回源可以使用：
    ```cpp
    auto [r1, r2, r3] = co_await seastar::when_all(
        query_sndb_async(req1),
        query_sndb_async(req2),
        query_sndb_async(req3)
    );
    ```
  - 这样可以把“多路召回串行等待”改为“并发发起 + 统一汇聚”，更贴合 `feeda-mv-grc` 的召回汇聚服务定位。

- **建议三：在 `feeda-mv-grc/processor/multi_factor/gcf_sim_score_gen.cpp` 中拆分 CPU 计算与 I/O 等待**
  - `gcf_sim_score_gen.cpp` 从命名看可能涉及相似度生成、多因子打分、召回结果补充。
  - 如果该模块既有外部特征读取，又有本地 CPU 打分，应避免在 reactor 中执行长时间 CPU 循环。
  - 推荐拆分为：
    - I/O 部分：使用 `seastar::future` / `co_await` 非阻塞读取。
    - CPU 打分部分：按照 shard-local 数据分片执行，必要时通过 `smp::submit_to` 投递到目标 shard。
  - 示例方向：
    ```cpp
    seastar::future<ScoreResult> gen_score_async(Request req) {
        auto features = co_await load_features_async(req);
        co_return compute_score_on_local_shard(features);
    }
    ```

- **建议四：在 `feeda-mv-grg/model/model.h` 与 `feeda-mv-grg/model/paddle_model.h` 中谨慎评估模型预测接口异步化**
  - 当前接口是同步形式：
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
  - 如果 `predict` 只是纯 CPU 本地推理，直接改为 `seastar::future<int>` 不一定有收益，且可能增加协程开销。
  - 如果 `predict_with_tensor_input` 内部会访问远程模型服务、异步特征服务、GPU/推理队列，则建议新增异步接口，而不是直接替换原接口：
    ```cpp
    virtual seastar::future<int> predict_async(
        std::vector<RidTmpInfoPtr>& candidate_vec,
        uint32_t pos
    );
    ```
  - 推荐采用“双接口过渡”：
    - 保留原 `predict`，保障现有同步链路稳定。
    - 新增 `predict_async`，仅在异步请求链路中使用。
    - 待调用方完成 reactor 化后，再逐步收敛同步接口。

- **建议五：`feeda-mv-grg/operator/diversity/*.cpp` 和 `feeda-mv-grc/operator/adjuster/*.cpp` 更适合作为 shard-local 规则计算改造，而不是直接引入 future**
  - 例如：
    - `operator/diversity/author_diversity_rule.cpp`
    - `operator/diversity/title_diversity_rule.cpp`
    - `operator/diversity/unadjacent_rule.cpp`
    - `operator/adjuster/sketchy/newhot_ip_explore_cp.cpp`
    - `operator/adjuster/precise/distill_ucf_jp_adjuster.cpp`
  - 这些模块更可能是内存内规则判断、候选集调整、结果重排。
  - 如果没有 I/O 阻塞，不建议为了“统一异步风格”强行引入 `seastar::future`。
  - 更合理的优化方向是：
    - 将规则上下文设计为 shard-local，避免跨线程共享。
    - 避免在规则计算中访问全局 `unordered_map` 加锁结构。
    - 将只读配置提前复制到每个 shard。
    - 对大候选集处理做分批，避免单个 continuation 长时间占用 reactor。

---

### 4. ⚠️ 引入风险与限制

- **风险一：Seastar 是框架级迁移，不是局部容器替换**
  - 当前扫描中 `std::vector`、`std::string`、`std::unordered_map` 使用规模很大，但这些并不是 `seastar::future` 的直接替换对象。
  - 迁移重点应放在：
    - 阻塞 I/O；
    - RPC/HTTP 调用；
    - 多路召回；
    - DB/KV/cache 查询；
    - 线程池任务调度。
  - 如果只是把同步函数返回值改成 `seastar::future<T>`，但内部仍然阻塞线程，无法获得 Seastar 的性能收益。

- **风险二：Shared-Nothing 模型要求重构共享状态**
  - Seastar 默认禁止随意跨线程访问对象。
  - 当前业务代码中大量使用 `std::unordered_map<std::string, std::vector<int>>` 一类结构，例如 `service/grc_http_service.cpp` 中的 `depend_map`。
  - 如果这些结构未来变成全局缓存或共享索引，需要改造成：
    - 每 shard 一份本地副本；
    - 或通过 `seastar::sharded<T>` 管理；
    - 或通过 `smp::submit_to` 在目标 shard 执行访问。
  - 不能简单沿用传统多线程下的 mutex 保护方式，否则会抵消 Seastar 的核心收益。

- **风险三：CPU 密集型逻辑可能阻塞 reactor**
  - `feeda-mv-grg` 中的多样性规则、模型预测、候选集遍历，以及 `feeda-mv-grc` 中的多因子打分、调整器逻辑，可能存在较重 CPU 循环。
  - 在 Seastar 中，单个 task 执行时间过长会影响同 shard 上所有请求的尾延迟。
  - 对长循环应考虑：
    - 分批处理；
    - 周期性 `co_await seastar::yield()`；
    - 将 CPU-heavy 任务拆分；
    - 或放入专门的调度组 / execution stage。

- **风险四：与现有运行时、RPC 框架、线程池可能存在集成成本**
  - `service/grc_http_service.cpp` 当前看起来可能依赖已有 HTTP/RPC 框架控制对象，例如：
    ```cpp
    cntl->http_request().uri().GetQuery("off");
    ```
  - 如果现有框架本身是阻塞式或基于传统线程池，Seastar 的 reactor 线程不能直接调用阻塞 API。
  - 需要明确边界：
    - 要么整体请求入口迁移到 Seastar；
    - 要么使用适配层把阻塞调用隔离到外部线程池；
    - 避免在 reactor 中调用未知阻塞函数。

---

### 5. 推荐迁移路径

- **第一阶段：盘点阻塞点**
  - 优先扫描以下文件中的 RPC、HTTP、DB、KV、模型服务调用：
    - `feeda-mv-grc/service/grc_http_service.cpp`
    - `feeda-mv-grc/plugin/gcms_sndb.cpp`
    - `feeda-mv-grc/processor/multi_factor/gcf_sim_score_gen.cpp`
    - `feeda-mv-grg/model/paddle_model.h`
    - `feeda-mv-grg/model/model.h`

- **第二阶段：新增异步接口，不直接替换同步接口**
  - 例如：
    ```cpp
    int predict(...);
    seastar::future<int> predict_async(...);
    ```
  - 保留原链路，降低一次性迁移风险。

- **第三阶段：选择 `feeda-mv-grc` 的多路召回/聚合链路做 PoC**
  - `feeda-mv-grc` 的 `std::vector`、`std::unordered_map` 和业务模块规模更大，且服务形态更接近高并发 I/O 聚合。
  - 优先验证：
    - `when_all` 并发召回；
    - shard-local cache；
    - 非阻塞 HTTP/RPC；
    - P99 延迟变化。

- **第四阶段：再评估 `feeda-mv-grg` 的模型预测异步化**
  - 如果模型预测链路存在远程调用或排队等待，则适合异步化。
  - 如果主要是本地 CPU 推理，应重点优化批处理、SIMD、内存布局和任务切分，而不是盲目引入 future。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
