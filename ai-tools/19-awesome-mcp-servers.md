# #19 · awesome-mcp-servers — MCP Server 精选目录

> **仓库**: [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) · **Stars**: 持续增长 · **License**: MIT  
> **定位**: MCP 生态最权威的社区导航，覆盖 60+ 分类、数千个 server 实现，配套 [glama.ai/mcp/servers](https://glama.ai/mcp/servers) 可搜索 Web 目录

---

## 一句话价值

**「找 MCP Server 先来这里」**——不管是想给 Claude/Cursor/Codex 接数据库、浏览器、云平台还是自己写 Server，这个仓库是最快找到现成实现或参考的起点。

---

## 核心使用场景集合

### 场景 1：给 AI Coding Agent 接真实工具

最高频场景：为 Claude Code / Cursor / Codex 扩展能力。

**浏览器自动化**（Browser Automation 分类）：
```json
// Claude Code .claude/mcp.json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp"]
    }
  }
}
```
微软官方 Playwright MCP，让 Claude 直接操控浏览器，截图、填表、抓数据。

**数据库直连**（Databases 分类）：
```json
{
  "mcpServers": {
    "postgres": {
      "command": "npx",
      "args": ["@modelcontextprotocol/server-postgres"],
      "env": { "DATABASE_URL": "postgresql://..." }
    }
  }
}
```

**Git/GitHub 操作**（Version Control 分类）：
```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "ghp_..." }
    }
  }
}
```

---

### 场景 2：用 Aggregator 一个 server 接多个工具

Aggregators 分类是亮点，解决「MCP server 太多导致 context 爆炸」问题：

| 项目 | 特点 |
|------|------|
| [metatool-app](https://github.com/metatool-ai/metatool-app) | GUI 管理所有 MCP 连接，统一中间件 |
| [mcp-gateway](https://github.com/ViperJuice/mcp-gateway) | 9个稳定 meta-tool 替代 25+ server，按需加载 |
| [roundtable](https://github.com/askbudi/roundtable) | 自动发现 Claude Code/Cursor/Codex，零配置统一接口 |
| [forage](https://github.com/isaac-levine/forage) | AI 自主发现并安装新 MCP server，能力自扩展 |

**典型用法**：
```bash
# mcp-gateway：用 9 个 meta-tool 暴露 25+ server，节省 95% context
npx @viperJuice/mcp-gateway
```

---

### 场景 3：云平台运维自动化

Cloud Platforms 分类，AI 直接操控基础设施：

```bash
# AWS 官方 MCP
npx @aws/mcp-server

# Cloudflare 官方 MCP（Workers/KV/R2/D1）
npx @cloudflare/mcp-server-cloudflare

# Kubernetes（多个实现可选）
npx @flux159/mcp-server-kubernetes
```

场景：让 Claude Code 直接查 k8s pod 日志、改 deployment 副本数，不用手动跑 kubectl。

---

### 场景 4：多 AI Coding Agent 协同

Coding Agents 分类里有几个专门解决「多 agent 协作」的：

- **[roundtable](https://github.com/askbudi/roundtable)**：自动发现本机的 Claude Code、Cursor、Codex，通过统一 MCP 接口协调
- **[claude-concilium](https://github.com/spyrae/claude-concilium)**：三个 MCP server 分别包装 Codex/Gemini/Qwen，并行代码 review
- **[owlex](https://github.com/agentic-mcp-tools/owlex)**：同时查询多个 CLI agent（Claude/Codex/Gemini），deliberation rounds 投票
- **[bernstein](https://github.com/sipyourdrink-ltd/bernstein)**：支持 37 个 CLI coding agent 的确定性多 agent 编排器

---

### 场景 5：快速找「某类功能」的现成 server

目录的核心价值：不重复造轮子。常用查找路径：

```
需要搜索？         → Search & Data Extraction 分类
需要读写文件？     → File Systems 分类  
需要发消息通知？   → Communication 分类（Slack/Discord/Email）
需要知识库/记忆？  → Knowledge & Memory 分类
需要监控告警？     → Monitoring 分类
需要安全测试？     → Security 分类（含 SQLMap/NMAP/FFUF MCP 封装）
```

配套 Web 目录 [glama.ai/mcp/servers](https://glama.ai/mcp/servers) 支持按分类、语言、平台筛选，比看 README 更快。

---

## 图例速查

```
🎖️  官方实现（厂商自己出的）
🐍  Python
📇  TypeScript/JavaScript  
🏎️  Go
🦀  Rust
☁️  云服务（需要联网 API）
🏠  本地服务（操控本地软件）
🍎/🪟/🐧  平台限定
```

---

## 值得关注的新趋势（2026）

1. **x402 微支付**：越来越多 server 支持用 USDC 按调用付费，不需要 API key，直接 pay-per-call
2. **Remote MCP**：`url` 类型的 server 配置（不用本地跑 npx），直接连远端托管 server
3. **Agent-to-Agent**：多个 server 在 Aggregators 里实现了 AI agent 互相发现、调用、支付的协议
4. **自扩展能力**：forage/magg 等 server 让 AI 自主发现并安装新工具，不需要人工配置

---

## 快速上手

```bash

## 实践案例

**场景：用 mcp-gateway 聚合多个 Server，解决 context 被工具描述占满的问题**

**问题背景**：同时配置了 GitHub、PostgreSQL、Playwright、Slack 四个 MCP Server，Claude Code 每次对话开头 context 就被工具 schema 描述占掉 12,000+ token，可用于代码分析的空间严重不足。

**安装配置**：

```bash
# 在 awesome-mcp-servers 的 Aggregators 分类找到 mcp-gateway
npm install -g @viperJuice/mcp-gateway
```

```json
{
  "mcpServers": {
    "gateway": {
      "command": "mcp-gateway",
      "args": ["--config", "./mcp-gateway.json"]
    }
  }
}
```

```json
{
  "servers": [
    { "name": "github",   "command": "npx", "args": ["@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "ghp_xxx" } },
    { "name": "postgres", "command": "npx", "args": ["@modelcontextprotocol/server-postgres"],
      "env": { "DATABASE_URL": "postgresql://user:pass@localhost/mydb" } },
    { "name": "playwright","command": "npx", "args": ["@playwright/mcp"] },
    { "name": "slack",    "command": "npx", "args": ["@modelcontextprotocol/server-slack"],
      "env": { "SLACK_BOT_TOKEN": "xoxb-xxx" } }
  ],
  "strategy": "lazy"
}
```

**使用效果**（Claude Code 中正常使用，无感知）：

```
用户：查一下 payment_orders 表最近一天失败的订单数

Claude → gateway.query_postgres:
  SELECT COUNT(*) FROM payment_orders
  WHERE status='failed' AND created_at > NOW() - INTERVAL '1 day';
  结果：47 条（较昨天同期 +12%）
```

**Context 占用对比**：

```
直接配置 4 个独立 Server：工具 schema 描述 ~12,400 tokens，代码空间 ~3,600 tokens
使用 mcp-gateway（lazy）：  工具描述 ~2,800 tokens（↓77%），代码空间 ~13,200 tokens
```

**awesome-mcp-servers 最独特的地方**：不只是工具列表，而是按使用场景分类的导航——Aggregators 分类专门解决"多 Server 导致 context 爆炸"的实际痛点，Security 分类有 SQLMap/NMAP/FFUF 的 MCP 封装，Cloud Platforms 有 AWS/Cloudflare/Kubernetes 官方实现。配套 [glama.ai/mcp/servers](https://glama.ai/mcp/servers) 支持按分类筛选，找到合适工具通常只需 2 分钟。


# 1. 找到需要的 server（Web 目录更方便）
# https://glama.ai/mcp/servers

# 2. 加入 Claude Code 配置
cat >> .claude/mcp.json << 'EOF'
{
  "mcpServers": {
    "your-server": {
      "command": "npx",
      "args": ["server-package-name"]
    }
  }
}
EOF

# 3. 或者加入 Claude Desktop
# ~/.claude/claude_desktop_config.json 同样格式
```

---

*自动生成 · 2026-05-20 · OpenClaw Daily Task*
