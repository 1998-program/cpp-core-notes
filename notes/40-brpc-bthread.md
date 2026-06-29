# brpc::bthread — M:N 协程调度引擎与工作窃取队列

> **笔记编号**: 40
> **日期**: 2026-06-29
> **关键词**: bthread, WorkStealingQueue, M:N 协程, butex, 用户态调度, brpc, ng-framework
> **技术栈**: brpc / jemalloc / Protobuf / ng-framework

---

## 1. 核心定位

`bthread` 是 brpc 的**灵魂组件**，实现了 M:N 协程模型：
- **M 个用户态协程**映射到 **N 个内核线程**（pthread）
- 协程可在线程间迁移（不同于 N:1 协程如 libco）
- 创建/切换开销从微秒级降至纳秒级
- 单机可承载**百万级并发协程**

```
┌─────────────────────────────────────────────────────────────┐
│                    TaskControl (全局唯一)                    │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐   │
│  │  TaskGroup    │  │  TaskGroup    │  │  TaskGroup    │   │
│  │  (worker 0)   │  │  (worker 1)   │  │  (worker N)   │   │
│  │  ┌─────────┐  │  │  ┌─────────┐  │  │  ┌─────────┐  │   │
│  │  │  _rq    │  │  │  │  _rq    │  │  │  │  _rq    │  │   │
│  │  │ (local) │◄─┼──┼──│ steal!  │  │  │  │         │  │   │
│  │  └─────────┘  │  │  └─────────┘  │  │  └─────────┘  │   │
│  │  ┌─────────┐  │  │               │  │               │   │
│  │  │_remote_rq│ │  │               │  │               │   │
│  │  │(remote) │  │  │               │  │               │   │
│  │  └─────────┘  │  │               │  │               │   │
│  └───────────────┘  └───────────────┘  └───────────────┘   │
│                                                             │
│  每个 TaskGroup 持有一个 WorkStealingQueue<bthread_t>      │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 三大核心组件

### 2.1 TaskControl（TC）

- **进程内全局唯一**的单例
- 负责创建和管理所有 `TaskGroup`
- 初始化时创建 `concurrency` 个 pthread worker

```cpp
// TaskControl::init() 核心逻辑
int TaskControl::init(int concurrency) {
    for (int i = 0; i < concurrency; ++i) {
        pthread_create(&_workers[i], NULL, worker_thread, this);
    }
}
```

### 2.2 TaskGroup（TG）

- **每个 pthread 一个实例**（通过 `tls_task_group` 线程局部变量访问）
- 维护两个任务队列：
  - `_rq`: 本地任务队列（`WorkStealingQueue<bthread_t>`）
  - `_remote_rq`: 远程任务队列（来自非 worker 线程的任务）
- 执行主循环 `run_main_task()`

```cpp
struct TaskGroup {
    WorkStealingQueue<bthread_t> _rq;      // 本地队列，可被偷窃
    RemoteTaskQueue _remote_rq;            // 远程队列
    ContextualStack* _main_stack;          // 主栈
    bthread_t _main_tid;                   // 主 bthread ID
    size_t _steal_seed;                    // 偷窃随机种子
    size_t _steal_offset;                  // 偷窃偏移
};
```

### 2.3 TaskMeta（TM）

- **每个 bthread 一个实例**
- 保存协程上下文：栈指针、回调函数、参数、状态等
- 通过 `bthread::ResourceId<TaskMeta>` 从资源池分配（类似 slab 分配器）

```cpp
struct TaskMeta {
    butil::atomic<ButexWaiter*> current_waiter;
    ContextualStack* stack;
    bthread_attr_t attr;
    void* (*fn)(void*);
    void* arg;
    bool stop;
    bool interrupted;
    bool about_to_quit;
    LocalStorage local_storage;
    int64_t cpuwide_start_ns;
    TaskStatistics stat;
    bthread_t tid;
    uint32_t* version_butex;  // 版本号，用于 tid 生成
};
```

---

## 3. WorkStealingQueue — 无锁环形队列

### 3.1 设计思想

```
                steal (top)          push/pop (bottom)
                     ↓                      ↓
    ┌────┬────┬────┬────┬────┬────┬────┬────┐
    │ T0 │ T1 │ T2 │ T3 │ T4 │ T5 │ T6 │ T7 │
    └────┴────┴────┴────┴────┴────┴────┴────┘
    ↑                                         ↑
    top (其他线程偷窃)                   bottom (本线程操作)
