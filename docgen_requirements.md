
# 公文输出模块（DocGen）需求与设计约束说明

## 1. 模块目标（Goal）

实现一个【公文输出模块】，用于：
- 将 LLM 的回答结果按 **固定公文格式** 生成 Word（.docx）文件
- 将 LLM 回答内容 **插入到指定章节**
- 将 **证据信息集中放入“备注/说明”章节**
- 保证输出文档 **可追溯、可审计、格式稳定**
- 适配 GraphRAG 场景，不允许正文出现引用信息

## 2. 总体设计原则（Design Principles）

1. **模板驱动（Template-Driven）**
   - 所有样式、段落、编号、字体由 Word 模板决定
   - 代码只做“内容填充”，不控制样式

2. **结构化输入，确定性输出**
   - LLM 只能输出结构化 JSON
   - 严禁 LLM 直接生成 Word / 富文本

3. **正文与证据强隔离**
   - 正文：不出现任何来源、页码、引用
   - 证据：只出现在“备注 / 说明”章节

4. **失败可控**
   - JSON 不合法 → 直接报错或降级
   - 缺章节 → 用“（待补充）”占位

## 3. 功能需求（Functional Requirements）

### 3.1 输入

#### 3.1.1 Word 模板
- 格式：`.docx`
- 包含固定占位符（Placeholder）
- 示例占位符：
  - `{{TITLE}}`
  - `{{DOC_NO}}`
  - `{{SECTION_1_INTRO}}`
  - `{{SECTION_2_FACTS}}`
  - `{{SECTION_3_ANALYSIS}}`
  - `{{SECTION_4_DECISION}}`
  - `{{EVIDENCE_NOTES}}`

#### 3.1.2 LLM 输出（结构化 JSON）
- 必须严格符合 Schema（见第 4 节）
- 不允许正文 text 中包含任何证据信息

### 3.2 输出

1. Word 文件（.docx）
2. 元数据文件（meta.json），用于审计与追溯
3. （可选）PDF 文件

## 4. LLM 输出数据结构约束（Schema）

### 4.1 顶层结构

```json
{
  "title": "string",
  "doc_no": "string",
  "sections": [],
  "evidence_notes": []
}
```

### 4.2 正文章节（sections）

```json
{
  "key": "SECTION_1_INTRO",
  "text": "正文内容"
}
```

**约束（强制）：**
- `text` 中不得出现文件名、页码、引用说明
- 必须符合正式公文语气

### 4.3 证据备注（evidence_notes）

```json
{
  "topic": "基本情况依据",
  "items": [
    {
      "claim": "结论性表述",
      "evidence": [
        {
          "source": "文件名",
          "page": 3,
          "excerpt": "证据摘录"
        }
      ]
    }
  ]
}
```

## 5. Word 渲染规则（Rendering Rules）

### 5.1 占位符替换规则
- 使用字符串占位符
- 若章节缺失，替换为：`（待补充）`

### 5.2 备注章节渲染规则

```
备注一：<topic>
1. 关于“<claim>”的依据：
   - 来源：<source>，第 <page> 页
     摘录：<excerpt>
```

## 6. 模块划分（Code Structure）

```
functions/
├─ docgenfunc.py
├─ docgen_schema.py
├─ docgen_utils.py
```

## 7. 技术与实现约束

- 使用 `python-docx`
- 不允许修改模板样式
- 渲染逻辑必须确定性

## 8. 禁止事项

- 正文中出现任何引用或证据信息
- LLM 控制 Word 样式
- 在渲染阶段调用 LLM

## 9. 非功能性要求

- 相同输入必须生成相同输出
- 必须支持单元测试
