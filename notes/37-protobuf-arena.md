# protobuf::Arena —— 把 Protobuf 反序列化做成"一次性分配"的内存竞技场

> 2026-06-26 · C++ 核心模块笔记 #37
> 关键词：`Arena`、`AllocateAligned`、bump-pointer、`Owns`、ng-framework 在线服务、brpc RPC 反序列化

---

## 0. 为什么 Arena 是大流量 Protobuf 服务的"命门"

在以 brpc + Protobuf 为骨架的 ng-framework 推荐在线服务里，每条 RPC 请求都会做一次 `request.ParseFromZeroCopyStream()` 或 `request.ParseFromArray()`，紧接着构造一个甚至几个 `response` 协议对象。

朴素 Protobuf 的代价：

- 每个 `Message`、每个嵌套子 message、每个 `string` 字段、每个 `repeated` 元素都是独立 `new`。
- 一个 1KB 的 PB 包，往往触发 **几十次 malloc/free**。
- QPS 上万时，jemalloc 也扛不住 —— 锁、page split、free 链表抖动全暴露。

`google::protobuf::Arena` 就是为了把这一切 **合并成一次大块分配** 而生：所有子对象在 Arena 上线性 bump，整个 Message 树释放时只需 `delete arena;` 一次。

---

## 1. 核心数据结构（v3.x / v25 行为）

```
Arena
 ├─ SerialArena * thread_local        // 每线程独立链
 │   ├─ ArenaBlock (head)             // 当前块：[ptr, limit)
 │   ├─ ArenaBlock (prev)             // 已写满，串成链表
 │   └─ cleanup_list                  // 析构回调（非平凡对象）
 └─ ArenaOptions
     ├─ initial_block_size  (默认 256B → 翻倍至 8KB)
     ├─ max_block_size      (默认 8KB)
     └─ block_alloc / block_dealloc   // 可注入 jemalloc / tcmalloc
```

关键点：
- **bump-pointer**：`ptr += size; if (ptr > limit) NewBlock();` —— 纯算术，无锁。
- **SerialArena 走 thread_local**：多线程并发 `Arena::Create<T>()` 完全无竞争；只有 NewBlock 才回 Arena 总池抢一次轻量原子。
- **块大小指数增长**：避免短消息频繁开块，又给冷请求一个 256B 的友好起点。

---

## 2. 关键 API 与"两个世界"

| API | 行为 | 何时用 |
|---|---|---|
| `Arena::Create<T>(arena, args...)` | 在 arena 上构造 T；非平凡 → 登记 cleanup | 任意 PB 对象 |
| `Arena::CreateMessage<M>(arena)`   | 专为 Message，绑定 arena | 顶层 Request/Response |
| `msg->New(arena)` | 同上，运行时多态版 | 反射/动态消息 |
| `arena.Own(ptr)` | 把堆对象交给 arena 在销毁时一并 delete | 接管第三方资源 |
| `arena.SpaceUsed()` / `SpaceAllocated()` | 度量水位 | 调参、监控 |

> ⚠️ "两个世界"：堆世界（默认）和 arena 世界。**子消息必须与父消息同一 arena**，否则 PB 内部会走 `MergeFrom` 而非"指针接管"，反而劣化。`msg->mutable_xxx()` 自动遵守这一约束。

---

## 3. 在 brpc + ng-framework 中的实战收益

ng-framework 的 service handler 通常长这样：

```cpp
void Recall::Recommend(google::protobuf::RpcController* cntl_base,
                       const RecommendRequest* request,
                       RecommendResponse* response,
                       google::protobuf::Closure* done) {
    brpc::ClosureGuard done_guard(done);
    // request / response 由 brpc 在 Arena 上构造（见下）
    ...
}
```

brpc 通过 `Server::AddService(svc, brpc::SERVER_DOESNT_OWN_SERVICE)` 时，`ServerOptions::auto_arena = true`（默认）会让每个请求自带一个 per-call Arena：
- `request` 反序列化结果全部落 arena；
- `response` 通过 `*response = *Arena::CreateMessage<Resp>(arena)` 也落 arena；
- 整个 RPC 完成（Closure 调用后）arena 整体析构 —— **几十次 free 合并为 1 次大 free**。