```

- **push / pop**: 在 `bottom` 端操作，仅被本线程调用
- **steal**: 在 `top` 端操作，被其他线程调用
- **并发场景**: steal-steal, steal-push, steal-pop（push 和 pop 不会并发）

### 3.2 核心实现

#### push（入队）

```cpp
bool push(const T& x) {
    const size_t b = _bottom.load(butil::memory_order_relaxed);
    const size_t t = _top.load(butil::memory_order_acquire);
    
    if (b >= t + _capacity) return false;  // 队列满
    
    _buffer[b & (_capacity - 1)] = x;
    _bottom.store(b + 1, butil::memory_order_release);  // (A)
    return true;
}
```

- `_bottom` 用 `relaxed` 读取（只有本线程修改）
- `_top` 用 `acquire` 读取（需要看到其他线程的修改）
- 位置 (A) 使用 `release` 确保 steal/pop 能看到数据写入

#### pop（出队）

```cpp
bool pop(T* val) {
    const size_t b = _bottom.load(butil::memory_order_relaxed);
    size_t t = _top.load(butil::memory_order_relaxed);
    
    if (t >= b) return false;  // 快速判断为空
    
    const size_t newb = b - 1;
    _bottom.store(newb, butil::memory_order_relaxed);
    
    butil::atomic_thread_fence(butil::memory_order_seq_cst);  // (A) 关键！
    
    t = _top.load(butil::memory_order_relaxed);
    if (t > newb) {
        _bottom.store(b, butil::memory_order_relaxed);
        return false;  // 队列已空
    }
    
    *val = _buffer[newb & (_capacity - 1)];
    
    if (t != newb) return true;  // 多于一个元素，无竞争
    
    // 最后一个元素，与 steal 竞争
    const bool popped = _top.compare_exchange_strong(
        t, t + 1, 
        butil::memory_order_seq_cst,
        butil::memory_order_relaxed
    );
    
    _bottom.store(b, butil::memory_order_relaxed);
    return popped;
}
```

**关键点**: `seq_cst` 内存序保证全局唯一顺序，防止 pop 和 steal 同时成功。

#### steal（偷窃）

```cpp
bool steal(T* val) {
    size_t t = _top.load(butil::memory_order_acquire);
    size_t b = _bottom.load(butil::memory_order_acquire);
    
    if (t >= b) return false;
    
    do {
        butil::atomic_thread_fence(butil::memory_order_seq_cst);  // (B)
        
        b = _bottom.load(butil::memory_order_acquire);
        if (t >= b) return false;
        
        *val = _buffer[t & (_capacity - 1)];
    } while (!_top.compare_exchange_strong(
        t, t + 1,
        butil::memory_order_seq_cst,
        butil::memory_order_relaxed
    ));
    
    return true;
}
```

**seq_cst 的作用**（关键理解）:
- C++ 标准中 `seq_cst` 保证所有线程看到相同的全局顺序
- 在多线程竞争场景下（如 1 个 pop vs 多个 steal），`seq_cst` 保证只有一个操作成功
- 没有 `seq_cst`，可能出现 pop 和 steal 同时获取同一个元素

### 3.3 ng-framework 在线服务应用

在推荐系统的在线服务中：

```cpp
// ng-framework 典型用法
void RecommenderService::process_request(const Request& req, Response* resp) {
    // 1. 将大任务拆分为多个子任务
    for (auto& feature : req.features()) {
        bthread_start_background(
            &tid, NULL, 
            extract_feature, 
            &feature_context
        );
    }
    
    // 2. 等待所有子任务完成
    for (int i = 0; i < n; ++i) {
        bthread_join(tids[i], NULL);
    }
    
    // 3. 合并结果
    merge_results(resp);
}
```

**WorkStealingQueue 的优势**:
- 负载均衡：忙的 worker 被偷走任务，闲的 worker 自动获取任务
- 缓存友好：本地队列无锁，缓存命中率高
- 无全局竞争：避免单一任务队列的瓶颈

---

## 4. butex — 用户态 futex

### 4.1 为什么需要 butex？

如果在 bthread 中使用 `pthread_mutex`:
- 会挂起整个 pthread（bthread_worker）
- 该 worker 上的其他 bthread 无法执行
- **性能灾难**

### 4.2 butex vs futex

| 特性 | futex | butex |
|------|-------|-------|
| 等待粒度 | pthread | bthread |
| 内核介入 | 是（竞争时） | 否（纯用户态） |
| 等待队列 | 内核态 | 用户态链表 |
| 适用场景 | pthread 同步 | bthread 同步 |

### 4.3 核心数据结构

```cpp
struct Butex {
    butil::atomic<int> value;         // 状态标记
    ButexWaiterList waiters;          // 等待队列（链表）
    internal::FastPthreadMutex waiter_lock;  // 保护 waiters
};

