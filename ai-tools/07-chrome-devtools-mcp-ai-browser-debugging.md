# 07 · Chrome DevTools MCP — 让 AI Agent 控制浏览器调试

**项目**：[ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp)
**Stars**：41.9k ⭐ · TypeScript · Apache-2.0
**定位**：Google 官方出品，将 Chrome DevTools Protocol（CDP）封装为 MCP Server，让 AI coding agent 直接调试网页、分析性能、执行 JavaScript

---

## 根本问题

AI coding agent 调试前端 bug 时，只能看代码文件，看不到运行时的实际状态——不知道某个 DOM 节点的真实样式是什么，不知道某个 API 请求返回了什么，不知道性能瓶颈在哪。Chrome DevTools MCP 给 agent 接入了 Chrome 的完整调试通道，让 agent 能像人一样打开 DevTools：执行 JS、查 DOM、拦截网络请求、分析 CPU/内存性能数据。

---

## 核心工作原理

Chrome DevTools Protocol（CDP）是 Chrome 提供的低级调试 API，DevTools 面板本身就是用 CDP 实现的。这个 MCP Server 把 CDP 接口包装成 agent 友好的高层工具：

```
Claude Code / Cursor
    ↓ MCP
Chrome DevTools MCP Server
    ↓ WebSocket（CDP）
Chrome/Chromium（--remote-debugging-port=9222）
    ↓ CDP 协议
页面 JavaScript 运行时 / DOM / Network / Performance
```

核心工具：

```typescript
// 执行 JavaScript（完整访问权限）
chrome.evaluate("document.querySelector('.rec-item').getBoundingClientRect()")
// → {x: 100, y: 200, width: 300, height: 150}

// 获取 DOM 元素属性
chrome.get_dom_attributes(".user-card")
// → {class: "user-card active", data-user-id: "12345", ...}

// 截图（当前视口）
chrome.screenshot()
// → base64 PNG

// 拦截网络请求
chrome.get_network_requests({url_filter: "/api/recommend"})
// → [{url, method, status, response_body, timing}]

// CPU Profile（找 JS 性能瓶颈）
chrome.start_profiling() 
// ... do stuff ...
chrome.stop_profiling()
// → FlameGraph 数据
```

---

## 安装 / 快速上手

```bash
# 安装
npx @chrome-devtools/mcp@latest

# 启动 Chrome（开启远程调试）
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-debug

# 配置到 Claude Code
claude mcp add chrome-devtools \
  -- npx @chrome-devtools/mcp@latest --port 9222
```

---

## 实践案例

**场景**：推荐系统的前端展示页面，用户反馈某些情况下推荐卡片的点击埋点没有上报，但 QA 无法稳定复现。需要让 Claude Code 帮助定位问题，不需要 QA 手动操作。

**调试流程**：

```bash
# 在 Claude Code 里
> 帮我调试推荐卡片的点击埋点问题。先截图看看当前页面状态

# Claude 调用 chrome.screenshot()，返回页面截图
# → 显示推荐列表已加载，有多个卡片

> 检查第一个推荐卡片的 DOM 结构和绑定的事件监听器

# Claude 调用：
# chrome.evaluate(`
#   const card = document.querySelector('.rec-item:first-child');
#   const events = getEventListeners(card);
#   return {className: card.className, events: Object.keys(events)};
# `)
# → {className: "rec-item loading", events: []}  ← 没有事件监听器！

> loading 状态下没有绑定事件？检查卡片完全加载后的状态

# chrome.evaluate(`
#   const loaded = document.querySelector('.rec-item:not(.loading)');
#   return getEventListeners(loaded);
# `)
# → {click: [Array(1)], mousedown: [Array(1)]}  ← 有事件

> 模拟快速点击一个正在 loading 状态的卡片，监控网络请求

# claude: 先监听网络，然后点击
# 1. chrome.start_network_monitoring({url_filter: "/api/track"})
# 2. chrome.evaluate(`document.querySelector('.rec-item.loading').click()`)
# 3. chrome.get_network_requests() → 返回空列表

# 问题找到了：loading 状态下没有绑定 click 事件，
# 用户在图片加载完成前点击不会触发埋点
```

**Claude 给出修复建议**：

```javascript
// 修复：用事件委托代替直接绑定
// 旧代码（绑定时机有竞态条件）
document.querySelectorAll('.rec-item').forEach(item => {
  item.addEventListener('click', trackClick);  // loading 时还没绑定
});

// 新代码（事件委托，不受加载状态影响）
document.querySelector('.rec-list').addEventListener('click', (e) => {
  const item = e.target.closest('.rec-item');
  if (item) trackClick(item);
});
```

这个 bug 用传统方式需要 QA 手动复现 + 开发者打断点，可能花半天。Claude + Chrome DevTools MCP 10 分钟内定位并修复。

---

## 关键特性速查

- **Google 官方出品**：基于 Chrome DevTools Protocol，与 Chrome DevTools 能力完全对等
- **完整 JS 执行权限**：可在页面上下文执行任意 JS，访问所有 DOM/BOM API
- **网络请求拦截**：查看请求/响应内容、timing 数据，无需 Fiddler/Charles
- **性能分析**：CPU Profile、堆内存快照，直接生成 FlameGraph 数据
- **截图能力**：agent 可以"看到"当前页面状态，结合视觉理解调试 UI 问题

---

**GitHub**：https://github.com/ChromeDevTools/chrome-devtools-mcp
