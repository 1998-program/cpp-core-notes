# #18 · prompts.chat — 社区 Prompt 平台

> **仓库**: [f/prompts.chat](https://github.com/f/prompts.chat) · **Stars**: 143k+ · **License**: CC0  
> **定位**: 从一个 CSV 文件起家，演化为完整的 Prompt 管理平台——社区贡献、版本控制、MCP Server、多语言全覆盖

---

## 一句话价值

**1645+ 经过社区验证的 Prompt 模板 + 自托管平台 + MCP Server**——不只是「prompt 列表」，是可以直接集成进你的 Agent 工具链的 Prompt 基础设施。

---

## 核心使用场景集合

### 场景 1：直接用 CSV 批量导入 Prompt 到本地工具

最简单的用法，`prompts.csv` 是整个项目的数据源，1645 行，每行一个角色 prompt：

```bash
# 导入到 LM Studio（PR #1149 新增的官方脚本）
python3 scripts/import-to-lmstudio.py              # 全部导入
python3 scripts/import-to-lmstudio.py --dev-only   # 只导入 for_devs=TRUE 的
python3 scripts/import-to-lmstudio.py --dry-run    # 预览不写文件
```

重启 LM Studio → presets 面板直接出现所有 prompt，每个预设都带 system prompt，无需联网无需 API key。

**同理可迁移到任何接受 system prompt 的本地工具**（Ollama、Jan 等）。

---

### 场景 2：通过 API 动态拉取 Prompt 注入 Agent

平台提供公开 API，可以在 Agent 运行时按需拉取：

```bash
# 搜索 prompt
GET /api/prompts?q=linux+terminal&perPage=10

# 返回结构
{
  "data": [{
    "act": "Linux Terminal",
    "prompt": "I want you to act as a linux terminal...",
    "for_devs": true
  }]
}
```

**速率限制**（PR #1125 修复）：60 req/60s per IP，perPage 上限 100，超限返回 429 + Retry-After。

自托管时可换成 Redis 后端突破单机限制。

---

### 场景 3：MCP Server 直连 Claude Code / Cursor

```json
// .claude/mcp.json
{
  "mcpServers": {
    "prompts": {
      "command": "npx",
      "args": ["@f/prompts-mcp"]
    }
  }
}
```

接入后，在 Claude Code 里直接：
```
/prompts linux terminal     ← 搜索并注入 system prompt
/prompts code reviewer      ← 切换为 code reviewer 角色
```

不用离开终端，不用手动复制粘贴 prompt。

---

### 场景 4：自托管团队私有 Prompt 库

```bash
# Docker 一键起
docker compose up -d

# 环境变量
DATABASE_URL=postgresql://...
NEXTAUTH_SECRET=...
GITHUB_ID=...         # OAuth 登录
GITLAB_WEB_URL=...    # 支持私有 GitLab 实例（PR #1033）
```

团队成员登录后可以：
- 创建私有 prompt，不公开
- fork 社区 prompt 后修改
- 给 prompt 打 tag、搜索
- 通过同一套 `/api/prompts` 接口供 CI/CD 和 Agent 消费

**适合场景**：公司内部有大量 prompt 需要版本管理，不想把敏感 prompt 放在公共平台上。

---

### 场景 5：学习/参考——Prompt Book

`/book` 路由是完整的 Prompt Engineering 教程，25+ 章节，支持中文（简体/繁体）、英文等多语言，可在线读也可下载 PDF：

```
/book          ← 在线版（交互式示例）
/book/print    ← 打印/PDF 版
/kids          ← 儿童版（给非技术人员）
```

从「什么是 prompt」到「few-shot / chain-of-thought / role prompting」都有，配有可直接运行的示例。

---

## 数据结构一览

```csv
# prompts.csv（核心数据源）
act,prompt,for_devs
"Linux Terminal","I want you to act as a linux terminal...",TRUE
"English Translator","I want you to act as an English translator...",FALSE
"Code Reviewer","I want you to act as a code reviewer...",TRUE
```

`for_devs=TRUE` 的有约 400 条，是开发者最常用的子集。

---

## 快速上手

```bash
# 方式 1：直接用 CSV（最简单）
curl https://raw.githubusercontent.com/f/awesome-chatgpt-prompts/main/prompts.csv

# 方式 2：MCP Server
npx @f/prompts-mcp

# 方式 3：自托管
git clone https://github.com/f/prompts.chat
cd prompts.chat && cp .env.example .env
npm install && npm run dev
```

---

## 注意点

- **公开 API 有速率限制**：60 req/60s，批量拉取请自托管
- **CSV 是单一数据源**：PR 贡献直接改 CSV，审核后自动同步到平台
- **MCP Server 需要 Node.js 18+**：`npx` 方式最便捷
- **自托管需要 PostgreSQL**：没有 SQLite 模式，生产环境用 PG

---

## 关键文件索引

```
prompts.chat/
├── prompts.csv              # 核心数据，1645+ prompts
├── scripts/
│   └── import-to-lmstudio.py  # 批量导入 LM Studio
├── src/
│   ├── app/api/prompts/     # 公开 REST API
│   └── lib/rate-limit.ts    # 速率限制（可换 Redis）
└── packages/mcp/            # @f/prompts-mcp MCP Server
```

---

*自动生成 · 2026-05-19 · OpenClaw Daily Task*
