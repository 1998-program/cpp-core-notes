# FlatBuffers · 零拷贝序列化设计

> **库**：[google/flatbuffers](https://github.com/google/flatbuffers) ⭐ 23k+  
> **日期**：2026-05-02  
> **主题**：零拷贝、vtable 寻址、schema evolution、Cache-line 优化

---

## 一、设计思想

传统序列化（Protobuf / JSON）的本质是「编码 → 传输 → 解码」三步走：数据先被打包成字节流，读取时再反序列化回内存对象，这意味着每次访问都要经历一次内存分配和拷贝。

FlatBuffers 的核心洞察是：**既然最终要放到内存里，为什么不直接把内存布局当作序列化格式？**

它设计了一套 **vtable + offset** 机制，让 buffer 本身就是可以直接寻址的内存结构：
- 读取时零解析、零拷贝、零内存分配
- 写入时倒序构建（构建子对象后再引用），代价稍高
- 对读多写少场景（游戏资产、网络协议、ML 模型参数）极为划算

---

## 二、核心实现

### 内存布局

```
低地址                                          高地址
[root_offset(4B)] [data...] [object] [vtable_offset(4B)] [vtable]
                                      ↑ 对象起始
```

### vtable 结构

```cpp
// vtable 存储在对象之前（负偏移方向）
struct VTable {
  uint16_t vtable_size;    // vtable 总字节数
  uint16_t object_size;    // 对象总字节数（含 vtable_offset 字段）
  uint16_t field_0_offset; // 字段0相对于对象起始的偏移，0 = 字段不存在
  uint16_t field_1_offset;
  // ...
};
```

### 零拷贝读取路径

```cpp
// 1. 从 buffer 头部拿 root offset，仅做指针转换，无任何分配
inline const Monster* GetMonster(const void* buf) {
  return flatbuffers::GetRoot<Monster>(buf);
}

// 2. 访问字段时展开为：
//    *(base + vtable[field_index])
auto hp = monster->hp();
// 展开：
//   int32_t vtable_offset = *(int32_t*)obj_ptr;  // 读 vtable 负偏移
//   uint16_t field_off = *(uint16_t*)(obj_ptr - vtable_offset + 4); // 查 vtable
//   return *(int16_t*)(obj_ptr + field_off);  // 直接寻址
```

### vtable 去重机制

Builder 内部维护一个 `vtable_cache`，构建对象前对当前 vtable 内容做哈希，命中则复用已有 vtable，**相同布局的多个对象共用同一份 vtable**，大幅压缩 schema 重复字段的开销：

```cpp
// flatbuffers/include/flatbuffers/flatbuffers.h（简化）
uoffset_t EndTable(uoffset_t start) {
  auto it = std::find_if(vtables_.begin(), vtables_.end(),
    [&](uoffset_t vt) {
      return memcmp(vtable_ptr, buf_.data_at(vt), vt_size) == 0;
    });
  if (it != vtables_.end()) {
    WriteScalar(object_offset, *it - object_offset); // 复用，不新建
  }
}
```

---

## 三、性能优化原理

### 🔥 Cache-line 友好访问

连续字段按声明顺序紧密排列，顺序读取触发 CPU 硬件预取（Hardware Prefetcher）。
相比 Protobuf 的 tag-value 链表（字段散布在堆上的多个对象），局部性好 **3~5 倍**：

```
// Protobuf 对象：字段分散在堆上
[Message*] → [field_1: string*] → [heap: "hello"]
           → [field_2: int32_t = 42]  // 可能在另一个 cache line

// FlatBuffers：字段紧凑排列在 buffer 中
[hp: 300][mana: 150][name_offset: 24][...]
  ↑                                  ↑
  同一个 64B cache line 内
```

### 🔥 零分配读取

`GetRoot<T>()` 只做一次指针转换，所有字段访问都是栈上的偏移计算，**不触发任何 malloc/free**，对 GC-less 系统（游戏 tick、低延迟交易）至关重要。

### 🔥 Schema Evolution 向前兼容

旧代码读新数据时，新字段的 vtable offset 超出旧 vtable 大小 → 自动返回默认值，无需版本号字段，兼容成本为零：

```cpp
// 源码中的字段访问辅助函数
template<typename T> T GetField(voffset_t field, T defaultval) const {
  auto field_offset = GetOptionalFieldOffset(field);
  return field_offset ? ReadScalar<T>(data_ + field_offset) : defaultval;
  //                    ↑ offset=0 时直接返回默认值，无异常
}
```

---

## 四、使用示例

### 1. 定义 Schema（monster.fbs）

```fbs
namespace MyGame.Sample;

enum Color:byte { Red = 0, Green, Blue = 2 }

table Monster {
  pos:Vec3;
  mana:short = 150;
  hp:short = 100;
  name:string;
  color:Color = Blue;
}

root_type Monster;
```

### 2. 生成代码

```bash
./flatc --cpp monster.fbs
# 生成 monster_generated.h
```

### 3. 写入 Buffer

```cpp
#include "monster_generated.h"
using namespace MyGame::Sample;

flatbuffers::FlatBufferBuilder builder(1024); // 预分配，减少 realloc

auto name    = builder.CreateString("Orc");
auto monster = CreateMonster(builder, nullptr, 150, 300, name, 0, Color_Red);
builder.Finish(monster);

uint8_t* buf = builder.GetBufferPointer();
int      size = builder.GetSize();
// buf 可直接 memcpy 传输或 mmap 落盘
```

### 4. 读取（零拷贝）

```cpp
// 假设 buf 来自网络/文件，直接解引用
auto m = GetMonster(buf);
assert(m->hp()   == 300);
assert(m->name()->str() == "Orc"); // string_view，无拷贝

// 安全校验（生产环境务必加）
flatbuffers::Verifier verifier(buf, size);
assert(VerifyMonsterBuffer(verifier));
```

---

## 五、性能基准

| 操作 | FlatBuffers | Protobuf 3 | Cap'n Proto | JSON (nlohmann) |
|------|-------------|------------|-------------|-----------------|
| 读取延迟 | ~1 ns | ~45 ns | ~2 ns | ~800 ns |
| 内存分配次数 | **0** | N 次 | 0 | N 次 |
| 序列化吞吐 | ~800 MB/s | ~400 MB/s | ~850 MB/s | ~50 MB/s |
| 二进制大小 | 中 | 小 | 小 | 大 |

> 数据来源：[FlatBuffers 官方 benchmark](https://google.github.io/flatbuffers/flatbuffers_benchmarks.html)，读取性能领先 Protobuf **35~50 倍**。

---

## 六、适用场景与限制

**✅ 适合**
- 游戏引擎资产文件（读多写少，文件直接 mmap 使用）
- 高频网络协议（推荐服务、广告引擎的内部 RPC）
- ML 推理服务的模型参数传递
- 嵌入式/实时系统（无 GC、无分配）

**❌ 不适合**
- 写密集场景（倒序构建开销大）
- 需要跨语言 schema registry 管理的大型系统（Protobuf 生态更成熟）
- 人类可读需求（用 JSON）

---

*下一篇：[absl::flat_hash_map · SIMD 哈希探测](./02-absl-flat-hash-map.md)*
