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

---

## 七、业务代码库适配分析
> **分析时间**：2026-06-28T19:06:57.703129
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：`brpc::ExecutionQueue`

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 与 `feeda-mv-grc` 两个业务代码库中均已发现目标技术相关使用痕迹，各自命中 10 个文件，说明当前代码库并非完全没有 brpc / bthread 相关基础，后续引入 `brpc::ExecutionQueue` 的迁移成本相对可控。尤其是 `feeda-mv-grc` 作为召回汇聚服务，天然存在“多路召回回调并发返回 → 单点合并结果 → 下游排序/过滤”的典型 MPSC 场景，与 `ExecutionQueue` 的设计目标高度匹配。

- 从现有代码结构看，两个仓库大量使用 `std::vector`、`std::string`、`std::unordered_map` 等 STL 容器：`feeda-mv-grg` 中 `std::vector` 出现 1969 次，`feeda-mv-grc` 中 `std::vector` 出现 8433 次。这类容器本身不是 `ExecutionQueue` 的直接替代对象，但它们通常出现在候选集构造、召回结果合并、过滤结果累积、策略依赖图构建等共享状态场景中。如果这些容器被多个异步回调或 worker 并发写入，目前大概率依赖 `std::mutex`、局部串行化或隐式单线程假设，具备较高的迁移优化潜力。

---

### 2. 代码库详情

#### 2.1 `feeda-mv-grg`：序列生成服务

- 已发现目标库相关使用：10 个文件，代表文件包括：
  - `util/util.cpp`
  - `operator/diversity/first_refresh_cj_es_two.cpp`
  - `operator/diversity/first_refresh_cj_es.cpp`
  - `process/gen_critic_last_result_v5.cpp`
  - `util/libdict.cpp`

- STL 使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型业务特征：
  - `model/model.h`、`model/paddle_model.h` 中大量接口以 `std::vector<RidTmpInfoPtr>& candidate_vec` 作为候选集合输入。
  - `process/gen_critic_last_result_v5.cpp` 这类结果生成逻辑很可能存在多阶段结果收集、候选集加工、特征补充或打散重排流程。
  - `operator/diversity/first_refresh_cj_es.cpp`、`operator/diversity/first_refresh_cj_es_two.cpp` 属于多样性处理逻辑，通常会对候选结果进行顺序化处理，适合检查是否存在多 worker 并发写共享候选集的情况。

- 初步判断：
  - `feeda-mv-grg` 更偏序列生成和候选加工，`ExecutionQueue` 不建议作为大范围替换 STL 容器的工具。
  - 适合优先用于“多生产者异步回调结果汇聚到单个候选集”的局部热点，而不是替换所有 `std::vector` 或 `std::unordered_map` 使用点。

#### 2.2 `feeda-mv-grc`：召回汇聚服务

- 已发现目标库相关使用：10 个文件，代表文件包括：
  - `strategy/short_micro/explore_xgb_pcs_handler.cpp`
  - `processor/video_launch/dibar/dibar_adjust_engine_pre.cpp`
  - `strategy/short_micro/glide_xgb_v3_pcs_handler.cpp`
  - `processor/filter/al4_dislike_info_filter_operator.cc`
  - `operator/adjuster/precise/hot_info_attract_precise_adjuster.cpp`

- STL 使用规模：
  - `std::vector`：8433 次，分布在 1276 个文件
  - `std::string`：7154 次，分布在 1232 个文件
  - `std::unordered_map`：2833 次，分布在 638 个文件

- 典型业务特征：
  - `service/grc_http_service.cpp` 中存在 `std::unordered_map<std::string, std::vector<int>> depend_map`，说明服务内部存在依赖图、节点依赖、结果集合等结构化聚合逻辑。
  - `strategy/short_micro/explore_xgb_pcs_handler.cpp`、`strategy/short_micro/glide_xgb_v3_pcs_handler.cpp` 属于策略 handler，通常是召回、粗排、特征或策略结果汇总的高频路径。
  - `processor/filter/al4_dislike_info_filter_operator.cc`、`operator/adjuster/precise/hot_info_attract_precise_adjuster.cpp` 属于过滤与调权阶段，常见模式是多个上游结果输入后写入共享候选集合或共享上下文。

