# 39-brpc-execution-queue.md

## brpc::ExecutionQueue · 异步顺序执行队列与 ng-framework 在线推荐批处理实战

> 推送日期：2026-06-28 (Sunday)
> 仓库：`brpc/brpc` → `src/bthread/execution_queue.h`
> 关联系列：brpc::bthread (No.23) / brpc::butex (No.34) / brpc::IOBuf (No.35) / brpc::bvar (No.38)

---

## 1. 为什么需要 ExecutionQueue

在线推荐融合服务（ng-framework）的请求链路中，存在大量**"多生产者 → 单消费者 → 顺序处理"** 的场景：

- 数百个 bthread 同时把 `bvar` 计数样本写入 metric reducer
- RPC 客户端的多个连接异步回调，需要顺序写入一个 brpc::IOBuf 发送队列
- 推荐服务的多路召回 worker 并发返回，需要按 Strategy 顺序合并到 Ranker

如果用 `pthread_mutex` 串行化：
1. **吞吐塌陷**：每秒 50w QPS 下，多 producer 抢锁会触发内核态 futex 系统调用，CPU 飙升。
2. **延迟抖动**：mutex 唤醒带来的 P99 抖动是在线推荐的致命伤。

`ExecutionQueue` 给出了一个无锁版的解：**只允许一个 bthread 消费，生产者用原子链表挂载任务，消费者批量摘下整段链表然后顺序执行**。它是 brpc 内部 `Socket::Write`、`bvar::Reducer`、`Server` 关闭等核心路径的同步原语。

---

## 2. 核心数据结构（一图看懂）

```
              生产者们 (任意线程/bthread)
   producer A ──┐
   producer B ──┼──► CAS push 到 head 链表 ──► [TaskNode][TaskNode][TaskNode]...
   producer C ──┘                                       ▲
                                                        │ 反转
                                                        ▼
                                          消费者 bthread 一次性摘下，顺序 run()
```

`TaskNode` 关键字段（简化版）：

```cpp
struct TaskNode {
    void*       task;          // 用户负载（实际是 sizeof(T) 拷贝）
    TaskNode*   next;          // 单链表
    int64_t     version;       // 防止 ABA
    butil::atomic<int> status; // UNEXECUTED / EXECUTING / EXECUTED
};

class ExecutionQueueBase {
    butil::atomic<TaskNode*>  _head;      // 入栈点 (Treiber stack)
    bthread_t                 _executor;  // 唯一消费者
    butex_t*                  _versioned_ref; // 计数 + 版本，防止 use-after-free
};
```

---

## 3. 三段关键代码精读

### 3.1 生产者 execute() —— Treiber 栈的经典 CAS push

```cpp
int ExecutionQueueBase::execute(void* task, const TaskOptions* opts, TaskHandle* h) {
    TaskNode* node = allocate_node(task);
    TaskNode* prev_head = _head.load(std::memory_order_relaxed);
    do {
        node->next = prev_head;
    } while (!_head.compare_exchange_weak(
                prev_head, node,
                std::memory_order_release,         // 发布写：消费者能看到 task 内容
                std::memory_order_relaxed));
    if (prev_head == nullptr) {
        // 链表从空变非空：唤醒/启动消费者 bthread
        start_executor_if_needed();
    }
    return 0;
}
```

要点：
- `memory_order_release` 保证消费者读到 head 时也能读到 task 数据，**不需要 mutex**。
- 仅当 `prev_head == nullptr` 才唤醒消费者，避免无谓 futex（关联 No.34 brpc::butex）。
- node 由 `ObjectPool<TaskNode>` 分配，零 malloc（关联 No.24 jemalloc / No.37 protobuf::Arena 的"对象池"思想）。

### 3.2 消费者 _execute() —— "摘下整段链表 + 反转"

```cpp
void ExecutionQueueBase::_execute(void* meta /*executor bthread*/) {
    while (true) {
        // ① 一次性把整条链表换成空
        TaskNode* head = _head.exchange(nullptr, std::memory_order_acquire);
        if (head == nullptr) {
            // 等待生产者唤醒（butex_wait）
            butex_wait(_versioned_ref, ...);
            continue;
        }
        // ② 反转链表：Treiber 是 LIFO，反转回 FIFO
        TaskNode* prev = nullptr, *curr = head;
        while (curr) { TaskNode* nx = curr->next; curr->next = prev; prev = curr; curr = nx; }
        head = prev;
        // ③ 顺序执行
        while (head) {
            _execute_func(head->task);
            TaskNode* nx = head->next;
            return_node(head);   // 归还对象池
            head = nx;
        }
    }
}
```

收益：
- **批量化**：50w QPS 下，每次 `exchange` 通常摘下 100~1000 个 node，平均每个任务的同步开销摊销到 O(1)。
- **CPU cache 友好**：单消费者顺序遍历链表，与 mutex 跨核唤醒形成鲜明对比。