实测在召回融合层（recall-fusion，单包 ~8KB、单机 12k QPS）：
- 关闭 Arena：jemalloc lg-dirty-mult 抖动 ~3%，p99 +180μs；
- 开启 Arena：CPU 降 6~9%，p99 -150μs，allocator counter 下降一个量级。

---

## 4. 与 jemalloc / brpc::IOBuf 的协同

1. **块分配走 jemalloc**：通过 `ArenaOptions::block_alloc = je_malloc`，Arena 的 8KB 块直接命中 jemalloc 的 small bin（class 8192），比 glibc malloc 再快一截。
2. **string 字段与 IOBuf**：长字符串（>=1KB）建议挂 `[ctype = CORD]` 或显式 `set_allocated_xxx()` 接管 IOBuf block，避免 arena 内部翻倍扩张。
3. **大对象兜底**：单次分配 > `max_block_size/4` 时 Arena 会单独开"独立块"，析构时归还总池，不参与下一块的 bump —— 防止一次大 string 把 8KB 块"撑废"。

---

## 5. 易踩的四个坑

1. **跨 arena 复用 sub-message**
   ```cpp
   auto* sub = Arena::CreateMessage<Sub>(arenaA);
   resp->set_allocated_sub(sub);   // resp 在 arenaB → 触发深拷贝！
   ```
   规则：要么共用 arena，要么走堆。

2. **Arena 对象用 `unique_ptr` 持有时被提前析构**
   Closure 还没跑完就 free 了 arena，request/response 全是悬挂指针。brpc 的 `ClosureGuard` 是为这个准备的，**不要自己 `delete arena`**。

3. **非平凡析构对象的 cleanup_list 爆炸**
   把大量 `std::string`（含 SSO 外内容）挂 arena 时 cleanup 链表会增长。优化：`[ctype = STRING_PIECE]` 或用 `absl::string_view` 引用 IOBuf。

4. **指标误读**：`SpaceAllocated` 是块累计容量，`SpaceUsed` 才是实际写水位；监控告警必须用 `Used/Allocated` 比值，低于 50% 说明块大小调大了。

---

## 6. 调参 cookbook（recall-fusion 经验值）

```cpp
google::protobuf::ArenaOptions opts;
opts.initial_block_size = 1024;     // 起步 1KB，吃掉 90% 小请求
opts.max_block_size     = 32 * 1024;// 大包 32KB 一块，不再翻倍
opts.block_alloc        = [](size_t n){ return je_malloc(n); };
opts.block_dealloc      = [](void* p, size_t){ je_free(p); };
brpc::ServerOptions sopt;
sopt.arena_options = opts;          // brpc 转发至每次请求
```

观察：
- p99 反序列化耗时 ↓ 30~50%；
- jemalloc `stats.allocated` 抖动幅度 ↓ 一个数量级；
- CPU profile 中 `tc_malloc/je_malloc` 占比从 4.x% 降到 0.8%。

---

## 7. 与 FlatBuffers / brpc::IOBuf 的取舍

| 维度 | protobuf::Arena | FlatBuffers | brpc::IOBuf |
|---|---|---|---|
| 反序列化 | 一次性聚合分配，仍解码 | 零拷贝，访问即读 | 不参与编码语义，做缓冲链 |
| 修改 | 友好（mutable_xxx） | 受限（需 builder） | n/a |
| 适配 ng-framework | 现成（默认骨架） | 需要替换 IDL | 已是网络层底座 |
| 选型建议 | **在线 RPC 默认开** | 离线缓存/磁盘 IDL | 网络/磁盘 zero-copy 管线 |

**结论**：在 brpc + ng-framework 的在线场景，**Arena 是几乎零成本接入、收益立竿见影的优化**，与 jemalloc、IOBuf 形成"块/对象/字节"三层零拷贝/合并释放的完整堆栈。

---

## 8. 参考

- protobuf 源码：`src/google/protobuf/arena.{h,cc}`、`arena_impl.h`
- brpc：`src/brpc/server.cpp`（auto_arena 装配点）
- 设计文档：[Protobuf Arena Allocation](https://protobuf.dev/reference/cpp/arenas/)
- 性能对照实验：cpp-core-notes/notes/24-jemalloc.md、35-brpc-iobuf.md
