# #16 · FlatBuffers — 零拷贝序列化设计解析

> **仓库**: [google/flatbuffers](https://github.com/google/flatbuffers) · `include/flatbuffers/`  
> **定位**: 序列化后的 buffer 可以**直接解引用访问字段，无需解析/拷贝步骤**，延迟只有一次内存读取

---

## 一句话价值

**vtable 间接寻址 + 相对 offset**——字段读取 = 查 vtable 偏移 + 一次解引用，零拷贝、向前/向后兼容、天然支持 mmap 直接访问 buffer。

---

## 核心内存布局

```
Buffer (从高地址向低地址构建，读取时从低到高)：

+------------------+  ← buf 起始
| root offset (4B) |  → 指向 root Table
+------------------+
|   [vtable]       |  ← Table 前面，负偏移访问
|   vtsize (2B)    |
|   data_size (2B) |
|   field_0 (2B)   |  → 字段0在 data_ 中的偏移，0表示不存在
|   field_1 (2B)   |
|   ...            |
+------------------+
|   [Table data_]  |  ← Table::data_[0] 起点
|   soffset_t(4B)  |  → 指向 vtable（负值）
|   field values   |
|   inline scalars |
|   offsets to ... |  → 嵌套 Table/String/Vector
+------------------+
|   [nested objs]  |
+------------------+
```

**关键设计**：vtable 可以在多个 Table 间**共享**（内容相同时复用），节省空间。

---

## vtable 访问原理（Table::GetField 逐行解析）

```cpp
// table.h
const uint8_t* GetVTable() const {
    // data_[0..3] 存的是 soffset_t（有符号），vtable 在 data_ 前面
    return data_ - ReadScalar<soffset_t>(data_);
}

voffset_t GetOptionalFieldOffset(voffset_t field) const {
    auto vtable = GetVTable();
    auto vtsize = ReadScalar<voffset_t>(vtable);  // vtable 第一个字段是自身大小
    // field >= vtsize 说明这是新版 schema 加的字段，老 buffer 没有 → 返回 0
    return field < vtsize ? ReadScalar<voffset_t>(vtable + field) : 0;
}

template <typename T>
T GetField(voffset_t field, T defaultval) const {
    auto field_offset = GetOptionalFieldOffset(field);
    // field_offset == 0 表示字段不存在，返回默认值（兼容性核心）
    return field_offset ? ReadScalar<T>(data_ + field_offset) : defaultval;
}
```

**整个读取路径只有**：
1. `data_[0..3]` → 找到 vtable
2. `vtable[field]` → 找到字段偏移
3. `data_[offset]` → 读取值

共 3 次内存读取，无任何 parse 步骤。

---

## Struct vs Table 的根本区别

```
Struct（flatbuffers/struct.h）：
  - 字段按固定偏移存储，无 vtable
  - 所有字段必须存在，不可省略，不向后兼容
  - 访问：直接 reinterpret_cast，零开销
  - 适合：坐标、颜色等固定小对象

Table（flatbuffers/table.h）：
  - 字段通过 vtable 间接寻址
  - 字段可以缺失（返回默认值），支持版本演进
  - 访问：3 次内存读取
  - 适合：大多数业务对象
```

```cpp
// FLATBUFFERS_MANUALLY_ALIGNED_STRUCT 保证跨编译器布局一致
FLATBUFFERS_MANUALLY_ALIGNED_STRUCT(4) Vec3 {
  float x_;
  float y_;
  float z_;
  // 直接读：reinterpret_cast<Vec3*>(data) 无任何开销
} FLATBUFFERS_STRUCT_END(Vec3, 12);
```

---

## 向前/向后兼容性机制

```
旧代码读新 buffer（新字段）：
  vtsize 变大，新字段 field_offset 超出旧 vtsize
  → GetOptionalFieldOffset 返回 0
  → GetField 返回 defaultval
  → 无崩溃，静默忽略

新代码读旧 buffer（缺字段）：
  旧 buffer vtable 没有新字段的槽
  → 同样返回 0 → defaultval
  → 无崩溃
```

**代价**：新增字段必须有 default value；字段不能删除（只能废弃）；字段编号不能复用。

---

## FlatBufferBuilder：从高向低构建

```cpp
// flatbuffer_builder.h 核心设计
class FlatBufferBuilder {
    vector_downward buf_;  // 从 buf 末尾向前增长
    // ...
};
```

为什么倒序构建：
- offset 是「当前位置到目标的相对距离」
- 倒序保证所有 offset 都是正数（指向已写入的数据）
- 最终 `Finish()` 写入 root offset，返回的 buffer 直接可用

```cpp
// 典型构建流程
flatbuffers::FlatBufferBuilder fbb;
auto name = fbb.CreateString("Alice");

MonsterBuilder builder(fbb);
builder.add_name(name);
builder.add_hp(100);
auto monster = builder.Finish();
fbb.Finish(monster);

// 访问（零拷贝，直接在 buf 上解引用）
auto* m = GetMutableRoot<Monster>(fbb.GetBufferPointer());
assert(m->hp() == 100);
m->mutate_hp(200);  // in-place 修改，无需重新序列化
```

---

## In-place Mutation（推荐系统的热更新场景）

```cpp
// 标量字段可以原地修改（不改变 buffer 大小）
m->mutate_hp(200);         // ✅ 直接写回 buffer
m->mutate_position(&pos);  // ✅ Struct 字段原地修改

// 不支持原地修改的：
m->mutate_name("Bob");  // ❌ String 长度可能变化，不支持
```

**推荐系统场景**：用户特征 buffer 存在 Redis，热路径直接 mmap，修改分数后写回，无需反序列化 → 修改 → 再序列化整个对象。

---

## 与 Protobuf 对比

| 特性 | FlatBuffers | Protobuf |
|------|-------------|----------|
| 读取前是否需要 parse | ❌ 不需要 | ✅ 需要 ParseFromString |
| 内存拷贝 | 零拷贝（直接读 buffer） | 需要拷贝到对象 |
| 随机访问字段 | O(1) | 需要先完整 parse |
| 原地修改 | ✅ 标量支持 | ❌ 需重新序列化 |
| 编码体积 | 稍大（vtable overhead） | 更紧凑（varint 编码） |
| Schema 必须 | ✅ 编译时 | ✅ 编译时（动态可选） |
| 流式读取 | ❌ | ✅ |
| 适合场景 | 高频读、低延迟、大 buffer | 网络传输、紧凑编码 |

---

## 关键文件索引

```
include/flatbuffers/
├── flatbuffers.h          # 总入口，聚合所有头文件
├── table.h                # Table 类：vtable 访问核心（本文重点）
├── flatbuffer_builder.h   # FlatBufferBuilder：构建 buffer
├── struct.h               # Struct：固定布局，无 vtable
├── vector.h               # Vector 包装
├── verifier.h             # 安全校验，防 OOB 读
├── vector_downward.h      # 倒序构建的内部 buffer
└── base.h                 # uoffset_t/soffset_t/voffset_t 类型定义
```

---

*自动生成 · 2026-05-20 · OpenClaw Daily Task*
