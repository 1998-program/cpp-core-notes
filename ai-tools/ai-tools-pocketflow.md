# PocketFlow：100 行 LLM 框架深度解析

> GitHub: https://github.com/The-Pocket/PocketFlow
> Stars: ~10,700 | Language: Python | License: MIT
> 核心理念：极简主义——用 100 行代码覆盖 LLM 框架的核心抽象

---

## 一、核心定位

PocketFlow 是一个**极简 LLM 框架**，只有 100 行 Python 代码，但能实现 Agent、Multi-Agent、RAG、Workflow 等全部主流 AI 设计模式。它的核心主张是：LangChain（405K 行）、CrewAI（18K 行）、LangGraph（37K 行）都太重了，LLM 框架的本质只是一个**图（Graph）**，而图只需要 100 行就能表达清楚。

框架对比表：

| 框架 | 行数 | 包大小 | 抽象层 |
|------|------|--------|--------|
| LangChain | 405K | +166MB | Agent, Chain |
| CrewAI | 18K | +173MB | Agent, Chain |
| LangGraph | 37K | +51MB | Agent, Graph |
| AutoGen | 7K（核心） | +26MB | Agent |
| **PocketFlow** | **100** | **+56KB** | **Graph** |

PocketFlow 有多语言版本：TypeScript、Java、C++、Go、Rust、PHP，其中 C++ 版本对后端开发者尤其值得关注。

---

## 二、架构设计

### 三层抽象：Node → Flow → Shared Store

```
Node（计算单元）
  ├── prep(shared)      → 从 shared 读取输入，返回 prep_res
  ├── exec(prep_res)    → 执行核心逻辑（LLM 调用/工具调用），返回 exec_res
  └── post(shared, prep_res, exec_res) → 写回 shared，返回 action 字符串

Flow（图调度器）
  ├── 维护 Node 的有向图（successors 字典）
  ├── 根据 post() 返回的 action 字符串决定下一个 Node
  └── 循环执行直到没有后继节点

Shared Store（全局状态）
  └── dict，所有 Node 共享读写，是节点间通信的唯一通道
```

这个三层设计的核心洞察：
- **prep/exec/post 分离**：prep 处理 I/O，exec 处理逻辑，post 控制流转。三者职责明确，易于测试和复用
- **action 字符串路由**：post 返回 "search"/"answer"/"retry" 这样的字符串，Flow 根据字符串查 successors 决定下一步，比 LangGraph 的条件边更直观
- **Shared Store 是显式的**：没有魔法，所有状态都在一个 dict 里，调试时一目了然

### 完整的 100 行源码解读

```python
import asyncio, warnings, copy, time

class BaseNode:
    def __init__(self): 
        self.params, self.successors = {}, {}
    
    def next(self, node, action="default"):
        """注册后继节点，action 是路由键"""
        if action in self.successors: 
            warnings.warn(f"Overwriting successor for action '{action}'")
        self.successors[action] = node
        return node
    
    def prep(self, shared): pass   # 子类重写：从 shared 读数据
    def exec(self, prep_res): pass  # 子类重写：执行核心逻辑
    def post(self, shared, prep_res, exec_res): pass  # 子类重写：写回 shared，返回 action
    
    def _run(self, shared):
        """三步走：prep → exec → post"""
        p = self.prep(shared)
        e = self._exec(p)
        return self.post(shared, p, e)
    
    # 语法糖：>> 连接节点（默认 action），- "action" >> 连接带 action 的节点
    def __rshift__(self, other): return self.next(other)
    def __sub__(self, action):
        if isinstance(action, str): return _ConditionalTransition(self, action)
        raise TypeError("Action must be a string")

class Node(BaseNode):
    def __init__(self, max_retries=1, wait=0):
        super().__init__()
        self.max_retries, self.wait = max_retries, wait
    
    def _exec(self, prep_res):
        """带重试的 exec，内置异常处理"""
        for self.cur_retry in range(self.max_retries):
            try: 
                return self.exec(prep_res)
            except Exception as e:
                if self.cur_retry == self.max_retries - 1: 
                    return self.exec_fallback(prep_res, e)
                if self.wait > 0: 
                    time.sleep(self.wait)

class Flow(BaseNode):
    def _orch(self, shared, params=None):
        """图调度器核心：循环执行节点直到没有后继"""
        curr = copy.copy(self.start_node)  # 注意：每次 copy，节点状态隔离
        last_action = None
        while curr:
            curr.set_params(params or self.params)
            last_action = curr._run(shared)
            # 根据 action 查 successors，找不到则终止
            curr = copy.copy(self.get_next_node(curr, last_action))
        return last_action
```

