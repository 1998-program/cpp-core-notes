# #22 · LangGraph — 构建有状态、长时运行 Agent 的低级编排框架

> **仓库**: langchain-ai/langgraph · Python
> **定位**: 基于图结构（Graph）的低级 Agent 编排框架，天然支持持久化状态、Human-in-the-loop、多 Agent 协作，是 LangChain 生态中 Agent 基础设施的核心

---

## 一句话价值

LangGraph 把"Agent 执行流"建模为一张**有向图**（StateGraph）：每个节点是一个处理函数，每条边是状态转移规则，Checkpoint 机制保证 Agent 可以从任意状态恢复——既解决了"Agent 执行到一半崩溃"的痛点，又让"暂停等人审核、多 Agent 协同"变成框架内置能力，而不是业务代码里的 if-else 胡乱拼凑。

---

## 核心使用场景

### 场景 1：构建可中断、可恢复的 Agent

```python
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

def human_review_node(state: MessagesState):
    """需要人工审核的节点——框架层面中断，等待输入"""
    user_decision = interrupt({"question": "是否批准该操作？", "context": state["messages"]})
    return {"messages": [{"role": "system", "content": f"用户决策: {user_decision}"}]}

builder = StateGraph(MessagesState)
builder.add_node("agent", call_model)
builder.add_node("human_review", human_review_node)
builder.add_node("execute_action", execute_tool)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_review, {
    "review": "human_review",
    "skip": "execute_action",
})
builder.add_edge("human_review", "execute_action")
builder.add_edge("execute_action", END)

# MemorySaver 持久化每个节点执行后的状态
memory = MemorySaver()
graph = builder.compile(checkpointer=memory, interrupt_before=["human_review"])

# 第一次运行：遇到 human_review 节点自动暂停
config = {"configurable": {"thread_id": "review_thread_001"}}
result = graph.invoke({"messages": [...]}, config=config)

# 人工审核后，用 Command 继续执行（从断点恢复）
from langgraph.types import Command
graph.invoke(Command(resume="approved"), config=config)
```

### 场景 2：多 Agent 协作（Supervisor + SubAgent 模式）

```python
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command
from langchain_core.tools import tool

# 两个专用子 Agent
research_agent = create_react_agent(llm, tools=[search_web, read_docs])
code_agent = create_react_agent(llm, tools=[write_code, run_tests])

# Supervisor 负责分发任务
def supervisor_node(state):
    decision = supervisor_llm.invoke([...])  # 决定派谁去
    return Command(goto=decision.next_agent, update={"task": decision.task})

builder = StateGraph(OverallState)
builder.add_node("supervisor", supervisor_node)
builder.add_node("research", research_agent)
builder.add_node("code", code_agent)
# Supervisor 根据任务类型动态路由
builder.add_conditional_edges("supervisor", lambda s: s["next"], ["research", "code", END])
builder.add_edge("research", "supervisor")  # 子 Agent 完成后汇报 Supervisor
builder.add_edge("code", "supervisor")
```

### 场景 3：带长期记忆的对话 Agent

```python
from langgraph.store.memory import InMemoryStore
from langgraph.prebuilt import create_react_agent

# 跨会话持久化记忆（生产中接 PostgreSQL）
store = InMemoryStore()

def save_memory_tool(content: str):
    """Agent 自主决定何时保存重要信息"""
    store.put(("memories", user_id), key=str(uuid4()), value={"content": content})

agent = create_react_agent(
    llm,
    tools=[save_memory_tool],
    store=store,  # 注入 Store，Agent 自动获取历史记忆作为上下文
)

# 不同 thread_id 代表不同会话，但共用同一个 store（长期记忆）
result = agent.invoke({"messages": [...]}, config={"configurable": {
    "thread_id": "session_20260525",  # 短期：本次会话
    "user_id": "hujiyang",            # 长期：跨会话记忆 key
}})
```

### 场景 4：流式输出（Token 级 + 状态级）

```python
# 同时流式获取 LLM token 输出和节点状态变化
async for chunk in graph.astream(
    {"messages": [HumanMessage(content="分析这段代码")]},
    config=config,
    stream_mode=["messages", "updates"],  # 同时监听两种流
):
    if "messages" in chunk:
        print(chunk["messages"][-1].content, end="", flush=True)  # 逐 token
    elif "updates" in chunk:
        print(f"\n[节点完成]: {list(chunk['updates'].keys())}")    # 节点执行完毕
```

### 场景 5：Subgraph 复合图（模块化编排）

```python
# 把复杂流程封装为可复用的子图
def build_retrieval_subgraph():
    sub = StateGraph(RetrievalState)
    sub.add_node("embed_query", embed)
    sub.add_node("vector_search", search)
    sub.add_node("rerank", rerank)
    sub.add_edge(START, "embed_query")
    sub.add_edge("embed_query", "vector_search")
    sub.add_edge("vector_search", "rerank")
    sub.add_edge("rerank", END)
    return sub.compile()

# 主图直接嵌入子图节点
main_graph.add_node("retrieve", build_retrieval_subgraph())
```

---

## 架构关键点

### 核心抽象：StateGraph + Pregel 执行模型

