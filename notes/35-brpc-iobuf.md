# brpc::IOBuf 零拷贝缓冲链与 ng-framework 在线服务实战

> 笔记编号：35
> 日期：2026-06-24
> 主题：brpc::IOBuf — 百度 brpc 内核里跑了十几年的零拷贝 IO 缓冲，是 ng-framework / 推荐在线 / 检索栈最基础的"血液"
> 关联技术栈：brpc · jemalloc · Protobuf · ng-framework

---

## 1. 为什么单独把 brpc::IOBuf 拉出来讲

平时聊"零拷贝缓冲链"，大家容易想到 `folly::IOBuf`、`absl::Cord`，或者干脆 `std::vector<char>` 拼接。但在百度推荐 / 检索 / 广告这一系列在线服务里，**真正在生产环境跑了几十亿 QPS 的、是 brpc::IOBuf**。它和 folly::IOBuf 看起来都是"一段段 buffer 串成链"，但设计哲学差别非常大：

| 维度 | folly::IOBuf | brpc::IOBuf |
| --- | --- | --- |
| 节点单位 | 每节点 = 一个堆对象，refcount | 每节点 = `Block*` + offset/length 视图，**Block 池化** |
| 拷贝 | shared_ptr 语义，clone 共享底层 | 引用计数 Block，append/cut 全是 O(1) 视图操作 |
| 内存分配 | 走 jemalloc/通用 allocator | 大小固定 (8KB 默认) 的 `Block` 池，配合 jemalloc 大块走 mmap |
| 与 RPC 集成 | 自己包一层 transport | **天生为 RPC 设计**：write 时 writev 直接打散到 socket |
| 与 Protobuf 集成 | 需 ZeroCopyInputStream 适配器 | `IOBufAsZeroCopyInputStream` 内置，零成本 |

一句话：folly::IOBuf 是"通用零拷贝链表"，brpc::IOBuf 是"为 RPC + Protobuf 而生的零拷贝链表"。

---

## 2. 核心数据结构

```cpp
// brpc/src/butil/iobuf.h（精简版）
class IOBuf {
public:
    struct Block {
        std::atomic<int> nshared;   // 引用计数
        uint16_t flags;
        uint16_t abi_check;
        uint32_t size;
        uint32_t cap;
        Block* portal_next;         // tls free list
        char data[0];               // flexible array，数据紧跟其后
    };

    struct BlockRef {
        uint32_t offset;            // 在 Block 内的起点
        uint32_t length;            // 长度
        Block*   block;             // 共享指向的 Block
    };

private:
    // 小数组优化：≤2 个 BlockRef 直接内联，避免堆分配
    union {
        BlockRef _sv[2];
        struct { BlockRef* refs; uint32_t cap; uint32_t start; } _bv;
    };
    uint32_t _nref;
};
```

关键设计点：

1. **Block 是数据的真正持有者**，IOBuf 本身只是 `BlockRef` 数组（视图）。
2. **BlockRef 用 offset/length 切片**，cut/append 永远不拷贝数据。
3. **小向量优化**：≤2 段时整个 IOBuf 只占栈上 48 字节，绝大多数 RPC body 都命中这个 fast path。
4. **TLS Block 池**：每线程一个 free list，避免 contention，归还 Block 时不立即 free，留给同线程下一次复用 —— 这是 brpc 在高 QPS 下吞吐的关键之一。

---

## 3. 三大核心操作的 O(1) 视图语义

### 3.1 append：拼接两段 IOBuf

```cpp
IOBuf a, b;
// a, b 各持有若干 Block
a.append(b);   // 不拷贝任何字节，只是把 b 的 BlockRef 数组接到 a 后面，并对 Block 引用计数 +1
```

实现重点：当 a 末尾 BlockRef 和 b 首个 BlockRef 指向**同一个 Block 且物理相邻**时，brpc 会做 **ref 合并**（length 相加），减少 BlockRef 数量，这一步对长链路 RPC 转发场景吞吐影响显著。

### 3.2 cutn / cut_until：从前面切一段出来

```cpp
IOBuf body;
butil::IOBuf header;
body.cutn(&header, 16);   // 从 body 前面切走 16 字节进 header
```

一行搞定 protocol header 的解析切割，**全程 O(BlockRef 数量)，无 memcpy**。在 baidu_std/http 等协议解析里到处都是这种用法。

### 3.3 与 socket 的 writev / readv 集成

发包时 brpc 直接把 IOBuf 内部的 BlockRef 转成 `iovec[]`，一次 `writev()` 把整条 RPC 响应丢给内核：

```cpp
ssize_t IOBuf::pcut_into_file_descriptor(int fd, off_t offset, size_t size_hint);
```

