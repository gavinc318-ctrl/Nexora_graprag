# Graph 查询中实体召回优化方案（Hybrid：ILIKE + 向量检索）

## 背景问题

当前 GraphRAG 查询阶段，实体召回主要依赖 SQL：

```sql
name ILIKE %query% OR aliases::text ILIKE %query%
```

该方案在以下场景表现良好：

* 实体名与 query 字面相同或高度相似
* 短词、关键词查询（如：跑步、心率、睡眠）
* 需要强可解释性、可调试性的场景

但在真实用户查询中存在明显不足：

* 同义 / 近义表达无法命中（如：跑步数据 ≠ 跑步记录）
* 用户使用自然语言描述意图而非实体名
* 抽象概念或组合表达（如：我之前完成的跑步）

因此，仅依赖 ILIKE 会导致实体召回不足，进而影响 Graph 起点选择与整体问答质量。

---

## 设计结论（核心原则）

**不替换 ILIKE，而是采用 Hybrid 方案：**

> **ILIKE（精确召回） + 向量检索（语义扩展） + 结果融合排序**

结论要点：

* ILIKE 必须保留，作为第一召回层
* 向量检索只作为补充，而非唯一来源
* 最终实体集合通过统一打分排序


## 推荐的实体召回流程

### Step 1：ILIKE 精确 / 半精确匹配（保留现有实现）

使用当前函数：

```python
find_entities_by_name_or_alias(ctx, query, limit)
```

特点：

* 命中结果直接视为高置信度实体
* 适合作为 Graph 查询的稳定锚点

---

### Step 2：向量语义召回（条件触发）

仅在以下情况下触发：

* ILIKE 返回结果数量 < limit
* query 来源于自然语言（如 LLM 抽取的概念实体）

示例接口：

```python
find_entities_by_embedding(ctx, query, limit)
```

向量召回用于补充：

* 同义实体
* 语义接近但字面不相同的实体

---

### Step 3：结果合并与统一排序（关键步骤）

对两路结果进行：

* entity_id 去重
* 统一打分
* 按 score 降序排序

#### 推荐打分公式

```text
final_score =
  1.0 * ilike_match
+ 0.7 * embedding_similarity
+ 0.2 * log(occurrence_count + 1)
```

说明：

* ilike_match：ILIKE 命中记为 1，否则为 0
* embedding_similarity：向量相似度（0~1）
* occurrence_count：实体在文档中的出现频次，作为先验

---

## 最小侵入式实现示例

```python
import math

def find_entities_hybrid(ctx, query, limit=10):
    results = {}

    # 1. ILIKE 精确匹配
    for r in find_entities_by_name_or_alias(ctx, query, limit):
        results[r["entity_id"]] = {
            **r,
            "score": 1.0 + 0.2 * math.log(r["occurrence_count"] + 1),
        }

    # 2. 向量语义补充
    if len(results) < limit:
        for r, sim in find_entities_by_embedding(ctx, query, limit):
            if r["entity_id"] not in results:
                results[r["entity_id"]] = {
                    **r,
                    "score": 0.7 * sim + 0.2 * math.log(r["occurrence_count"] + 1),
                }

    return sorted(
        results.values(),
        key=lambda x: x["score"],
        reverse=True
    )[:limit]
```

---

## 适用场景说明

### 仅使用 ILIKE 即可的场景

* query 本身就是实体名或关键词
* UI 下拉选择 / 固定标签
* Debug / Explain / 审计场景

### 必须启用 Hybrid 的场景

* 自然语言问句
* 用户描述“想找什么”而非“叫什么”
* Graph Traversal 的起始实体选择

---

## 总结

* ILIKE 是 Graph 实体召回的“骨架”，必须保留
* 向量检索是语义扩展能力，用于补漏
* Hybrid 方案在稳定性、召回率、可解释性之间取得最佳平衡

