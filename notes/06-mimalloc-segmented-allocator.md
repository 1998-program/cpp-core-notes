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