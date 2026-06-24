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

---

## 七、业务代码库适配分析
> **分析时间**：2026-06-24T19:02:48.419521
> **目标代码库**：feeda-mv-grg（序列生成）、feeda-mv-grc（召回汇聚）

## 业务代码库适配分析

### 1. 分析摘要

- 从扫描结果看，`feeda-mv-grg` 和 `feeda-mv-grc` 两个业务代码库中都已经存在 `brpc::IOBuf` 相关使用，各发现 5 个文件，说明业务侧并非完全从零引入，已经具备一定落地基础。现有使用主要集中在 HTTP Service、Response 构造、GRG/GRC 之间的结果透传等在线链路核心位置，符合 `brpc::IOBuf` 在 RPC body、HTTP body、Protobuf 序列化缓冲中的典型使用场景。

- 两个代码库中 `std::string`、`std::vector` 使用规模都较大，尤其是 `feeda-mv-grc` 中 `std::vector` 达到 8432 次、`std::string` 达到 7153 次，说明存在大量内存拼接、临时容器、序列化中间态的潜在优化空间。不过需要注意，`IOBuf` 并不是 `std::vector` / `std::string` 的通用替代品，最适合优先迁移的是 **网络收发、Response 拼接、HTTP body 透传、Protobuf 序列化结果缓存、跨服务广播复用** 等场景，而不是算法内部的随机访问容器。

---

### 2. 代码库详情

#### feeda-mv-grg：序列生成服务

- 已发现 `brpc::IOBuf` / 目标库相关使用文件共 5 个：
  - `process/vids_diversity_embedding_function.cpp`
  - `process/response_function.cpp`
  - `process/new_response_function.cpp`
  - `process/vids_diversity_his_embedding_function.cpp`
  - `service/grg_http_service.cpp`

- 该代码库中标准库容器使用规模：
  - `std::vector`：1969 次，分布在 356 个文件
  - `std::string`：2443 次，分布在 425 个文件
  - `std::unordered_map`：734 次，分布在 205 个文件

- 从示例看，`model/model.h`、`model/paddle_model.h` 中大量使用 `std::vector<RidTmpInfoPtr>& candidate_vec` 作为模型预测输入，这类数据结构更偏向算法计算和随机访问，不建议直接迁移为 `IOBuf`。  
  `IOBuf` 更适合应用在 `process/response_function.cpp`、`process/new_response_function.cpp`、`service/grg_http_service.cpp` 这类响应构造、HTTP 输出、RPC body 拼接路径中。

- 已有 `process/response_function.cpp`、`process/new_response_function.cpp` 可作为业务内部参考点，建议优先梳理其中是否已经存在：
  - `std::string resp_str` 中间拼接；
  - Protobuf 序列化到 `std::string` 后再写入 brpc response；
  - 多段结果合并时发生重复 append/copy；
  - HTTP response body 从 string 复制到 brpc attachment 的路径。

#### feeda-mv-grc：召回汇聚服务

- 已发现 `brpc::IOBuf` / 目标库相关使用文件共 5 个：
  - `processor/searchc_related_deepin/searchc_immersive_response.cpp`
  - `processor/video_launch/news_response.cpp`
  - `processor/video_launch/news_response_in_video.cpp`
  - `service/grc_http_service.cpp`
  - `processor/video_launch/response_for_grg.cpp`

- 该代码库中标准库容器使用规模更大：
  - `std::vector`：8432 次，分布在 1275 个文件
  - `std::string`：7153 次，分布在 1231 个文件
  - `std::unordered_map`：2833 次，分布在 638 个文件

- `feeda-mv-grc` 作为召回汇聚服务，天然存在多路召回结果聚合、过滤、排序、再透传给 GRG 或下游服务的场景。`processor/video_launch/response_for_grg.cpp`、`processor/video_launch/news_response.cpp`、`processor/video_launch/news_response_in_video.cpp` 这类文件是最值得评估 `IOBuf` 优化收益的位置。

