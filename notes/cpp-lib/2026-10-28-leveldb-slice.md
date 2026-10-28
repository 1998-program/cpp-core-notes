> TEST FIXTURE — 本文件由 fixture_generator.py 生成，不是真实文章，仅用于流程测试。
> 用例标识：default

## 一个具体的小场景

假设你要缓存一批用户标签，key 和 value 都是变长字符串，这里用 leveldb::Slice 来读它们 [E1]。

## 组件是什么

leveldb::Slice 只保存一个指针和一个长度，不拥有也不复制底层字节 [E1][E2]。

## 真实例子

```cpp
#include <cstdio>
#include "leveldb/slice.h"
int main() {
  leveldb::Slice s("hello world");
  s.remove_prefix(6);
  std::printf("%s %zu\n", s.ToString().c_str(), s.size());
  return 0;
}
```

## 两条核心机制

第一，构造时只记录指针与长度，不分配内存 [E1][E3]。

第二，前缀裁剪只前移指针、减少长度，底层字节不变 [E4]。

## 取舍与边界

当你只需要一段只读视图、且能保证底层内存在视图存活期间不被释放时，用它；
需要持有数据副本时，用 ToString 显式拷贝 [E5]。

## 固定版本来源

源码固定于 commit `bb74ef739973a70ca9f0d90788a1da89b788c0ad`（https://github.com/google/leveldb.git）。
