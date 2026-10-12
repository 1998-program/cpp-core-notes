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
---

## 七、业务代码库适配分析
> **分析时间**：2026-05-29T19:01:46.263882
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析：RocksDB BlockCache 分层缓存设计

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 和 `feeda-mv-grc` 两个业务代码库中均已发现 RocksDB 相关目标库使用，各扫描到 10 个相关文件，说明业务侧已经具备一定的 RocksDB 接入基础，并非完全从零引入。结合技术笔记中的 BlockCache 设计，后续优化重点不应仅停留在“是否使用 RocksDB”，而应进一步检查是否正确配置了 `BlockBasedTableOptions::block_cache`、索引/过滤器缓存、L0 pinning、高优先级缓存比例以及缓存容量监控。

- 从业务形态看，`feeda-mv-grg` 偏序列生成与候选 embedding/特征读取，`feeda-mv-grc` 偏召回汇聚、多因子计算和策略调整，两者都存在大量随机读、特征/索引读取和热点数据复用场景。尤其是 `std::vector`、`std::string`、`std::unordered_map` 使用规模很大，说明代码中存在大量内存态数据组织和临时缓存逻辑。若底层数据读取依赖 RocksDB，合理配置 BlockCache 有较高收益潜力，可降低 SSD 随机读、减少反序列化/解压重复开销，并改善 P99 延迟。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现 RocksDB 目标库相关使用：10 个文件。
- 代表性文件包括：
  - `process/bge_candidate_nid_emb_function.cpp`
  - `process/pk_generate_candidate_nid_emb_function_longterm_v1.cpp`
  - `parser/string_parser.cpp`
  - `process/user_model_service_input_function_gen_v5.cpp`
  - `process/author_history_cate_meta.cpp`

- 现有 STL 容器使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 典型业务特征：
  - `model/model.h`、`model/paddle_model.h` 中大量使用 `std::vector<RidTmpInfoPtr>& candidate_vec` 作为候选集合传递，说明服务存在批量候选处理流程。
  - `process/bge_candidate_nid_emb_function.cpp`、`process/pk_generate_candidate_nid_emb_function_longterm_v1.cpp` 从命名看很可能涉及候选 nid embedding 或长期特征读取，这类数据通常具有“热点用户/热点内容重复访问”的特征，适合通过 RocksDB BlockCache 提升读性能。
  - `process/user_model_service_input_function_gen_v5.cpp` 可能承担模型输入特征构造，如果其依赖 RocksDB 读取用户、作者、内容侧特征，应优先检查 BlockCache 命中率和 block size 配置。

#### feeda-mv-grc：召回汇聚服务

- 已发现 RocksDB 目标库相关使用：10 个文件。
- 代表性文件包括：
  - `common/common.h`
  - `operator/adjuster/sketchy/shixiao_interest_sketchy.cpp`
  - `operator/adjuster/sketchy/short_story_wy_sketchy_adjuster.cpp`
  - `processor/multi_factor/playtime_efficient_factor_gen.cpp`
  - `strategy/dibar/newhot_mark_strategy.cpp`

- 现有 STL 容器使用规模：
  - `std::vector`：8382 次，分布在 1266 个文件
  - `std::string`：7107 次，分布在 1222 个文件
  - `std::unordered_map`：2828 次，分布在 636 个文件

- 典型业务特征：
  - `service/grc_http_service.cpp` 中存在 `std::unordered_map<std::string, std::vector<int>> depend_map`，说明召回链路中存在复杂依赖图、策略编排和中间结果聚合。
  - `operator/adjuster/sketchy/shixiao_interest_sketchy.cpp`、`operator/adjuster/sketchy/short_story_wy_sketchy_adjuster.cpp` 这类兴趣调整器通常需要读取用户兴趣、内容标签、统计窗口或倒排相关数据，若底层使用 RocksDB，BlockCache 对热点兴趣块、索引块、过滤器块的缓存收益较明显。
  - `processor/multi_factor/playtime_efficient_factor_gen.cpp`、`strategy/dibar/newhot_mark_strategy.cpp` 更接近在线打分/策略计算路径，若存在同步 RocksDB 查询，需要重点关注 P99/P999 延迟和缓存 miss 带来的尾延迟放大。

---

### 3. 💡 适用性评估与建议

