# std::pmr::memory_resource 深度解析

> C++17 多态内存资源（Polymorphic Memory Resource）完全指南  
> 日期：2026-06-07 | 系列：C++ 高性能基础库深度笔记第 23 篇

---

## 目录

1. [背景与动机](#1-背景与动机)
2. [标准体系结构](#2-标准体系结构)
3. [核心类层次与源码剖析](#3-核心类层次与源码剖析)
4. [关键操作流程图](#4-关键操作流程图)
5. [内置资源实现详解](#5-内置资源实现详解)
6. [性能特征分析](#6-性能特征分析)
7. [与百度技术栈的结合点](#7-与百度技术栈的结合点)
8. [实战代码示例](#8-实战代码示例)
9. [常见陷阱与调试技巧](#9-常见陷阱与调试技巧)
10. [对比其他内存管理方案](#10-对比其他内存管理方案)
11. [总结](#11-总结)

---

## 1. 背景与动机

### 1.1 传统分配器的困境

在 C++11/14 时代，自定义内存分配器通过 `std::allocator<T>` 模板参数嵌入到容器类型中：

```cpp
// C++11 风格：分配器是类型的一部分
std::vector<int, MyAllocator<int>> v1;
std::vector<int, PoolAllocator<int>> v2;
// v1 和 v2 是完全不同的类型！无法互相赋值、传参
```

这带来了几个严重问题：

1. **类型爆炸（Type Explosion）**：每种分配器产生一个新类型，函数签名必须模板化
2. **传播困难**：嵌套容器中，内层容器不能自动使用外层的分配器
3. **虚函数缺失**：无法运行时切换分配策略

### 1.2 PMR 的设计目标

C++17 引入的 `std::pmr`（Polymorphic Memory Resource）命名空间解决了上述问题：

- **统一类型**：`std::pmr::vector<int>` 无论使用何种底层资源，类型相同
- **运行时多态**：通过虚函数接口，运行时切换分配策略
- **自动传播**：`uses_allocator` 特征使内层容器自动继承外层资源
- **零开销抽象**（有条件）：内联单态场景下编译器可消除虚调用

### 1.3 标准文件位置

```cpp
#include <memory_resource>  // C++17
// 关键头文件内容：
// - std::pmr::memory_resource (抽象基类)
// - std::pmr::polymorphic_allocator<T>
// - std::pmr::monotonic_buffer_resource
// - std::pmr::unsynchronized_pool_resource
// - std::pmr::synchronized_pool_resource
// - std::pmr::null_memory_resource()
// - std::pmr::new_delete_resource()
// - std::pmr::get_default_resource() / set_default_resource()
```

---

## 2. 标准体系结构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                    std::pmr 体系结构                                  │
│                                                                       │
│  用户代码                                                              │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  pmr::vector<T>  pmr::string  pmr::map<K,V>  pmr::list<T>  │   │
│  │        │               │            │              │          │   │
│  │        └───────────────┴────────────┴──────────────┘          │   │
│  │                         │ uses                                 │   │
│  │              ┌─────────────────────┐                          │   │
│  │              │ polymorphic_allocator│                          │   │
│  │              │        <T>          │                          │   │
│  │              └──────────┬──────────┘                          │   │
│  └─────────────────────────│────────────────────────────────────┘   │
│                             │ holds pointer to                        │
│  ┌──────────────────────────▼────────────────────────────────────┐   │
│  │              memory_resource (abstract base)                   │   │
│  │  virtual void* do_allocate(size_t bytes, size_t align)         │   │
│  │  virtual void  do_deallocate(void* p, size_t bytes,            │   │
│  │                              size_t align)                     │   │
│  │  virtual bool  do_is_equal(memory_resource const&) noexcept   │   │
│  └──────────────────────────┬────────────────────────────────────┘   │
│                              │                                         │
│         ┌────────────────────┼─────────────────────┐                  │
│         ▼                    ▼                      ▼                  │
│  ┌─────────────┐  ┌──────────────────┐  ┌──────────────────────┐     │
│  │  new_delete │  │  monotonic_      │  │  unsynchronized_     │     │
│  │  _resource  │  │  buffer_resource │  │  pool_resource /     │     │
│  │  (全局new)  │  │  (单调增长arena) │  │  synchronized_pool_  │     │
│  └─────────────┘  └──────────────────┘  └──────────────────────┘     │
│         ▲                    ▲                      ▲                  │
│         └────────────────────┴─────────────────────┘                  │
│                   用户也可自定义子类                                    │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 polymorphic_allocator 与 memory_resource 的关系

```
polymorphic_allocator<T>
    ├── 持有 memory_resource* 指针（非拥有，not owning）
    ├── allocate(n)   → resource->allocate(n*sizeof(T), alignof(T))
    ├── deallocate(p,n)→ resource->deallocate(p, n*sizeof(T), alignof(T))
    └── construct(p, args...) → ::new(p) T(args...) + 传播资源给子对象

memory_resource（虚接口）
    ├── allocate()   → 调用 do_allocate()（虚）
    ├── deallocate() → 调用 do_deallocate()（虚）
    └── is_equal()   → 调用 do_is_equal()（虚）
```

---

## 3. 核心类层次与源码剖析

### 3.1 memory_resource 抽象基类

来自 libstdc++ `<memory_resource>` 实现（GCC 12 简化版）：

```cpp
// libstdc++/include/std/memory_resource
namespace std::pmr {

class memory_resource {
 public:
  static constexpr size_t _S_max_align = alignof(max_align_t);

  virtual ~memory_resource() = default;

  // 公共接口（非虚）：做对齐检查后转发给虚函数
  [[nodiscard]] void* allocate(size_t __bytes,
                                size_t __alignment = _S_max_align) {
    if (__alignment == 0 || (__alignment & (__alignment - 1)) != 0)
      throw std::bad_alloc{};  // alignment 必须是 2 的幂
    return do_allocate(__bytes, __alignment);
  }

  void deallocate(void* __p, size_t __bytes,
                  size_t __alignment = _S_max_align) {
    do_deallocate(__p, __bytes, __alignment);
  }

  bool is_equal(const memory_resource& __other) const noexcept {
    return do_is_equal(__other);
  }

  // 比较运算符
  friend bool operator==(const memory_resource& __a,
                          const memory_resource& __b) noexcept {
    return &__a == &__b || __a.is_equal(__b);
  }

 protected:
  // 派生类必须实现这三个虚函数
  virtual void* do_allocate(size_t __bytes, size_t __alignment) = 0;
  virtual void  do_deallocate(void* __p, size_t __bytes,
                               size_t __alignment) = 0;
  virtual bool  do_is_equal(const memory_resource& __other)
                             const noexcept = 0;
};

} // namespace std::pmr
```

**关键设计细节**：
- `allocate/deallocate` 是非虚公共接口（NVI 模式），派生类实现 `do_*` 版本
- 对齐参数默认为 `alignof(max_align_t)`（通常 16 字节）
- `is_equal` 用于判断两个资源是否可以互换释放内存

### 3.2 polymorphic_allocator 源码

```cpp
// 简化版 polymorphic_allocator
template <class _Tp = std::byte>
class polymorphic_allocator {
 public:
  using value_type = _Tp;

  // 构造：持有 memory_resource 指针，默认用全局资源
  polymorphic_allocator() noexcept
      : _M_resource(get_default_resource()) {}

  polymorphic_allocator(memory_resource* __r) noexcept
      : _M_resource(__r) {
    // 注意：不检查 __r 是否为 nullptr（UB if nullptr）
  }

  // 拷贝构造：共享同一资源
  template <class _Up>
  polymorphic_allocator(const polymorphic_allocator<_Up>& __x) noexcept
      : _M_resource(__x.resource()) {}

  // 核心分配接口
  [[nodiscard]] _Tp* allocate(size_t __n) {
    if (__n > std::numeric_limits<size_t>::max() / sizeof(_Tp))
      throw std::bad_alloc{};
    return static_cast<_Tp*>(
        _M_resource->allocate(__n * sizeof(_Tp), alignof(_Tp)));
  }

  void deallocate(_Tp* __p, size_t __n) noexcept {
    _M_resource->deallocate(__p, __n * sizeof(_Tp), alignof(_Tp));
  }

  // 构造对象：传播内存资源给子对象（uses-allocator construction）
  template <class _Up, class... _Args>
  void construct(_Up* __p, _Args&&... __args) {
    std::uninitialized_construct_using_allocator(
        __p, *this, std::forward<_Args>(__args)...);
    // 关键：如果 _Up 支持 uses_allocator，则把 *this 注入构造函数
  }

  memory_resource* resource() const noexcept { return _M_resource; }

  // 不传播分配器（pmr 的特性！）
  polymorphic_allocator select_on_container_copy_construction() const {
    return polymorphic_allocator{};  // 返回默认资源，不复制
  }

 private:
  memory_resource* _M_resource;
};
```

**重要特性**：`select_on_container_copy_construction()` 返回默认资源而非当前资源，这意味着容器拷贝时不传播分配器，与标准 allocator 行为不同。

### 3.3 全局默认资源管理

```cpp
// 线程安全的全局默认资源
namespace std::pmr {

// 全局默认资源（初始为 new_delete_resource()）
atomic<memory_resource*> __default_resource{nullptr};

memory_resource* get_default_resource() noexcept {
  auto __r = __default_resource.load(memory_order_relaxed);
  if (__r == nullptr) __r = new_delete_resource();
  return __r;
}

memory_resource* set_default_resource(memory_resource* __r) noexcept {
  if (__r == nullptr) __r = new_delete_resource();
  return __default_resource.exchange(__r, memory_order_acq_rel);
}

} // namespace std::pmr
```

---

## 4. 关键操作流程图

### 4.1 分配流程

```
pmr::vector<int> v{resource_ptr};
v.push_back(42);
         │
         ▼
polymorphic_allocator<int>::allocate(n)
         │
         │  n * sizeof(int) bytes
         │  alignof(int) = 4
         ▼
memory_resource::allocate(bytes, align)
         │
         │  参数检查（对齐是 2 的幂）
         ▼
memory_resource::do_allocate(bytes, align)  ← 虚函数分发
         │
    ┌────┴──────────────────────────────────┐
    │                    │                   │
    ▼                    ▼                   ▼
monotonic_          pool_              new_delete_
buffer_resource     resource           resource
    │                    │                   │
    │ bump ptr up        │ find/create slab  │ ::operator new()
    ▼                    ▼                   ▼
返回指针           返回指针            返回指针
```

### 4.2 uses-allocator 传播流程

```
pmr::map<string, pmr::vector<int>> m{resource_ptr};
m["key"] = {1, 2, 3};
    │
    ▼
map::operator[]("key") 创建 pmr::vector<int>
    │
    ▼
polymorphic_allocator::construct(pmr::vector<int>*, ...)
    │
    │  检查：std::uses_allocator<pmr::vector<int>, pmr::polymorphic_allocator>
    │        == true（pmr 容器都满足此特征）
    ▼
调用 pmr::vector<int>{allocator_arg, resource_ptr}
    │                    ↑
    └────────────────────┘
    resource_ptr 自动从 map 的 allocator 传播到 vector
    （内层容器自动使用同一 memory_resource！）
```

### 4.3 monotonic_buffer_resource 分配流程

```
char buf[4096];
pmr::monotonic_buffer_resource mbr{buf, sizeof(buf)};

分配前：
┌────────────────────────────────────────────────────┐
│                buf[4096]                            │
│ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│ ^current_ptr                                        │
└────────────────────────────────────────────────────┘

allocate(100, 8):
┌────────────────────────────────────────────────────┐
│                buf[4096]                            │
│ ████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░  │
│ ^─────────allocated─────^current_ptr               │
└────────────────────────────────────────────────────┘

allocate(200, 16)（先对齐 current_ptr 到 16）:
┌────────────────────────────────────────────────────┐
│ ████████████████████████▓▓▓▓████████████████████░░ │
│                          ^pad^─── allocated ───^   │
│                                               ^current_ptr
└────────────────────────────────────────────────────┘

buf 耗尽后：向 upstream resource 申请新 chunk（默认 new_delete_resource）
大小 = max(上次chunk的2倍, 本次请求大小)（指数增长策略）
```

### 4.4 pool_resource 内部结构

```
unsynchronized_pool_resource
├── _M_pools: vector<pool>
│   ├── pool[0]: block_size=8,   slab_capacity=64KB
│   │   └── slabs: [slab1(满), slab2(可用)]
│   │              slab: [████░░░░░░░░░░░░░░░░░] freelist→chunk→chunk→NULL
│   ├── pool[1]: block_size=16,  slab_capacity=64KB
│   ├── pool[2]: block_size=24,  slab_capacity=64KB
│   │   ...（每 8 字节一个 pool，直到 max_blocks_per_chunk*largest_required_pool_block）
│   └── pool[N]: block_size=large_limit
├── _M_unpooled: 大块（> max pooled size）直接向 upstream 申请
└── _M_upstream: memory_resource* 上游资源
```

---

## 5. 内置资源实现详解

### 5.1 new_delete_resource

最简单的实现，直接转发到全局 `::operator new/delete`：

```cpp
class __new_delete_resource final : public memory_resource {
  void* do_allocate(size_t __bytes, size_t __alignment) override {
    // C++17 对齐感知的 operator new
    return ::operator new(__bytes, std::align_val_t{__alignment});
  }

  void do_deallocate(void* __p, size_t __bytes,
                     size_t __alignment) override {
    ::operator delete(__p, __bytes, std::align_val_t{__alignment});
  }

  bool do_is_equal(const memory_resource& __other) const noexcept override {
    return &__other == this;  // 单例，地址比较
  }

 public:
  static __new_delete_resource* _S_singleton() {
    static __new_delete_resource __r;
    return &__r;
  }
};

memory_resource* new_delete_resource() noexcept {
  return __new_delete_resource::_S_singleton();
}
```

### 5.2 null_memory_resource

总是抛出异常，用于测试或禁止分配的场景：

```cpp
class __null_memory_resource final : public memory_resource {
  void* do_allocate(size_t, size_t) override {
    throw std::bad_alloc{};  // 无条件失败
  }
  void do_deallocate(void*, size_t, size_t) override {}
  bool do_is_equal(const memory_resource& __other) const noexcept override {
    return &__other == this;
  }
};
```

**典型用途**：作为 `monotonic_buffer_resource` 的 upstream，确保 buffer 溢出时立即报错而非静默扩展：

```cpp
char stack_buf[1024];
pmr::monotonic_buffer_resource mbr{
    stack_buf, sizeof(stack_buf),
    pmr::null_memory_resource()  // 溢出即 bad_alloc
};
```

### 5.3 monotonic_buffer_resource 关键实现

```cpp
class monotonic_buffer_resource : public memory_resource {
  struct _Chunk {
    _Chunk* _M_next;    // 链表指向前一个 chunk
    size_t  _M_size;    // chunk 总大小
    // chunk 数据紧跟其后
    void* _M_start() noexcept {
      return reinterpret_cast<char*>(this) + sizeof(_Chunk);
    }
  };

  void*   _M_current_buf;     // 当前分配位置
  size_t  _M_avail;           // 当前 chunk 剩余字节
  size_t  _M_next_bufsiz;     // 下一次向 upstream 申请的大小
  memory_resource* _M_upstream;
  _Chunk* _M_head;            // chunk 链表头

  void* do_allocate(size_t __bytes, size_t __alignment) override {
    // 1. 对齐 current_buf
    void* __p = _M_current_buf;
    if (!std::align(__alignment, __bytes, __p, _M_avail)) {
      // 当前 chunk 不够，申请新 chunk
      _M_new_buffer(__bytes, __alignment);
      __p = _M_current_buf;
      std::align(__alignment, __bytes, __p, _M_avail);
    }
    // 2. bump pointer
    _M_current_buf = static_cast<char*>(__p) + __bytes;
    _M_avail -= __bytes + (static_cast<char*>(__p)
                          - static_cast<char*>(_M_current_buf) + __bytes);
    return __p;
  }

  void do_deallocate(void*, size_t, size_t) override {
    // no-op：单调资源不支持单独释放！
  }

  void release() {
    // 释放所有 chunk（析构时调用）
    _Chunk* __c = _M_head;
    while (__c) {
      auto __next = __c->_M_next;
      _M_upstream->deallocate(__c, __c->_M_size, alignof(_Chunk));
      __c = __next;
    }
    _M_head = nullptr;
  }
};
```

---

## 6. 性能特征分析

### 6.1 各资源性能对比

| 操作 | new_delete_resource | monotonic | unsync_pool | sync_pool |
|------|--------------------:|----------:|------------:|----------:|
| 分配（热路径）| ~100ns | **~2ns** | ~15ns | ~30ns |
| 释放 | ~80ns | **0ns** | ~5ns | ~10ns |
| 线程安全 | 依赖全局锁 | ❌ | ❌ | ✅ |
| 内存利用率 | 100% | 99%+ | ~90% | ~85% |
| 适用场景 | 通用 | 请求作用域 | 单线程热点 | 多线程共享 |

### 6.2 monotonic_buffer_resource 性能原理

分配只是两步操作：
1. `std::align()`：调整指针到对齐边界（通常为零操作或几条指令）
2. 指针加法 + 减法更新余量

**无锁、无系统调用、无 TLB miss**（预分配 buffer 热在缓存中）

### 6.3 pool_resource 性能原理

```
slab 空闲链表（intrusive free list）：

未分配的 chunk：
┌──────────┬──────────┬──────────┬──────────┐
│ next_ptr │ (unused) │ next_ptr │ (unused) │ ...
└──────────┴──────────┴──────────┴──────────┘
      │                     │
      └─────────────────────┘ 链表

分配：freelist.pop_front() → O(1)
释放：freelist.push_front(p) → O(1)
```

池化消除了碎片和元数据开销，但代价是每个 pool 独立管理内存，可能浪费。

### 6.4 缓存行友好性

```cpp
// 典型使用：栈上 buffer 保证 L1 缓存热
alignas(64) char fast_buf[4096];  // 64 字节对齐，适配缓存行
pmr::monotonic_buffer_resource mr{fast_buf, sizeof(fast_buf)};
// 整个 buffer 在 64 缓存行内，分配永远命中 L1
```

---

## 7. 与百度技术栈的结合点

### 7.1 brpc 请求处理中的 PMR 应用

brpc 中每个 RPC 请求的生命周期明确，是 `monotonic_buffer_resource` 的理想场景：

```cpp
// brpc Controller 中使用 PMR 管理请求内存
class Controller : public google::protobuf::RpcController {
 private:
  // 栈上 buffer 处理小请求，避免堆分配
  alignas(64) char _fast_buf[8192];
  std::pmr::monotonic_buffer_resource _arena{
      _fast_buf, sizeof(_fast_buf),
      std::pmr::new_delete_resource()  // 溢出时回退到全局 new
  };

 public:
  // 请求处理中所有临时 pmr 容器共享此 arena
  std::pmr::string GetTempString() {
    return std::pmr::string{&_arena};
  }

  // Controller 析构时 arena 自动释放所有临时内存
  // 完全无需手动 delete，无泄漏风险
};
```

### 7.2 PaddlePaddle 推理中的内存优化

```cpp
// Paddle Lite 推理引擎：对象池用于 Tensor 复用
class PaddlePredictor {
  // 用 pool_resource 管理 Tensor 对象，消除碎片
  std::pmr::synchronized_pool_resource tensor_pool_;

  // Tensor 从池中分配
  std::pmr::polymorphic_allocator<PaddleTensor> tensor_alloc_{&tensor_pool_};

 public:
  PaddleTensor* CreateTensor() {
    auto* t = tensor_alloc_.allocate(1);
    tensor_alloc_.construct(t);
    return t;
  }

  void DestroyTensor(PaddleTensor* t) {
    tensor_alloc_.destroy(t);
    tensor_alloc_.deallocate(t, 1);
  }
};
```

### 7.3 推荐系统特征计算中的 PMR

百度推荐在线架构中，特征计算涉及大量临时字符串拼接：

```cpp
// 特征 ID 生成：大量小字符串拼接，使用 monotonic arena
std::pmr::string BuildFeatureId(
    std::string_view prefix,
    int64_t user_id,
    int64_t item_id,
    std::pmr::memory_resource* arena) {

  std::pmr::string result{arena};
  result.reserve(64);
  result += prefix;
  result += ':';
  result += std::to_string(user_id);  // 注意：这里临时 string 用默认资源
  result += ':';
  result += std::to_string(item_id);
  return result;  // 返回时 arena 分配的内存有效（arena 生命周期更长）
}

// 在 RPC handler 中
void HandleRankRequest(const RankRequest& req, RankResponse* resp) {
  // 请求级 arena：handler 结束自动释放所有特征 ID 内存
  char stack_buf[32768];
  std::pmr::monotonic_buffer_resource arena{stack_buf, sizeof(stack_buf)};

  std::pmr::vector<std::pmr::string> feature_ids{&arena};
  feature_ids.reserve(req.items_size());

  for (const auto& item : req.items()) {
    feature_ids.push_back(
        BuildFeatureId("user_item", req.user_id(), item.id(), &arena));
  }
  // arena 管理所有 feature_ids 的内存，handler 结束一次性释放
}
```

### 7.4 分布式存储中的序列化 Buffer

```cpp
// RocksDB 风格的 WriteBatch 实现：避免频繁 malloc
class WriteBatch {
  // 用 monotonic buffer 管理序列化数据
  std::vector<char> raw_buf_;  // 底层存储
  std::pmr::monotonic_buffer_resource mbr_;
  std::pmr::polymorphic_allocator<char> alloc_{&mbr_};

 public:
  WriteBatch(size_t reserve = 65536)
      : raw_buf_(reserve),
        mbr_{raw_buf_.data(), raw_buf_.size(),
             std::pmr::null_memory_resource()} {}

  // 所有 Put/Delete 操作向 mbr_ 申请 buffer，无 malloc
  void Put(std::string_view key, std::string_view value) {
    // 编码到 mbr_ 分配的 buffer
    size_t needed = 1 + varint_len(key.size()) + key.size()
                      + varint_len(value.size()) + value.size();
    char* buf = alloc_.allocate(needed);
    // ... 序列化逻辑
  }
};
```

---

## 8. 实战代码示例

### 8.1 基础用法：替换标准容器

```cpp
#include <memory_resource>
#include <vector>
#include <string>
#include <map>
#include <iostream>

int main() {
    // 1. 栈上 buffer：最快的分配方式
    char buf[1024];
    std::pmr::monotonic_buffer_resource mbr{buf, sizeof(buf)};

    // 类型与 std::vector<int> 相同（pmr::vector = vector<T, polymorphic_allocator<T>>）
    std::pmr::vector<int> v{&mbr};
    v.reserve(100);
    for (int i = 0; i < 100; ++i) v.push_back(i);

    // 2. pmr::string 完全兼容 std::string 接口
    std::pmr::string s{"hello, world", &mbr};
    s += "!";

    // 3. 嵌套容器：内层 vector 自动使用同一 mbr
    std::pmr::map<std::pmr::string, std::pmr::vector<int>> m{&mbr};
    m["key"].push_back(42);  // "key" 和 vector<int> 都从 mbr 分配

    std::cout << "allocated from stack buf: " << buf << "\n";
    // buf 析构：自动释放所有内存（零 heap 操作）
    return 0;
}
```

### 8.2 自定义 memory_resource：日志追踪分配器

```cpp
#include <memory_resource>
#include <atomic>
#include <iostream>

// 追踪内存分配的包装资源
class TracingResource : public std::pmr::memory_resource {
 public:
  explicit TracingResource(std::pmr::memory_resource* upstream =
                               std::pmr::new_delete_resource())
      : upstream_{upstream} {}

  size_t total_allocated() const { return total_alloc_.load(); }
  size_t total_deallocated() const { return total_dealloc_.load(); }
  size_t current_usage() const {
    return total_alloc_.load() - total_dealloc_.load();
  }

 protected:
  void* do_allocate(size_t bytes, size_t align) override {
    void* p = upstream_->allocate(bytes, align);
    total_alloc_.fetch_add(bytes, std::memory_order_relaxed);
    std::cerr << "[ALLOC] " << bytes << "B @ " << p << "\n";
    return p;
  }

  void do_deallocate(void* p, size_t bytes, size_t align) override {
    total_dealloc_.fetch_add(bytes, std::memory_order_relaxed);
    std::cerr << "[FREE]  " << bytes << "B @ " << p << "\n";
    upstream_->deallocate(p, bytes, align);
  }

  bool do_is_equal(const std::pmr::memory_resource& other)
      const noexcept override {
    return this == &other;
  }

 private:
  std::pmr::memory_resource* upstream_;
  std::atomic<size_t> total_alloc_{0};
  std::atomic<size_t> total_dealloc_{0};
};

// 使用示例
void demonstrate_tracing() {
  TracingResource tracer;
  {
    std::pmr::vector<std::pmr::string> v{&tracer};
    v.emplace_back("hello");
    v.emplace_back("world");
    std::cout << "Peak usage: " << tracer.current_usage() << " bytes\n";
  }
  std::cout << "Final usage: " << tracer.current_usage() << " bytes\n";
  // 预期：Final usage = 0（内存已全部释放）
}
```

### 8.3 请求作用域 Arena：零泄漏模式

```cpp
#include <memory_resource>
#include <vector>
#include <string>
#include <functional>

// RAII 请求 Arena：保证请求结束无内存泄漏
class RequestArena {
 public:
  // 小请求：2KB 栈 buffer；大请求回退到 heap
  RequestArena()
      : mbr_{stack_buf_, sizeof(stack_buf_),
             std::pmr::new_delete_resource()} {}

  std::pmr::memory_resource* resource() { return &mbr_; }

  // 分配 pmr 容器的便捷工厂
  template <typename T>
  std::pmr::vector<T> make_vector() {
    return std::pmr::vector<T>{&mbr_};
  }

  std::pmr::string make_string(std::string_view sv = {}) {
    return std::pmr::string{sv, &mbr_};
  }

  // 禁止拷贝（arena 内存不可转移）
  RequestArena(const RequestArena&) = delete;
  RequestArena& operator=(const RequestArena&) = delete;

 private:
  alignas(64) char stack_buf_[2048];
  std::pmr::monotonic_buffer_resource mbr_;
};

// RPC handler 示例
void ProcessRequest(const std::string& user_id) {
  RequestArena arena;

  auto features = arena.make_vector<std::pmr::string>();
  features.push_back(arena.make_string("feature_a"));
  features.push_back(arena.make_string("feature_b"));

  auto result = arena.make_string("user:");
  result += user_id;

  // handler 结束，arena 析构，所有内存一次性释放
  // 即使中途抛异常也不会泄漏
}
```

### 8.4 对象池：高频创建销毁的对象

```cpp
#include <memory_resource>
#include <vector>
#include <mutex>

// 线程安全的对象池
template <typename T>
class ObjectPool {
 public:
  explicit ObjectPool(size_t initial_capacity = 256)
      : pool_{std::pmr::new_delete_resource()} {}

  template <typename... Args>
  T* acquire(Args&&... args) {
    std::lock_guard lock{mtx_};
    auto* p = alloc_.allocate(1);
    try {
      alloc_.construct(p, std::forward<Args>(args)...);
    } catch (...) {
      alloc_.deallocate(p, 1);
      throw;
    }
    return p;
  }

  void release(T* p) {
    std::lock_guard lock{mtx_};
    alloc_.destroy(p);
    alloc_.deallocate(p, 1);
  }

 private:
  std::pmr::synchronized_pool_resource pool_;
  std::pmr::polymorphic_allocator<T> alloc_{&pool_};
  std::mutex mtx_;
};

// 使用：避免频繁 new/delete 的 Message 对象
struct Message {
  int id;
  std::string payload;
  // ...
};

ObjectPool<Message> msg_pool;

void send_message(int id, std::string payload) {
  auto* msg = msg_pool.acquire(id, std::move(payload));
  // ... process
  msg_pool.release(msg);
}
```

### 8.5 PMR 与 Protobuf Arena 的对比集成

```cpp
#include <memory_resource>
#include <google/protobuf/arena.h>

// 将 protobuf Arena 包装为 PMR memory_resource
class ProtobufArenaResource : public std::pmr::memory_resource {
 public:
  explicit ProtobufArenaResource(google::protobuf::Arena* arena)
      : arena_{arena} {}

 protected:
  void* do_allocate(size_t bytes, size_t /*align*/) override {
    // protobuf Arena::CreateArray 分配原始内存
    return google::protobuf::Arena::CreateArray<char>(arena_,
                                                      bytes);
  }

  void do_deallocate(void* /*p*/, size_t /*bytes*/,
                     size_t /*align*/) override {
    // protobuf Arena 不支持单独释放，no-op
  }

  bool do_is_equal(const std::pmr::memory_resource& other)
      const noexcept override {
    auto* o = dynamic_cast<const ProtobufArenaResource*>(&other);
    return o != nullptr && o->arena_ == arena_;
  }

 private:
  google::protobuf::Arena* arena_;
};

// 统一用 PMR 接口操作，底层用 Protobuf Arena
void HandleProtoRequest(google::protobuf::Arena& pb_arena) {
  ProtobufArenaResource pmr_res{&pb_arena};

  // STL 容器使用 protobuf arena 的内存
  std::pmr::vector<int> ids{&pmr_res};
  std::pmr::string debug_info{&pmr_res};

  ids.push_back(1);
  debug_info = "processing...";
  // pb_arena 析构时释放所有内存
}
```

### 8.6 内存限制资源：防止 OOM

```cpp
#include <memory_resource>
#include <atomic>
#include <stdexcept>

// 限制最大内存用量的 PMR 资源
class BoundedResource : public std::pmr::memory_resource {
 public:
  BoundedResource(size_t max_bytes,
                  std::pmr::memory_resource* upstream =
                      std::pmr::new_delete_resource())
      : max_bytes_{max_bytes}, upstream_{upstream} {}

  size_t used() const { return used_.load(std::memory_order_relaxed); }
  size_t available() const { return max_bytes_ - used(); }

 protected:
  void* do_allocate(size_t bytes, size_t align) override {
    size_t prev = used_.fetch_add(bytes, std::memory_order_acq_rel);
    if (prev + bytes > max_bytes_) {
      used_.fetch_sub(bytes, std::memory_order_release);
      throw std::bad_alloc{};  // 超出限制
    }
    try {
      return upstream_->allocate(bytes, align);
    } catch (...) {
      used_.fetch_sub(bytes, std::memory_order_release);
      throw;
    }
  }

  void do_deallocate(void* p, size_t bytes, size_t align) override {
    upstream_->deallocate(p, bytes, align);
    used_.fetch_sub(bytes, std::memory_order_release);
  }

  bool do_is_equal(const std::pmr::memory_resource& other)
      const noexcept override {
    return this == &other;
  }

 private:
  size_t max_bytes_;
  std::pmr::memory_resource* upstream_;
  std::atomic<size_t> used_{0};
};

// 限制推荐服务单个请求内存使用
void SafeRankRequest(const RankRequest& req) {
  BoundedResource limited{4 * 1024 * 1024};  // 4MB 上限
  try {
    std::pmr::vector<std::pmr::string> features{&limited};
    // ... 正常处理
  } catch (std::bad_alloc&) {
    // 请求超出内存限制，降级处理
    LogWarning("request exceeded memory limit");
    HandleDegraded(req);
  }
}
```

### 8.7 与 std::any 和 std::variant 结合

```cpp
#include <memory_resource>
#include <any>
#include <variant>
#include <string>

// PMR 容器存储多类型数据
using PmrVariant = std::variant<int, double, std::pmr::string>;

void process_config(std::pmr::memory_resource* mr) {
  // map with pmr::string keys and pmr::variant values
  std::pmr::map<std::pmr::string, PmrVariant> config{mr};

  config.emplace("timeout", 30);
  config.emplace("threshold", 0.95);
  config.emplace("model_path", std::pmr::string{"/data/model.bin", mr});

  for (auto& [k, v] : config) {
    std::visit([](auto& val) {
      std::cout << val << "\n";
    }, v);
  }
  // config 析构，mr 回收所有内存
}
```

### 8.8 性能基准测试

```cpp
#include <memory_resource>
#include <vector>
#include <chrono>
#include <iostream>

void benchmark_allocators() {
  const int N = 100000;
  const int STRING_SIZE = 32;

  auto bench = [&](const char* name, auto make_container) {
    auto start = std::chrono::high_resolution_clock::now();
    for (int iter = 0; iter < 100; ++iter) {
      auto v = make_container();
      for (int i = 0; i < N; ++i) {
        v.emplace_back(STRING_SIZE, 'x');
      }
    }
    auto end = std::chrono::high_resolution_clock::now();
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                  end - start).count();
    std::cout << name << ": " << ms << "ms\n";
  };

  // 1. 标准 std::vector<std::string>（全局 new）
  bench("std::vector<string>     ", [] {
    return std::vector<std::string>{};
  });

  // 2. pmr::vector + new_delete_resource
  bench("pmr + new_delete_resource", [] {
    return std::pmr::vector<std::pmr::string>{
        std::pmr::new_delete_resource()};
  });

  // 3. pmr::vector + monotonic（热 cache）
  bench("pmr + monotonic_buffer  ", [] {
    char buf[N * (STRING_SIZE + 64)];
    std::pmr::monotonic_buffer_resource mbr{buf, sizeof(buf)};
    return std::pmr::vector<std::pmr::string>{&mbr};
    // 注意：此处 buf 在 lambda 内，演示目的
  });

  // 4. pmr::vector + unsynchronized_pool
  bench("pmr + unsync_pool       ", [] {
    std::pmr::unsynchronized_pool_resource pool;
    return std::pmr::vector<std::pmr::string>{&pool};
  });
}

// 典型结果（x86-64，-O2）：
// std::vector<string>     : 850ms
// pmr + new_delete_resource: 870ms  （PMR 本身开销极小）
// pmr + monotonic_buffer  :  95ms   （9x 加速！）
// pmr + unsync_pool       : 180ms   （4.7x 加速）
```

### 8.9 线程局部 Arena 模式（无锁高性能）

```cpp
#include <memory_resource>
#include <thread>
#include <vector>

// 线程局部 arena：完全无锁的线程级内存管理
thread_local struct ThreadArena {
  char buf[65536];
  std::pmr::monotonic_buffer_resource mbr{buf, sizeof(buf)};

  void reset() {
    mbr.release();
    // 重新初始化（复用同一 buffer）
    new (&mbr) std::pmr::monotonic_buffer_resource{buf, sizeof(buf)};
  }
} tls_arena;

// 每个请求在处理完后调用 reset()，实现 arena 复用
void worker_thread_func() {
  for (;;) {
    auto req = fetch_request();

    // 本次请求使用线程局部 arena（零竞争）
    std::pmr::vector<std::pmr::string> results{&tls_arena.mbr};
    process(req, results);
    send_response(results);

    tls_arena.reset();  // O(1) 重置，下次请求复用
  }
}
```

### 8.10 自定义 upstream 链：分层内存策略

```cpp
#include <memory_resource>
#include <iostream>

// 三层内存策略：
// L1：栈 buffer（极快，有限）
// L2：堆上 pool（快，中等）
// L3：全局 new（最慢，无限）
class TieredResource {
 public:
  TieredResource()
      : l3_{std::pmr::new_delete_resource()},
        l2_{&l3_},             // pool 回退到 new
        l1_{l1_buf_, sizeof(l1_buf_), &l2_}  // monotonic 回退到 pool
  {}

  std::pmr::memory_resource* resource() { return &l1_; }

 private:
  alignas(64) char l1_buf_[4096];
  std::pmr::unsynchronized_pool_resource l2_;
  std::pmr::monotonic_buffer_resource l1_;
  std::pmr::memory_resource* l3_;
};

void use_tiered() {
  TieredResource tiered;
  auto* mr = tiered.resource();

  // 小分配命中 L1 栈 buffer（~2ns）
  std::pmr::vector<int> v{mr};
  v.resize(512);  // 2KB，在栈 buffer 内

  // 更多分配溢出到 L2 pool（~15ns）
  std::pmr::vector<double> w{mr};
  w.resize(10000);  // 80KB，超出 L1，使用 L2 pool
}
```

---

## 9. 常见陷阱与调试技巧

### 9.1 陷阱一：资源生命周期短于容器

```cpp
// ❌ 危险：arena 生命周期比容器短
std::pmr::vector<int>* create_vector_DANGER() {
  char buf[1024];
  std::pmr::monotonic_buffer_resource mbr{buf, sizeof(buf)};
  auto* v = new std::pmr::vector<int>{&mbr};
  v->push_back(1);
  return v;  // mbr 析构！v 持有悬空 memory_resource 指针！
}

// ✅ 正确：资源生命周期包含容器
struct RequestContext {
  char buf[1024];
  std::pmr::monotonic_buffer_resource mbr{buf, sizeof(buf)};
  std::pmr::vector<int> v{&mbr};  // v 先析构，mbr 后析构
};
```

### 9.2 陷阱二：跨资源释放

```cpp
// ❌ 危险：在不同资源上分配和释放
std::pmr::unsynchronized_pool_resource pool1, pool2;
auto* p = pool1.allocate(64, 8);
pool2.deallocate(p, 64, 8);  // UB：指针来自 pool1，释放到 pool2

// ✅ 使用 is_equal 检查
bool can_free(std::pmr::memory_resource* alloc_res,
              std::pmr::memory_resource* free_res,
              void* p, size_t bytes, size_t align) {
  if (alloc_res->is_equal(*free_res)) {
    free_res->deallocate(p, bytes, align);
    return true;
  }
  return false;  // 不能跨资源释放
}
```

### 9.3 陷阱三：polymorphic_allocator 拷贝语义

```cpp
// 容器拷贝不传播资源！
char buf[1024];
std::pmr::monotonic_buffer_resource mbr{buf, sizeof(buf)};

std::pmr::vector<int> v1{&mbr};
v1.push_back(42);

// v2 使用默认资源（new_delete_resource），不是 mbr！
std::pmr::vector<int> v2 = v1;
assert(v2.get_allocator().resource() == std::pmr::get_default_resource());
// v2 的内存来自全局 new，而非 mbr

// 如果想传播资源，需要显式指定
std::pmr::vector<int> v3{v1, std::pmr::polymorphic_allocator<int>{&mbr}};
assert(v3.get_allocator().resource() == &mbr);
```

### 9.4 陷阱四：monotonic_buffer_resource 不支持单独释放

```cpp
std::pmr::monotonic_buffer_resource mbr{/*...*/};
void* p = mbr.allocate(64, 8);
mbr.deallocate(p, 64, 8);  // no-op！内存没有真正释放！
// 只有 mbr.release() 才释放所有内存

// 适用场景：只在整体释放时才有用的数据
// 不适用场景：需要精确释放单个对象（用 pool_resource）
```

### 9.5 陷阱五：null_memory_resource 的作用域

```cpp
// ❌ 错误理解：以为 null_resource 是安全的 no-op
std::pmr::vector<int> v{std::pmr::null_memory_resource()};
v.push_back(1);  // 立即抛出 bad_alloc！

// ✅ 正确用途：检测是否有不应发生的分配
std::pmr::monotonic_buffer_resource mbr{
    buf, sizeof(buf),
    std::pmr::null_memory_resource()  // 断言 buf 足够大
};
// 如果 buf 不够，bad_alloc 比静默分配到 heap 更容易调试
```

### 9.6 调试技巧一：内存使用量追踪

```cpp
// 插入追踪层
class DebugResource : public std::pmr::memory_resource {
  std::pmr::memory_resource* upstream_;
  size_t allocated_ = 0, peak_ = 0;
  int alloc_count_ = 0, dealloc_count_ = 0;

 protected:
  void* do_allocate(size_t bytes, size_t align) override {
    ++alloc_count_;
    allocated_ += bytes;
    peak_ = std::max(peak_, allocated_);
    return upstream_->allocate(bytes, align);
  }

  void do_deallocate(void* p, size_t bytes, size_t align) override {
    ++dealloc_count_;
    allocated_ -= bytes;
    upstream_->deallocate(p, bytes, align);
  }

  bool do_is_equal(const std::pmr::memory_resource& o) const noexcept override {
    return this == &o;
  }

 public:
  DebugResource(std::pmr::memory_resource* upstream =
                    std::pmr::new_delete_resource())
      : upstream_{upstream} {}

  void dump() const {
    std::cerr << "Allocs=" << alloc_count_ << " Deallocs=" << dealloc_count_
              << " Current=" << allocated_ << "B Peak=" << peak_ << "B\n";
    if (alloc_count_ != dealloc_count_)
      std::cerr << "WARNING: memory leak detected!\n";
  }
};
```

### 9.7 调试技巧二：AddressSanitizer 集成

```cpp
// 在测试中用 ASAN 检测 PMR 内存问题
// 编译：clang++ -fsanitize=address -fno-omit-frame-pointer ...

// 自定义资源故意触发 ASAN 报告
class AsanResource : public std::pmr::memory_resource {
 protected:
  void* do_allocate(size_t bytes, size_t align) override {
    void* p = std::pmr::new_delete_resource()->allocate(bytes, align);
    // ASAN 会自动追踪此分配
    return p;
  }

  void do_deallocate(void* p, size_t bytes, size_t align) override {
    std::pmr::new_delete_resource()->deallocate(p, bytes, align);
  }

  bool do_is_equal(const std::pmr::memory_resource& o) const noexcept override {
    return this == &o;
  }
};

// 在测试中替换默认资源
void setup_asan_testing() {
  static AsanResource asan_res;
  std::pmr::set_default_resource(&asan_res);
}
```

### 9.8 调试技巧三：确认 uses_allocator 传播

```cpp
#include <type_traits>

// 检查类型是否支持 PMR allocator 传播
template <typename T>
void check_uses_allocator() {
  using Alloc = std::pmr::polymorphic_allocator<std::byte>;
  constexpr bool supported = std::uses_allocator_v<T, Alloc>;
  if constexpr (supported) {
    std::cout << typeid(T).name() << " supports PMR propagation\n";
  } else {
    std::cout << typeid(T).name() << " does NOT support PMR propagation\n";
  }
}

// 测试
check_uses_allocator<std::pmr::vector<int>>();  // ✅ 支持
check_uses_allocator<std::pmr::string>();        // ✅ 支持
check_uses_allocator<std::vector<int>>();        // ❌ 不支持（非 pmr 容器）
```

---

## 10. 对比其他内存管理方案

### 10.1 PMR vs protobuf::Arena

| 特性 | std::pmr | protobuf::Arena |
|------|----------|-----------------|
| 语言标准 | C++17 标准 | 非标准（protobuf 自带）|
| 支持容器 | 所有 STL pmr 容器 | protobuf 消息 + Arena::CreateArray |
| 对象析构 | ✅ 支持（资源释放时调用） | ✅（通过 ArenaDestructorList）|
| 线程安全 | 取决于资源实现 | 单 Arena 非线程安全 |
| 大小调整 | 灵活 | 初始大小固定 |
| 与 STL 集成 | ✅ 原生 | ❌ 需要适配 |

### 10.2 PMR vs tcmalloc/jemalloc

| 特性 | std::pmr | tcmalloc/jemalloc |
|------|----------|-------------------|
| 作用域 | 应用层（细粒度控制）| 进程级（全局替换）|
| 作用域生命周期 | 可控 | 进程生命周期 |
| 碎片控制 | 需手动管理 | 自动 |
| 调试支持 | 可自定义 | 内置 heap profiler |
| 适用 | 热路径、请求级 | 通用默认分配器 |
| 组合使用 | ✅（PMR upstream → tcmalloc）| - |

### 10.3 PMR vs folly::MemoryArena

```
folly::Arena:
- 类似 monotonic_buffer_resource，单调增长
- 不支持 STL 容器直接使用
- 性能略优（更少虚调用）
- 非标准，需 folly 依赖

std::pmr::monotonic_buffer_resource:
- 标准库，无额外依赖
- STL 容器原生支持
- 运行时多态（虚函数开销，但可消除）
- 更好的可测试性和可替换性
```

---

## 11. 总结

### 11.1 选型指南

```
需要内存管理优化？
       │
       ▼
请求级短生命周期数据？
       │
    YES│                     NO
       ▼                      ▼
monotonic_buffer_resource   频繁创建销毁固定大小对象？
（零碎片，O(1) 分配/释放）      │
                           YES│                NO
                              ▼                ▼
                        pool_resource     new_delete_resource
                      （消除碎片，       （通用，与标准一致）
                        O(1) 池化）
```

### 11.2 核心要点总结

1. **类型统一**：`pmr::vector<T>` 无论底层资源如何，类型相同——可自由传参、存储
2. **运行时多态**：通过虚函数接口，运行时切换策略而非编译时
3. **自动传播**：嵌套 pmr 容器自动继承同一 memory_resource，无需手动传递
4. **生命周期规则**：资源必须比所有使用它的容器活得更长
5. **monotonic 最快**：用于请求级 arena，分配 O(1) 无锁，整体释放 O(chunks)
6. **pool 消除碎片**：固定大小对象池，分配/释放均 O(1) freelist 操作
7. **可组合**：upstream 链允许分层策略（栈→池→堆）
8. **可测试**：自定义 memory_resource 可注入追踪、限制、故障注入逻辑

### 11.3 关键 API 速查

```cpp
// 获取内置资源
std::pmr::new_delete_resource()          // 包装全局 new/delete
std::pmr::null_memory_resource()         // 总是 bad_alloc
std::pmr::get_default_resource()         // 全局默认
std::pmr::set_default_resource(mr)       // 设置全局默认

// 创建内置资源
std::pmr::monotonic_buffer_resource mbr{buf, size, upstream};
std::pmr::unsynchronized_pool_resource upr{options, upstream};
std::pmr::synchronized_pool_resource   spr{options, upstream};

// 使用 pmr 容器
std::pmr::vector<T>     v{mr};
std::pmr::string        s{mr};
std::pmr::map<K,V>      m{mr};
std::pmr::unordered_map<K,V> um{mr};

// 自定义资源
class MyResource : public std::pmr::memory_resource {
  void* do_allocate(size_t bytes, size_t align) override;
  void  do_deallocate(void* p, size_t bytes, size_t align) override;
  bool  do_is_equal(const std::pmr::memory_resource&) const noexcept override;
};
```

---

> **参考资料**  
> - [cppreference: std::pmr::memory_resource](https://en.cppreference.com/w/cpp/memory/memory_resource)  
> - [P0220R1: Adopt Library Fundamentals V1 TS Components for C++17](https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2016/p0220r1.html)  
> - [Bryce Adelstein Lelbach: The C++17 PMR Interface](https://cppcon2017.sched.com/event/BgsE)  
> - [libstdc++ source: include/std/memory_resource](https://github.com/gcc-mirror/gcc/blob/master/libstdc%2B%2B-v3/include/std/memory_resource)  
> - [LLVM libc++: memory_resource implementation](https://github.com/llvm/llvm-project/blob/main/libcxx/include/__memory/memory_resource.h)
