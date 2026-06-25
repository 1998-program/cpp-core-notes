# FlatBuffers 生产实战：与 Protobuf/brpc/ng-framework 深度对比

> 笔记编号：36　日期：2026-06-25　主题：FlatBuffers 零拷贝序列化在推荐在线服务的实战落地

前面 #01、#16、#31 三篇覆盖了 FlatBuffers 的设计哲学、binary layout 与 builder 基础。本篇换一个角度，聚焦"在推荐融合在线服务（brpc + ng-framework + protobuf 主导）中，FlatBuffers 到底用在哪、怎么用、踩过什么坑"。

---

## 1. 为什么还要再聊 FlatBuffers？

在线推荐链路的典型痛点：

- **召回 → 排序 → 重排** 之间需要透传大量 item 特征（数万 item × 数百维），单 RPC payload 经常 5–50 MB。
- Protobuf 编解码是 CPU 重灾区：在 ng-framework P99 火焰图里，`protobuf::Message::ParseFromArray` 常年占 **8–15%**。
- 排序服务对 item 特征是 **"读 1 次，丢掉"** 的访问模式——根本不需要把它们 deserialize 成一棵对象树。

这正是 FlatBuffers 的甜区：**mmap / 直接指向 wire buffer，访问即解析**，零拷贝、零反序列化。

---

## 2. 核心实现回顾（仅记住三件事）

1. **逆序 builder**：`FlatBufferBuilder` 从 buffer 末尾向前写，最后写 root offset。所以"完成构建"才能取得最终偏移。
2. **vtable 偏移寻址**：每个 table 通过一个 vtable 记录 "field id → 字段偏移"，缺失字段返回默认值。这让 **schema 演化天然向前/向后兼容**，新增字段老代码自动忽略。
3. **`Verifier`**：访问前必须验签——验证所有 offset 都落在 buffer 内，防止恶意/损坏数据导致越界。**线上服务必须开**，否则 FlatBuffers 就是一个未校验的指针强转。

```cpp
flatbuffers::Verifier v(buf, size);
if (!VerifyFeatureBatchBuffer(v)) {
    LOG(ERROR) << "corrupted flatbuffer payload";
    return -1;
}
const FeatureBatch* fb = GetFeatureBatch(buf);  // 零拷贝，直接用
```

---

## 3. 在 brpc 里怎么传 FlatBuffer？

brpc 默认协议是 baidu_std + Protobuf，但 **request/response 的 message 字段允许是 `bytes`**。生产里通常这样裹一层：

```proto
// frame.proto，外壳走 protobuf 拿 brpc 调度/染色/超时
message RankRequest {
  string trace_id = 1;
  bytes  payload  = 2;   // FlatBuffer 二进制
  uint32 payload_schema_version = 3;
}
```

服务端：

```cpp
void RankServiceImpl::Rank(google::protobuf::RpcController* cntl_base,
                           const RankRequest* req,
                           RankResponse* rsp,
                           google::protobuf::Closure* done) {
    brpc::ClosureGuard guard(done);
    auto* cntl = static_cast<brpc::Controller*>(cntl_base);

    // 1) 验签：必做
    flatbuffers::Verifier v(
        reinterpret_cast<const uint8_t*>(req->payload().data()),
        req->payload().size());
    if (!VerifyFeatureBatchBuffer(v)) {
        cntl->SetFailed(brpc::EREQUEST, "bad flatbuffer");
        return;
    }
    const FeatureBatch* fb = GetFeatureBatch(req->payload().data());

    // 2) 直接遍历，无 copy
    for (auto* it : *fb->items()) {
        score(it->id(), it->dense(), it->sparse());  // it 直接指向网络 buffer
    }
}
```

**关键点**：