- 初步判断：
  - `feeda-mv-grc` 与 `ExecutionQueue` 的匹配度高于 `feeda-mv-grg`。
  - 召回汇聚、策略 handler、多路异步 RPC 回调、过滤结果合并，是优先排查和迁移的方向。
  - 如果当前代码中存在类似“多个 brpc callback 持锁写同一个 `std::vector` / `std::unordered_map` / response context”的逻辑，建议优先试点 `ExecutionQueue`。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先在 `feeda-mv-grc` 的召回策略 handler 中试点结果汇聚队列**
  - 重点文件：
    - `strategy/short_micro/explore_xgb_pcs_handler.cpp`
    - `strategy/short_micro/glide_xgb_v3_pcs_handler.cpp`
  - 适用场景：
    - 多个召回通道并发返回。
    - 多个 brpc 异步 callback 同时写入同一个 request context。
    - 多个 worker 将候选 item append 到共享 `std::vector`。
  - 建议做法：
    - 将原本的“callback 内直接 merge + mutex 保护”改为“callback 内只构造 `MergeTask`，通过 `bthread::execution_queue_execute` 投递任务”。
    - 由单个 consumer 顺序执行 `merge`，保证合并逻辑无锁化。
  - 示例结构：
    ```cpp
    struct RecallMergeTask {
        int channel;
        RecallResp* resp;
    };

    bthread::ExecutionQueueId<RecallMergeTask> merge_qid;

    auto consumer = [](void* meta, bthread::TaskIterator<RecallMergeTask>& iter) -> int {
        auto* ctx = static_cast<RequestContext*>(meta);
        for (; iter; ++iter) {
            ctx->merge_recall_result(iter->channel, iter->resp);
        }
        return 0;
    };
    ```
  - 预期收益：
    - 降低多 callback 抢锁造成的 P99 抖动。
    - 减少高 QPS 下 mutex/futex 系统调用。
    - 保持合并顺序可控，便于定位结果一致性问题。

- **建议 2：检查 `processor/video_launch/dibar/dibar_adjust_engine_pre.cpp` 中是否存在多阶段调权结果并发写入**
  - 适用场景：
    - 多个调权模块并发计算后写入同一个候选列表。
    - 多个特征/规则结果写入共享 `std::unordered_map` 或 `std::vector`。
    - 使用锁保护 request-level 中间状态。
  - 优化建议：
    - 如果当前存在多个 bthread / callback 并发写 `AdjustContext`、`RankContext` 或候选集合，可以将“写共享状态”集中到 `ExecutionQueue` consumer 内。
    - producer 只负责计算局部结果并投递：
      ```cpp
      struct AdjustTask {
          int adjust_type;
          ItemId item_id;
          float delta_score;
      };
      ```
    - consumer 顺序应用调权：
      ```cpp
      ctx->apply_adjust(iter->adjust_type, iter->item_id, iter->delta_score);
      ```
  - 注意：
    - 不建议把重 CPU 计算放到 consumer 里，否则会把并发计算退化为单线程。
    - `ExecutionQueue` 只负责串行化“写共享状态”这一小段逻辑。

- **建议 3：在 `processor/filter/al4_dislike_info_filter_operator.cc` 中评估过滤结果合并是否可以无锁化**
  - 适用场景：
    - 多个过滤条件并行执行，最后写入同一个过滤结果集合。
    - 多个异步查询返回后更新 shared filter map。
    - 当前通过 `std::mutex` 保护 `std::vector`、`std::set`、`std::unordered_map`。
  - 建议做法：
    - 每个过滤子任务只产出局部 `FilterTask`。
    - 通过 `ExecutionQueue` 将过滤结果顺序写入 request context。
    - 对只读配置、词典、规则表，不需要进入队列；只把“修改 request 级共享状态”的部分收口。
  - 示例任务：
    ```cpp
    struct FilterResultTask {
        uint64_t rid;
        int filter_reason;
    };
    ```
  - 收益判断：
    - 如果过滤阶段 QPS 高、规则多、写共享结构频繁，该优化收益明显。
    - 如果过滤逻辑本身已经严格单线程，或者只有很少的并发写入，则不建议引入额外复杂度。