- `service/grc_http_service.cpp` 中扫描到典型 `std::string resp_str`、`std::vector<std::string>` 使用：
  ```cpp
  std::string resp_str;

  std::vector<std::string> sub_access_off_vec;
  std::vector<std::string> sub_access_on_vec;
  ```
  其中配置解析、query 参数拆分仍然适合使用 `std::string` / `std::vector<std::string>`；但如果 `resp_str` 最终作为 HTTP body 或 RPC response body 输出，则可以重点评估是否改为 `brpc::IOBuf`，避免 response 构造过程中的多次扩容和复制。

---

### 3. 💡 适用性评估与建议

- **建议一：优先优化 Response 构造路径，减少 `std::string` 中间缓冲**
  - 适用文件：
    - `feeda-mv-grg/process/response_function.cpp`
    - `feeda-mv-grg/process/new_response_function.cpp`
    - `feeda-mv-grc/processor/video_launch/news_response.cpp`
    - `feeda-mv-grc/processor/video_launch/news_response_in_video.cpp`
  - 建议场景：
    - 如果当前逻辑是先把多个 doc、feature、item 字段拼成 `std::string`，再写入 brpc response attachment，可以改为直接向 `brpc::IOBuf` 追加。
    - 对于多段 response，例如 header、metadata、doc list、debug info，可以分别构造为多个 `IOBuf` 片段，然后通过 `append` 合并，避免大字符串反复扩容。
  - 迁移方向示例：
    ```cpp
    brpc::Controller* cntl = ...;
    butil::IOBuf& out = cntl->response_attachment();

    butil::IOBuf body;
    body.append(header_buf);
    body.append(doc_list_buf);
    body.append(feature_buf);

    out.append(body);
    ```
  - 预期收益：
    - 降低大 response 场景下的 `std::string::append` / `resize` / `memcpy` 开销；
    - 减少 P99 延迟中由大对象分配和拷贝带来的抖动。

- **建议二：GRG/GRC 之间的 body 透传优先使用 `IOBuf`，避免反序列化后再序列化**
  - 适用文件：
    - `feeda-mv-grc/processor/video_launch/response_for_grg.cpp`
    - `feeda-mv-grg/service/grg_http_service.cpp`
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 建议场景：
    - GRC 聚合结果转发给 GRG；
    - HTTP 请求 body 透传到下游 RPC；
    - 下游 RPC response body 原样拼入上游 response。
  - 如果业务逻辑不需要理解 body 内容，应避免：
    1. `IOBuf -> std::string`
    2. 解析 string
    3. 再序列化回 `std::string`
    4. 写入 response
  - 更推荐：
    ```cpp
    butil::IOBuf body = cntl->request_attachment();
    downstream_cntl.request_attachment().append(body);
    ```
  - 注意这里的 `append` 共享底层 Block，只增加引用计数，不复制实际字节，非常适合召回汇聚服务的多下游广播和透传场景。

- **建议三：Protobuf 序列化结果避免落到 `std::string`，直接接入 `IOBufAsZeroCopyOutputStream`**
  - 适用文件：
    - `feeda-mv-grg/process/vids_diversity_embedding_function.cpp`
    - `feeda-mv-grg/process/vids_diversity_his_embedding_function.cpp`
    - `feeda-mv-grc/processor/searchc_related_deepin/searchc_immersive_response.cpp`
  - 建议场景：
    - embedding / history embedding / immersive response 这类结果通常字段较多，可能包含大量特征、doc、向量或扩展信息；
    - 如果当前路径存在 `proto.SerializeToString(&str)`，再 `attachment.append(str)`，可以改为：
      ```cpp
      butil::IOBuf buf;
      butil::IOBufAsZeroCopyOutputStream os(&buf);
      proto.SerializeToZeroCopyStream(&os);

      cntl->response_attachment().append(buf);
      ```
  - 预期收益：
    - 避免 Protobuf 序列化到 `std::string` 的连续内存分配；
    - 减少一次从 string 到 IOBuf / socket buffer 的复制；
    - 对大 doc list、大 feature list 响应收益更明显。