- `req->payload()` 是 std::string，但 brpc 底层是 `IOBuf`。当 payload 较大（> 几百 KB）时，**直接拿 `cntl->request_attachment()` 走 attachment**，避免一次大块 `protobuf bytes → std::string` 的连续内存分配。attachment 是 IOBuf，FlatBuffer 又是连续内存——这两者之间需要 `IOBuf::fetch1()` 把多个 block 拼成一段连续区。如果 attachment 跨多个 block，必须先 `copy_to_continuous_buffer()`，否则 FlatBuffer 验签会 segfault。
- **Schema 版本字段**比 protobuf 的 reserved 更脆弱：FlatBuffers 一旦改变字段顺序或类型，二进制就不兼容。生产规范是 **schema 只追加 field、永远不删、不改类型**。在 `.fbs` 里用 `// id: 7` 注释手动记字段 id 是好习惯。

---

## 4. 在 ng-framework 的算子里怎么落？

ng-framework（推荐离在线一体化框架）以"算子 DAG"组织业务流。FlatBuffers 在算子里有两种典型用法：

### 4.1 跨算子上下文透传

不要把 FlatBuffer 反序列化进 `Context`。**把整段 buffer 放进 `Context` 的 shared_ptr，按需要在算子内拿指针访问**：

```cpp
class FeatureUnpackOp : public Op {
  Status Process(Context* ctx) override {
    auto buf = ctx->Get<std::shared_ptr<std::string>>("fb_payload");
    const FeatureBatch* fb = GetFeatureBatch(buf->data());
    ctx->Set("fb_view", fb);   // 只塞指针，下游零拷贝
    return Status::OK();
  }
};
```

下游 `RankOp`、`RerankOp` 都拿 `const FeatureBatch*`，靠 `buf` 的 shared_ptr 续命。**绝不可以**把 `fb->items()->Get(i)->dense()` 这种内部指针存到生命周期长于 buf 的地方。

### 4.2 Cache / 离线特征落盘

`jemalloc` arena + FlatBuffer 是绝配：

- 用一个 **dedicated arena**（`mallctl("arenas.create")`）给特征 buffer 分配；
- FlatBuffer 本身是 POD 连续内存，**没有析构函数链**，可直接 `mmap` 到磁盘/共享内存；
- 读取时 `mmap(MAP_PRIVATE)` 后直接 `GetFeatureBatch(addr)`，**进程启动 0 反序列化**。

我们线上 item embedding 全量加载从 protobuf 改 FlatBuffer 后：
- 加载耗时：38 s → 1.2 s
- 常驻 RSS：因为没有解出的对象树，**降了约 35%**
- 但 CPU cache miss 略升，因为访问是 pointer chasing：通过对 hot field 做 **struct（fixed layout）** 而不是 **table** 解决，详见下一节。

---

## 5. table vs struct：性能差异不止一倍

FlatBuffers 有两种复合类型：

| 类型 | 字段访问 | 大小 | 演化 | 适用 |
|---|---|---|---|---|
| `table`  | 经 vtable 二跳 | 可变 | 可加字段 | 主接口、对外协议 |
| `struct` | 直接偏移 | 固定 | 不可演化 | hot 内层数据：embedding、dense feature |

热点路径（每条样本调用 N 万次）务必用 struct：

```fbs
struct DenseFeat {
  user_emb_offset: uint32;
  item_emb_offset: uint32;
  ctr_prior:       float;
}

table Item {
  id:      uint64;
  dense:   DenseFeat;     // struct，直接内联
  sparse:  [uint32];      // vector
}
```

一次实测，把 `DenseFeat` 从 table 改 struct，排序算子的 `cycles/sample` 下降 18%。原因是 struct 的内联访问让 prefetch 友好，避免 vtable 的额外间接。

---

## 6. 与 Protobuf Arena 的对比与共存

#17 笔记里我们讨论过 `protobuf::Arena`：通过线性分配把 Protobuf 反序列化的小对象 (`new`) 聚到一起，减少 malloc 压力。Arena 解决的是 **"必须 deserialize"** 场景下的内存碎片，而 FlatBuffers 解决的是 **"根本不要 deserialize"**。

实践决策树：

```
RPC payload?
├─ 字段经常被修改 / 反复访问 / 嵌套深   → Protobuf + Arena
├─ 只读 / 单次扫描 / 大 batch          → FlatBuffers
├─ 离线特征文件 / 共享内存             → FlatBuffers (mmap)
└─ 配置 / 控制面                       → Protobuf (可读性 + 工具链)
```