LangGraph 的灵感来自 **Google Pregel 图计算系统**（[论文](https://research.google/pubs/pub37252/)）：

```
┌─────────────────────────────────────────┐
│              StateGraph                  │
│                                          │
│  State（TypedDict）= 整图共享状态         │
│  Node = 纯函数（接收 State → 返回 State） │
│  Edge = 确定跳转 / Conditional Edge = 动态路由 │
│  Checkpoint = 每次 State 变更后自动快照   │
└─────────────────────────────────────────┘
```

关键设计：
1. **State 的 Reducer（类似 Redux）**：同一个状态字段可以定义合并策略，多 Agent 并发更新不会覆盖，而是 `append`（消息列表）或自定义合并；
2. **Checkpoint 机制**：每个节点执行完后，自动将 State 写入 CheckpointSaver（内存/Redis/PostgreSQL），崩溃后 `graph.invoke(None, config)` 即可从上次断点继续；
3. **`Command` 对象**：节点返回 `Command(goto="next_node", update={...})` 可以动态控制下一个执行节点，突破静态图结构；
4. **`interrupt()` 原语**：在节点内调用 `interrupt(payload)` 即可暂停整个图执行，框架负责序列化状态等待外部信号（人工审核、工具确认等）。

---

## 真实用户场景（来自 Issues）

**Issue #7417：长工具调用被静默重执行的问题**

用户构建了一个主 Agent 派发子 Agent 的系统（通过 `task()` 工具内部调用 `subagent.ainvoke()`），子 Agent 通常需要 3-10 分钟执行完毕。但他们发现，当工具调用超过约 180 秒时，LangGraph Cloud 会**静默地从最后一个 Checkpoint 重新调度**这次工具调用，导致原始调用和重复调用同时在跑，产生 2-3x 的冗余计算和费用。

根本原因：`BG_JOB_HEARTBEAT = 120` 硬编码，队列扫描机制 240 秒后把 "仍在运行的任务" 标记为重试。这个 issue 揭示了 **LangGraph Cloud 的有状态持久化是双刃剑**：它保证了短任务的可靠性，但对于长时运行的子 Agent，需要显式调整心跳/超时配置，或者用 `asyncio.to_thread()` 包装来规避（实测可以把重执行周期延长到 ~360s）。

对实际工程的启示：**使用 LangGraph 构建多 Agent 系统时，如果子任务执行时间超过 2 分钟，必须提前规划超时配置，否则 Cloud 层的可靠性机制会反过来成为问题**。

---

## 对你的实际提升

结合推荐架构组技术栈（brpc / jemalloc / Protobuf / ng-framework DAG 计算图）：

1. **DAG 计算图的高层对比参照**：ng-framework 的 DAG 是静态声明的计算节点图，LangGraph 的 StateGraph 是动态可分支的 Agent 执行图——两者都解决"有向图上的状态传播"问题，但 LangGraph 的 Conditional Edge + Command 动态路由是 ng-framework 里较难做到的。研究 LangGraph 的 Pregel 执行模型，有助于理解如何在推荐 DAG 中引入条件分支和动态 fallback；

2. **Checkpoint 对 brpc 服务的启示**：LangGraph 的 Checkpoint 本质上是"幂等执行 + 断点续传"。推荐在线服务的 brpc 调用链路中，对于耗时较长的子任务（如实时特征计算），可以参考 Checkpoint 模式，把中间结果缓存到 Redis，服务重启后从 Redis 恢复，而不是从头重算；

3. **Human-in-the-loop 模式落地**：推荐系统的策略调整往往需要人工审核（如新算法上线前的人工 check）。LangGraph 的 `interrupt()` 原语 + `Command(resume=...)` 模式是一个非常干净的实现，可以直接借用这个模式设计推荐策略的人工干预节点，而不是在业务代码里硬塞 `if human_flag:` 逻辑；

4. **多 Agent 架构参考**：如果团队需要构建"离线分析 + 在线决策"的 LLM 辅助系统（如自动特征工程、自动告警分析），LangGraph 的 Supervisor-SubAgent 模式是最成熟的参考实现，代码量少、可观测性强（配 LangSmith）。

---

## 局限性

1. **学习曲线陡**：StateGraph 的 Reducer、Annotation、Command 体系初看晦涩，官方文档虽然丰富但概念层级多，新手容易在 `add_node` / `add_edge` / `add_conditional_edges` 之间迷失；

2. **调试复杂图困难**：当节点数超过 10 个、有多个 Subgraph 嵌套时，不借助 LangSmith Studio 的可视化几乎无法排查状态转移问题；

3. **LangGraph Cloud 强绑定**：生产级 Checkpoint（PostgreSQL）、长时运行任务、流式部署等特性在自托管时需要自行搭建完整基础设施，实际上会逐渐推向付费的 LangSmith Deployment；

4. **长工具调用有隐患**（见上方 Issue #7417）：对于执行时间超 2 分钟的子 Agent，需要手动调整心跳配置，框架没有开箱即用的方案；

5. **与 LangChain 生态耦合**：虽然标榜"可不依赖 LangChain 使用"，但实际上大多数示例和社区支持都是围绕 `langchain-core` 的 `BaseChatModel` / `ToolMessage` 体系，换用裸 OpenAI SDK 时需要更多适配。

---

**GitHub**：https://github.com/langchain-ai/langgraph ⭐ 32k+  
**文档**：https://docs.langchain.com/oss/python/langgraph/overview  
**在线课程**：https://academy.langchain.com/courses/intro-to-langgraph（免费）
