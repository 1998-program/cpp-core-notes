# 04 · GitHub MCP Server — 让 AI Agent 直接操作 GitHub

**项目**：[github/github-mcp-server](https://github.com/github/github-mcp-server)
**Stars**：30.2k ⭐ · Go · MIT
**定位**：GitHub 官方 MCP Server，将 GitHub API 封装为标准 MCP 工具，让 AI agent 在不离开终端的情况下管理 issue、PR、CI 等

---

## 根本问题

AI coding agent（Claude Code、Cursor 等）写完代码后，后续的 GitHub 工作流——开 PR、回 review 评论、查 CI 状态、搜索相关 issue——仍然需要手动切换到浏览器完成，打断了"全在终端"的开发体验。GitHub MCP Server 把 GitHub REST API 和 GraphQL API 包装成 40+ 个标准 MCP 工具，让 agent 可以在同一个对话里完成从"写代码"到"PR 合并"的全流程，不用离开终端。

---

## 核心工作原理

GitHub MCP Server 是一个**协议适配层**，运行在本地，通过 stdio 接收 MCP 请求并转发给 GitHub API：

```
Claude Code / Cursor / Gemini CLI
    ↓ MCP (stdio)
GitHub MCP Server（本地子进程）
    ↓ REST API / GraphQL
GitHub API（github.com）
    ↓
Response → 转换为 MCP tool result → 返回给 agent
```

**6 大类 40+ 工具**：

| 类别 | 代表工具 |
|------|---------|
| 仓库 | `search_repositories`, `get_file_contents`, `list_branches` |
| Issue | `create_issue`, `add_issue_comment`, `list_issues` |
| PR | `create_pull_request`, `merge_pull_request`, `get_pull_request_diff` |
| Code Search | `search_code` |
| Actions | `list_workflow_runs`, `get_workflow_run_logs` |
| 用户/组织 | `list_organization_repositories` |

**鉴权**：通过 `GITHUB_TOKEN` 环境变量注入 PAT（Personal Access Token）。支持 Fine-grained PAT，可精确控制到单仓库的读写权限，安全性高。

---

## 安装 / 快速上手

```bash
# 方法一：通过 Claude Code 安装
claude mcp add github-mcp-server \
  -e GITHUB_TOKEN=ghp_your_token_here \
  -- npx @modelcontextprotocol/server-github

# 方法二：本地安装（npm）
npm install -g @modelcontextprotocol/server-github
export GITHUB_TOKEN=ghp_your_token_here

# 方法三：Docker（隔离运行）
docker run -e GITHUB_TOKEN=ghp_xxx ghcr.io/github/github-mcp-server
```

**Token 权限最小化建议**：只用来读 issue 和 PR 时，只需 `repo:read`；需要写操作才加 `repo` 完整权限。

---

## 实践案例

**场景**：维护一个开源工具库，有 30+ 个待处理 issue，需要批量分类、回复重复 issue、对重要 bug 开 PR 并追踪 CI 状态。以前每个操作都要手动开浏览器，现在用 Claude Code + GitHub MCP 一气呵成。

**流程**：

```bash
# 在 Claude Code 里对话
> 列出 1998-program/cpp-core-notes 里所有 open issue，按 reactions 数量排序

# Claude 调用 list_issues 工具，返回：
# Issue #47: "Add notes on DPDK rte_ring" (12 reactions)
# Issue #43: "Wrong example in folly::fbstring" (8 reactions)  
# Issue #38: "Request: io_uring deep dive" (6 reactions)
# ...

> 对 issue #43 回复："已确认 bug，将在下一个 commit 修复"，并给它加上 bug 标签

# Claude 调用：
# 1. add_issue_comment(issue=43, body="已确认 bug，将在下一个 commit 修复")
# 2. add_issue_labels(issue=43, labels=["bug"])

> 查看最新一次 Actions 运行状态，如果失败了，找出失败原因

# Claude 调用：
# 1. list_workflow_runs → 找到最新运行 ID
# 2. get_workflow_run_logs → 返回失败日志

# 示例输出：
# ❌ Run #156 failed at step "Build and Test"
# Error: include/absl/flat_hash_map.h: No such file or directory
# Fix: Add abseil-cpp to CMakeLists.txt dependencies

> 根据这个错误，修复 CMakeLists.txt 并开一个 PR

# Claude 修复文件，然后调用 create_pull_request：
# Title: "fix: add missing abseil-cpp dependency in CMakeLists.txt"
# Base: main → Head: fix/abseil-dep
# Body: "Fixes #43. Adds missing find_package(absl REQUIRED) to CMakeLists.txt"
```

整个流程在终端内完成，不用打开浏览器，从发现 issue 到开 PR 约 5 分钟。

---

## 关键特性速查

- **官方出品**：GitHub 自己维护，工具覆盖完整，API 调用稳定
- **细粒度权限**：支持 Fine-grained PAT，token 泄露影响面可控制到最小
- **两种部署模式**：本地 stdio（推荐，token 不离本地）/ GitHub 托管 Remote（OAuth，无需安装）
- **跨仓库代码搜索**：`search_code` 工具可在任意公开仓库搜索代码，适合查找开源库用法
- **Actions 集成**：直接读 CI 日志，让 agent 自诊断构建失败原因并修复

---

**GitHub**：https://github.com/github/github-mcp-server