### 节点连接语法

```python
# 方式 1：默认连接（action="default"）
node_a >> node_b  # 等价于 node_a.next(node_b)

# 方式 2：带 action 的条件连接
decide - "search" >> search_node   # 当 post 返回 "search" 时路由到 search_node
decide - "answer" >> answer_node   # 当 post 返回 "answer" 时路由到 answer_node

# 方式 3：链式连接
node_a >> node_b >> node_c  # 线性流程
```

---

## 三、核心使用场景

### 场景 1：ReAct Agent（搜索-推理循环）

这是 LLM Agent 最常见的设计模式：决策 → 搜索 → 决策（循环）→ 回答。

```python
from pocketflow import Node, Flow
from utils import call_llm, search_web  # 用户自己实现这两个工具

class DecideAction(Node):
    def prep(self, shared):
        return shared["question"], shared.get("context", "无历史搜索")
    
    def exec(self, inputs):
        question, context = inputs
        prompt = f"""
问题: {question}
已有信息: {context}

决定下一步行动。返回 YAML 格式：
```yaml
action: search  # 或 answer
search_query: <搜索词>  # action=search 时填
answer: <回答>          # action=answer 时填
```
"""
        response = call_llm(prompt)
        import yaml, re
        yaml_block = re.search(r"```yaml(.*?)```", response, re.DOTALL).group(1)
        return yaml.safe_load(yaml_block)
    
    def post(self, shared, prep_res, exec_res):
        if exec_res["action"] == "search":
            shared["search_query"] = exec_res["search_query"]
            return "search"  # 路由到 SearchWeb 节点
        else:
            shared["answer"] = exec_res["answer"]
            return "answer"  # 路由到结束


class SearchWeb(Node):
    def prep(self, shared):
        return shared["search_query"]
    
    def exec(self, query):
        return search_web(query)  # 调用搜索工具
    
    def post(self, shared, prep_res, exec_res):
        # 把搜索结果追加到 context
        shared["context"] = shared.get("context", "") + f"\n搜索: {prep_res}\n结果: {exec_res}"
        return "decide"  # 回到决策节点


class GiveAnswer(Node):
    def prep(self, shared):
        return shared["question"], shared.get("context", "")
    
    def exec(self, inputs):
        question, context = inputs
        return call_llm(f"基于以下信息回答问题:\n{context}\n\n问题: {question}")
    
    def post(self, shared, prep_res, exec_res):
        shared["answer"] = exec_res
        return "done"


# 构建图：connect nodes
decide = DecideAction()
search = SearchWeb()
answer = GiveAnswer()

decide - "search" >> search   # search 路由 → SearchWeb
decide - "answer" >> answer   # answer 路由 → GiveAnswer
search - "decide" >> decide   # 搜索完回到 DecideAction（形成循环）

flow = Flow(start=decide)

# 运行
shared = {"question": "2026年最热门的 AI Agent 框架是哪个?"}
flow.run(shared)
print(shared["answer"])
```

### 场景 2：Multi-Agent 并发（async 模式）

两个 Agent 通过 asyncio.Queue 异步通信，实现真正的并发协作：

