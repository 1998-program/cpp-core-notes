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
