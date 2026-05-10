# 08-rocksdb-block-cache.md

## RocksDB::BlockCache · 分层缓存设计

### 设计思想

RocksDB 是 Facebook 基于 LevelDB 开发的高性能嵌入式键值存储引擎，广泛应用于分布式数据库和存储系统。其 BlockCache 模块采用**分层缓存策略**，将数据按访问频率和大小分层存储：内存 BlockCache → OS Page Cache → SSD/HDD。核心设计哲学是**空间换时间**，通过多级缓存减少磁盘 I/O，同时利用 SSD 的随机读性能优势。BlockCache 支持 LRU、Clock、自适应等多种淘汰算法，可根据工作负载动态调整缓存策略。

### 核心实现

```cpp
// 关键数据结构：BlockCache、LRUHandle、Shard
class BlockCache {
private:
    std::vector<CacheShard*> shards_;      // 分片数组
    size_t capacity_;                      // 总容量
    std::atomic<int64_t> usage_;           // 当前使用量
    
public:
    // 插入缓存块
    Status Insert(const Slice& key, void* value, size_t charge,
                  void (*deleter)(const Slice& key, void* value),
                  Handle** handle, Priority priority = Priority::LOW) {
        uint32_t hash = HashSlice(key);
        CacheShard* shard = GetShard(hash);
        return shard->Insert(key, hash, value, charge, deleter, handle, priority);
    }
    
    // 查找缓存块
    Handle* Lookup(const Slice& key) {
        uint32_t hash = HashSlice(key);
        CacheShard* shard = GetShard(hash);
        return shard->Lookup(key, hash);
    }
};

// LRU 缓存节点
struct LRUHandle {
    void* value;                          // 缓存值
    size_t charge;                        // 内存占用
    uint32_t hash;                        // 哈希值
    char key_data[1];                     // 内联键数据
    
    LRUHandle* next_hash;                 // 哈希表链表
    LRUHandle* next;                      // LRU 链表
    LRUHandle* prev;
    bool in_cache;                        // 是否在缓存中
    uint32_t refs;                        // 引用计数
    uint32_t flags;                       // 状态标志
};

// 缓存分片（减少锁竞争）
class CacheShard {
private:
    ShardedLRUCache lru_;                 // LRU 链表
    HandleTable table_;                   // 哈希表
    mutable port::Mutex mutex_;           // 分片锁
};
```

BlockCache 采用**分片哈希表 + LRU 链表**的双层结构，每个分片独立管理一部分缓存项，减少全局锁竞争。缓存项按优先级（HIGH/LOW）分类，HIGH 优先级项（如索引块）更不容易被淘汰。

### 性能优化原理

- **分片并发**：将缓存划分为多个独立分片，每个分片有自己的锁，减少线程竞争。
- **分层淘汰**：LRU 链表分为 hot/warm/cold 区域，根据访问频率调整位置，避免一次扫描淘汰过多热数据。
- **预取优化**：顺序扫描时预读相邻块到缓存，利用空间局部性。
- **压缩块缓存**：缓存解压后的数据块，避免重复解压开销。
- **自适应大小**：根据工作负载动态调整各优先级区域比例。
- **写时复制**：缓存块在写入时创建副本，避免读写竞争。

### 与 LevelDB Cache 对比

| 特性 | RocksDB BlockCache | LevelDB Cache |
|------|-------------------|---------------|
| 并发设计 | 分片哈希表，多锁 | 全局哈希表，单锁 |
| 淘汰算法 | LRU/Clock/自适应 | 简单 LRU |
| 优先级 | HIGH/LOW 两级 | 无优先级 |
| 压缩支持 | 缓存压缩/未压缩块 | 仅未压缩块 |
| 统计信息 | 详细命中率统计 | 基础统计 |
| 动态调整 | 支持容量动态调整 | 固定容量 |

### 使用示例

```cpp
// 1. 创建 BlockCache
#include "rocksdb/cache.h"
#include "rocksdb/table.h"

// 创建 8GB 容量的 LRU 缓存，16个分片
std::shared_ptr<rocksdb::Cache> block_cache = 
    rocksdb::NewLRUCache(8 * 1024 * 1024 * 1024LL,  // 8GB
                         16,                        // 分片数
                         false,                     // 严格容量限制
                         0.0);                      // 高优先级比例

// 2. 配置 RocksDB 使用自定义缓存
rocksdb::BlockBasedTableOptions table_options;
table_options.block_cache = block_cache;
table_options.cache_index_and_filter_blocks = true;   // 缓存索引和过滤器
table_options.pin_l0_filter_and_index_blocks_in_cache = true;  // 固定 L0 索引

rocksdb::Options options;
options.table_factory.reset(rocksdb::NewBlockBasedTableFactory(table_options));

// 3. 打开数据库
rocksdb::DB* db;
rocksdb::Status status = rocksdb::DB::Open(options, "/path/to/db", &db);

// 4. 监控缓存命中率
rocksdb::TablePropertiesCollection props;
db->GetPropertiesOfAllTables(&props);

for (const auto& kv : props) {
    const auto& tp = kv.second;
    double hit_rate = static_cast<double>(tp.block_cache_hit_count) /
                      (tp.block_cache_hit_count + tp.block_cache_miss_count);
    printf("表 %s 缓存命中率: %.2f%%\n", kv.first.c_str(), hit_rate * 100);
}

// 5. 动态调整缓存
block_cache->SetCapacity(12 * 1024 * 1024 * 1024LL);  // 扩容到 12GB
```