```python
from pocketflow import AsyncNode, AsyncFlow
import asyncio

class HinterAgent(AsyncNode):
    """提示者 Agent：给出关于目标词的提示"""
    async def prep_async(self, shared):
        guess = await shared["hinter_queue"].get()  # 等待猜测者的猜测
        if guess == "GAME_OVER":
            return None
        return shared["target"], shared["forbidden"], shared.get("guesses", [])
    
    async def exec_async(self, inputs):
        if inputs is None:
            return None
        target, forbidden, past_guesses = inputs
        prompt = f"为词语'{target}'生成提示，禁用词：{forbidden}，历史错误：{past_guesses}，不超过5个词"
        return await call_llm_async(prompt)
    
    async def post_async(self, shared, prep_res, exec_res):
        if exec_res is None:
            return "end"
        await shared["guesser_queue"].put(exec_res)  # 发送提示
        return "continue"


class GuesserAgent(AsyncNode):
    """猜测者 Agent：根据提示猜词"""
    async def prep_async(self, shared):
        hint = await shared["guesser_queue"].get()
        return hint, shared.get("guesses", [])
    
    async def exec_async(self, inputs):
        hint, past = inputs
        return await call_llm_async(f"提示：{hint}，历史错误：{past}，猜一个词：")
    
    async def post_async(self, shared, prep_res, exec_res):
        if exec_res.lower() == shared["target"].lower():
            await shared["hinter_queue"].put("GAME_OVER")
            return "end"
        shared.setdefault("guesses", []).append(exec_res)
        await shared["hinter_queue"].put(exec_res)
        return "continue"


async def run_multi_agent():
    shared = {
        "target": "量子纠缠",
        "forbidden": ["量子", "纠缠", "物理", "粒子", "叠加"],
        "hinter_queue": asyncio.Queue(),
        "guesser_queue": asyncio.Queue(),
    }
    
    # 两个 Flow 并发运行
    hinter_flow = AsyncFlow(start=HinterAgent())
    guesser_flow = AsyncFlow(start=GuesserAgent())
    
    await shared["hinter_queue"].put("")  # 触发第一次提示
    await asyncio.gather(
        hinter_flow.run_async(shared),
        guesser_flow.run_async(shared)
    )

asyncio.run(run_multi_agent())
```

### 场景 3：Batch 并行处理（AsyncParallelBatchNode）

```python
from pocketflow import AsyncParallelBatchNode, AsyncFlow

class TranslateNode(AsyncParallelBatchNode):
    """并行翻译一批文本"""
    async def prep_async(self, shared):
        # prep 返回列表，框架自动并行处理每个元素
        return shared["texts"]
    
    async def exec_async(self, text):
        # 每个元素独立调用，并行执行
        return await call_llm_async(f"翻译成英文：{text}")
    
    async def post_async(self, shared, prep_res, exec_res):
        # exec_res 是所有并行结果的列表
        shared["translations"] = exec_res
        return "done"

# 使用示例
shared = {
    "texts": [
        "推荐系统的核心是协同过滤",
        "召回阶段负责候选集生成",
        "精排阶段使用复杂模型打分",
        "重排阶段综合多样性和时效性"
    ]
}

flow = AsyncFlow(start=TranslateNode())
import asyncio
asyncio.run(flow.run_async(shared))
print(shared["translations"])
# 4 个翻译任务并行执行，耗时约等于单个 LLM 调用
```

### 场景 4：MCP 集成

PocketFlow 提供了 MCP（Model Context Protocol）集成，让 Agent Flow 直接接入 MCP 工具链：

