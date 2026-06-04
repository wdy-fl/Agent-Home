# Agent-Home

Agent-Home 是一个面向 Agent 的本地状态底座，用 REST daemon 的形式为 Agent 提供会话时间线、工作区对象和长期记忆的统一管理能力。

它的目标不是替代 Agent Core，而是把 Agent 长期运行所需的状态能力从核心推理逻辑中剥离出来，让 Agent 通过稳定 API 读写上下文、文件和记忆。

## 项目背景

当 Agent 从一次性对话 Demo 走向长期运行的研发、办公或自动化场景时，状态管理会逐渐成为核心问题：

- 会话消息、运行状态、检查点和恢复位置分散在不同组件中；
- 工作区文件、运行产物和中间结果只落在本地目录，难以治理和迁移；
- 长期记忆、用户偏好和项目事实缺少统一的写入、检索和删除接口；
- 多 Agent 场景下需要明确的身份、隔离和访问边界。

Agent-Home 将这些能力收敛到一个本地优先的服务层中。Agent Core 只需要调用 API，不需要直接关心底层如何存储 session、message、workspace object 或 memory。

## 当前定位

当前版本是 Agent-Home 的 MVP daemon，提供：

- 基于 FastAPI 的本地 REST 服务；
- 基于 SQLite 的元数据和状态持久化；
- 基于本地目录的 workspace object blob 存储；
- 基于 Agent token 的接口鉴权；
- timeline、workspace、memory 三类核心状态能力。

默认服务监听 `127.0.0.1:8765`，默认数据库文件为 `agent_home.sqlite`，workspace object 默认存储在 `.agent-home/objects/` 下。

## 核心能力

### 1. Agent 身份与鉴权

Agent-Home 以 `agent_id` 作为逻辑隔离单位。创建 Agent 后，服务返回一次性 token；后续访问该 Agent 的受保护接口时，需要通过 Bearer token 鉴权。

已实现能力：

- 创建 Agent；
- 查询 Agent 配置；
- 校验 Agent token；
- 为每个 Agent 隔离 timeline、workspace 和 memory 数据。

### 2. Timeline：会话、运行与检查点

Timeline 模块用于记录 Agent 会话中的结构化运行历史。

已实现能力：

- 创建和查询 session；
- 为 session 自动创建初始 branch；
- 创建 run 并更新运行状态；
- 写入 message，并按 branch 维护递增 sequence；
- 查询 branch 消息列表和最新 sequence；
- 创建 checkpoint；
- resume session，恢复当前 active branch 的可见消息；
- rewind session，从 user snapshot checkpoint 派生新 branch。

这使 Agent 可以把“继续运行”“从检查点回退”“识别中断状态”等能力建立在明确的时间线数据上。

### 3. Workspace：工作区对象存储

Workspace 模块用于按路径管理 Agent 的工作区对象。

已实现能力：

- 写入对象；
- 读取对象内容；
- 按 prefix 列举对象；
- 删除对象；
- 记录 object id、路径、类型、大小、内容哈希和时间戳。

当前 workspace 是本地优先实现：对象元数据存储在 SQLite 中，blob 内容存储在本地 `.agent-home/objects/` 目录中。对象路径必须是绝对路径，且不能包含 `..`。单个对象当前最大为 1 MB。

### 4. Memory：长期记忆管理

Memory 模块用于管理 Agent 的长期事实、偏好和项目记忆。

已实现能力：

- 手动创建 memory；
- 按关键词搜索 active memory；
- 读取单条 memory；
- 更新 memory 内容或 tags；
- 软删除 memory；
- 提供 memory extraction endpoint。

当前自动抽取能力默认关闭。调用 extraction endpoint 时，如果 Agent 配置中未启用 `memory.auto_extract.enabled`，服务会返回 `auto_extract_disabled`。

## 当前架构

```text
Agent Core / Agent Runtime
        │
        ▼
Agent-Home REST API
  ├─ Agent Identity & Auth
  ├─ Timeline Service
  ├─ Workspace Service
  └─ Memory Service
        │
        ├─ SQLite metadata/state store
        └─ Local workspace object blobs
```

核心实现文件：