### 生产环境配置

```cpp
// 推荐服务优化配置
rocksdb::BlockBasedTableOptions table_opt;

// 1. 缓存策略
table_opt.block_cache = rocksdb::NewLRUCache(
    32 * 1024 * 1024 * 1024LL,  // 32GB 缓存
    std::thread::hardware_concurrency() * 2,  // 分片数 = 2 * CPU核心
    false,     // 不严格限制容量（允许临时超限）
    0.25       // 25% 容量分配给高优先级块
);

// 2. 缓存内容优化
table_opt.cache_index_and_filter_blocks = true;      // 缓存索引
table_opt.pin_l0_filter_and_index_blocks_in_cache = true;  // 固定 L0
table_opt.cache_index_and_filter_blocks_with_high_priority = true;  // 高优先级

// 3. 块大小优化
table_opt.block_size = 16 * 1024;      // 16KB 块（SSD 友好）
table_opt.block_restart_interval = 16; // 重启点间隔
table_opt.filter_policy.reset(rocksdb::NewBloomFilterPolicy(10, false));

// 4. 预读优化
table_opt.read_amp_bytes_per_bit = 32;  // 每 32 字节统计一次访问
options.compaction_readahead_size = 2 * 1024 * 1024;  // 2MB 预读
```

### 性能数据参考

- **缓存命中率**：推荐服务典型 85 95%，索引块命中率 > 99%
- **QPS 提升**：启用 BlockCache 后，随机读 QPS 提升 3 5倍
- **延迟降低**：P99 读延迟减少 60 80%
- **内存效率**：每 GB 缓存可服务 10 20GB 数据（压缩后）
- **SSD 寿命**：减少 70 90% 的 SSD 读取放大

### 推荐在线架构应用

1. **用户特征缓存**：用户 embedding/特征向量缓存到 BlockCache，减少特征计算。
2. **倒排索引块**：搜索索引块缓存，加速召回阶段。
3. **模型参数缓存**：小型模型参数缓存，减少模型加载时间。
4. **实时统计窗口**：滑动窗口统计结果缓存，避免重复计算。

### 源码分析要点

1. **LRU 实现**：`util/lru_cache.cc` 中的 LRU 链表和淘汰逻辑。
2. **分片管理**：`util/sharded_cache.cc` 中的分片哈希和并发控制。
3. **块缓存集成**：`table/block_based/block_based_table_reader.cc` 中的缓存集成。
4. **统计监控**：`monitoring/statistics.cc` 中的缓存命中率统计。

### 常见问题排查

1. **缓存命中率低**：
   - 检查 `block_cache_hit_count` 和 `block_cache_miss_count`
   - 调整 `cache_index_and_filter_blocks` 和 `pin_l0_filter_and_index_blocks_in_cache`
   - 增加缓存容量或调整分片数

2. **内存占用过高**：
   - 监控 `block_cache_usage` 和 `block_cache_pinned_usage`
   - 检查是否有大量 pinned 块未释放
   - 考虑使用 `CacheEntryStatsCollector` 分析缓存内容

3. **锁竞争严重**：
   - 增加分片数（建议为 CPU 核心数 2 4倍）
   - 使用 `CLOCK` 算法替代 `LRU` 减少链表操作
   - 启用 `adaptive_mutex` 减少锁开销

4. **冷启动性能差**：
   - 预热缓存：`DB::CompactRange()` 强制加载数据到缓存
   - 使用 `Cache::SetStrictCapacityLimit(false)` 允许临时超限
   - 配置更大的 `readahead_size` 预读数据

### 参考链接

- [GitHub 仓库](https://github.com/facebook/rocksdb)
- [BlockCache 文档](https://github.com/facebook/rocksdb/wiki/Block-Cache)
- [性能调优指南](https://github.com/facebook/rocksdb/wiki/RocksDB-Tuning-Guide)
- [生产实践](https://github.com/facebook/rocksdb/wiki/RocksDB-in-production)