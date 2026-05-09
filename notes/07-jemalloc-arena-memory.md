# 07-jemalloc-arena-memory.md

## jemalloc · Arena 内存管理

### 设计思想

jemalloc 是 FreeBSD 默认内存分配器，后被 Facebook、Redis、RocksDB 等高性能系统广泛采用。其核心设计哲学是**减少锁竞争**和**内存碎片**。jemalloc 采用多层分配策略：线程缓存（tcache）→ arena → slab，通过 arena 分区将全局锁竞争分散到多个独立内存区域。每个线程绑定到特定 arena，相同大小的对象分配到相同 slab，这种设计在多线程环境下提供出色的扩展性。

### 核心实现

```c
// 关键数据结构：arena、bin、slab
typedef struct arena_s {
    malloc_mutex_t    lock;           // arena 锁
    size_t            nthreads;       // 绑定线程数
    bin_t             bins[NBINS];    // 大小分类 bin
    chunk_t*          chunks;         // chunk 链表
    struct arena_s*   next;           // arena 链表
} arena_t;

typedef struct bin_s {
    malloc_mutex_t    lock;           // bin 锁
    slab_t*           slabs_full;     // 满 slab
    slab_t*           slabs_nonfull;  // 非满 slab
    size_t            reg_size;       // 对象大小
} bin_t;

typedef struct slab_s {
    void*             start;          // slab 起始地址
    bitmap_t          free_map;       // 空闲位图
    size_t            nfree;          // 空闲对象数
    struct slab_s*    next;           // slab 链表
} slab_s;

// 分配路径：tcache → bin → arena → system
void* je_malloc(size_t size) {
    tcache_t* tcache = tcache_get();           // 获取线程缓存
    if (size <= TCACHE_MAX_SIZE) {
        return tcache_alloc(tcache, size);     // 线程缓存分配
    }
    
    arena_t* arena = arena_get();              // 获取绑定 arena
    bin_t* bin = arena_bin_for_size(arena, size);
    malloc_mutex_lock(&bin->lock);
    slab_t* slab = bin_slab_alloc(bin);        // slab 分配
    void* ptr = slab_alloc(slab);
    malloc_mutex_unlock(&bin->lock);
    return ptr;
}
```

jemalloc 采用**大小分类**策略，将对象按大小分组到不同 bin（如 8B、16B、32B、...、2KB），每个 bin 管理相同大小的 slab。slab 是从 chunk（通常 2MB）划分的连续内存区域，包含多个相同大小的对象。

### 性能优化原理

- **线程本地缓存（tcache）**：小对象分配完全无锁，减少 arena 锁竞争。
- **Arena 分区**：多个 arena 分散全局锁，每个线程绑定到特定 arena，减少锁争用。
- **Slab 分配**：相同大小对象集中存储，提高缓存局部性，减少内存碎片。
- **Chunk 复用**：释放的 chunk 加入空闲列表，避免频繁的 mmap/munmap 系统调用。
- **位图管理**：slab 使用位图跟踪对象状态，分配/释放 O(1) 复杂度。
- **大小对齐**：对象大小向上对齐到缓存行（通常 64B），减少 false sharing。

### 与 mimalloc 对比

| 特性 | jemalloc | mimalloc |
|------|----------|----------|
| 核心结构 | Arena + slab | 分段式 + 页 |
| 线程竞争 | tcache + arena 锁 | 线程本地页，几乎无锁 |
| 内存管理 | chunk（2MB）划分 | 段（4MB）划分 |
| 适用场景 | 长期运行服务，内存池 | 短生命周期对象，多线程 |
| 扩展性 | 优秀（多 arena） | 极佳（完全无锁） |
| 安全特性 | 较少 | MI_SECURE（可选） |

### 使用示例

```cpp
// 1. 编译链接
// gcc -o program program.c -ljemalloc

// 2. 环境变量控制
// export MALLOC_CONF="prof:true,lg_prof_sample:19"
// ./your_program

// 3. 编程接口
#include <jemalloc/jemalloc.h>

int main() {
    // 基本分配
    void* ptr = je_malloc(1024);
    je_free(ptr);
    
    // 对齐分配
    void* aligned = je_aligned_alloc(64, 4096);
    je_free(aligned);
    
    // 统计信息
    const char* stats = je_malloc_stats_print(NULL, NULL, NULL);
    printf("%s\n", stats);
    
    return 0;
}

// 4. Redis 集成示例（redis/src/zmalloc.c）
void *zmalloc(size_t size) {
    void *ptr = je_malloc(size + PREFIX_SIZE);
    if (!ptr) zmalloc_oom_handler(size);
    update_used_memory(size);
    return (char*)ptr + PREFIX_SIZE;
}
```

### 生产环境配置

```bash
# 1. 基础配置（减少锁竞争）
export MALLOC_CONF="narenas:4,tcache:true"

# 2. 内存统计（调试）
export MALLOC_CONF="stats_print:true"

# 3. 性能调优（根据 CPU 核心数）
export MALLOC_CONF="narenas:8,lg_tcache_max:13"

# 4. 内存分析
export MALLOC_CONF="prof:true,lg_prof_sample:19,prof_prefix:jeprof"
```

### 性能数据参考

- **多线程扩展性**：32线程下比 glibc malloc 快 5-10倍
- **内存碎片**：长期运行后碎片率比 glibc 低 60-70%
- **Redis 性能**：QPS 提升 15-20%，内存使用减少 25%
- **推荐服务场景**：对象池命中率 85%+，分配延迟降低 40%

### 推荐在线架构应用

1. **brpc 内存池**：替换默认分配器，RPC 请求/响应对象分配加速。
2. **ng-framework DAG**：中间计算结果缓存，减少重复分配。
3. **Protobuf 序列化**：消息对象池化，重用内存。
4. **FuncExecutor 任务**：任务对象快速分配/释放。

### 源码分析要点

1. **arena 管理**：`src/arena.c` 中的 arena 分配和绑定逻辑。
2. **bin/slab 分配**：`src/bin.c` 和 `src/slab.c` 中的对象分配策略。
3. **tcache 优化**：`src/tcache.c` 中的线程本地缓存实现。
4. **chunk 管理**：`src/chunk.c` 中的大内存块管理。

### 常见问题排查

1. **内存泄漏**：使用 `je_malloc_stats_print()` 或 `jeprof` 工具分析。
2. **锁竞争**：增加 `narenas` 参数（通常设置为 CPU 核心数 2-4倍）。
3. **碎片问题**：监控 `stats.allocated` 和 `stats.active` 比例。
4. **性能瓶颈**：启用 `prof:true` 进行性能分析。

### 参考链接

- [GitHub 仓库](https://github.com/jemalloc/jemalloc)
- [官方文档](http://jemalloc.net/jemalloc.3.html)
- [性能调优指南](https://github.com/jemalloc/jemalloc/wiki/Use-Case:-Redis)
- [与 tcmalloc 对比](https://github.com/jemalloc/jemalloc/wiki/Background)