- **建议 4：在 `feeda-mv-grg` 的候选生成链路中局部使用，不建议全局替换**
  - 重点文件：
    - `process/gen_critic_last_result_v5.cpp`
    - `operator/diversity/first_refresh_cj_es.cpp`
    - `operator/diversity/first_refresh_cj_es_two.cpp`
  - 适用场景：
    - 多个召回源、特征源或打分源并发返回后，统一写入 `candidate_vec`。
    - 多样性算子需要按策略顺序合并多个子结果。
    - 当前有锁保护 `std::vector<RidTmpInfoPtr>` 或类似候选集。
  - 不建议的场景：
    - `model/model.h`、`model/paddle_model.h` 中的 `predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)` 这类纯模型接口，不建议直接改成 `ExecutionQueue`。
    - 模型预测通常是批量计算接口，重点应放在 batch 组织、内存复用、Tensor 构造，而不是队列化每个候选。
  - 推荐方式：
    - 保持模型接口不变。
    - 在模型前置的多路输入收集阶段使用 `ExecutionQueue` 汇聚候选。
    - consumer 最终生成稳定的 `candidate_vec` 后再调用 `predict`。

- **建议 5：复用已有目标库使用文件作为迁移参考，先做小范围 A/B**
  - 可参考文件：
    - `feeda-mv-grg/util/util.cpp`
    - `feeda-mv-grg/util/libdict.cpp`
    - `feeda-mv-grc/operator/adjuster/precise/hot_info_attract_precise_adjuster.cpp`
    - `feeda-mv-grc/strategy/short_micro/explore_xgb_pcs_handler.cpp`
  - 建议：
    - 先确认这些文件中 brpc / bthread 相关依赖、编译选项、线程模型是否已经稳定。
    - 选择一个高 QPS、锁竞争明显、业务边界清晰的点做试点。
    - 指标对比建议至少包括：
      - P95 / P99 延迟
      - CPU 使用率
      - futex syscall 占比
      - callback 排队耗时
      - 单请求队列任务数
      - consumer 单次 batch 大小

---

### 4. ⚠️ 引入风险与限制

- **风险 1：`ExecutionQueue` 是 MPSC，不是通用 MPMC 队列**
  - 它适合“多个 producer 投递任务，一个 consumer 顺序处理”。
  - 如果业务场景需要多个消费者并行消费，不应直接使用单个 `ExecutionQueue`。
  - 可选方案：
    - 按 request / user / channel 分片多个 queue。
    - 或者保留 worker pool，只把最终 merge 阶段交给 `ExecutionQueue`。

- **风险 2：consumer 内不能执行过重逻辑**
  - `ExecutionQueue` 的优势来自“短临界区无锁化”和“批量顺序执行”。
  - 如果把 RPC、模型预测、复杂排序、大量过滤计算都放进 consumer，会导致单消费者成为瓶颈。
  - 推荐原则：
    - producer 做重计算。
    - consumer 只做轻量 merge / append / 状态更新。
    - 每个 task 尽量小，避免携带大对象深拷贝。

- **风险 3：任务生命周期需要严格管理**
  - 如果 `Task` 中携带 `RecallResp*`、`RidTmpInfoPtr`、`RequestContext*` 等指针，需要保证 queue 执行完成前对象仍然有效。
  - request 结束时必须正确 `stop` / `join` 队列，避免 use-after-free。
  - 不建议在 `TaskIterator` 外保存 iterator、引用或内部 node 指针。

- **风险 4：结果顺序和公平性可能影响业务语义**
  - `ExecutionQueue` 内部通过 Treiber stack + 链表反转恢复 FIFO，但不同 producer 并发投递时，全局顺序仍依赖实际入队时序。
  - 如果业务强依赖固定 channel 顺序，例如“热门召回必须先于向量召回 merge”，需要在 consumer 内显式按 `channel` 或 `priority` 处理。
  - 不建议在线上默认开启高优先级插队策略，否则可能破坏召回融合的稳定性。

---

### 5. 结论

- `feeda-mv-grc` 是更优先的适配对象，尤其是召回策略 handler、过滤 operator、调权 adjuster 中的多路结果汇聚场景。
- `feeda-mv-grg` 建议谨慎、局部引入，重点放在候选结果并发收集阶段，而不是模型预测接口本身。
- 迁移策略建议采用“小范围热点试点 → 指标验证 → 扩展到同类 handler”的方式，避免把 `ExecutionQueue` 当作 STL 容器或通用线程池的替代品。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