| 文件 | 作用 |
| --- | --- |
| `agent_home/app.py` | 创建 FastAPI app，注册路由，处理 Agent 鉴权 |
| `agent_home/main.py` | daemon 启动入口 |
| `agent_home/storage.py` | SQLite schema、连接和事务管理 |
| `agent_home/timeline.py` | session、branch、run、message、checkpoint、resume、rewind API |
| `agent_home/workspace.py` | workspace object 的写入、读取、列举和删除 |
| `agent_home/memory.py` | memory 的创建、搜索、读取、更新、删除和抽取入口 |
| `agent_home/models.py` | Pydantic 请求和响应模型 |

## API 概览

### Health

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |

### Agent

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/agents` | 创建 Agent 并返回 token |
| `GET` | `/v1/agents/{agent_id}` | 查询 Agent 配置 |

### Timeline

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/agents/{agent_id}/sessions` | 创建 session |
| `GET` | `/v1/agents/{agent_id}/sessions/{session_id}` | 查询 session |
| `POST` | `/v1/agents/{agent_id}/sessions/{session_id}/resume` | 恢复 session |
| `POST` | `/v1/agents/{agent_id}/sessions/{session_id}/rewind` | 从 checkpoint 回退并创建新 branch |
| `POST` | `/v1/agents/{agent_id}/runs` | 创建 run |
| `PATCH` | `/v1/agents/{agent_id}/runs/{run_id}/status` | 更新 run 状态 |
| `PATCH` | `/v1/agents/{agent_id}/branches/{branch_id}` | 更新 branch resume head |
| `POST` | `/v1/agents/{agent_id}/messages` | 写入 message |
| `GET` | `/v1/agents/{agent_id}/branches/{branch_id}/messages` | 查询 branch messages |
| `GET` | `/v1/agents/{agent_id}/branches/{branch_id}/messages/latest-sequence` | 查询最新 message sequence |
| `POST` | `/v1/agents/{agent_id}/checkpoints` | 创建 checkpoint |
| `GET` | `/v1/agents/{agent_id}/branches/{branch_id}/checkpoints` | 查询 branch checkpoints |

### Workspace

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `PUT` | `/v1/agents/{agent_id}/workspace/object?path=/path` | 写入对象 |
| `GET` | `/v1/agents/{agent_id}/workspace/object?path=/path` | 读取对象 |
| `GET` | `/v1/agents/{agent_id}/workspace/objects?prefix=/prefix` | 列举对象 |
| `DELETE` | `/v1/agents/{agent_id}/workspace/object?path=/path` | 删除对象 |

### Memory

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/agents/{agent_id}/memory` | 创建 memory |
| `GET` | `/v1/agents/{agent_id}/memory/search?q=keyword` | 搜索 memory |
| `GET` | `/v1/agents/{agent_id}/memory/{memory_id}` | 读取 memory |
| `PATCH` | `/v1/agents/{agent_id}/memory/{memory_id}` | 更新 memory |
| `DELETE` | `/v1/agents/{agent_id}/memory/{memory_id}` | 删除 memory |
| `POST` | `/v1/agents/{agent_id}/memory/extractions` | 触发记忆抽取 |

## 运行方式

安装依赖：

```bash
python3 -m pip install -e '.[test]'
```

启动本地 daemon：

```bash
python3 -m agent_home.main
```

服务默认监听：

```text
http://127.0.0.1:8765
```

## 测试

运行测试：

```bash
python3 -m pytest
```

测试覆盖当前主要能力：

- health check；
- Agent 创建与鉴权；
- session、resume、rewind；
- workspace object；
- memory CRUD 和搜索。

## 设计原则

Agent-Home 当前遵循以下原则：

1. **Agent Core 与状态底座解耦**：Agent 通过 API 操作状态，不直接绑定具体存储实现。
2. **本地优先，接口稳定**：MVP 使用 SQLite 和本地目录，但对外暴露 REST API，便于后续替换底层存储。
3. **以 Agent 为隔离单位**：timeline、workspace 和 memory 均绑定 `agent_id`。
4. **时间线可恢复**：通过 branch、checkpoint 和 resume head 支持中断恢复与回退。
5. **记忆可控**：当前 memory 以手动写入和显式搜索为主，自动抽取默认关闭。

## 后续方向

后续可以在当前 MVP 基础上扩展：

- 更完整的 memory extraction 和候选记忆晋升机制；
- memory 与 workspace 的统一检索；
- SQLite FTS、向量检索或 hybrid search；
- workspace 的远端持久化、同步或挂载能力；
- 多 Agent / 多用户 / 多项目的权限、审计和配额治理；
- 面向不同 Agent Runtime 的 SDK 或插件接入。