这意味着：**Protobuf 序列化结果 → IOBuf → 内核 socket buffer，没有一次额外的用户态拷贝**。对比常见 `string + write()` 模式，整条 RPC 链路至少省 1~2 次 memcpy。

---

## 4. 与 Protobuf 的零拷贝桥接

brpc 标配两个适配器，把 IOBuf 接到 Protobuf 的 `ZeroCopyStream` 之上：

```cpp
class IOBufAsZeroCopyInputStream  : public google::protobuf::io::ZeroCopyInputStream;
class IOBufAsZeroCopyOutputStream : public google::protobuf::io::ZeroCopyOutputStream;
```

序列化路径：
```
Foo.SerializeToZeroCopyStream(IOBufAsZeroCopyOutputStream(&iobuf))
                                       │
                                       └─ 直接在 brpc::Block 里写字节
                                          → IOBuf → writev → socket
```

反序列化路径：
```
socket → readv → Block 池 → IOBuf → IOBufAsZeroCopyInputStream → Foo.ParseFromZeroCopyStream
```

整条收发路径里 **Protobuf message 的 bytes 字段、嵌套 message 的内存只 alloc 一次**，这个性质和 `protobuf::Arena` 配合得极好：Arena 控制 message 对象的生命周期，IOBuf 控制原始 bytes 的生命周期，两者通过引用计数解耦。

---

## 5. 在 ng-framework / 推荐在线的实战角色

ng-framework 是百度推荐 / 搜索 / 广告 大量在线服务的统一基座（基于 brpc）。IOBuf 在里面承担的角色比一般人想象的多：

1. **RPC body 的标准载体**：Service 的 request/response 在线程间、模块间传递时，统一是 IOBuf，避免一次拷贝。
2. **召回结果序列化**：召回算子产出的大批 doc + features，会先序列化进 IOBuf，再在打分层用 `IOBufAsZeroCopyInputStream` 反序列化进 Arena 里的 Protobuf —— 这对动辄上千个 doc、每个 doc 几十个特征的场景，是把吞吐做高的关键。
3. **shared body 复用**：同一份特征要发往 N 个下游打分服务时，brpc 直接 `IOBuf::append` 一次构造，N 个 RPC 共享同一批 Block，**广播零拷贝**。
4. **HTTP body 透传**：网关层接到 HTTP 请求，IOBuf 直接当 body 透传到下游 RPC，期间 body 不落地、不拷贝。

---

## 6. 与 jemalloc 的协作

brpc 的 Block 默认大小 8KB，超过这个尺寸的 user buffer（如大 doc body）会走 **`IOBuf::append_user_data`** 接管外部内存，并接受自定义 deleter。这条路径下：

- 小块（≤8KB）走 brpc 自己的 TLS Block 池 + jemalloc small bin。
- 大块走 jemalloc 的 large/huge class（通常直接 mmap），由 deleter 回调释放。

这恰好对应 jemalloc 的 size class 哲学：**小对象池化避免争用，大对象直 mmap 避免碎片**。两者刚好分层，这是 brpc 在 P99 延迟稳定性上比"naive new/delete"实现高一档的根本原因。

---

## 7. 易踩的坑

1. **不要把 IOBuf 当 `std::string` 用**：随机访问 `IOBuf::byte(i)` 是 O(BlockRef 数量)，热点路径里别这么写。
2. **`copy_to_cstr`/`to_string` 会真拷贝**，调试日志里随手 `to_string()` 是常见性能事故来源；线上日志请用 `butil::IOBuf::to_string_view` 风格的零拷贝接口或限长打印。
3. **Block 不是越大越好**：超过 8KB 默认值会绕开 TLS pool，反复创建大 Block 反而更慢。需要大 buffer 时用 `append_user_data` 自管。
4. **跨线程持有 IOBuf 时注意 Block 引用计数走的是原子**，但**不要在持有 IOBuf 时阻塞线程**，否则会拖住 Block 池的回收节奏，影响整体内存占用。

---

## 8. 一句话总结

> brpc::IOBuf 不是"又一个 zero-copy buffer"，它是把 **池化 Block + 视图引用计数 + writev + Protobuf ZeroCopyStream** 这四件事缝在一起的一个工程产物。它能让推荐在线、ng-framework 里的 RPC 链路从 socket 到 Protobuf message 之间真正只发生一次内存分配 —— 这是百度内大规模在线服务能稳住 P99 的隐形地基。

---

## 关联阅读

- `notes/18-brpc-bthread.md` — bthread 调度与 IOBuf 配合
- `notes/34-brpc-butex.md` — bthread 同步原语
- `notes/17-protobuf-arena-allocator.md` — Arena 与 IOBuf 的生命周期协作
- `notes/24-jemalloc.md` — Block 池底层的内存分配
- `notes/25-folly-iobuf.md` — 对比版：folly 的零拷贝缓冲设计