```python
from pocketflow import Node, Flow
# MCP 工具调用示例（来自 cookbook/pocketflow-mcp）

class MCPToolNode(Node):
    """把 MCP 工具包装成 PocketFlow Node"""
    def __init__(self, tool_name: str, mcp_client):
        super().__init__(max_retries=3, wait=1)
        self.tool_name = tool_name
        self.mcp_client = mcp_client
    
    def prep(self, shared):
        return shared.get("tool_input", {})
    
    def exec(self, tool_input):
        # 调用 MCP 工具（同步包装器）
        result = self.mcp_client.call_tool(self.tool_name, tool_input)
        return result
    
    def post(self, shared, prep_res, exec_res):
        shared["tool_result"] = exec_res
        return "done"


# 或者直接用 AsyncNode 配合 mcp 的 async client
class AsyncMCPNode(AsyncNode):
    async def exec_async(self, prep_res):
        async with Client(mcp_server_url) as client:
            result = await client.call_tool("search", {"query": prep_res})
            return result.content
```

### 场景 5：RAG 流程

```python
from pocketflow import Node, Flow

class EmbedQuery(Node):
    def prep(self, shared):
        return shared["question"]
    
    def exec(self, question):
        return get_embedding(question)  # 获取问题的向量表示
    
    def post(self, shared, prep_res, exec_res):
        shared["query_embedding"] = exec_res
        return "retrieve"


class RetrieveChunks(Node):
    def prep(self, shared):
        return shared["query_embedding"]
    
    def exec(self, embedding):
        # 向量检索，返回最相关的 k 个 chunk
        return vector_store.search(embedding, top_k=5)
    
    def post(self, shared, prep_res, exec_res):
        shared["retrieved_chunks"] = exec_res
        return "generate"


class GenerateAnswer(Node):
    def prep(self, shared):
        return shared["question"], shared["retrieved_chunks"]
    
    def exec(self, inputs):
        question, chunks = inputs
        context = "\n".join([c["text"] for c in chunks])
        return call_llm(f"基于以下上下文回答：\n{context}\n\n问题：{question}")
    
    def post(self, shared, prep_res, exec_res):
        shared["answer"] = exec_res
        return "done"


# 构建 RAG flow
embed = EmbedQuery()
retrieve = RetrieveChunks()
generate = GenerateAnswer()

embed - "retrieve" >> retrieve - "generate" >> generate

rag_flow = Flow(start=embed)
shared = {"question": "uni-que 服务的 filter-server 耗时分析结论是什么？"}
rag_flow.run(shared)
```

---

## 四、BatchNode vs BatchFlow

这是 PocketFlow 里一个容易混淆的设计，issue #54 有用户详细讨论过：

| | BatchNode | BatchFlow |
|--|---------|----------|
| 粒度 | 对单个 Node 并行执行 | 对整个 Flow 并行执行 |
| 结果收集 | 自动聚合为列表 | 各 batch 写同一 shared，可能冲突 |
| 适合场景 | 并行调用 LLM（翻译/分类等） | 并行处理多个独立任务 |

```python
# BatchNode：自动并行执行 exec()，返回结果列表
class ClassifyBatch(BatchNode):
    def prep(self, shared):
        return shared["items"]  # 返回列表，框架自动拆分
    
    def exec(self, item):
        return call_llm(f"分类：{item}")  # 每个元素独立调用
    
    def post(self, shared, prep_res, exec_res):
        # exec_res 是所有结果的列表
        shared["classifications"] = exec_res
        return "done"

# BatchFlow：对每组 params 跑一次完整 flow
class ProcessBatchFlow(BatchFlow):
    def prep(self, shared):
        # 返回参数列表，每组参数跑一次完整的 Flow
        return [{"doc": d} for d in shared["documents"]]
```

---

## 五、真实 Issue 分析

**Issue #12：流式输出需求**（https://github.com/The-Pocket/PocketFlow/issues/12）

一位用户构建了基于 Gradio 的 AI 应用，需要在 Flow 执行过程中**实时显示中间节点的输出**（比如边思考边显示），而不是等 Flow 全部结束后才展示。

