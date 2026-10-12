# 06-mimalloc-segmented-allocator.md

## mimalloc · 分段式内存分配器

### 设计思想

mimalloc 是微软开发的高性能内存分配器，核心设计哲学是"小而快"。不同于 jemalloc 的 arena 分区和 tcmalloc 的线程缓存，mimalloc 采用**分段式内存管理**，将内存分为固定大小的段（segment），每个段内部分为固定大小的页（page）。这种设计减少了锁竞争和碎片化，特别适合多线程环境下的短生命周期对象分配。其目标是在保持低内存开销的同时，提供接近最优的分配性能。

### 核心实现

```c
// 关键数据结构：段（segment）和页（page）的层次结构
typedef struct mi_segment_s {
  size_t          segment_size;    // 段大小（通常 4MB）
  size_t          used;            // 已使用字节数
  mi_page_t*      pages;           // 页数组
  struct mi_segment_s* next;       // 链表下一个段
} mi_segment_t;

typedef struct mi_page_s {
  uint8_t*        free;            // 空闲块链表
  size_t          block_size;      // 块大小（8B,16B,32B,...）
  size_t          capacity;        // 页容量（块数）
  size_t          reserved;        // 预留块数
  struct mi_page_s* next;          // 同大小页链表
} mi_page_t;

// 分配核心：线程本地缓存 + 段分配
void* mi_malloc(size_t size) {
  mi_heap_t* heap = mi_heap_get_default();
  mi_page_t* page = mi_page_find(heap, size);  // 查找合适页
  if (!page) {
    page = mi_page_alloc(heap, size);          // 分配新页
  }
  return mi_page_malloc(page);                 // 从页中分配块
}
```

mimalloc 的核心创新在于**三级分配策略**：
1. **线程本地缓存**（thread-local free lists）处理高频小对象
2. **段内页分配**中等大小对象
3. **直接 mmap** 大对象

每个线程维护自己的页列表，减少全局锁竞争。段采用**惰性提交**策略，只在需要时提交物理内存。

### 性能优化原理

- **无锁线程本地分配**：每个线程有自己的空闲列表，小对象分配完全无锁，避免全局锁竞争。
- **固定大小页**：相同大小的对象分配到同一页，提高缓存局部性，减少内存碎片。
- **段内内存复用**：释放的内存优先在同一段内重用，避免跨段碎片和频繁的系统调用。
- **硬件预取优化**：分配器布局考虑 CPU 缓存行对齐，减少 false sharing。
- **安全模式（MI_SECURE）**：可选的守卫页、随机化分配、释放后清零，防止 use-after-free 和缓冲区溢出。

### 使用示例

```cpp
// 1. 直接链接使用
#include <mimalloc.h>

int main() {
    // 自动替换 malloc/free
    int* arr = (int*)mi_malloc(100 * sizeof(int));
    mi_free(arr);
    
    // 对齐分配
    void* aligned = mi_malloc_aligned(4096, 64);
    mi_free_aligned(aligned);
    
    return 0;
}

// 2. LD_PRELOAD 方式（无需修改代码）
// LD_PRELOAD=/usr/lib/libmimalloc.so ./your_program

// 3. 替换特定分配器
void* custom_alloc(size_t size) {
    return mi_malloc(size);
}

// 4. 性能统计
mi_stats_t stats;
mi_stats_reset();
// ...运行代码...
mi_stats_merge(&stats);
printf("峰值内存: %zu\n", stats.peak);
```

### 性能数据参考

根据官方基准测试（2026年）：
- **小对象分配**：比 jemalloc 快 1.5-2倍（<256B）
- **多线程扩展性**：32线程下比 jemalloc 快 3倍
- **内存开销**：比 glibc malloc 低 10-15%，碎片率减少 40%
- **Redis 测试**：QPS 提升 7%，内存使用减少 12%

### 与 jemalloc 对比

| 特性 | mimalloc | jemalloc |
|------|----------|----------|
| 设计哲学 | 小而快，低开销 | 可扩展，多线程优化 |
| 核心结构 | 分段式 + 页 | Arena 分区 + slab |
| 线程竞争 | 线程本地页，几乎无锁 | 线程缓存 + arena 锁 |
| 内存碎片 | 段内复用，碎片少 | slab 分配，碎片可控 |
| 适用场景 | 短生命周期对象，多线程 | 长期运行服务，内存池 |
| 安全特性 | MI_SECURE（可选） | 较少安全特性 |

