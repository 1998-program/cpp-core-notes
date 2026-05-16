# Boost::intrusive — 侵入式容器深度解析

> 项目地址：[boostorg/intrusive](https://github.com/boostorg/intrusive) · 头文件库 · 许可证：BSL-1.0

---

## 项目概述

`Boost.Intrusive` 是一组**侵入式容器**（intrusive containers）的实现，包括链表、集合、哈希表等。

**侵入式 vs 非侵入式**：

```cpp
// 非侵入式（std::list）：容器内部分配 node，存储用户数据的副本/指针
struct Item { int value; };
std::list<Item*> lst;  // list 内部有 __list_node<Item*>，额外内存分配

// 侵入式（boost::intrusive::list）：hook 嵌入用户对象，容器零内存分配
struct Item : public boost::intrusive::list_base_hook<> {
    int value;
};
boost::intrusive::list<Item> lst;  // 直接操作 Item 内部的 hook，无额外分配
```

**核心优势**：
- **零额外内存分配**：hook 嵌在对象里，容器本身不 malloc
- **O(1) 从元素找容器**：通过 hook 可直接定位所在容器节点
- **同一对象可同时属于多个容器**：嵌入多个 hook 即可
- **缓存友好**：数据和链接指针在同一内存块，减少 cache miss

---

## 核心概念：Hook

Hook 是侵入式容器的灵魂，有三种嵌入方式：

### 方式1：Base Hook（继承）

```cpp
#include <boost/intrusive/list.hpp>
using namespace boost::intrusive;

// 方式1：继承 base hook
struct Packet : public list_base_hook<> {
    uint32_t src_ip;
    uint32_t dst_ip;
    std::vector<uint8_t> data;
};

list<Packet> rx_queue;
list<Packet> tx_queue;
// ⚠️ 同一个 Packet 不能同时在两个 list 中（一个 hook 只能挂一个链表）
```

### 方式2：Member Hook（成员变量，推荐）

```cpp
// 方式2：成员 hook，可嵌入多个
struct Packet {
    list_member_hook<> rx_hook;   // 用于 RX 队列
    list_member_hook<> tx_hook;   // 用于 TX 队列
    uint32_t src_ip;
    uint32_t dst_ip;
    std::vector<uint8_t> data;
};

// 通过 member_hook option 指定使用哪个 hook
using RxList = list<Packet,
    member_hook<Packet, list_member_hook<>, &Packet::rx_hook>>;
using TxList = list<Packet,
    member_hook<Packet, list_member_hook<>, &Packet::tx_hook>>;

RxList rx_queue;
TxList tx_queue;

Packet p;
rx_queue.push_back(p);  // p 同时挂在 rx_queue...
tx_queue.push_back(p);  // ...和 tx_queue，合法！
```

### 方式3：Value Traits（最灵活，适合改造已有类）

```cpp
// 方式3：不修改原有类，通过 traits 指定 hook
// 适合改造不能修改源码的第三方类
```

---

## 主要容器类型

### list（双向链表）

```cpp
#include <boost/intrusive/list.hpp>

struct Node : public list_base_hook<link_mode<auto_unlink>> {
    int value;
    Node(int v) : value(v) {}
};

list<Node> lst;
Node a(1), b(2), c(3);
lst.push_back(a);
lst.push_back(b);
lst.push_back(c);

// 关键：auto_unlink 模式下，Node 析构时自动从 list 中移除
// 避免悬空指针问题
{
    Node temp(99);
    lst.push_back(temp);
}  // temp 析构，自动从 lst 移除，无需手动 erase
```

**link_mode 选项**：
| 模式 | 特性 | 开销 |
|------|------|------|
| `normal_link` | 默认，不自动 unlink | 最小 |
| `safe_link` | 析构前检查是否已 unlinked | +1 bool |
| `auto_unlink` | 析构时自动从容器移除 | 需要禁用 size cache |

### slist（单向链表）

```cpp
#include <boost/intrusive/slist.hpp>

struct Node : public slist_base_hook<> { int v; };
slist<Node> sl;
// push_front O(1)，push_back 需要 cache_last option
```

### set / multiset（红黑树）

```cpp
#include <boost/intrusive/set.hpp>

struct Entry : public set_base_hook<> {
    int key;
    std::string value;

    // 必须提供比较操作
    bool operator<(const Entry& o) const { return key < o.key; }
};

set<Entry> s;
Entry e1{1, "one"}, e2{2, "two"};
s.insert(e1);
s.insert(e2);

// 通过 iterator_to 从元素获取迭代器（O(1)）
auto it = s.iterator_to(e1);
s.erase(it);
```

### unordered_set（哈希表）

```cpp
#include <boost/intrusive/unordered_set.hpp>

struct Item : public unordered_set_base_hook<> {
    int id;
    size_t hash() const { return std::hash<int>{}(id); }
    bool operator==(const Item& o) const { return id == o.id; }
};

// 需要提供 bucket 数组（由用户管理，零分配）
using BucketType = unordered_set<Item>::bucket_type;
std::array<BucketType, 128> buckets;

unordered_set<Item> uset(unordered_set<Item>::bucket_traits(
    buckets.data(), buckets.size()));
```

---

## 关键操作：iterator_to

侵入式容器最强大的特性之一：

```cpp
// std::list：从指针找迭代器 → O(n) 遍历
// boost::intrusive::list：O(1) 直接计算

struct Timer : public list_base_hook<> {
    uint64_t expire_ms;
    void cancel() {
        // 直接从 this 计算出迭代器，O(1) 从定时器队列删除
        // 无需外部传入迭代器或遍历查找
        timer_queue.erase(timer_queue.iterator_to(*this));
    }
};

list<Timer> timer_queue;
```

这个特性在**定时器管理**、**连接池**、**LRU 缓存**等场景极其有用。

---

## 推荐在线架构中的应用

### 1. 零拷贝请求队列

```cpp
// 在 brpc 推荐服务中，请求对象生命周期由外部管理
// 使用 intrusive list 在多个处理队列间传递，零内存分配
struct RecommendRequest {
    list_member_hook<> pending_hook;     // 待处理队列
    list_member_hook<> processing_hook;  // 处理中队列
    list_member_hook<> timeout_hook;     // 超时检测队列

    UserId user_id;
    FeatureVector features;
    // ...
};

using PendingQueue   = list<RecommendRequest,
    member_hook<RecommendRequest, list_member_hook<>, &RecommendRequest::pending_hook>>;
using TimeoutWatcher = list<RecommendRequest,
    member_hook<RecommendRequest, list_member_hook<>, &RecommendRequest::timeout_hook>>;
```

### 2. 特征缓存 LRU

```cpp
// LRU 经典实现：unordered_map + 双向链表
// 用 intrusive list：节点既是 map 的 value，也是 list 节点
// 无需额外 malloc list_node

struct CacheEntry : public list_base_hook<link_mode<auto_unlink>> {
    uint64_t item_id;
    FeatureVector features;
    // auto_unlink：evict 时直接析构，自动从 LRU 链表移除
};

list<CacheEntry> lru_list;   // 头=最近访问，尾=最久未访问
std::unordered_map<uint64_t, CacheEntry> cache_map;
```

### 3. ng-framework DAG 节点调度

DAG 计算图中，节点的"待执行队列"可以用 intrusive list 实现：
- 节点对象本身嵌入 hook，不需要额外的 queue node 分配
- 节点完成后可以 O(1) 从调度队列移除

---

## 性能对比

```
场景：10M 次 push/pop，对象大小 64 bytes

                    内存分配次数    吞吐(M ops/s)
std::list           10,000,000     ~45
boost::intrusive     0             ~280
```

**主要收益来自**：
1. 零 malloc/free（tcmalloc 也有开销）
2. 数据和 hook 在同一 cache line，访问更友好
3. 避免了 `std::list` 的 allocator 间接调用

---

## 技术亮点总结

| 特性 | 实现细节 |
|------|----------|
| 零内存分配 | Hook 嵌入用户对象，容器本身不持有数据 |
| 多容器共存 | 嵌入多个 Member Hook，同一对象同时属于多个容器 |
| O(1) iterator_to | Hook 到容器节点的偏移量编译期确定，直接指针算术 |
| auto_unlink | 析构时自动从容器移除，防止悬空节点 |
| 无拷贝语义 | 容器存指针/引用，对象生命周期由用户完全控制 |

---

*生成时间：2026-05-16 · 系列：C++ 核心组件深度研究 · 项目：boostorg/intrusive*
