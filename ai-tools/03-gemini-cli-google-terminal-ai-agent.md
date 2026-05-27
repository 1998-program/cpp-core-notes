# 03 · Gemini CLI — Google 的开源终端 AI Agent

**项目**：[google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)
**Stars**：104.7k ⭐ · TypeScript · Apache-2.0
**定位**：终端 AI agent，由 Google 官方出品，免费额度极高，内置代码理解/生成/工具调用能力

---

## 根本问题

Claude Code 优秀但价格昂贵；Gemini CLI 是 Google 推出的同类产品，针对个人开发者提供了极具竞争力的免费额度——使用 Google 账号登录每分钟可发 60 次请求、每天 1000 次，足以支撑日常开发工作。它直接在终端运行，读取本地代码库，支持 MCP、Google Search、本地文件操作等工具，是 Claude Code 的有力替代/补充。

---

## 核心工作原理

Gemini CLI 架构与 Claude Code 高度相似，但有几个差异化设计：

```
用户输入
    ↓
ReAct Agent Loop（Gemini Flash 2.0 默认，可换 2.5 Pro/Ultra）
    ↓
工具路由引擎
    ├── 本地工具：read_file, write_file, run_shell, search_files
    ├── Google 工具：google_search（实时搜索，原生集成）
    ├── MCP 工具：任意第三方 MCP Server
    └── Workspace 工具：Google Docs/Sheets（付费功能）
    ↓
结果流式输出到终端
```

**与 Claude Code 的主要差异**：
1. **Google Search 原生集成**：`@search` 前缀直接触发实时搜索，不需要额外配置
2. **免费额度**：Google 账号免费，Gemini 2.0 Flash + 1M token 上下文窗口
3. **`GEMINI.md`**：对应 Claude Code 的 `CLAUDE.md`，项目级系统提示词配置

```typescript
// GEMINI.md 示例（放在项目根目录）
# Project Context
This is a C++ recommendation service using brpc + Protobuf + jemalloc.
Key files: src/server.cpp (brpc server), proto/rec.proto (service definition)
Build: BCLOUD (similar to Bazel), run `bcmake` to build

# Code Style
- Use absl:: containers for hot path, not std::
- Always use RAII for resource management
- Log with LOG(INFO)/LOG(WARNING) (glog style)
```

---

## 安装 / 快速上手

```bash
# 安装
npm install -g @google/gemini-cli

# 登录（Google 账号，免费）
gemini

# 或用 API key（更高额度）
export GEMINI_API_KEY=your_key
gemini
```

基本使用：

```bash
# 在代码库目录里直接启动
cd /your/project
gemini

# 对话示例
> 解释一下这个项目的整体架构
> @search Gemini 2.5 Pro 最新基准测试结果
> 帮我给 src/cache.cpp 写单元测试
```

---

## 实践案例

**场景**：需要快速理解一个陌生的 C++ 服务代码库（1000+ 文件），找到性能瓶颈，并结合最新的性能优化技术给出改进建议。

**流程**：

```bash
# 1. 在项目目录启动 Gemini CLI
cd ~/workspace/rec-service
gemini

# 2. 代码库理解（利用 1M token 大上下文）
> 读取整个 src/ 目录，梳理服务的请求处理流程，重点关注热路径

# Gemini 会自动 read_file 遍历关键文件，输出：
# "主入口：src/server.cpp，RPC handler：src/handler.cpp
#  热路径：接收请求 → 特征提取(src/feature.cpp) → 模型打分(src/scorer.cpp) → 排序
#  瓶颈点：feature.cpp:234 行有个 std::unordered_map 在请求级别重复创建"

# 3. 结合最新优化技术（原生 Google Search）
> @search absl::flat_hash_map vs std::unordered_map benchmark 2025

# Gemini 实时搜索，返回最新基准数据，不是训练时的过时数据

# 4. 生成改进代码
> 基于以上分析，把 feature.cpp 里的热路径 unordered_map 替换为 absl::flat_hash_map

# 5. 运行验证
> 帮我写一个 micro-benchmark 对比改前改后的性能
```

**示例输出（步骤 2 节选）**：
```
📁 Reading src/ directory... (47 files)

## Request Processing Hot Path

1. Server::HandleRequest() [server.cpp:89]
   └── FeatureExtractor::Extract() [feature.cpp:112]  ← 主瓶颈
       ├── std::unordered_map<int64_t, float> scores  ← 每请求创建，开销大
       └── ItemStore::BatchGet() [store.cpp:78]        ← 潜在 N+1 问题
   └── Scorer::Score() [scorer.cpp:203]
   └── Ranker::Rank() [ranker.cpp:56]

Identified: feature.cpp:234 creates std::unordered_map per request (~200k ops/sec)
Recommendation: Use absl::flat_hash_map or thread-local pool
```

这个场景展示了 Gemini CLI 的两个核心优势：超大上下文一次性读懂整个代码库，以及 Google Search 集成确保建议基于最新技术。

---

## 关键特性速查

- **1M token 上下文**：可一次性读入整个中型代码库，不需要手动指定文件
- **Google Search 原生集成**：`@search` 实时查询，代码建议基于最新文档和技术
- **免费高额度**：Google 账号每天 1000 次请求，Gemini 2.0 Flash，足够日常使用
- **MCP 支持**：与 Claude Code 共享 MCP 生态，Context7、GitHub MCP 等均可使用
- **`GEMINI.md` 项目配置**：放在项目根目录，自动加载为系统提示词，让 agent 了解项目背景

---

**GitHub**：https://github.com/google-gemini/gemini-cli