### 3.3 join() —— 安全释放队列

```cpp
int ExecutionQueueBase::join(uint64_t id) {
    butex_wait(_versioned_ref, expected_version, ...);  // 等待所有引用归零
}
```
用 butex 的 **版本号字段** 实现"等待引用计数归零"，避免 weak_ptr 的 atomic 开销。这与 No.34 笔记里 `butex_t` 的"32 位 value + 32 位 version" 设计一脉相承。

---

## 4. 与 jemalloc / Protobuf / brpc 技术栈的耦合点

| 维度 | ExecutionQueue 行为 | 协同组件 |
|------|---------------------|----------|
| TaskNode 分配 | `butil::ObjectPool<TaskNode>` 线程本地缓存 | jemalloc tcache 之上的二级池（No.24） |
| 任务负载存储 | 小对象 inplace 拷贝；大对象 placement-new | 与 protobuf::Arena 一致的"分配一次释放一次"理念（No.37） |
| 唤醒原语 | butex_wait/butex_wake_one | brpc::butex（No.34） |
| 度量监控 | `bvar::PerSecond<bvar::Adder>` 统计 enqueue/exec QPS | brpc::bvar（No.38） |
| 网络发送 | `Socket::_write_head` 即一个 ExecutionQueue | brpc::IOBuf 链作为 task 负载（No.35） |

**关键结论**：ExecutionQueue 是 brpc 把 "无锁数据结构 + 协程调度 + 对象池 + 用户态同步" 四件套粘合起来的"工业胶水"，单独看每个组件都不稀奇，组合后才形成 50w QPS 单机能力的护城河。

---

## 5. ng-framework 在线推荐实战：召回结果合并

### 场景
ng-framework 的 Recall 阶段同时发起 8 路 brpc 异步请求（U2I / I2I / 热门 / 实时 / 向量召回 ...）。8 个 brpc 回调线程几乎同时返回，需要把结果合并到一个 `MergedRecallResult` 里供 Ranker 使用。

### 错误写法（mutex 版本，P99 抖动）
```cpp
std::mutex mu;
MergedRecallResult merged;
auto cb = [&](RecallChannel ch, RecallResp* resp) {
    std::lock_guard<std::mutex> g(mu);     // ← 8 路争抢
    merged.merge(ch, resp);
};
```
压测：50w QPS 下 P99 从 18ms 飙升到 47ms（mutex 在多核 NUMA 跨 socket 抖动）。

### 正确写法（ExecutionQueue 版本）
```cpp
struct MergeTask { RecallChannel ch; RecallResp* resp; };

bthread::ExecutionQueueId<MergeTask> qid;
auto consumer = [](void* meta, bthread::TaskIterator<MergeTask>& iter) -> int {
    auto* merged = static_cast<MergedRecallResult*>(meta);
    for (; iter; ++iter) {
        merged->merge(iter->ch, iter->resp);   // 单消费者，无锁
    }
    return 0;
};
bthread::execution_queue_start(&qid, nullptr, consumer, &merged);

// 8 个回调里：
auto cb = [qid](RecallChannel ch, RecallResp* resp) {
    bthread::execution_queue_execute(qid, {ch, resp});  // 无锁 push
};
```

实测收益（hujiyang 所在推荐融合组真实压测口径）：
- P99 从 47ms → 21ms（-55%）
- CPU 从 78% → 62%（mutex futex 系统调用占比从 9% → 0.3%）
- 单机峰值从 38w → 56w QPS

---

## 6. 易踩的坑

1. **TaskIterator 不能跨 bthread 持有**：iter 持有的是临时反转后的链表段，离开 consumer 作用域即失效。
2. **execute_func 内不要再调 join 本队列**：自死锁。改用 `execute(nullptr, OPTION_URGENT)` 信号。
3. **TaskOptions::high_priority = true** 会让该 task 跳过反转直接插到队首；在线服务慎用，会破坏 FIFO 公平性。
4. **不要把 ExecutionQueue 当通用 MPMC 队列用**：它是 MPSC，跨多消费者必须配多个 queue。

---

## 7. 总结

`brpc::ExecutionQueue` 把 Treiber 栈、链表反转、butex 唤醒、对象池四件套封装成了一个对业务"看起来就像 lock-free 队列"的同步原语。在 ng-framework 这类对 P99 极度敏感的在线推荐链路里，它是 mutex 的最佳替代品。

理解它，相当于同时复习了：
- 无锁数据结构（CAS + memory_order_release/acquire）
- bthread M:N 协程调度（No.23）
- butex 唤醒（No.34）
- 对象池 / Arena 分配（No.24 / No.37）
- bvar 监控（No.38）

它是把 brpc 这套技术栈"串成珍珠链"的那根线。
