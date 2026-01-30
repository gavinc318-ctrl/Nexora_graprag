# Graph 模块完整设计文档

## 0. 目录

1. [需求和架构总览](#1-需求和架构总览)
2. [数据库设计](#2-数据库设计)
3. [模块设计](#3-模块设计)
4. [手工编辑和优化](#4-手工编辑和优化)
5. [UI 界面设计](#5-ui-界面设计)
6. [后端接口设计](#6-后端接口设计)
7. [优化策略和工具](#7-优化策略和工具)
8. [工作流程](#8-工作流程)
9. [配置项](#9-配置项)
10. [监控和统计](#10-监控和统计)
11. [分阶段实现计划](#11-分阶段实现计划)

---

## 1. 需求和架构总览

### 1.1 核心目标

根据 graph_PRD.md，Lite GraphRAG 的目标是：

- 通过 **Lite Graph** 增强 RAG 系统，而不是替代现有流程
- 解决"多页分散规则、多条件问题、无证据幻觉"问题
- 第一阶段只用 PostgreSQL（不用真实图数据库如 Neo4j）
- 使用 LLM 进行轻量实体和关系抽取

### 1.2 设计原则

1. **Graph 是增强，而不是替代** - 向量检索仍是主召回手段
2. **实体优先** - 所有 Graph 能力围绕"实体"展开
3. **结构轻、约束强** - 宁可少实体、少关系，也要稳定可控
4. **可随时关闭** - Graph 旁路不影响现有功能

### 1.3 关键概念

**实体（Entity）：** 文档中的核心概念节点
- 术语 / 概念（如"重大资产重组"）
- 法规 / 制度 / 条款
- 组织 / 部门
- 产品 / 系统
- 指标 / 阈值

**Graph（Lite）：** 包含
- 实体节点（Entity）
- 实体共现边（Co-occurrence Edge）
- 轻语义边（Defines / Requires / Exempts 等）

### 1.4 工作流概览

```
离线流程 (Ingest):
  Chunk 已存在 → LLM 实体抽取 → 共现边构建 → 社区摘要生成
  
在线流程 (Query):
  用户问题 → Query 实体抽取 → Graph 子图检索 → 查询扩展 
  → 向量检索 → rerank → LLM 生成
```

---

## 2. 数据库设计

### 2.1 新增表结构

> 当前已统一使用 `graphrag_db`，真实落地结构以
> `graphfunc/002_graphrag_schema.sql` 为准。

#### 实体表 (entity)

```sql
CREATE TABLE entity (
    entity_id UUID PRIMARY KEY,
    app_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,           -- 术语、法规、组织、产品等
    aliases JSONB,
    confidence TEXT DEFAULT 'medium',
    classification SMALLINT DEFAULT 0,
    occurrence_count INT DEFAULT 1,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(app_id, name, type, classification)
);
```

**字段说明：**
- `entity_id`: UUID 主键
- `app_id`: 应用 ID，用于多租户隔离
- `name`: 实体名称（唯一约束配合 app_id 和 type）
- `type`: 实体类型（术语、法规、组织、产品、指标等）
- `aliases`: JSON 数组，存储别名和同义词
- `confidence`: 置信度等级 (high/medium/low)
- `classification`: 密级（0-3）
- `occurrence_count`: 提及次数（用于排序/衰减）
- `is_active`: 是否可用（删除文档后可下线）
- `created_at/updated_at`: 时间戳

---

#### 实体-Chunk 关联表 (entity_chunk)

```sql
CREATE TABLE entity_chunk (
    app_id TEXT NOT NULL,
    entity_id UUID NOT NULL,
    chunk_id UUID NOT NULL,
    mention_count INT DEFAULT 1,
    char_position INT,
    extracted_context TEXT,
    confidence TEXT DEFAULT 'medium',
    classification SMALLINT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY(app_id, entity_id, chunk_id)
);
```

**字段说明：**
- 记录实体在哪个 chunk 中出现
- `mention_count`: 在该 chunk 的提及次数
- `extracted_context`: 可选的抽取上下文
- 用于追踪实体的证据和上下文

---

#### 实体边表 (entity_edge)

```sql
CREATE TABLE entity_edge (
    app_id TEXT NOT NULL,
    src_entity_id UUID NOT NULL,
    dst_entity_id UUID NOT NULL,
    edge_type TEXT NOT NULL,      -- co_occurs, defines, requires, exempts, refers_to
    weight NUMERIC(5,3) DEFAULT 0.5,
    confidence TEXT DEFAULT 'medium',
    classification SMALLINT DEFAULT 0,
    evidence_count INT DEFAULT 1,
    evidence_chunk_ids JSONB,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY(app_id, src_entity_id, dst_entity_id, edge_type)
);
```

**字段说明：**
- `edge_type`: 边的类型
  - `co_occurs`: 共现边（自动生成）
  - `defines`: A 定义 B
  - `requires`: A 要求 B
  - `exempts`: A 豁免 B
  - `refers_to`: A 指向 B
- `weight`: 边的权重（共现频次 × 实体置信度）
- `evidence_count`: 证据计数（用于衰减）
- `evidence_chunk_ids`: 支撑此边的 chunk IDs

---

#### 实体社区摘要表 (entity_summary)

```sql
CREATE TABLE entity_summary (
    app_id TEXT NOT NULL,
    entity_id UUID NOT NULL,
    summary_text TEXT NOT NULL,
    summary_type TEXT DEFAULT 'entity',
    anchor_chunk_ids JSONB,
    confidence TEXT DEFAULT 'medium',
    classification SMALLINT DEFAULT 0,
    last_updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY(app_id, entity_id)
);
```

**字段说明：**
- `summary_text`: 由 LLM 生成的社区摘要（概念说明、规则、例外等）
- `anchor_chunk_ids`: 摘要所依据的 chunk IDs（用于追溯）
- `summary_type`: 摘要类型（entity/cluster 等）

---

#### Graph 异步维护任务表 (graph_job)

```sql
CREATE TABLE graph_job (
    job_id UUID PRIMARY KEY,
    app_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    error_message TEXT
);
```

### 2.2 创建脚本（已落地）

当前使用统一库脚本：
- `graphfunc/000_graphrag_db_setup.sql`
- `graphfunc/001_graphrag_extensions.sql`
- `graphfunc/002_graphrag_schema.sql`

旧的 `003_graph.sql` 方案已弃用（已合并到统一库）。


---

## 3. 模块设计

### 3.1 新增模块清单

| 模块名 | 位置 | 职责 |
|--------|------|------|
| **graph_pg_store.py** | `graphfunc/` | ✅ 已实现：Graph CRUD + Job |
| **实体抽取（内置）** | `core.py` | ✅ 已实现：call_vllm_chat |
| **图构建（内置）** | `core.py` | ✅ 已实现：co_occurs + entity_chunk |
| **图检索/扩展（内置）** | `core.py` | ✅ 已实现：query expansion |
| **graphmng_gr.py** | 独立 Graph 管理 UI | ✅ 已实现（基础版） |


### 3.2 修改的模块

| 模块 | 修改内容 |
|------|---------|
| **rag_pg_store.py** | 保留不变 |
| **core.py** | ✅ 已集成：抽取、构图、检索扩展、删除入队 |
| **config.py** | ✅ 已新增：GRAPH_ENABLED / GRAPH_JOB_POLL_INTERVAL |
| **requirements.txt** | 如需额外依赖（如 pyvis、networkx） |

---

## 4. 手工编辑和优化

### 4.1 核心编辑场景

| 场景 | 操作 | 说明 |
|------|------|------|
| **实体管理** | 创建、编辑、删除、合并 | 处理重复实体、纠正错误抽取、添加缺失实体 |
| **关系管理** | 创建、删除、调整权重 | 修复错误的共现关系，添加语义边 |
| **别名管理** | 添加、删除、设置主别名 | 处理别名重复和同义词 |
| **社区摘要** | 查看、编辑、重新生成 | 优化实体社区的上下文说明 |
| **质量检查** | 孤立实体、低置信度、异常权重 | 清理垃圾数据 |

### 4.2 编辑工作流程

```
初次构建（自动化）:
  文档摄入 → LLM 实体抽取 → 共现边构建 → 社区摘要生成

优化迭代（手工 + 自动化）:
  1. 运行质量检查
     ├─ 识别孤立实体、低置信度、重复别名
     └─ 生成问题报告
  
  2. 手工编辑阶段
     ├─ 实体管理：合并重复、删除错误
     ├─ 关系管理：删除噪音、调整权重
     └─ 摘要管理：优化文本、添加规则
  
  3. 自动优化
     ├─ 去重和标准化
     ├─ 权重平衡
     └─ 社区摘要重新生成
  
  4. 验证
     └─ 在实际查询上测试效果
```

---

## 5. UI 界面设计

### 5.1 总体布局

新增独立 Gradio `graphmng_gr.py`：
- **已完成（Phase 1）**：Graph 开关 / Graph Job 列表 / 手动维护 / 基础统计
- **后续（Phase 2）**：实体管理 / 关系管理 / 社区摘要 / 质量检查 / 可视化

完整管理台规划如下（Phase 2）：

```
┌─ Graph 管理 ──────────────────────────────┐
├─┬─────────────────────────────────────────┤
│ ├─ 实体管理
│ ├─ 关系管理
│ ├─ 社区摘要
│ ├─ 质量检查
│ └─ 可视化
└───────────────────────────────────────────┘
```

### 5.2 子界面详设计

#### 5.2.1 实体管理界面

```
┌───────────────────────────────────────────┐
│ Graph 管理 - 实体编辑                      │
├───────────────────────────────────────────┤
│ [应用选择 dropdown] [搜索实体 input]      │
├───────────────────────────────────────────┤
│ 实体表格：                                │
│ ┌─────────────────────────────────────┐  │
│ │ Name │ Type │ Aliases │ Conf │ 操作 │  │
│ │ ...  │ ...  │ ...     │ ... │[编][删]│
│ └─────────────────────────────────────┘  │
│                                          │
│ 批量操作:                                │
│ [合并实体] [删除低置信度] [导出为CSV]    │
└───────────────────────────────────────────┘
```

**功能清单：**
- ✅ 搜索实体（名称、别名、类型）
- ✅ 编辑实体信息（名称、类型、别名、置信度）
- ✅ 查看该实体的关联 chunk
- ✅ 合并实体：选择多个实体 → 保留主实体，其他作别名，合并关系
- ✅ 删除实体
- ✅ 导出为 CSV

#### 5.2.2 关系管理界面

```
┌───────────────────────────────────────────┐
│ Graph 管理 - 关系编辑                      │
├───────────────────────────────────────────┤
│ [应用选择 dropdown] [源实体 input]       │
├───────────────────────────────────────────┤
│ 关系表格：                                │
│ ┌──────────────────────────────────────┐ │
│ │ Src│ Dst│Type│Weight│Evidence│操作  │ │
│ │ ...│... │... │...   │...     │[编][删]│
│ └──────────────────────────────────────┘ │
│                                          │
│ 批量操作:                                │
│ [调整权重] [更改关系类型] [删除低权重]   │
└───────────────────────────────────────────┘
```

**功能清单：**
- ✅ 查看实体的进出关系
- ✅ 编辑关系权重、类型
- ✅ 查看关系的 Evidence（支撑 chunk）
- ✅ 删除错误关系
- ✅ 批量调整权重

#### 5.2.3 社区摘要管理界面

```
┌───────────────────────────────────────────┐
│ Graph 管理 - 社区摘要                      │
├───────────────────────────────────────────┤
│ [应用选择 dropdown] [实体选择 dropdown]  │
├───────────────────────────────────────────┤
│ 邻接实体:                                │
│ Entity1 [remove]  Entity2 [remove]      │
│                                          │
│ 摘要文本:                                │
│ ┌─────────────────────────────────────┐ │
│ │ [多行文本框] ← 可编辑                 │ │
│ └─────────────────────────────────────┘ │
│                                          │
│ 锚定 chunks: [显示列表] [编辑]           │
│                                          │
│ 操作:                                    │
│ [保存] [重新生成摘要]                    │
└───────────────────────────────────────────┘
```

**功能清单：**
- ✅ 查看邻接实体（可删除来优化邻域）
- ✅ 手工编辑摘要文本
- ✅ 调整锚定的 chunk
- ✅ 重新生成摘要（LLM）

#### 5.2.4 质量检查界面

```
┌───────────────────────────────────────────┐
│ Graph 管理 - 质量检查                      │
├───────────────────────────────────────────┤
│ [应用选择 dropdown]                      │
├───────────────────────────────────────────┤
│ 检查项选择:                              │
│ ☐ 孤立实体(无边)                         │
│ ☐ 低置信度实体(< medium)                 │
│ ☐ 低权重边(< threshold)                  │
│ ☐ 重复别名                               │
│ ☐ 过度密集的实体簇(>20 neighbors)        │
│                                          │
│ 结果表格:                                │
│ ┌──────────────────────────────────────┐ │
│ │ Issue | Count | Entity | Action      │ │
│ │ ...   │ ...   │ ...    │ [delete]   │ │
│ └──────────────────────────────────────┘ │
│                                          │
│ 批量处理:                                │
│ [删除全选] [合并重复] [降权] [导出报告]  │
└───────────────────────────────────────────┘
```

**功能清单：**
- ✅ 自动检测数据质量问题
- ✅ 显示详细的问题列表
- ✅ 一键修复或手动处理

#### 5.2.5 可视化界面

```
┌───────────────────────────────────────────┐
│ Graph 管理 - 可视化                       │
├───────────────────────────────────────────┤
│ [应用选择] [实体选择] [深度: 1-3] [权重显示: ☐]
├───────────────────────────────────────────┤
│                                          │
│    ┌───────────────┐                    │
│    │   Entity1     │                    │
│    │  (术语)       │                    │
│    └───────────────┘                    │
│           /|\                           │
│          / | \                          │
│      [E2] [E3] [E4]                    │
│    (法规) (组织) (产品)                  │
│                                          │
│ 单击节点查看详情 | 单击边调整权重        │
└───────────────────────────────────────────┘
```

**功能清单：**
- ✅ 网络图可视化（用 pyvis + networkx）
- ✅ 动态调整展示范围（hops）
- ✅ 交互式编辑关系
- ✅ 悬停显示节点详情

---