- **建议四：`service/grc_http_service.cpp` 中区分“控制数据”和“body 数据”，不要盲目替换所有 string/vector**
  - 适用文件：
    - `feeda-mv-grc/service/grc_http_service.cpp`
  - 当前扫描示例中有：
    ```cpp
    std::unordered_map<std::string, std::vector<int>> depend_map;
    std::vector<std::string> sub_access_off_vec;
    std::vector<std::string> sub_access_on_vec;
    std::string resp_str;
    ```
  - 建议：
    - `depend_map`、`sub_access_off_vec`、`sub_access_on_vec` 属于控制面数据结构，需要查找、遍历、随机访问，不建议替换成 `IOBuf`。
    - `resp_str` 如果只是最终 HTTP 输出 body，可评估替换为 `butil::IOBuf`。
    - 如果 `resp_str` 需要大量 `operator+=` 拼接 JSON / HTML / debug 文本，短期可先做两步优化：
      1. 对调试页面、小响应保留 `std::string`；
      2. 对大结果响应、新增业务协议响应使用 `IOBuf`。
  - 这样可以避免把 `IOBuf` 用在不适合的控制逻辑中，同时优先覆盖最有收益的数据面路径。

- **建议五：以已有目标库使用文件作为迁移模板，先做局部灰度**
  - `feeda-mv-grg` 中可参考：
    - `process/response_function.cpp`
    - `process/new_response_function.cpp`
    - `service/grg_http_service.cpp`
  - `feeda-mv-grc` 中可参考：
    - `processor/video_launch/response_for_grg.cpp`
    - `processor/video_launch/news_response.cpp`
    - `service/grc_http_service.cpp`
  - 推荐迁移顺序：
    1. 先在单个 response 构造函数中替换 `std::string` 中间缓冲；
    2. 增加 response size、序列化耗时、attachment append 耗时、P99 延迟监控；
    3. 对比 string 版本和 IOBuf 版本；
    4. 再推广到同类型 response 文件。
  - 不建议一次性在全仓范围批量替换 `std::string` / `std::vector`，否则收益不确定且回归风险较高。

---

### 4. ⚠️ 引入风险与限制

- **`IOBuf` 不适合替代算法内部的 `std::vector`**
  - 例如：
    - `feeda-mv-grg/model/model.h`
    - `feeda-mv-grg/model/paddle_model.h`
  - 示例中的 `std::vector<RidTmpInfoPtr>& candidate_vec` 是模型预测输入，通常需要随机访问、排序、过滤、遍历和按下标访问。
  - `IOBuf` 的优势是分段 buffer 的零拷贝拼接和网络写出，不适合承担候选集容器职责。此类代码不建议迁移。

- **避免在热路径中调用 `to_string()` / `copy_to_cstr()`**
  - 如果迁移后为了兼容旧接口频繁执行：
    ```cpp
    std::string s = iobuf.to_string();
    ```
    那么会重新引入完整拷贝，甚至比原始 `std::string` 路径更差。
  - 对 `process/response_function.cpp`、`processor/video_launch/response_for_grg.cpp` 这类核心响应路径，需要重点检查是否存在为了日志、打点、debug 而把完整 body 转成 string 的行为。
  - 日志建议只做限长采样，避免打印完整 response body。

- **生命周期和跨线程持有需要谨慎**
  - `IOBuf` 通过 Block 引用计数共享底层内存，跨线程传递是可行的，但如果业务把大 `IOBuf` 长时间挂在异步上下文、缓存对象或闭包中，会导致 Block 迟迟不能回收到 TLS Block 池。
  - 对召回汇聚场景，尤其是 `feeda-mv-grc/processor/video_launch/response_for_grg.cpp` 这种可能扇出到多个下游的路径，需要确认：
    - 下游回调是否及时释放；
    - 是否有请求级上下文长期持有 response body；
    - 是否存在失败重试导致多个 IOBuf 副本同时引用同一批 Block。

- **大对象不一定适合直接塞入默认 Block**
  - brpc 默认 Block 通常为 8KB，小块走 TLS Block 池更高效。
  - 对特别大的 embedding、doc feature blob、图片/视频相关扩展字段，如果已经有外部连续内存，可以评估 `append_user_data`，避免额外复制。
  - 但 `append_user_data` 需要明确 deleter 和内存所有权，迁移时必须保证：
    - deleter 不重复释放；
    - 外部 buffer 在 IOBuf 生命周期内有效；
    - 不把栈内存或临时 string 的 data 错误挂入 IOBuf。

---

---
*本章节由 Hermes Agent 自动分析生成，基于代码库静态扫描结果。*