- **建议 1：优先梳理现有 RocksDB 初始化位置，统一 BlockCache 配置**
  - 适用代码：
    - `feeda-mv-grg`：`process/bge_candidate_nid_emb_function.cpp`、`process/pk_generate_candidate_nid_emb_function_longterm_v1.cpp`
    - `feeda-mv-grc`：`common/common.h`、`strategy/dibar/newhot_mark_strategy.cpp`
  - 建议做法：
    - 搜索这些文件中 RocksDB `Options`、`BlockBasedTableOptions`、`DB::Open`、`ColumnFamilyOptions` 的初始化逻辑。
    - 如果当前仅使用默认 RocksDB 配置，建议显式引入共享 `block_cache`：
      ```cpp
      rocksdb::BlockBasedTableOptions table_opt;
      table_opt.block_cache = rocksdb::NewLRUCache(
          cache_size_bytes,
          std::thread::hardware_concurrency() * 2,
          false,
          0.25
      );
      table_opt.cache_index_and_filter_blocks = true;
      table_opt.cache_index_and_filter_blocks_with_high_priority = true;
      table_opt.pin_l0_filter_and_index_blocks_in_cache = true;
      ```
    - 多个 RocksDB 实例如果存储的是同类在线特征数据，建议复用同一个 `std::shared_ptr<rocksdb::Cache>`，避免每个实例单独建 cache 导致内存碎片和容量不可控。

- **建议 2：候选 embedding / 特征读取链路优先开启索引与过滤器缓存**
  - 适用代码：
    - `feeda-mv-grg`：`process/bge_candidate_nid_emb_function.cpp`
    - `feeda-mv-grg`：`process/user_model_service_input_function_gen_v5.cpp`
    - `feeda-mv-grg`：`process/author_history_cate_meta.cpp`
  - 场景判断：
    - 这些文件从命名看涉及 candidate nid embedding、用户模型输入、作者历史类目元信息，通常是在线预测前的高频特征读取路径。
    - 这类场景通常 key 分布呈现长尾，热点 key 和热点 block 会被反复访问。
  - 建议做法：
    - 开启：
      - `cache_index_and_filter_blocks = true`
      - `cache_index_and_filter_blocks_with_high_priority = true`
      - `pin_l0_filter_and_index_blocks_in_cache = true`
    - 对于 embedding 类 value 较大的场景，建议评估 `block_size = 16KB` 或 `32KB`，避免 block 太小造成索引膨胀，也避免 block 太大导致无效读取。
    - 对只读或近只读的 embedding DB，可以在服务启动后通过小流量 warmup 或按热点 key 预读方式预热 BlockCache，而不是依赖线上请求自然填充。

- **建议 3：召回汇聚中的兴趣调整、策略打分模块适合做缓存命中率专项观测**
  - 适用代码：
    - `feeda-mv-grc`：`operator/adjuster/sketchy/shixiao_interest_sketchy.cpp`
    - `feeda-mv-grc`：`operator/adjuster/sketchy/short_story_wy_sketchy_adjuster.cpp`
    - `feeda-mv-grc`：`processor/multi_factor/playtime_efficient_factor_gen.cpp`
    - `feeda-mv-grc`：`strategy/dibar/newhot_mark_strategy.cpp`
  - 建议做法：
    - 在这些在线召回/调整模块涉及 RocksDB 读取的位置，补充 RocksDB statistics 采集：
      - `rocksdb.block.cache.hit`
      - `rocksdb.block.cache.miss`
      - `rocksdb.block.cache.index.hit`
      - `rocksdb.block.cache.filter.hit`
      - `rocksdb.block.cache.usage`
      - `rocksdb.block.cache.pinned.usage`
    - 按模块维度输出命中率，例如：
      - 兴趣调整器命中率
      - 多因子特征生成命中率
      - 新热策略命中率
    - 如果某模块 cache miss 高且 P99 延迟明显抖动，可优先增大 BlockCache 或为索引/过滤器分配高优先级缓存比例。

