# C++ 高性能基础库模块深度解析

> 每日自动更新，深入剖析 C++ 高性能基础库的设计思想、核心实现与性能优化原理。

## 目录

| # | 模块 | 库 | 核心主题 | 文档 |
|---|------|----|---------|------|
| 01 | FlatBuffers | google/flatbuffers | 零拷贝序列化设计 | [查看](./notes/01-flatbuffers-zero-copy.md) |

## 系列规划

1. **FlatBuffers** · 零拷贝序列化设计 ✅
2. **absl::flat_hash_map** · SIMD 哈希探测
3. **absl::InlinedVector** · 栈上优化小容器
4. **folly::fbstring** · SSO 字符串优化
5. **folly::F14Map** · 向量化哈希表
6. **mimalloc** · 分段式内存分配器
7. **jemalloc** · Arena 内存管理
8. **RocksDB::BlockCache** · 分层缓存设计
9. **LevelDB::SkipList** · 无锁跳表实现
10. **seastar::future** · 用户态协程调度
11. **DPDK::rte_ring** · 无锁环形队列
12. **Boost::intrusive** · 侵入式容器设计
13. **abseil::Cord** · 绳索字符串 (Rope)
14. **folly::AtomicHashMap** · 无锁哈希表
15. **tcmalloc::ThreadCache** · 线程本地缓存

---

*由 OpenClaw 每日定时任务自动生成并推送*
