# Graph 模块 PRD（Lite GraphRAG）

## 1. 背景与目标

### 1.1 当前问题
当前系统基于：
- 文档切分（chunk）
- 向量检索（pgvector）
- rerank
- LLM 生成

在以下场景中存在明显不足：
- 规则/定义分散在多页、多段，难以整体理解
- 多条件 + 例外问题，回答逻辑不完整
- 当语料中无相关信息时，模型容易“合理胡说”

### 1.2 引入 Graph 的目标
在 **不替换现有 RAG 主流程** 的前提下：
- 提升多页、多段规则问题的准确性
- 为检索阶段提供“语义范围约束”
- 降低无证据情况下的幻觉概率

### 1.3 范围约束（第一阶段）
- ❌ 不引入独立图数据库
- ❌ 不做复杂社区发现算法
- ❌ 不做 Text2Cypher / 图查询语言
- ✅ 使用 PostgreSQL 存储 Graph 结构
- ✅ 使用 LLM 进行实体与轻关系抽取

---

## 2. 核心设计原则

1. **Graph 是增强，而不是替代**
   - 向量检索仍是主召回手段
2. **实体优先**
   - 所有 Graph 能力围绕“实体”展开
3. **结构轻、约束强**
   - 宁可少实体、少关系，也要稳定可控
4. **可随时关闭**
   - Graph 旁路不影响现有功能

---

## 3. 核心概念定义

### 3.1 实体（Entity）
实体是文档中的核心概念节点，例如：
- 术语 / 概念（如“重大资产重组”）
- 法规 / 制度 / 条款
- 组织 / 部门
- 产品 / 系统
- 指标 / 阈值

### 3.2 Graph（Lite）
第一阶段的 Graph 包含：
- 实体节点（Entity）
- 实体共现边（Co-occurrence Edge）
- 可选轻语义边（Defines / Requires / Exempts 等）

---

## 4. 离线流程设计（Ingest 阶段）

### 4.1 Chunk 生成（已有）
输入：
- PDF / 图片页

输出：
- chunk_id
- doc_id
- page_no
- text
- meta

---

### 4.2 实体抽取

#### 4.2.1 抽取策略
- 每个 chunk 最多抽取 1–3 个实体
- 仅抽取高价值实体（术语/法规/组织/系统）
- 输出包含：
  - entity_name
  - entity_type
  - aliases（可选）
  - confidence（high / medium / low）

#### 4.2.2 存储结构

**entity**
- entity_id (PK)
- app_id
- name
- type
- aliases (jsonb)
- created_at

**entity_chunk**
- app_id
- entity_id
- chunk_id
- doc_id
- page_no
- confidence
- weight

---

### 4.3 Graph 边构建

#### 4.3.1 共现边（必做）
规则：
- 两个实体在同一 chunk / 页面中出现，即建立共现边

边类型：
- `co_occurs`

权重：
- 共现次数 × 实体置信度

#### 4.3.2 轻语义边（可选）
只抽取少量高确定性关系：
- defines
- requires
- exempts
- refers_to

#### 4.3.3 存储结构

**entity_edge**
- app_id
- src_entity_id
- dst_entity_id
- edge_type
- weight
- evidence_chunk_ids (jsonb)
- updated_at

---

### 4.4 实体社区摘要（推荐）

#### 4.4.1 社区定义
- 以单个高频实体为中心
- 取 1-hop 邻居实体
- 汇总关联 chunk

#### 4.4.2 摘要内容
- 概念说明
- 核心规则 / 条件
- 流程（如有）
- 例外 / 不确定点

#### 4.4.3 存储结构

**entity_summary**
- app_id
- entity_id
- summary_text
- anchor_chunk_ids (jsonb)
- updated_at

---

## 5. 在线流程设计（Query 阶段）

### 5.1 Query 实体抽取
- 从用户问题中抽取 1–5 个实体候选
- 进行别名归一与模糊匹配

---

### 5.2 Graph 子图检索
- 命中实体作为 seed
- 拉取 1-hop 邻居实体（TopK）
- 获取：
  - entity_summary
  - anchor_chunk_ids

---

### 5.3 Graph 增强检索（核心）

#### 5.3.1 Query Expansion
构建增强检索 Query：
- 用户原始问题
- 命中实体名称 / 别名
- 关联实体名称
- 实体社区摘要（短）

#### 5.3.2 Anchor-first 策略
- anchor_chunk_ids 优先进入候选集
- 再合并 pgvector TopN 结果
- 统一 rerank

---

### 5.4 LLM 生成策略

#### Prompt 结构
1. Graph Context（不可引用）
   - 实体
   - 社区摘要
2. Evidence Context（可引用）
   - 原始 chunk 文本 + page_no

规则：
- 所有结论必须由 Evidence 支撑
- Graph Context 仅用于组织逻辑

---

### 5.5 兜底与失败策略
- 无实体命中 → 进入保守 RAG 模式
- 检索相似度整体偏低 → 明确提示“资料不足”
- 禁止无证据推断

---

## 6. 实施计划（1–2 周）

### Week 1
- 实体抽取
- entity / entity_chunk 表
- 共现边构建

### Week 2
- 实体摘要生成
- Query 增强
- rerank 对比评估
- 幻觉控制验证

---

## 7. 可扩展方向（后续）
- 引入真实图数据库（Neo4j / NebulaGraph）
- 多 hop 路径推理
- Graph-based rerank
- 与权限 / 分类标签深度结合