- **建议 4：对 `std::unordered_map` 本地缓存较多的路径，评估是否可下沉到 RocksDB BlockCache**
  - 适用代码：
    - `feeda-mv-grc`：`service/grc_http_service.cpp`
    - `feeda-mv-grc`：`common/common.h`
    - `feeda-mv-grg`：`parser/string_parser.cpp`
  - 背景：
    - 两个代码库中 `std::unordered_map` 使用规模较大，`feeda-mv-grc` 达到 2828 次，说明业务侧可能存在不少进程内临时 map 缓存或索引结构。
    - 如果这些 map 是为了缓存 RocksDB 查询结果，且生命周期、容量、淘汰策略缺少统一管理，可能造成内存不可控、重复缓存和数据一致性问题。
  - 建议做法：
    - 对以下类型的本地 map 缓存进行排查：
      - key 为 `std::string` / nid / uid，value 为特征、标签、embedding、统计窗口
      - 请求间复用但无明确 TTL / LRU 的缓存
      - 每个 worker 或每个模块各自维护一份相似缓存
    - 如果数据源本身在 RocksDB，优先依赖 RocksDB BlockCache 缓存 block，而不是业务层重复缓存 value。
    - 对确实需要 value 级缓存的场景，可保留业务缓存，但应限制容量，并与 RocksDB BlockCache 总内存预算统一规划。

- **建议 5：已有目标库使用文件可作为迁移参考，但需要补齐配置模板**
  - 可作为参考的现有文件：
    - `feeda-mv-grg`：`process/bge_candidate_nid_emb_function.cpp`、`process/pk_generate_candidate_nid_emb_function_longterm_v1.cpp`
    - `feeda-mv-grc`：`common/common.h`、`operator/adjuster/sketchy/shixiao_interest_sketchy.cpp`
  - 建议沉淀统一封装：
    - 新增或改造一个 RocksDB 工厂类，例如 `RocksDBReaderFactory` / `FeatureDBManager`，集中管理：
      - `rocksdb::Options`
      - `rocksdb::BlockBasedTableOptions`
      - `rocksdb::Cache`
      - `rocksdb::Statistics`
      - cache size、shard 数、高优先级比例等参数
    - 避免每个业务文件独立配置 RocksDB，导致参数不一致、线上表现难以归因。

---

### 4. ⚠️ 引入风险与限制

- **风险 1：BlockCache 会显著增加进程常驻内存，需要统一内存预算**
  - `feeda-mv-grc` 容器使用规模很大，`std::vector` 和 `std::string` 分别超过 8000 和 7000 次，业务本身已有较高内存压力。
  - 如果再为多个 RocksDB 实例分别创建大容量 BlockCache，可能导致 RSS 快速上涨，触发 OOM 或容器内存限制。
  - 建议统一规划：
    - 业务对象内存
    - 模型/embedding 内存
    - RocksDB BlockCache
    - jemalloc/tcmalloc 碎片
    - OS Page Cache

- **风险 2：缓存命中率收益依赖访问局部性，长尾随机读不一定显著受益**
  - 如果 `process/bge_candidate_nid_emb_function.cpp` 或 `processor/multi_factor/playtime_efficient_factor_gen.cpp` 中的 key 访问高度随机，且热点不明显，单纯增大 BlockCache 可能收益有限。
  - 迁移前建议先按模块采样：
    - key 重复率
    - block cache hit/miss
    - RocksDB read latency
    - SSD read IOPS
  - 不建议在缺少指标的情况下直接大幅增加缓存容量。

- **风险 3：索引和过滤器 pinning 可能导致 pinned usage 过高**
  - `pin_l0_filter_and_index_blocks_in_cache = true` 对读延迟有帮助，但在 L0 文件较多、compaction 压力大或多 column family 场景下，pinned block 可能占用大量不可淘汰内存。
  - 需要重点监控：
    - `block_cache_usage`
    - `block_cache_pinned_usage`
    - L0 文件数
    - compaction backlog
  - 如果 pinned usage 长期过高，应降低 pinning 范围或调整 compaction 策略。

- **风险 4：多线程高并发场景下，分片数配置不合理会引入锁竞争**
  - `feeda-mv-grg` 和 `feeda-mv-grc` 都是在线服务，高并发读取下 BlockCache shard 数过少可能造成 cache mutex 竞争。
  - 建议初始配置为：
    - shard 数约为 `CPU 核心数 * 2`
    - 高并发读多写少场景可评估 `CPU 核心数 * 4`
  - 若监控发现 RocksDB cache mutex 竞争明显，可进一步评估 CLOCK Cache 或提高 shard 数，但需避免 shard 过多导致单 shard 容量过小、淘汰效率下降。

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
