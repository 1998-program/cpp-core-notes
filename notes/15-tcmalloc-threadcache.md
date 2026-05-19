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