struct ButexBthreadWaiter : public ButexWaiter {
    bthread_t tid;
    TaskMeta* task_meta;
    TimerThread::TaskId sleep_id;    // 超时定时器
    WaiterState waiter_state;
    int expected_value;
    Butex* initial_butex;
    TaskControl* control;
    const timespec* abstime;
};
```

### 4.4 butex_wait 流程

```cpp
int butex_wait(void* arg, int expected_value, const timespec* abstime) {
    Butex* b = container_of(static_cast<butil::atomic<int>*>(arg), Butex, value);
    
    // 1. 快速路径：值不匹配，立即返回
    if (b->value.load(butil::memory_order_relaxed) != expected_value) {
        errno = EWOULDBLOCK;
        return -1;
    }
    
    // 2. 创建 waiter
    ButexBthreadWaiter bbw;
    bbw.tid = g->current_tid();
    bbw.task_meta = g->current_task();
    bbw.expected_value = expected_value;
    bbw.initial_butex = b;
    
    // 3. 设置 remain 函数，在切换前执行
    g->set_remained(wait_for_butex, &bbw);
    
    // 4. 切换到其他 bthread
    TaskGroup::sched(&g);
    
    // ... 被唤醒后返回
}
```

**关键**: 通过 `set_remained()` 在上下文切换前将 bthread 加入等待队列。

### 4.5 butex_wake 流程

```cpp
int butex_wake(void* arg, bool nosignal) {
    Butex* b = container_of(static_cast<butil::atomic<int>*>(arg), Butex, value);
    
    ButexWaiter* front = NULL;
    {
        BAIDU_SCOPED_LOCK(b->waiter_lock);
        if (b->waiters.empty()) return 0;
        
        front = b->waiters.head()->value();
        front->RemoveFromList();
    }
    
    // pthread vs bthread
    if (front->tid == 0) {
        wakeup_pthread(static_cast<ButexPthreadWaiter*>(front));
    } else {
        ButexBthreadWaiter* bbw = static_cast<ButexBthreadWaiter*>(front);
        TaskGroup* g = get_task_group(bbw->control, nosignal);
        
        if (g == tls_task_group) {
            // 本地唤醒：直接执行
            run_in_local_task_group(g, bbw->tid, nosignal);
        } else {
            // 远程唤醒：加入 remote_rq
            g->ready_to_run_remote(bbw->tid, nosignal);
        }
    }
    
    return 1;
}
```

---

## 5. 协程上下文切换

### 5.1 汇编实现

```asm
; bthread_make_fcontext: 初始化协程上下文
bthread_make_fcontext:
    movq %rdi, %rax          ; 栈底地址
    andq $-16, %rax          ; 16 字节对齐
    leaq -0x48(%rax), %rax   ; 预留 72 字节上下文空间
    
    movq %rdx, 0x38(%rax)    ; 保存入口函数
    stmxcsr (%rax)           ; 保存 MXCSR
    fnstcw 0x4(%rax)         ; 保存 FPU 控制字
    
    leaq finish(%rip), %rcx
    movq %rcx, 0x40(%rax)    ; 设置返回地址
    ret
```

### 5.2 栈内存布局

```
高地址（栈底）
┌─────────────────────────────────┐
│  Guard Page (PROT_NONE)         │  ← 防止栈溢出
├─────────────────────────────────┤
│                                 │
│  协程栈空间（默认 1MB）          │
│                                 │
├─────────────────────────────────┤
│  Context Structure (72 bytes)   │  ← fcontext 保存的寄存器
│  - MXCSR, FPU Control Word      │
│  - Entry Function               │
│  - Return Address               │
├─────────────────────────────────┤
│  Guard Page (PROT_NONE)         │
└─────────────────────────────────┘
低地址
```

---

## 6. 与 jemalloc / Protobuf / ng-framework 集成

### 6.1 内存分配优化

```cpp
// TaskMeta 资源池使用 butil::ResourceId
// 底层是 slab 分配器，类似 jemalloc 的 small object 分配

butil::ResourceId<TaskMeta> slot;
TaskMeta* m = butil::get_resource(&slot);  // O(1) 分配

