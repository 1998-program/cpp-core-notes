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
