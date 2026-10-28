# #15 · tcmalloc::ThreadCache — 线程本地分配缓存

> **仓库**: [google/tcmalloc](https://github.com/google/tcmalloc) · `tcmalloc/thread_cache.h`  
> **定位**: 每线程独立的内存分配缓存，malloc/free 快速路径完全无锁，避免全局堆竞争

---

## 一句话价值

**线程本地 FreeList + TLS 快速读取 + 动态容量调节**——每次 malloc/free 在 99% 的情况下只碰自己的 per-thread 链表，不需要任何锁，延迟在纳秒级。

---

## 整体架构

```
应用层 malloc()
    ↓
ThreadCache::Allocate(size_class)   ← 快速路径，纯内联，无锁
    ├── list_[size_class].TryPop()  ← 命中：直接返回，~5ns
    └── FetchFromTransferCache()   ← 未命中：去 TransferCache 批量拉取
              ↓
          CentralFreeList / PageHeap  ← 慢速路径，有锁，但极少触发
```

TCMalloc 三层架构：**ThreadCache（无锁）→ TransferCache（轻锁）→ PageHeap（全局锁）**  
ThreadCache 是吸收 99% 流量的第一层。

---

## 核心数据结构

### ThreadCache 内存布局（热字段优先）

```cpp
class ABSL_CACHELINE_ALIGNED ThreadCache {  // 64B cache line 对齐
  FreeList list_[kNumClasses];  // 按 size class 索引的链表数组，最热字段
  size_t size_;      // 当前 ThreadCache 总占用字节
  size_t max_size_;  // 阈值，超过则触发 Scavenge()
  pthread_t tid_;
  // ...
};
```

### FreeList（每个 size class 一个）

```cpp
class FreeList : public LinkedList {
  uint32_t lowater_;        // 历史最低水位，用于判断链表是否"被频繁消耗"
  uint32_t max_length_;     // 动态上限，根据使用模式自动调整
  uint32_t length_overages_; // 超过 max_length_ 的次数，触发缩容
};
```

`max_length_` 不是固定的，是**自适应**的：
- 链表频繁被消耗到空 → `max_length_` 增大（减少去 TransferCache 的次数）
- 链表长期积压 → `max_length_` 减小（节省内存）

---

## 快速路径：Allocate() 内联分析

```cpp
inline ABSL_ATTRIBUTE_ALWAYS_INLINE void* ThreadCache::Allocate(size_t size_class) {
    const size_t allocated_size = tc_globals.sizemap().class_to_size(size_class);

    FreeList* list = &list_[size_class];
    void* ret;
    if (ABSL_PREDICT_TRUE(list->TryPop(&ret))) {  // 99% 走这里
        size_ -= allocated_size;
        return ret;  // 全程：读TLS → 数组寻址 → 链表pop → 返回，无任何锁
    }

    return FetchFromTransferCache(size_class, allocated_size);  // 慢路径
}
```

**为什么 TryPop 这么快**：
```cpp
// LinkedList::TryPop 本质上就是:
bool TryPop(void** ret) {
    if (head_ == nullptr) return false;
    *ret = head_;
    head_ = *reinterpret_cast<void**>(head_);  // 从对象头部读取 next 指针
    length_--;
    return true;
}
```

对象本身的前 8 字节存 next 指针——**空闲对象就是链表节点**，零额外内存开销。

---

## 快速路径：Deallocate() 的位运算技巧

```cpp
inline void ABSL_ATTRIBUTE_ALWAYS_INLINE
ThreadCache::Deallocate(void* ptr, size_t size_class) {
    FreeList* list = &list_[size_class];
    size_ += tc_globals.sizemap().class_to_size(size_class);
    ssize_t size_headroom = max_size_ - size_ - 1;

    list->Push(ptr);
    ssize_t list_headroom =
        static_cast<ssize_t>(list->max_length()) - list->length();

    // 关键位运算：两个条件合并成一次分支
    if ((list_headroom | size_headroom) < 0) {
        DeallocateSlow(ptr, list, size_class);  // 链表满 OR 总size超限
    }
}
```

`(a | b) < 0` 等价于 `a < 0 || b < 0`（符号位 OR）——**把两次条件判断压成一次**，减少分支预测失败概率。这是 Google 在热路径代码里的经典优化手法。

---

## TLS 双轨机制：__thread + pthread_key

```cpp
// 双重 TLS：两套机制同时维护同一个指针
ABSL_CONST_INIT static thread_local ThreadCache* thread_local_data_
    ABSL_ATTRIBUTE_INITIAL_EXEC;   // __thread，读取比 pthread_getspecific 快

static pthread_key_t heap_key_;    // pthread key，用于线程退出时的析构回调
```

**为什么需要两套**：
- `__thread` (`ABSL_ATTRIBUTE_INITIAL_EXEC`)：编译器直接生成 `mov %fs:offset, %rax` 指令，极快，但没有析构回调
- `pthread_key`：有析构回调 `DestroyThreadCache()`，线程退出时归还内存给 TransferCache

`ABSL_ATTRIBUTE_INITIAL_EXEC` 代价：使用 initial-exec TLS model，**不能被 `dlopen()` 加载**。TCMalloc 认为 malloc 库不应该被 dlopen，所以值得换这个速度。

---

## 动态容量调节：Scavenge + Steal

### Scavenge：缩容

```
触发条件：size_ > max_size_
动作：遍历 list_[]，对每个链表把 length - lowater 的对象归还给 TransferCache
结果：ThreadCache 缩回到低水位，lowater 反映的是真实活跃需求
```

### Steal：跨线程抢配额

```cpp
void ThreadCache::IncreaseCacheLimit() {
    // 轮询 thread_heaps_ 链表，找一个 size_ 最小的线程
    // 从那个线程的 max_size_ 里偷 kStealAmount 给自己
    // 全局 per_thread_cache_size_ 不变，只是重新分配
}
```

需要持 `threadcache_lock_`，**但这是慢路径**，只有 ThreadCache 不够用时才触发，正常分配完全不走这里。

---

## size class 映射

TCMalloc 把所有内存请求映射到约 88 个 size class（如 8, 16, 32, 48...字节）：

```cpp
// malloc(17 字节) → size_class=3 (对应32字节)
// malloc(64 字节) → size_class=6 (对应64字节)
size_t size_class = tc_globals.sizemap().SizeClass(requested_size);
```

`list_[size_class]` 直接数组下标，O(1)，没有哈希，没有查找。

---

## 与 jemalloc tcache 对比

| 特性 | tcmalloc::ThreadCache | jemalloc tcache |
|------|-----------------------|------------------|
| TLS 读取 | `__thread` initial-exec | `__thread` |
| 容量调节 | 动态 steal/scavenge | 固定 ncached_max |
| 回收时机 | 超限即 scavenge | GC 周期触发 |
| size class 数 | ~88 | ~36 (small) |
| 跨线程归还 | 归还到 TransferCache | 归还到 extent |
| dlopen 支持 | ❌ (initial-exec) | ✅ |

---

## 关键文件索引

```
tcmalloc/
├── thread_cache.h          # ThreadCache + FreeList 定义（本文重点）
├── thread_cache.cc         # Scavenge / IncreaseCacheLimit 实现
├── internal/linked_list.h  # FreeList 底层链表
├── transfer_cache.h        # ThreadCache 的上游，批量转移对象
├── static_vars.h           # tc_globals（包含 sizemap）
└── common.h                # kNumClasses, kStealAmount 等常量
```

---

*自动生成 · 2026-05-19 · OpenClaw Daily Task*

---

## 七、业务代码库适配分析
> **分析时间**：2026-05-31T19:09:49.363836
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 1. 分析摘要

- 本次扫描的两个业务代码库 `feeda-mv-grg` 与 `feeda-mv-grc` 中，均已发现少量目标库相关使用痕迹，各自约 10 个文件，说明代码库并非完全没有接触过该技术，可以优先复用已有接入方式、编译链接方式和运行参数作为参考。

- 从 `std::vector`、`std::string`、`std::unordered_map` 的使用规模看，两个代码库都存在大量小对象、变长容器、哈希表和临时字符串分配场景。  
  其中 `feeda-mv-grc` 的容器使用规模显著更大，`std::vector` 超过 8000 次、`std::string` 超过 7000 次、`std::unordered_map` 超过 2800 次，属于更适合优先评估 TCMalloc ThreadCache 收益的代码库。  
  由于 TCMalloc 主要通过替换全局 `malloc/free/new/delete` 生效，业务代码通常不需要大规模改写容器类型，迁移成本相对可控，适合作为服务级内存分配器优化方案引入。

---

## 2. 代码库详情

### 2.1 `feeda-mv-grg`：序列生成服务

- 已发现目标库相关使用：约 10 个文件，代表文件包括：
  - `operator/diversity/long_interest_soft_rule.cpp`
  - `operator/diversity/mcv_yx_manju_tgi_soft_rule.cpp`
  - `process/cal_history_prefer.cpp`
  - `process/post_mark.cpp`
  - `operator/diversity/new_hot_diversity_rule.cpp`

- 标准容器使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型场景：
  - `model/model.h`
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
    `candidate_vec` 作为预测链路中的核心候选集容器，可能在请求级处理过程中频繁构造、扩容、遍历。

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

- 初步判断：
  - `feeda-mv-grg` 的核心链路中存在较多候选集、规则过滤、排序和预测相关容器操作。
  - 对于这类请求级短生命周期对象，TCMalloc ThreadCache 能够降低 `new/delete`、`malloc/free` 的锁竞争和系统分配开销。
  - 如果当前服务线程数较高、QPS 较大，收益更明显。

---

### 2.2 `feeda-mv-grc`：召回汇聚服务

- 已发现目标库相关使用：约 10 个文件，代表文件包括：
  - `common/session_context.h`
  - `processor/sketchy_rank.cpp`
  - `processor/response_with_set2set.cpp`
  - `operator/adjuster/function_queue/xinre_function_queue.cpp`
  - `operator/adjuster/precise/author_rank_recall_flow_step.cpp`

- 标准容器使用规模：
  - `std::vector`：8382 次，分布在 1266 个文件
  - `std::string`：7107 次，分布在 1222 个文件
  - `std::unordered_map`：2828 次，分布在 636 个文件

- 典型场景：
  - `service/grc_http_service.cpp`
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    auto &all_vertex = graph_engine->get_vertexs_message(graph_name);
    for (int i = 0; i < all_vertex.size(); ++i) {
        for (auto &depend : all_vertex[i].depends) {
    ```
    该场景同时包含 `unordered_map`、`string`、`vector<int>`，容易产生大量小块内存分配。

  - `service/grc_http_service.cpp`
    ```cpp
    static std::vector<std::string> colors{"#FFB6C1", "#DC143C", "#DB7093", "#FF1493", "#FF00FF", "#800080",
                                           "#4B0082", "#7B68EE", "#0000FF", "#4169E1", "#778899", "#4682B4",
    ```
    静态配置型容器不一定是性能热点，但可以作为观察全局分配器替换后启动期和常驻内存变化的样本。

  - `service/grc_http_service.cpp`
    ```cpp
    std::string resp_str;

    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    const std::string *sub_access_off_vec_str = cntl->http_request().uri().GetQuery("off");
    const std::string *sub_access_on_vec_str = cntl->http_request().uri().GetQuery("on");
    ```
    HTTP 请求解析、字符串拆分、响应拼接通常会产生较多短生命周期字符串和 vector 扩容，是适合观察 TCMalloc ThreadCache 收益的场景。

- 初步判断：
  - `feeda-mv-grc` 容器使用规模明显大于 `feeda-mv-grg`，且召回汇聚服务通常存在多路召回、结果合并、去重、过滤、排序等逻辑，内存分配压力更高。
  - 建议优先在 `feeda-mv-grc` 上进行 TCMalloc 灰度接入和性能对比。
  - `common/session_context.h`、`processor/sketchy_rank.cpp`、`processor/response_with_set2set.cpp` 等请求上下文和处理器文件，可以作为重点排查对象。

---

## 3. 💡 适用性评估与建议

- **建议一：优先在 `feeda-mv-grc` 服务级接入 TCMalloc，而不是逐个替换容器类型**
  - 适用文件：
    - `service/grc_http_service.cpp`
    - `common/session_context.h`
    - `processor/sketchy_rank.cpp`
    - `processor/response_with_set2set.cpp`
  - 原因：
    - `feeda-mv-grc` 中 `std::vector`、`std::string`、`std::unordered_map` 使用量非常大，手工替换容器成本高、风险大。
    - TCMalloc 可以通过替换全局 `malloc/free/new/delete` 的方式对 STL 容器自动生效。
    - 对 `std::unordered_map<std::string, std::vector<int>>` 这类组合容器，ThreadCache 对小对象节点分配、字符串 buffer 分配、vector 扩容分配都有潜在收益。
  - 建议动作：
    - 先在测试环境通过链接 `tcmalloc` 或运行时预加载方式验证：
      ```bash
      LD_PRELOAD=libtcmalloc.so ./grc_service
      ```
      或在构建系统中显式链接：
      ```bash
      -ltcmalloc
      ```
    - 对比指标：
      - P50 / P99 / P999 延迟
      - CPU 使用率
      - QPS
      - RSS
      - malloc/free 调用耗时
      - 线程数较高时的锁竞争情况

- **建议二：将 `service/grc_http_service.cpp` 作为首个热点验证点**
  - 适用场景：
    - HTTP 参数解析
    - `std::string` 临时对象构造
    - `std::vector<std::string>` 临时列表
    - `std::unordered_map<std::string, std::vector<int>>` 构建
  - 典型代码：
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    ```
    ```cpp
    std::string resp_str;
    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    ```
  - 优化方向：
    - 首先通过 TCMalloc 降低频繁小对象分配成本。
    - 如果接入 TCMalloc 后仍有明显分配热点，再进一步做业务层优化：
      - 对 `std::vector` 提前 `reserve()`；
      - 对 `std::unordered_map` 提前 `reserve()`；
      - 响应字符串 `resp_str` 提前估算长度并 `reserve()`。
  - 说明：
    - TCMalloc ThreadCache 能减少分配器开销，但不能消除容器扩容次数。
    - 因此推荐组合优化：**全局分配器优化 + 局部 reserve 优化**。

- **建议三：在 `feeda-mv-grg` 的预测和候选集链路验证收益**
  - 适用文件：
    - `model/model.h`
    - `model/paddle_model.h`
    - `process/cal_history_prefer.cpp`
    - `process/post_mark.cpp`
  - 典型代码：
    ```cpp
    virtual int predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos) = 0;
    ```
    ```cpp
    int predict_with_tensor_input(std::vector<RidTmpInfoPtr>& candidate_vec,
                general_predict::PredictSample* predict_sample = nullptr,
                bool is_from_cube = true) const {
        return predict<ModelDependInput>(candidate_vec, predict_sample, is_from_cube);
    }
    ```
  - 适用原因：
    - 推荐、召回、排序链路中的 `candidate_vec` 通常是请求级核心数据结构。
    - 如果候选集在不同阶段反复构造、过滤、复制、扩容，会产生较高分配压力。
    - TCMalloc 对多线程下短生命周期对象回收较友好，ThreadCache 命中时无需全局锁。
  - 建议动作：
    - 在压测中重点观察预测链路耗时。
    - 对比接入前后：
      - 单请求内存分配次数；
      - `predict()` 调用耗时；
      - 规则处理阶段 P99 延迟；
      - 线程数提升时性能是否更稳定。

- **建议四：已有目标库使用文件可作为接入参考，优先统一接入方式**
  - `feeda-mv-grg` 中可参考：
    - `operator/diversity/long_interest_soft_rule.cpp`
    - `operator/diversity/mcv_yx_manju_tgi_soft_rule.cpp`
    - `operator/diversity/new_hot_diversity_rule.cpp`
  - `feeda-mv-grc` 中可参考：
    - `common/session_context.h`
    - `processor/sketchy_rank.cpp`
    - `processor/response_with_set2set.cpp`
    - `operator/adjuster/function_queue/xinre_function_queue.cpp`
  - 建议动作：
    - 检查这些文件中目标库的使用方式：
      - 是仅包含头文件；
      - 还是通过构建系统链接；
      - 是否依赖特定宏；
      - 是否已经有线上运行经验。
    - 将接入方式沉淀到统一的 Bazel/CMake/Makefile 配置中，避免不同模块各自引入不同 allocator 或不同版本。
  - 重点：
    - 分配器类基础设施不建议模块级随意切换。
    - 应该以服务进程为粒度统一接入，避免同一进程中多个 allocator 混用导致释放路径不一致。

- **建议五：对 `std::unordered_map` 密集场景做专项验证**
  - 适用文件：
    - `service/grc_http_service.cpp`
    - `processor/sketchy_rank.cpp`
    - `operator/adjuster/precise/author_rank_recall_flow_step.cpp`
  - 原因：
    - `std::unordered_map` 节点分配通常是小对象、离散分配，对通用分配器压力较大。
    - TCMalloc 的 size class + ThreadCache 对这类固定大小节点分配有较好适配性。
  - 建议动作：
    - 对热点 map 增加 `reserve()`：
      ```cpp
      std::unordered_map<std::string, std::vector<int>> depend_map;
      depend_map.reserve(expected_size);
      ```
    - 使用 TCMalloc 后观察：
      - rehash 次数是否仍然偏高；
      - bucket 分配是否成为热点；
      - RSS 是否上升明显。
    - 如果 map 生命周期严格限定在请求内，可以进一步评估对象池或 `std::pmr`，但这属于二阶段优化，不建议作为第一步迁移。

---

## 4. ⚠️ 引入风险与限制

- **风险一：TCMalloc 的 TLS initial-exec 模型对 `dlopen()` 场景不友好**
  - ThreadCache 为了极致快速读取 TLS，使用了类似 `__thread` + `initial-exec` 的模型。
  - 这意味着如果业务中存在插件化加载、运行时 `dlopen()` 加载 allocator 或相关 so 的场景，可能存在兼容性风险。
  - 建议排查：
    - 服务是否通过插件方式加载业务 so；
    - allocator 是否在进程启动时就已链接；
    - 是否存在运行时切换 allocator 的设计。
  - 对 `feeda-mv-grc`、`feeda-mv-grg`，建议优先采用进程启动时静态链接或明确 `LD_PRELOAD`，不要在运行过程中动态加载。

- **风险二：ThreadCache 会提升局部缓存命中率，但也可能增加 RSS**
  - TCMalloc ThreadCache 会在每个线程本地缓存一批空闲对象。
  - 在线程数较多、请求峰值波动较大的服务中，短期 RSS 可能上升。
  - 需要关注：
    - 常驻内存；
    - 峰值 RSS；
    - 容器大量扩容后的内存是否及时归还；
    - 空闲线程是否仍持有较多缓存。
  - 建议在压测中同时观察延迟收益和内存成本，不要只看 CPU 或 QPS。

- **风险三：分配器替换必须保证分配和释放路径一致**
  - 不建议在同一进程中混用多个 allocator，例如一部分 so 使用系统 malloc，另一部分使用 TCMalloc，并跨边界传递需要释放的内存。
  - 风险场景包括：
    - A 模块 `new`，B 模块 `delete`；
    - 第三方库返回由其内部分配的指针，业务侧释放；
    - C 接口跨 so 传递 ownership。
  - 建议：
    - 以进程为单位统一 allocator；
    - 检查第三方库是否自带 jemalloc/tcmalloc；
    - 明确构建链接顺序，避免 allocator 符号被错误覆盖。

- **风险四：TCMalloc 不能替代业务层容器使用优化**
  - TCMalloc 解决的是分配器层面的锁竞争和小对象分配效率问题。
  - 对以下问题，仍需要业务代码优化：
    - `std::vector` 反复扩容；
    - `std::string` 多次拼接未 `reserve()`；
    - `std::unordered_map` 未 `reserve()` 导致频繁 rehash；
    - 大对象频繁拷贝；
    - 请求内临时容器过多。
  - 因此建议迁移路径为：
    - 第一阶段：服务级接入 TCMalloc，确认整体收益；
    - 第二阶段：定位热点文件，如 `service/grc_http_service.cpp`、`model/paddle_model.h`、`process/cal_history_prefer.cpp`；
    - 第三阶段：针对热点容器做 `reserve()`、复用、对象池或 `std::pmr` 优化。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
