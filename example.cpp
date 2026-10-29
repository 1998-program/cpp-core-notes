#include <cstdio>
#include <string>

#include "leveldb/slice.h"

int main() {
  // 4 字节头部 + 3 字节负载 a 0x00 b，共 7 字节，没有尾随字节
  const char record[] = "\x00\x00\x00\x03" "a\0b";

  // 引用整块缓冲；sizeof(record) - 1 是 7
  leveldb::Slice whole(record, sizeof(record) - 1);

  // 负载从偏移 4 字节处（第 5 个字节）开始、长度 3：用（指针, 长度）把窗口限定死
  leveldb::Slice payload(whole.data() + 4, 3);

  std::printf("whole=%d payload=%d shared=%d nul=%d\n",
              static_cast<int>(whole.size()),
              static_cast<int>(payload.size()),
              (payload.data() == whole.data() + 4) ? 1 : 0,
              (payload[1] == '\0') ? 1 : 0);

  // remove_prefix 在自己的窗口内收缩：3 -> 2，仍落在负载区间
  leveldb::Slice tail = payload;
  tail.remove_prefix(1);
  std::printf("tail=%d tail_is_payload_plus_1=%d\n",
              static_cast<int>(tail.size()),
              (tail.data() == payload.data() + 1) ? 1 : 0);

  // 只有需要独立副本时才复制；这里只会复制那 3 字节
  std::string owned = payload.ToString();
  std::printf("owned=%d\n", static_cast<int>(owned.size()));
  return 0;
}