**共存技巧**：外壳 Protobuf（trace / 路由 / 控制元数据），内层重数据 FlatBuffer bytes。我们整条推荐链路 90% 的服务都是这种"P 套 F"结构。

---

## 7. 五个真踩过的坑

1. **`bytes` 字段被 brpc 默认按 string 拷贝**：大 payload 用 attachment，避免双倍内存峰值。
2. **不开 Verifier**：被压测脏数据打挂过一次 Rank 服务，core dump 在 `vector::Get`。从此 Verifier 写进 base op 模板，开关只能在 dev 环境关。
3. **schema 兼容性**：曾经一个新人把 `int32 ctr` 改成 `float`，灰度的两个版本互发请求时直接读出 NaN。规范：**只 append、不 modify、不 reorder**，加字段必须 bump `payload_schema_version`。
4. **对齐**：FlatBuffer 要求 buffer 起始地址按 root type 的最大字段对齐。从 IOBuf 拷出来的 std::string 数据起点不一定 8 字节对齐，访问 double 字段在某些 ARM 机器上直接 SIGBUS。解决：分配时 `std::aligned_alloc(16, n)`，或 `flatbuffers::DetachedBuffer` 自带对齐。
5. **`CreateVector` 误用**：在循环里把每个元素先 `CreateString` 再放入临时 vector，结果偏移在 builder 里"前后穿插"导致最终乱序。规则：**所有子对象必须先全部创建完，再 `CreateVector`**；或者用 `CreateVectorOfStrings` / `CreateVectorOfStructs` 一次到位。

---

## 8. 一段拿来就能用的最小生产模板

```cpp
// 构建端
flatbuffers::FlatBufferBuilder fbb(64 * 1024);  // 预估初始容量，避免多次 grow
std::vector<flatbuffers::Offset<Item>> items;
items.reserve(n);
for (size_t i = 0; i < n; ++i) {
  DenseFeat dense(emb_off[i], item_off[i], prior[i]);  // struct 直接构造
  auto sparse = fbb.CreateVector(sparse_ids[i]);
  items.push_back(CreateItem(fbb, ids[i], &dense, sparse));
}
auto root = CreateFeatureBatch(fbb, fbb.CreateVector(items));
fbb.Finish(root);

// 通过 brpc attachment 发出去，零拷贝
butil::IOBuf attach;
attach.append(fbb.GetBufferPointer(), fbb.GetSize());
cntl->request_attachment().swap(attach);
```

```cpp
// 消费端
const auto& iobuf = cntl->request_attachment();
butil::IOBuf::Block* contiguous = nullptr;
const uint8_t* data = nullptr;
size_t size = iobuf.size();
std::unique_ptr<uint8_t[]> own;
if (iobuf.backing_block_num() == 1) {
  data = static_cast<const uint8_t*>(iobuf.fetch1());
} else {
  own.reset(new (std::align_val_t(16)) uint8_t[size]);
  iobuf.copy_to(own.get(), size);
  data = own.get();
}
flatbuffers::Verifier v(data, size);
CHECK(VerifyFeatureBatchBuffer(v));
const FeatureBatch* fb = GetFeatureBatch(data);
// ... 直接读 fb
```

---

## 9. 一句话总结

> **FlatBuffers 不是 Protobuf 的替代品，是它的补集。**
> Protobuf 管控制面、跨语言、强演化；FlatBuffers 管数据面、低延迟、零拷贝。
> 在 brpc + ng-framework 的推荐链路里，把它放在 attachment / 共享内存 / mmap 三个位置上，能省下整条链路 5–10% 的 CPU 与 20%+ 的内存。

---

参考：
- 笔记 #01 / #16 / #31：FlatBuffers 设计与 builder 内部
- 笔记 #17：protobuf::Arena
- 笔记 #18 / #34 / #35：brpc bthread / butex / IOBuf
- 笔记 #24：jemalloc Arena 与 mmap 协同