### 生产环境注意事项

1. **版本兼容性**：3.3.0+ 版本在多线程环境下可能出现段错误（issue #1287），建议使用 3.2.8 或启用 MI_SECURE。
2. **安全模式开销**：MI_SECURE=ON 会增加约 5-10% 的性能开销，但提供更好的内存安全。
3. **大对象处理**：超过 1MB 的对象直接使用 mmap，性能可能略低于 jemalloc 的 arena 策略。
4. **与 jemalloc 共存**：可以通过环境变量控制，如 `MIMALLOC_SHOW_STATS=1` 调试分配情况。

### 推荐使用场景

1. **推荐服务**：短生命周期请求对象，适合 mimalloc 的低延迟分配。
2. **brpc 服务**：RPC 请求/响应对象的快速分配。
3. **ng-framework 计算图**：中间结果的临时分配。
4. **Protobuf 反序列化**：临时对象的快速分配和释放。

### 源码分析要点

1. **段管理**：`src/segment.c` 中的段分配和回收逻辑。
2. **页分配**：`src/page.c` 中的页查找和分配策略。
3. **线程本地**：`src/init.c` 中的线程初始化和管理。
4. **安全模式**：`src/secure.c` 中的安全检查实现。

### 参考链接

- [GitHub 仓库](https://github.com/microsoft/mimalloc)
- [官方文档](https://microsoft.github.io/mimalloc/)
- [性能基准](https://github.com/microsoft/mimalloc#performance)
- [与 jemalloc 对比](https://github.com/microsoft/mimalloc#comparison-with-other-allocators)
---

## 七、业务代码库适配分析
> **分析时间**：2026-05-26T19:01:07.968820
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：mimalloc 分段式内存分配器

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 和 `feeda-mv-grc` 两个业务代码库中均已发现 mimalloc 或目标分配器相关使用痕迹，各有 10 个文件涉及，说明业务侧并非完全零基础引入，已有局部接入经验可复用。  
- 两个代码库中 `std::vector`、`std::string`、`std::unordered_map` 使用规模很大，尤其是 `feeda-mv-grc` 中 `std::vector` 出现 8330 次、`std::string` 出现 7099 次、`std::unordered_map` 出现 2799 次，说明业务中存在大量动态内存分配场景。对于推荐、召回、特征计算、Protobuf 反序列化、请求级临时对象等短生命周期对象密集场景，mimalloc 具备较高迁移收益。

- 综合来看，mimalloc 更适合作为**进程级 allocator 替换**或**局部热点模块 allocator 优化**引入，而不是大规模改写 STL 容器代码。优先建议通过链接替换或 `LD_PRELOAD` 方式进行灰度验证，再针对热点文件中的高频临时容器、请求级对象池、召回候选集构建逻辑做专项优化。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现目标库相关使用：10 个文件，当前扫描列出的代表文件包括：
  - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
  - `data/news_info.h`
  - `operator/diversity/first_refresh_cj_es_two.cpp`
  - `operator/diversity/first_refresh_push_cate_v2_soft_rule.cpp`
  - `process/history_interest_info_function.cpp`

- STL 动态分配容器使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型场景：
  - `model/model.h` 中 `predict(std::vector<RidTmpInfoPtr>& candidate_vec, uint32_t pos)` 使用候选集向量作为模型预测输入。
  - `model/paddle_model.h` 中 `predict`、`predict_with_tensor_input` 继续传递 `std::vector<RidTmpInfoPtr>& candidate_vec`，说明序列生成链路中候选对象集合会跨模型、特征、排序阶段传递。
  - `process/pk_generate_candidate_nid_emb_function_v4.cpp`、`process/history_interest_info_function.cpp` 这类处理逻辑通常涉及请求级候选集、历史兴趣、embedding 特征等短生命周期对象，适合评估 mimalloc 对分配延迟和碎片率的改善。

- 初步判断：
  - `feeda-mv-grg` 的 STL 容器规模中等偏大，且业务形态偏推荐序列生成，存在大量请求级临时对象。
  - 已有目标库相关文件可作为迁移参考，建议先在序列生成主链路、候选集构建、历史兴趣处理模块做小流量压测。

#### feeda-mv-grc：召回汇聚服务

- 已发现目标库相关使用：10 个文件，当前扫描列出的代表文件包括：
  - `processor/new_adjust/precise_score_init_first_refresh.cpp`
  - `operator/adjuster/precise/newfusion_dongtai_manyi_precise_adjuster.cpp`
  - `processor/compute_readlist_surprise_info.cpp`
  - `operator/adjuster/sketchy/ltr_cp_adjuster.cpp`
  - `processor/attention_score.cpp`

- STL 动态分配容器使用规模：
  - `std::vector`：8330 次，分布在 1258 个文件
  - `std::string`：7099 次，分布在 1219 个文件
  - `std::unordered_map`：2799 次，分布在 633 个文件

- 典型场景：
  - `user_data/pcs_precise_parallel_commented.cpp` 中存在大量 `::std::vector<double>` 特征数组，例如 `yitiao_latest_tgi_fea`、`yitiao_3days_tgi_fea`、`ertiao_latest_dur_fea`。
  - 同文件中还存在 `::std::vector<::std::string>` 特征 key 列表，例如 `yitiao_latest_tgi_fea_key`、`yitiao_3days_tgi_fea_key`。
  - `processor/attention_score.cpp`、`processor/compute_readlist_surprise_info.cpp`、`processor/new_adjust/precise_score_init_first_refresh.cpp` 等处理器模块很可能在每次请求内构造特征、候选结果、打分上下文，属于 mimalloc 更容易受益的短生命周期分配场景。

- 初步判断：
  - `feeda-mv-grc` 的容器使用规模显著高于 `feeda-mv-grg`，尤其是召回汇聚、精排调整、注意力分计算等模块，存在更大的 allocator 优化空间。
  - 建议将 `feeda-mv-grc` 作为 mimalloc 性能验证的重点代码库，优先通过进程级替换观察 P99 延迟、CPU 使用率、RSS、内存碎片和 QPS 变化。

---

### 3. 💡 适用性评估与建议

- **建议一：优先采用进程级替换验证，避免大规模侵入式改造**
  - 对 `feeda-mv-grg` 和 `feeda-mv-grc` 均建议优先使用链接替换或 `LD_PRELOAD` 方式接入 mimalloc。
  - 适合先在召回、推荐、序列生成服务的压测环境开启，例如：
    - `feeda-mv-grg`：重点观察 `process/pk_generate_candidate_nid_emb_function_v4.cpp`、`process/history_interest_info_function.cpp`
    - `feeda-mv-grc`：重点观察 `processor/new_adjust/precise_score_init_first_refresh.cpp`、`processor/attention_score.cpp`
  - 这样可以直接覆盖 `std::vector`、`std::string`、`std::unordered_map` 背后的默认 `malloc/free`，无需修改大量业务代码，迁移成本最低。

- **建议二：以已有目标库使用文件作为参考样板，沉淀统一接入方式**
  - 两个代码库均已发现 10 个目标库相关文件，说明已有接入经验。
  - `feeda-mv-grg` 可参考：
    - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
    - `data/news_info.h`
    - `operator/diversity/first_refresh_cj_es_two.cpp`
    - `operator/diversity/first_refresh_push_cate_v2_soft_rule.cpp`
    - `process/history_interest_info_function.cpp`
  - `feeda-mv-grc` 可参考：
    - `processor/new_adjust/precise_score_init_first_refresh.cpp`
    - `operator/adjuster/precise/newfusion_dongtai_manyi_precise_adjuster.cpp`
    - `processor/compute_readlist_surprise_info.cpp`
    - `operator/adjuster/sketchy/ltr_cp_adjuster.cpp`
    - `processor/attention_score.cpp`
  - 建议梳理这些文件中的接入方式，统一为一套工程规范，例如：
    - 是否通过 Bazel/CMake 链接 `libmimalloc`
    - 是否允许直接调用 `mi_malloc/mi_free`
    - 是否开启 `MIMALLOC_SHOW_STATS`
    - 是否允许在业务代码中混用 `malloc/free` 与 `mi_malloc/mi_free`

- **建议三：重点优化请求级临时容器密集场景**
  - `feeda-mv-grc` 中 `user_data/pcs_precise_parallel_commented.cpp` 存在大量临时特征向量和特征 key 列表：
    - `::std::vector<double> yitiao_latest_tgi_fea`
    - `::std::vector<::std::string> yitiao_latest_tgi_fea_key`
    - `::std::vector<::std::string> yitiao_3days_tgi_fea_key`
  - 如果这些对象在每次请求中频繁构造和销毁，mimalloc 的线程本地分配和页内复用可以降低分配锁竞争和碎片。
  - 建议压测中单独关注该类模块的：
    - 单请求分配次数
    - P95/P99 延迟
    - CPU cycles
    - RSS 峰值
    - allocator stats 中 small object 分配占比

- **建议四：候选集、模型预测输入链路适合做专项收益评估**
  - `feeda-mv-grg` 中 `model/model.h` 和 `model/paddle_model.h` 的 `std::vector<RidTmpInfoPtr>& candidate_vec` 是典型候选集传递结构。
  - 相关链路如果存在频繁的候选对象构造、扩容、过滤、去重、排序，mimalloc 对小对象和中等对象分配会有较高收益。
  - 建议结合以下文件做链路级性能对比：
    - `model/model.h`
    - `model/paddle_model.h`
    - `process/pk_generate_candidate_nid_emb_function_v4.cpp`
    - `operator/diversity/first_refresh_cj_es_two.cpp`
  - 如果压测发现 `std::vector` 扩容明显，也可以同时检查是否需要补充 `reserve()`，mimalloc 负责降低分配成本，但不能替代容器容量规划。

- **建议五：对 `std::unordered_map` 密集模块重点观察碎片和 CPU**
  - 两个代码库中 `std::unordered_map` 使用量较大：
    - `feeda-mv-grg`：734 次
    - `feeda-mv-grc`：2799 次
  - `std::unordered_map` 节点分配通常是大量小对象离散分配，容易导致 allocator 压力和缓存局部性问题。
  - 对于 `processor/compute_readlist_surprise_info.cpp`、`processor/attention_score.cpp`、`process/history_interest_info_function.cpp` 这类可能使用 map 做特征、计数、索引的模块，建议在 mimalloc 替换前后对比：
    - 哈希表构建耗时
    - 请求内临时节点分配次数
    - 内存碎片率
    - 多线程下 allocator lock contention

---

### 4. ⚠️ 引入风险与限制

- **风险一：需要避免跨 allocator 分配与释放**
  - 如果业务中直接使用 `mi_malloc` 分配，则必须使用 `mi_free` 释放，不能与 `free`、`delete`、自定义内存池混用。
  - 对于已有自定义 allocator、对象池、第三方库内部分配器的模块，需要确认分配释放边界。
  - 建议优先进程级替换，而不是在业务代码中零散引入 `mi_malloc/mi_free`。

- **风险二：版本选择需要谨慎**
  - 技术笔记中提到 mimalloc `3.3.0+` 在多线程环境下可能存在段错误风险，建议生产环境优先评估稳定版本，例如 `3.2.8`。
  - 在 `feeda-mv-grc` 这类高并发召回汇聚服务中，需要重点做长稳压测和异常流量压测，避免 allocator 变更引入偶发 crash。

- **风险三：大对象场景收益不一定明显**
  - mimalloc 对小对象、短生命周期对象、多线程分配更友好。
  - 如果某些模块主要分配大块 tensor、embedding buffer、模型输入输出缓冲区，超过 mimalloc 大对象阈值后可能直接走 `mmap`，收益不一定优于 jemalloc 或现有分配器。
  - 对 `model/paddle_model.h` 相关模型推理链路，需要区分候选集对象分配和大块 tensor 分配，分别评估。

- **风险四：仅替换 allocator 不能解决所有 STL 性能问题**
  - 对 `std::vector` 高频扩容、`std::string` 反复拼接、`std::unordered_map` rehash 等问题，mimalloc 只能降低底层分配成本，不能替代业务侧容器优化。
  - 例如 `user_data/pcs_precise_parallel_commented.cpp` 中固定长度特征数组如果每次请求都动态构造，除了使用 mimalloc，还应考虑：
    - 使用 `reserve()`
    - 固定长度场景改为 `std::array`
    - 静态特征 key 列表改为 `static const std::array<std::string_view, N>`
  - 因此建议 allocator 替换与容器使用优化同时推进。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