用户自行扩展了 `StreamNode`：
```python
class StreamNode(Node):
    def exec_stream(self, prep_res):
        """返回 Generator，支持逐 token 输出"""
        for chunk in call_llm_stream(prep_res):
            yield chunk
    
    def _exec(self, prep_res):
        chunks = []
        for chunk in self.exec_stream(prep_res):
            chunks.append(chunk)
            # 通过回调把 chunk 推给前端
            if self.stream_callback:
                self.stream_callback(chunk)
        return "".join(chunks)
```

作者（zachary62）回应说这个扩展方向很有价值，正在考虑内置回调机制。目前官方的解决方案是 [pocketflow-llm-streaming](https://github.com/The-Pocket/PocketFlow/tree/main/cookbook/pocketflow-llm-streaming) 示例，使用 `AsyncNode` + `asyncio.Queue` 实现流式推送。

这个 issue 说明：PocketFlow 的极简设计反过来促使社区自行扩展，核心 100 行不动，功能通过组合叠加。

---

## 六、与同类工具对比

| 维度 | PocketFlow | LangGraph | AutoGen | CrewAI |
|------|-----------|-----------|---------|--------|
| 核心行数 | 100 | 37K | 7K | 18K |
| 依赖 | 0 | 几十个 | 若干 | 若干 |
| 图可视化 | 需额外工具 | 内置 | ❌ | ❌ |
| 状态持久化 | 手动（外部 DB） | 内置（Checkpointer） | ❌ | ❌ |
| Streaming | 通过扩展 | 内置 | 部分 | 部分 |
| Human-in-Loop | 通过扩展 | 内置 interrupt | 内置 | 部分 |
| Multi-Agent | asyncio.Queue | 内置 | 内置 | 内置 |
| 学习曲线 | 极低（1小时） | 中等（1天） | 中等 | 中等 |
| 适合场景 | 原型/教学/轻量生产 | 复杂有状态 Agent | 企业对话 Agent | 多角色协作 |

**选 PocketFlow 的场景**：
- 快速原型验证（想法到可运行 demo < 1 小时）
- 教学场景（理解 Agent 框架的本质）
- 需要零依赖嵌入（边缘侧部署、C++ 版本）
- 现有代码库不想引入大依赖

**选 LangGraph 的场景**：
- 需要内置状态持久化（断点续传）
- 需要内置流式输出
- 复杂的 Human-in-Loop 流程

---

## 七、安装与快速接入

### 方式 A：pip 安装（推荐）

```bash
# 安装（仅 56KB，无依赖）
pip install pocketflow

# 验证
python3 -c "from pocketflow import Node, Flow; print('OK')"
```

### 方式 B：直接复制源码

PocketFlow 的核心只有 100 行，直接复制到项目里：

```bash
# 下载单文件
curl -O https://raw.githubusercontent.com/The-Pocket/PocketFlow/main/pocketflow/__init__.py
# 重命名
mv __init__.py pocketflow.py

# 在代码里直接引用
from pocketflow import Node, Flow
```

这种方式在内网环境（无法 pip install 外部包）特别有用。

### 方式 C：C++ 版本（后端服务场景）

```bash
# PocketFlow 有官方 C++ 版本
git clone https://github.com/The-Pocket/PocketFlow-CPP
# 头文件库，include 即用
```

### 最简可运行示例（30 行）

```python
from pocketflow import Node, Flow
import openai

def call_llm(prompt):
    client = openai.OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content

class SummarizeNode(Node):
    def prep(self, shared):
        return shared["text"]
    
    def exec(self, text):
        return call_llm(f"用3句话总结：{text}")
    
    def post(self, shared, prep_res, exec_res):
        shared["summary"] = exec_res
        return "done"

# 运行
flow = Flow(start=SummarizeNode())
shared = {"text": "PocketFlow 是一个100行的LLM框架..."}
flow.run(shared)
print(shared["summary"])
```

---

## 八、Agentic Coding 模式

PocketFlow 推崇"**用 AI coding agent 来构建 AI Agent 框架**"，他们把这个叫做 Agentic Coding。

具体操作：把 PocketFlow 源码给 Claude Code / Cursor，然后用自然语言描述想要的 Agent 行为，让 AI 来写 Node 和 Flow 的连接代码。因为 PocketFlow 结构极简，AI 生成的代码几乎不会出错。

这和 OpenClaw 的 SKILL.md 机制有异曲同工之处——把框架的使用方式用文档描述清楚，AI 就能"看说明书"自主组合和调用。

---

## 九、设计哲学：为什么 100 行够用？

PocketFlow 作者 Zachary 的核心论点：

> **大多数 LLM 框架的额外代码解决的不是框架本身的问题，而是特定应用场景的包装代码**——比如 LangChain 的 QA chain、Summarization chain，这些是"应用层代码"被错误地打包进了"框架层"。

真正的框架抽象只需要：
1. **节点**（计算单元）
2. **边**（状态转移）
3. **共享状态**（节点间通信）

这三个概念 100 行足够，剩下的由用户自己实现（call_llm、search_web 等工具函数），保持最大灵活性。

这个哲学和 brpc 的设计取向有相似之处：brpc 也是把核心抽象做精，不内置业务逻辑，让用户在框架上搭建自己的逻辑。

---

## 十、局限与注意事项

1. **无内置状态持久化**：Flow 崩溃后无法从断点继续，需要自己在 post() 里写到 Redis/DB
2. **无内置监控**：没有 LangSmith 这类可观测性工具，Debug 复杂 Flow 时只能靠 print
3. **BatchFlow 的 shared 冲突**：多个 batch 并行写同一个 shared key 时会相互覆盖，issue #54 有讨论，需要用 key 隔离
4. **node copy 语义**：Flow 每次执行前 `copy.copy(node)`，浅拷贝，如果 Node 里有复杂的可变对象需要注意
5. **不适合大型生产系统**：缺少 LangGraph 的 Checkpointer、interrupt、时间旅行等企业级特性

---

## 十一、与推荐系统工程的结合点

在推荐在线架构场景（brpc + ng-framework DAG + C++ 主服务），PocketFlow 最直接的价值在于**Python 侧的 AI 自动化流程**，而不是替换 C++ 核心服务。

**具体场景**：

1. **自动化实验分析 Agent**：从 GitHub 或内部日志系统拉实验数据 → ReAct Agent 自动分析 → 生成报告。整个流程 < 100 行 Python，不需要引入 LangChain 这样的重依赖。

2. **代码生成辅助**：给 Claude Code 提供 PocketFlow 上下文，让它帮忙生成 Protobuf 服务的调用胶水代码——因为 PocketFlow 本身够简单，Claude Code 理解框架的成本极低。

3. **ng-framework DAG 配置生成**：用 PocketFlow 构建一个"理解需求 → 生成 DAG 配置 → 验证配置 → 输出"的 Agent Flow，辅助日常的 DAG 节点配置工作。

4. **C++ 版本**：PocketFlow 有 C++ 版本（https://github.com/The-Pocket/PocketFlow-CPP），理论上可以直接嵌入 brpc 服务内做轻量推理编排——不过这个场景建议先观察社区成熟度。

---

## 总结

PocketFlow 的核心价值是**把 LLM Agent 框架的本质暴露出来**：它只是一个图，节点是计算单元，边是状态转移。100 行代码把这件事说清楚了，比任何文档都更直接。

对于想快速验证 AI Agent 想法的工程师，PocketFlow 是最佳起点——安装时间 < 1 分钟，第一个可运行 Agent < 30 行代码。等原型验证完，如果需要状态持久化、流式输出、监控等功能，再迁移到 LangGraph 也不难，因为设计模式（Node-Flow-Shared）是一致的。

> **C++ 版本的存在**（https://github.com/The-Pocket/PocketFlow-CPP）是对后端工程师的额外加分项：理论上可以在 C++ 服务里嵌入 PocketFlow 做 AI 编排，零 Python 运行时依赖。
