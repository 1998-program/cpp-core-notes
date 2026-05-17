# #13 · abseil::Cord — 绳索字符串 (Rope) 设计解析

> **仓库**: [abseil/abseil-cpp](https://github.com/abseil/abseil-cpp) · `absl/strings/cord.h`  
> **定位**: 为大字符串的高频 prepend/append、引用共享、零拷贝外存接管场景设计，替代 `std::string`

---

## 一句话价值

**Cord = 引用计数 B-Tree 的 chunk 序列**——拷贝是 O(1)，prepend/append 是均摊 O(1)，代价是随机访问变 O(log n)。

---

## 核心数据结构

### 1. 三层表示

```
absl::Cord
  └── contents_ (InlineRep / CordRep*)
        ├── InlineRep  ≤15 bytes → 直接存在栈上，zero-alloc
        └── CordRep* → 堆上节点树
              ├── CordRepFlat    (flat chunk，带引用计数)
              ├── CordRepExternal (零拷贝外存，持有 Releaser 回调)
              ├── CordRepBtree   (B-Tree 组织多 chunk，O(log n) 访问)
              └── CordRepCrc     (CRC wrapper，out-of-band checksum)
```

**关键选择**：从 2022 年起内部树结构从 concat tree 迁移到 **CordRepBtree**，解决了旧实现在大量 Subcord 操作后树退化（深度无界）的问题。

### 2. InlineRep（≤15 字节优化）

```cpp
// cord_internal.h (简化)
struct InlineRep {
  static constexpr size_t kMaxInline = 15;
  char data_[kMaxInline + 1];  // data_[kMaxInline] 存 size/tag
  // tag & 1 == 0  → inline，高位 7 bits 存长度
  // tag & 1 == 1  → pointer to CordRep tree
};
```

小于 16 字节时完全无堆分配，`sizeof(Cord) == 16`（与 `string_view` 相同）。

### 3. CordRepBtree

```
CordRepBtree (height=2)
  ├── CordRepBtree (height=1)
  │     ├── CordRepFlat  "Hello, "
  │     └── CordRepFlat  "World"
  └── CordRepBtree (height=1)
        ├── CordRepExternal  <mapped file region>
        └── CordRepFlat  "suffix"
```

- **叶节点（leaf）**：`CordRepFlat` 或 `CordRepExternal`
- **分支节点**：最多 `kMaxCapacity=6` 个子节点（6-ary B-Tree）
- append/prepend 时优先复用末尾 flat 节点的尾部预留空间，避免分配

---

## 关键 API 设计

### O(1) 拷贝（Copy-on-Write）

```cpp
Cord a = MakeLargeCord();  // heap tree
Cord b = a;                // ← 仅递增 CordRep 根节点 refcount，O(1)
b.Append("!");             // ← 写时复制，只 clone 到需要修改的路径
```

### 零拷贝外存接管

```cpp
// 直接把 mmap 的内存页交给 Cord 管理，Releaser 在引用归零时调用
Cord cord = absl::MakeCordFromExternal(
    absl::string_view(mmap_ptr, file_size),
    [mmap_ptr, file_size](absl::string_view) {
        munmap(mmap_ptr, file_size);
    });
// Protobuf SerializeToString 内部大量用这个路径
```

### GetAppendBuffer — 复用尾部预留容量

```cpp
void FillCord(absl::Cord& cord, size_t n) {
    bool first = true;
    while (n > 0) {
        // 第一次尝试复用 cord 末尾节点的剩余容量
        absl::CordBuffer buf = first
            ? cord.GetAppendBuffer(n)      // 可能零拷贝复用
            : absl::CordBuffer::CreateWithDefaultLimit(n);
        auto data = buf.available_up_to(n);
        GenerateData(data.data(), data.size());
        buf.IncreaseLengthBy(data.size());
        cord.Append(std::move(buf));
        n -= data.size();
        first = false;
    }
}
```

### Chunk 迭代（推荐路径）

```cpp
// 零拷贝遍历：每个 chunk 是 string_view，不发生数据拷贝
size_t ComputeHash(const absl::Cord& cord) {
    size_t h = 0;
    for (absl::string_view chunk : cord.Chunks()) {
        h = HashCombine(h, chunk);
    }
    return h;
}
// 注意：CharIterator（逐字节）比 ChunkIterator 慢，尽量用后者
```

### TryFlat / Flatten

```cpp
// 如果 cord 恰好只有一个 flat chunk，零拷贝返回 string_view
if (auto flat = cord.TryFlat(); flat.has_value()) {
    ProcessFlat(*flat);  // fast path
} else {
    // 强制合并成一块（会分配，谨慎用）
    absl::string_view flat = cord.Flatten();
    ProcessFlat(flat);
}
```

---

## 性能模型

| 操作 | 复杂度 | 备注 |
|------|--------|------|
| `Append` / `Prepend` | 均摊 O(1) | 复用末尾预留空间时近似零开销 |
| `Subcord(pos, len)` | O(log n) | B-Tree 路径截取，不拷贝数据 |
| 拷贝构造 / 赋值 | O(1) | 仅递增 refcount |
| `operator[]` | O(log n) | B-Tree 叶节点定位 |
| `ChunkIterator++` | 均摊 O(1) | BtreeReader 顺序遍历 |
| `TryFlat()` | O(1) | 快速判断是否单 flat |
| `Flatten()` | O(n) | 合并所有 chunk，谨慎调用 |

---

## 内存计量三模式

```cpp
// kTotal: 粗略，共享内存可能被多个 Cord 各自计入（默认）
cord.EstimatedMemoryUsage(absl::CordMemoryAccounting::kTotal);

// kTotalMorePrecise: 去重，精确但慢
cord.EstimatedMemoryUsage(absl::CordMemoryAccounting::kTotalMorePrecise);

// kFairShare: 按引用数分摊，适合内存用量分配审计
cord.EstimatedMemoryUsage(absl::CordMemoryAccounting::kFairShare);
```

---

## 使用陷阱

### ❌ 陷阱1：用 CharIterator 做高频逐字节扫描

```cpp
// 慢：CharIterator 每次 ++ 都可能跨 chunk 边界检查
for (char c : cord.Chars()) { process(c); }

// 快：ChunkIterator，在 chunk 内部是线性扫描
for (absl::string_view chunk : cord.Chunks()) {
    for (char c : chunk) { process(c); }
}
```

### ❌ 陷阱2：对小数据使用 Cord

```cpp
// Bad: Cord 有 16-byte 最小开销 + tree 节点分配
absl::Cord small_cord("hello");  // ≤15字节会 inline，还可接受
                                  // 但不要用 Cord 存始终不变的短字符串

// Good: 小字符串用 std::string 或 string_view
```

### ❌ 陷阱3：MakeCordFromExternal 的生命周期 Bug

```cpp
void Foo(const char* buf, size_t len) {
    // ⚠️ BUG: Releaser 是空 lambda，buf 生命周期可能提前结束
    auto cord = absl::MakeCordFromExternal(
        {buf, len}, [](absl::string_view) {});
    Bar(cord);  // Bar 可能持有 cord 的 SubCord，延长 buf 生命周期
}  // buf 这里析构，但 cord 的 chunk 还指向它！

// Fix: Releaser 必须管理 buf 的真实生命周期
```

### ❌ 陷阱4：临时 Cord 的 Chunks() 生命周期

```cpp
// UB: CordFactory() 返回的临时对象在 for 头部析构
for (auto chunk : CordFactory().Chunks()) {  // ← 悬空迭代器
    ...
}
// Fix: 先绑定到具名变量
absl::Cord cord = CordFactory();
for (auto chunk : cord.Chunks()) { ... }
```

---

## 对推荐在线架构的实际提升

### 1. Protobuf 序列化 → brpc 传输链路

brpc 的 `butil::IOBuf` 与 `absl::Cord` 是同一设计思路（分段 refcount buffer）。理解 Cord 的 chunk tree 有助于：
- 分析 `IOBuf` 在 brpc pipeline 中的零拷贝路径
- 避免 `Flatten()` 类似的 `IOBuf::fetch()` 触发大拷贝
- 用 `MakeCordFromExternal` 思路实现 zero-copy RPC response（直接把 jemalloc arena block 的 ptr 附加引用计数）

### 2. ng-framework DAG 节点间大 tensor 传递

DAG 节点输出经常是大 blob（特征向量、中间结果）。Cord 的 O(1) SubCord 可以映射为：
- 节点 A 的输出 Cord 被节点 B、C 同时引用（refcount+1），无拷贝
- 节点 B 只消费前半段（SubCord 截取），节点 C 消费后半段，内存 fair-share

### 3. CRC 校验 out-of-band 存储

```cpp
// Cord 支持 out-of-band CRC32C，不影响数据内容和 hash
cord.SetExpectedChecksum(crc32c::Value(data, len));
// 传输层可以校验，不改变 cord 的语义
auto crc = cord.ExpectedChecksum();  // nullopt if not set
```

推荐服务里 feature cache 的 integrity check 可以借鉴这种「数据与校验分离」的模式。

---

## 关键文件索引

```
abseil-cpp/
├── absl/strings/
│   ├── cord.h                    # 主要公开 API
│   ├── cord.cc                   # Append/Prepend/Subcord 实现
│   ├── cord_buffer.h             # CordBuffer，GetAppendBuffer 返回类型
│   └── internal/
│       ├── cord_internal.h       # InlineRep, CordRep 基类
│       ├── cord_rep_btree.h      # B-Tree 节点实现
│       ├── cord_rep_btree_reader.h # BtreeReader，ChunkIterator 依赖
│       ├── cord_rep_flat.h       # Flat chunk + 预留容量
│       ├── cord_rep_crc.h        # CRC wrapper 节点
│       └── cordz_info.h          # Cordz 采样分析（生产诊断）
└── absl/crc/
    └── internal/crc_cord_state.h # CRC state 随 Cord 传播
```

---

*自动生成 · 2026-05-17 · OpenClaw Daily Task*
