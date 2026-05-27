# 10 · Claude Code Guide — 从入门到精通的完整指南

**项目**：[zebbern/claude-code-guide](https://github.com/zebbern/claude-code-guide)
**Stars**：4.2k ⭐ · 文档项目 · MIT
**定位**：Claude Code 最全面的社区指南，覆盖安装配置、核心命令、工作流、subagent、skill 开发、高级技巧，从新手到 power user 的完整路径

---

## 根本问题

Claude Code 的官方文档偏向功能列表，缺乏"怎么用好"的实战指导。很多用户用了几个月还停留在"帮我写个函数"的层面，不知道 subagent 并行、CLAUDE.md 工程化、skill 复用等高级用法。这个指南把社区积累的最佳实践系统化，让你快速跳过摸索阶段。

---

## 核心工作原理

这是一个文档项目，核心价值在于系统化整理了以下内容：

### CLAUDE.md 工程化（最高价值）

```markdown
# CLAUDE.md 最佳实践结构

## Project Overview
一句话描述项目，让 Claude 快速定位上下文

## Tech Stack
- Language: C++ (C++17)
- Build: BCLOUD (Bazel-like)
- RPC: brpc (baidu::rpc)
- Serialization: Protobuf 3
- Memory: jemalloc

## Code Conventions
- Use absl:: containers for hot path (NOT std::)
- Error handling: return Status, not exceptions
- Logging: LOG(INFO)/LOG(WARNING) (glog style)
- Build command: bcmake (NOT cmake)

## File Structure
src/          # Core service code
proto/        # Protobuf definitions  
test/         # Unit tests (gtest)
conf/         # Configuration files

## Common Tasks
- Build: bcmake && ./output/bin/server
- Test: bcmake test && ./output/test/all_tests
- Profile: MALLOC_CONF=prof:true ./output/bin/server

## DO NOT
- Never use printf for logging
- Never commit to main directly
- Never use std::unordered_map in hot path
```

### Subagent 并行工作流

```bash
# 串行（慢）：一个任务接一个任务
> 帮我重构 feature.cpp，然后写测试，然后更新文档

# 并行（快）：用 subagent 同时处理
> 用三个 subagent 并行处理：
  1. 重构 feature.cpp（优化热路径）
  2. 为 feature.cpp 写单元测试
  3. 更新 README 中的 feature 模块文档
  三个任务完成后汇总结果
```

### Slash Commands（自定义工作流）

```bash
# 在 .claude/commands/ 目录创建自定义命令
# .claude/commands/review.md
---
description: "Code review with team standards"
---
Review the following code against our standards:
1. Check absl:: usage in hot paths
2. Verify error handling (Status return, not exceptions)  
3. Check LOG() usage (not printf)
4. Verify BCLOUD BUILD file is updated
5. Check for missing unit tests

Code to review: $ARGUMENTS

# 使用
/review src/feature.cpp
```

---

## 安装 / 快速上手

```bash
# 克隆指南
git clone https://github.com/zebbern/claude-code-guide

# 在线阅读（推荐）
# 直接在 GitHub 上浏览 README.md 和各章节文档

# 安装 Claude Code（如果还没装）
npm install -g @anthropic-ai/claude-code
claude  # 启动
```

---

## 实践案例

**场景**：你刚加入一个新项目，需要快速上手 Claude Code 并建立高效的工作流。按照指南的推荐路径：

**第一步：建立项目 CLAUDE.md**

```bash
# 在项目根目录
cat > CLAUDE.md << 'EOF'
# rec-service

## Overview
百度推荐在线服务，C++ brpc 实现，处理推荐请求的召回/排序/过滤

## Tech Stack
- C++17, BCLOUD 构建
- brpc (baidu::rpc) RPC 框架
- Protobuf 3 序列化
- jemalloc 内存分配
- ng-framework DAG 计算图

## Build & Run
bcmake                    # 构建
./output/bin/rec_server   # 启动服务
bcmake test               # 运行测试

## Code Style
- 热路径用 absl:: 容器（flat_hash_map, InlinedVector）
- 错误处理返回 Status，不用异常
- 日志用 LOG(INFO)/LOG(WARNING)
- 禁止在热路径 new/delete

## Key Files
src/server.cpp      # brpc Server 入口
src/handler.cpp     # 请求处理主逻辑
src/feature.cpp     # 特征提取（热路径）
proto/rec.proto     # 服务接口定义
EOF
```

**第二步：建立常用 slash commands**

```bash
mkdir -p .claude/commands

# 创建 /perf-review 命令
cat > .claude/commands/perf-review.md << 'EOF'
---
description: "Review code for performance issues in hot path"
---
Review $ARGUMENTS for performance issues:
1. Check for heap allocations in hot path (new/delete/malloc)
2. Check for std:: containers that should be absl::
3. Check for unnecessary copies (should use move or ref)
4. Check for lock contention
5. Suggest specific optimizations with code examples
EOF
```

**第三步：使用 subagent 并行处理大任务**

```bash
# 在 Claude Code 里
> 我需要给 feature.cpp 做性能优化。请用 subagent 并行：
  1. 分析当前热路径的性能瓶颈
  2. 搜索 absl:: 和 folly:: 中适合替换的容器
  3. 查看 jemalloc profiling 文档，了解如何定位内存分配热点
  三个 subagent 完成后，综合结果给出优化方案
```

按照指南建立这套工作流后，日常开发效率提升显著——CLAUDE.md 确保 Claude 每次都了解项目背景，slash commands 把重复的 review 流程自动化，subagent 让并行任务不再需要等待。

---

## 关键特性速查

- **CLAUDE.md 模板**：提供多种项目类型的 CLAUDE.md 模板，直接复用
- **Subagent 模式**：详细讲解如何拆分任务、并行执行、汇总结果
- **Slash Commands**：自定义工作流命令，把重复操作变成一行命令
- **Skill 开发指南**：如何创建可复用的 skill，分享给团队
- **高级技巧**：上下文管理、token 优化、多文件编辑策略等实战经验

---

**GitHub**：https://github.com/zebbern/claude-code-guide