// 协程栈通过 mmap 分配
void allocate_stack_storage(StackStorage* s, int stacksize, int guardsize) {
    void* base = mmap(NULL, alloc_size, PROT_READ|PROT_WRITE,
                       MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    
    // 保护页
    mprotect(base, guardsize * page_size, PROT_NONE);
    mprotect((char*)base + alloc_size - guardsize * page_size, 
             guardsize * page_size, PROT_NONE);
    
    s->bottom = (char*)base + alloc_size;  // 栈从高地址向低地址增长
}
```

### 6.2 Protobuf Arena 集成

```cpp
// ng-framework RPC 处理
void handle_rpc(Controller* cntl, const Request* req, Response* resp) {
    // 使用 Arena 分配临时对象
    google::protobuf::Arena arena;
    
    auto* temp_msg = google::protobuf::Arena::CreateMessage<TempMessage>(&arena);
    
    // 在 bthread 中处理
    bthread_start_background(&tid, NULL, [](void* arg) {
        process_message(static_cast<TempMessage*>(arg));
        return nullptr;
    }, temp_msg);
    
    // Arena 在作用域结束时统一释放，无需手动管理
}
```

### 6.3 ng-framework 在线推荐场景

```cpp
// 推荐服务典型架构
class RecommenderServiceImpl : public RecommenderService {
    void GetRecommendations(google::protobuf::RpcController* cntl,
                            const GetRecommendationsRequest* request,
                            GetRecommendationsResponse* response,
                            google::protobuf::Closure* done) {
        brpc::ClosureGuard done_guard(done);
        
        // 1. 并行提取特征
        bthread_t tids[N_FEATURES];
        FeatureContext contexts[N_FEATURES];
        
        for (int i = 0; i < N_FEATURES; ++i) {
            contexts[i].request = request;
            bthread_start_background(&tids[i], NULL, 
                extract_feature_task, &contexts[i]);
        }
        
        // 2. WorkStealingQueue 自动负载均衡
        // 忙的 worker 上的任务会被其他 worker 偷走
        
        for (int i = 0; i < N_FEATURES; ++i) {
            bthread_join(tids[i], NULL);
        }
        
        // 3. 模型推理（可能使用 butex 等待）
        infer_and_rank(contexts, response);
    }
};
```

---

## 7. 性能对比

| 操作 | pthread | bthread | 提升 |
|------|---------|---------|------|
| 创建 | ~10μs | ~100ns | **100x** |
| 切换 | ~1μs | ~50ns | **20x** |
| 并发数 | ~1000 | ~1,000,000 | **1000x** |
| 内存/单位 | ~8MB | ~1MB | **8x** |

---

## 8. 最佳实践

### 8.1 避免阻塞操作

```cpp
// ❌ 错误：会阻塞整个 worker
pthread_mutex_lock(&mutex);
sleep(1);
pthread_mutex_unlock(&mutex);

// ✅ 正确：使用 bthread 同步原语
bthread_mutex_lock(&bmutex);
bthread_usleep(1000000);
bthread_mutex_unlock(&bmutex);
```

### 8.2 合理设置并发度

```bash
# 根据业务特性调整
--bthread_concurrency=16      # CPU 密集型：与 CPU 核心数相当
--bthread_concurrency=64      # I/O 密集型：可适当提高
--bthread_min_concurrency=8   # 最小并发度
```

### 8.3 栈大小调优

```cpp
// 默认 1MB，对于简单任务可以减小
bthread_attr_t attr = BTHREAD_ATTR_NORMAL;
attr.stack_size = 32768;  // 32KB

bthread_start_background(&tid, &attr, task_fn, arg);
```

---

## 9. 参考资源

- [bRPC 官方文档：work_stealing_queue](https://brpc.apache.org/zh/docs/blogs/sourcecodes/work_stealing_queue/)
- [bRPC 官方文档：butex](https://brpc.apache.org/zh/docs/blogs/sourcecodes/butex/)
- [知乎：bRPC的精华全在bthread上啦](https://zhuanlan.zhihu.com/p/294129746)
- [GitHub: apache/brpc](https://github.com/apache/brpc)

---

## 10. 相关笔记

- [[34-brpc-butex]] - butex 用户态 futex 深度解析
- [[35-brpc-iobuf]] - brpc::IOBuf 零拷贝缓冲链
- [[38-brpc-bvar]] - bvar 零竞争计数器
- [[39-brpc-execution-queue]] - ExecutionQueue 异步批处理
- [[37-protobuf-arena]] - Protobuf Arena 内存管理

---

> **总结**: bthread 是 brpc 高性能的核心秘密。通过 WorkStealingQueue 实现无锁任务调度，通过 butex 实现用户态同步，通过汇编优化实现纳秒级切换。在 ng-framework 在线推荐场景中，bthread 让我们能够以极低的成本处理海量并发请求，是现代 C++ 服务端开发的必备技术。
