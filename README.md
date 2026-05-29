# TenJudge Agent

TenJudge Agent 是 TenJudge 在线评测系统中的智能体服务模块，面向题目分析、代码理解、提交诊断、代码修改与平台知识问答等场景提供 AI 辅助能力。

该模块基于 FastAPI 提供 HTTP 接口，使用 LangGraph/LangChain 编排智能体执行流程，并通过 Redis Stream + SSE 实现异步流式任务输出。系统同时集成 PostgreSQL、PGVector、TenJudge Server 与 Judge 服务，使智能体具备检索、查询、代码编辑和提交测评等可执行能力。

## 核心能力

### 智能体编排

系统采用 Plan + ReAct 执行模式。Planner 根据会话上下文、用户请求和可用工具生成结构化计划，主 Agent 在 LangGraph 编排下进行推理与工具调用。

Agent 可使用的工具包括：

- 平台知识库检索
- 受控数据库查询
- 内部代码文件操作
- 代码提交测评
- 当前时间与用户信息查询

模型调用、结构化输出和部分工具结果均设计了重试与校验逻辑，以提升执行稳定性。

### 异步任务与流式输出

一次对话请求被拆分为两个阶段：

1. `POST /chat` 创建会话轮次与后台 Agent 任务
2. `GET /chat/{task_id}/events` 通过 SSE 订阅任务事件流

后台任务与前端连接解耦。任务执行过程中，进度、回答片段、标题、失败和结束事件会写入 Redis Stream。SSE 使用 Redis Stream ID 作为事件 ID，支持 `Last-Event-ID` 断线续读。

该设计避免了长时间模型调用阻塞普通 HTTP 请求，也保证前端刷新或网络中断时，后台 Agent 任务仍可继续执行。

### 会话与状态管理

系统持久化维护会话、消息、任务和 Agent 状态：

- `conversations`：会话元信息与运行状态
- `messages`：用户与 Agent 的历史消息
- `tasks`：每轮异步任务及其最终状态引用
- `states`：LangGraph Agent 状态快照

每轮任务完成后，最终 state 会保存至数据库，后续对话可基于上一轮状态继续执行。系统也支持从指定历史轮次重新开始，并清理该轮之后的消息、任务和状态。

### 内部代码文件系统

系统将用户上传代码、提交记录代码和 Agent 生成代码统一抽象为内部代码文件。Agent 可通过稳定的代码文件引用进行编辑、派生和提交测评，而不需要在上下文中反复复制完整源码。

代码文件工具支持：

- 创建代码文件
- 精确字符串替换
- 修改后另存为新文件
- 整文件覆盖
- 更新语言与描述信息

这种方式提升了代码修改的可控性，降低了大模型整段重写代码时产生误删、无关修改和幻觉的风险。同时，代码文件可直接提交至 TenJudge Judge 服务，形成“修改代码 - 提交测评 - 分析结果”的闭环。

### 受控数据库查询

Agent 可查询 OJ 平台中的公开数据，例如题目、标签、用户、比赛和榜单信息。

数据库访问通过独立受限角色和 `agent_read` 视图完成，避免直接暴露业务表。系统在数据库层和工具层同时限制访问范围：

- 仅授予只读权限
- 暴露有限字段
- 隐藏敏感信息
- 设置查询超时
- 限制返回行数、字段长度和总结果大小

### RAG 知识库

系统内置平台知识库检索能力，用于回答 TenJudge 平台规则、题目、提交、比赛等相关问题。

当前 RAG 模块支持本地文档导入、SHA256 变更检测、Parent-Child Chunking、BM25 关键词检索与 PGVector 向量检索。Agent 可在推理过程中自主调用知识库检索工具获取上下文。

后续可进一步扩展 query rewrite、RRF 多路融合、rerank，以及面向 Planner 的 skill 注入能力。

## 系统流程

```text
Frontend
  |
  | POST /chat
  v
FastAPI Agent Service
  |
  | create conversation / message / task
  | create Redis Stream
  | start background Agent task
  v
LangGraph Agent Runner
  |
  | plan, reason, call tools, persist state
  v
Redis Stream
  |
  | GET /chat/{task_id}/events
  v
Frontend receives SSE events
```

## 主要目录

```text
app/router        HTTP API
app/service       对话任务、后台 Runner、TenJudge Server 调用
app/agents        Agent state、LangGraph 编排、Planner、标题与代码摘要
app/tools         数据库、RAG、代码文件、判题等工具
app/rag           知识库导入与检索
app/repository    会话、消息、任务和状态持久化
```

## 运行方式

```bash
uv run uvicorn app.main:app --reload
```

运行测试：

```bash
uv run pytest
```

## 项目定位

TenJudge Agent 的目标是将大语言模型接入在线评测平台的实际业务流程，使其不仅能够回答问题，还能够基于题目、代码、提交记录和平台数据进行检索、分析、修改、测评和持续对话。

该模块重点关注智能体系统的可执行性、状态持久化、工具边界控制、异步流式交互和代码修改可靠性。
