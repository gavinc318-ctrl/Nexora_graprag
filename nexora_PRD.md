# 基于 Qwen3 的 RAG 平台 - 产品需求文档 (PRD)

**版本**: 1.0  
**日期**: 2026年1月16日  
**项目状态**: MVP开发中 (核心流程已完成，多格式支持扩展中)

---

## 1. Executive Summary

本项目旨在构建一个可商业化的 **RAG 平台**，整合文档解析、向量检索和大语言模型对话能力，为企业提供端到端的智能文档问答解决方案（当前代码未实现 GraphRAG/知识图谱）。

### 核心价值主张
- **多模态文档理解**：支持 PDF、Word、Excel、TXT、图片等多种格式的智能解析
- **高精度检索**：结合向量检索、页级合并与 Rerank 重排序，提供准确的上下文召回
- **企业级安全**：基于 RLS (Row Level Security) 的多租户隔离和权限控制
- **人机协同**：提供数据校准界面，支持人工修正 OCR 结果和 Chunk 质量

### MVP 目标
完成一个功能完整的 RAG 原型系统，包含：文档上传解析、向量入库、智能问答、数据管理等核心功能，并支持 3 种以上文档格式（PDF、TXT、图片），可在单机或小规模集群部署运行。

---

## 2. Mission

### 产品使命
让企业能够轻松构建自己的智能文档知识库，通过 AI 赋能知识管理和决策支持。

### 核心原则
1. **准确性优先**：通过多模态解析、向量检索、Rerank、人工校准等多层保障，确保答案质量
2. **开放可扩展**：采用标准接口（OpenAI 兼容 API）和模块化架构，方便集成和扩展
3. **企业级可靠**：支持多租户隔离、权限控制、数据溯源等企业必需特性
4. **渐进式交付**：MVP 先聚焦核心流程，后续逐步增强格式支持和高级功能
5. **人机协同**：AI 自动化处理 + 人工质量把控，达到最佳效果

---

## 3. Target Users

### 主要用户画像

**用户类型 1：企业知识库管理员**
- 角色：负责导入、维护企业文档知识库
- 技术水平：中等（会使用 Web 界面和 Docker）
- 核心需求：
  - 批量上传各类格式文档
  - 监控解析和入库状态
  - 修正 OCR 错误和低质量 Chunk
  - 管理文档权限和分类

**用户类型 2：终端业务用户**
- 角色：通过问答界面获取知识
- 技术水平：低（仅使用 Web 界面）
- 核心需求：
  - 用自然语言提问
  - 快速得到准确答案
  - 查看答案来源和引用页
  - 支持多轮对话

**用户类型 3：开发者 / 系统集成商**
- 角色：将 RAG 能力集成到自己的系统
- 技术水平：高
- 核心需求：
  - OpenAI 兼容的 API 接口
  - 清晰的部署文档和 Docker Compose
  - 可定制的模型和参数
  - 稳定的服务可用性

### 痛点
- 传统文档管理难以实现语义检索
- OCR 和 VLM 解析准确率不足
- 多格式文档处理复杂
- 企业数据安全和隔离要求高
- 缺少可视化的数据质量管理工具

---

## 4. MVP Scope

### In Scope (MVP 包含功能)

#### ✅ 核心功能
- ✅ 多格式文档上传与解析（PDF、TXT、图片、Word、Excel）
- ✅ OCR 文本提取（远程 OCR HTTP 服务，当前对接 PaddleOCR 服务端）
- ✅ PDF 文本层提取（PyMuPDF）
- ✅ VLM 视觉理解（可选，与 OCR 并行/组合）
- ✅ 文本分块 (Chunking)：支持滑窗和递归两种模式
- ✅ 向量化 (Embedding)：通过 vLLM 或 Ollama 的 OpenAI 兼容接口调用（默认 qwen3-embedding:8b）
- ✅ 向量检索：PostgreSQL + pgvector
- ✅ 重排序 (Rerank)：外部 HTTP Rerank 服务（模型可配置）
- ✅ 对话生成：默认 vLLM(Qwen3-VL-8B-Instruct)；可切换 OpenAI Responses API（如 GPT-5）
- ✅ 引用溯源：显示命中 Chunk 的文档来源和页码

#### ✅ 用户界面
- ✅ 文件导入界面（Gradio）：上传文档、配置解析参数、查看入库状态
- ✅ 用户查询界面（Gradio）：对话问答、查看引用页预览（PNG）
- ✅ 数据管理界面（Gradio）：OCR 结果校对、Chunk 人工编辑、重算向量

#### ✅ 技术实现
- ✅ 核心逻辑与 UI 解耦：core.py 提供可复用能力
- ✅ 对象存储：MinIO（存储原文件、OCR 产物、PNG）
- ✅ 向量数据库：PostgreSQL + pgvector
- ✅ 多租户隔离：基于 RLS 的 app_id + clearance 权限控制
- ✅ Docker 容器化：MinIO、PG、vLLM、Rerank、OCR 服务均可 Docker 部署
- ✅ 文档类型识别：自动根据扩展名调用对应解析器

#### ✅ 安全与配置
- ✅ 环境变量配置：config.py 统一管理
- ✅ 基础 RLS 权限：app_id 和 clearance 级别隔离
- ✅ API 鉴权：FastAPI 层已有 LDAP/JWT 登录与会话接口（角色/权限细化待完善）

### Out of Scope (MVP 不包含，后续版本考虑)

#### ❌ 高级功能
- ❌ 前端 React/Next.js 重构（当前仅 Gradio 原型）
- ❌ 完整的 RESTful API 覆盖与回归测试（api_server.py 已实现核心接口，但仍需完善和稳定性验证）
- ❌ 细粒度角色/权限管理体系（当前仅有 LDAP/JWT 登录与基础会话）
- ❌ 文档版本管理（目前仅 version_id，无版本对比）
- ❌ 批量导入和任务队列
- ❌ 实时监控和日志可视化

#### ❌ 集成与扩展
- ❌ 与企业 SSO 集成（如 OAuth2）
- ❌ 与外部知识库集成（如 Confluence、Notion）
- ❌ 更完善的多模型切换与路由（当前仅支持 vLLM 与 OpenAI 两类路径）
- ❌ 自定义 Prompt 模板管理

#### ❌ 部署与运维
- ❌ Kubernetes 部署方案
- ❌ 负载均衡和高可用配置
- ❌ 自动扩缩容
- ❌ 备份恢复策略

---

## 5. User Stories

### 主要用户故事

1. **作为知识库管理员**，我想要上传一份 200 页的 PDF 合同文档，系统能自动提取文本和表格，并分块存入向量数据库，以便后续检索。
   - **示例**：上传《供应商合作协议.pdf》，选择"OCR + PDF文本层"，设置 app_id 为 "legal"，系统自动完成解析、分块、向量化、入库。

2. **作为业务用户**，我想要用自然语言提问"第三方责任险的免赔额是多少"，系统能准确找到相关段落并给出答案，同时显示来源页码。
   - **示例**：输入问题后，系统召回 3 个 Chunk，经过 Rerank 排序，组装上下文，LLM 生成答案并标注"引用自《供应商合作协议.pdf》第 47 页"。

3. **作为知识库管理员**，我想要查看某个文档的 OCR 结果，发现第 15 页的表格识别有误，我能手动修正并更新向量。
   - **示例**：在数据管理界面输入 doc_dir 和 page_no=15，系统显示该页 PNG 和 OCR 文本，我编辑 Chunk 内容，点击"保存并重算向量"。

4. **作为开发者**，我想要通过 HTTP API 调用文档问答能力，将其集成到我的客服系统中。
   - **示例**：POST /v1/chat/send 传入 text 和 rag_app_id，返回 JSON 格式的答案和引用信息。

5. **作为知识库管理员**，我想要批量导入多种格式的文档（PDF、Word、Excel、图片），系统能自动识别格式并选择合适的解析器。
   - **示例**：上传文件夹包含 5 个 PDF、3 个 DOCX、10 张 JPG，系统根据扩展名自动调用 pdffunc、docxfunc、imgfunc。

6. **作为安全合规人员**，我希望不同部门的文档互相隔离，销售部门看不到财务部门的文档。
   - **示例**：销售部门文档设置 app_id="sales"，财务部门设置 app_id="finance"，查询时基于 RLS 自动过滤。

7. **作为运维人员**，我希望所有服务都能通过 Docker Compose 一键启动，方便在不同环境部署。
   - **示例**：执行 `docker-compose up -d`，自动拉起 MinIO、PostgreSQL、vLLM、Rerank、OCR 服务。

8. **作为产品经理**，我希望看到每个文档的解析统计（页数、字符数、Chunk 数、特殊块数），以评估系统性能。
   - **示例**：文档入库后返回 JSON：`{"pages": 50, "pdf_chars": 120000, "chunks": 60, "special_blocks": 5}`。

---

## 6. Core Architecture & Patterns

### 高层架构

```
┌─────────────────────────────────────────────────────────────┐
│                     前端层 (UI Layer)                        │
│   ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  │
│   │ 文件导入界面   │  │ 用户查询界面   │  │ 数据管理界面   │  │
│   │ (gradio_ui.py)│  │(user_query_ui)│  │(datamng_gr.py)│  │
│   └───────┬───────┘  └───────┬───────┘  └───────┬───────┘  │
└───────────┼──────────────────┼──────────────────┼──────────┘
            │                  │                  │
            └──────────────────┼──────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────┐
│                   核心逻辑层 (Core Layer)                  │
│                        core.py                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │ 文档解析      │  │ 向量检索      │  │ 对话生成      │   │
│  │ ingest_file() │  │ chat_send()   │  │ build_answer()│   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
└───────┬──────────────────────┬──────────────────┬─────────┘
        │                      │                  │
┌───────▼───────┐    ┌─────────▼─────────┐   ┌───▼────────┐
│ Functions层    │    │  存储层 (Storage) │   │ 模型服务层  │
│ ┌───────────┐ │    │ ┌───────────────┐ │   │ ┌────────┐ │
│ │ocrfunc    │ │    │ │ PostgreSQL +  │ │   │ │ vLLM   │ │
│ │pdffunc    │ │    │ │ pgvector      │ │   │ │(Qwen3) │ │
│ │txtfunc    │ │    │ └───────────────┘ │   │ └────────┘ │
│ │imgfunc    │ │    │ ┌───────────────┐ │   │ ┌────────┐ │
│ │docxfunc   │ │    │ │ MinIO         │ │   │ │ Rerank │ │
│ │xlsxfunc   │ │    │ │ (对象存储)     │ │   │ │(HTTP)  │ │
│ │chunkfunc  │ │    │ └───────────────┘ │   │ └────────┘ │
│ │vlmfunc    │ │    │                   │   │ ┌────────┐ │
│ └───────────┘ │    │                   │   │ │OCR服务 │ │
└───────────────┘    └───────────────────┘   │ │(Paddle)│ │
                                              │ └────────┘ │
                                              └────────────┘
```

### 目录结构

```
./code/
├── core.py                  # 核心逻辑（与 UI/API 解耦）
├── gradio_ui.py             # 文件导入界面（仅 UI），复用 core.py
├── datamng_gr.py            # Chunk数据人工校准界面（仅 UI），复用 core.py
├── user_query_ui.py         # 最终用户对话界面（仅 UI），复用 core.py
├── api_server.py            # FastAPI 服务端（已实现核心接口）
├── config.py                # 全局配置
├── requirements.txt         # Python 依赖
│
├── functions/               # 功能模块层
│   ├── __init__.py
│   ├── ocrfunc.py           # OCR 处理函数
│   ├── pdffunc.py           # PDF 处理函数
│   ├── txtfunc.py           # TXT 处理函数
│   ├── imgfunc.py           # 图像文件处理函数
│   ├── docxfunc.py          # Word 文件处理函数
│   ├── xlsxfunc.py          # Excel 文件处理函数
│   ├── vlmfunc.py           # VLM 处理函数
│   ├── chunkfunc.py         # Chunk 处理函数（分块、检测表格图形）
│   ├── docgenfunc.py        # 文档生成（模板填充）工具
│   ├── docgen_schema.py     # 文档生成数据结构
│   ├── rerank_client.py     # Rerank 客户端
│   ├── object_store.py      # MinIO 对象存储封装
│   └── rag_pg_store.py      # PostgreSQL + pgvector 封装
│
├── relateddocker/           # 相关服务的 Docker Compose 配置
│   ├── minio-docker/
│   ├── pgv-docker/
│   ├── vllm-docker/
│   ├── rerank-docker/
│   ├── ocr/
│   └── ocrgpu/
│
└── frontend/                # Next.js/React 前端目录（当前未接入主流程）
```

### 关键设计模式

**1. 分层解耦**
- **UI 层**：仅负责界面交互（Gradio），不包含业务逻辑
- **Core 层**：提供可复用的核心能力函数，供 UI 和 API 调用
- **Functions 层**：按功能模块拆分（解析、分块、存储），单一职责

**2. 统一入库抽象**
- `ingest_file(file_path, ...)` 作为所有文件的统一入口
- 根据扩展名自动路由到对应解析器（pdffunc、txtfunc、imgfunc 等）
- 非 PDF 文档均通过 `ingest_pages_common()` 完成入库

**3. Parser → Pages → Chunks 流水线**
- **Parser 层**：各格式解析器将文档转为统一的 `pages` 列表
  - `pages = [{"page_no": 1, "text": "...", "tables": "...", "png_bytes": b"..."}, ...]`
- **Chunk 层**：将 pages 切分为 chunks，并添加 `[[META ...]]` 头
- **Embedding 层**：对每个 chunk 调用嵌入服务（vLLM/Ollama）生成向量

**4. MinIO 对象 Key 约定**
- 每个文档创建独立目录：`{app_id}/{源文件名}_{uuid}/`
- OCR 产物固定子目录：`ocr/img/`、`ocr/text/`、`ocr/tab/`、`ocr/log/`
- 非 OCR 文档复用此结构（text/ 子目录存页面文本，img/ 存页面截图）

**5. RLS 多租户隔离**
- 每个请求传入 `RlsContext(app_id, clearance, request_id)`
- PostgreSQL 表启用 RLS，自动过滤不同 app_id 和 clearance 的数据
- 管理员可通过 admin 角色绕过 RLS

**6. Chunk Metadata 标记**
- 每个 chunk 以 `[[META type=... page=... caption=...]]` 开头
- 用于：按页聚合、来源标注、人工校准时定位

---

## 7. Features / Tools

### 功能模块详细设计

#### **功能 1：多格式文档解析**

**目的**：将各类格式文档统一转为结构化的 pages 列表

**支持格式**
- ✅ PDF：PyMuPDF 文本层 + OCR/VLM（可选）混合解析
- ✅ TXT：直接读取文本，按行/段落虚拟分页
- ✅ 图片 (JPG/PNG)：OCR 提取文字 + VLM 理解（可选）
- ✅ Word (DOCX)：按标题层级和字符数软硬分页
- ✅ Excel (XLSX)：按 Sheet 和行数分页，表格转文本

**核心操作（当前函数名）**
- `extract_pdf_multimodal_rag(pdf_path, use_ocr, use_vlm, ocr_lang)`
- `parse_txt_to_pages(file_path)`
- `parse_image_to_pages(file_path, use_ocr, use_vlm, ocr_lang)`
- `parse_docx_to_pages(file_path, soft_limit_chars, hard_limit_chars, ...)`
- `parse_xlsx_to_pages(file_path)`

**输出格式**
```python
pages = [
    {
        "page_no": 1,
        "text": "页面主要文本内容...",
        "tables": "[Table 1]\n表格文本...\n[Table 2]\n...",
        "png_bytes": b"\x89PNG..." or None
    },
    ...
]
```

---

#### **功能 2：Chunk 分块与元数据标记**

**目的**：将长文本切分为适合检索的小块，并添加元数据

**模式（当前实现）**
- **Normal Chunk**：对整本文本做滑窗切分（chunk_size=2000, overlap=200）
- **Page+Blocks Chunk（API 中称 advanced chunk）**：按页递归切分文本；表格/图形整块入库（来自 pages 中的 tables/figures）

**元数据标记**
```
[[META type=text page=5 caption=""]]
这是第 5 页的普通文本内容...

[[META type=table page=12 caption="Table 3: Sales Summary"]]
Table 3: Sales Summary
| Region | Q1 | Q2 |
...
```

**关键函数**
- `build_chunks_with_meta1(pages, chunk_size, overlap)` → List[str]
- `sliding_chunk_text(text, size, overlap)` → List[str]

---

#### **功能 3：向量检索与 Rerank**

**目的**：根据用户查询召回最相关的文档片段

**流程**
1. **向量检索**：用户查询 → embed_text() → pgvector 相似度检索 → 召回 top 10
2. **预合并**：将同页/同 caption 的 chunk 合并（避免碎片化）
3. **Rerank**：调用外部 Rerank 服务对候选重新排序 → 选 top 3
4. **上下文组装**：合并最终 chunks 为 pdf_context 字符串

**关键函数**
- `pg_store.search_chunks(ctx, query_embed, top_k=10)`
- `merge_hits_by_page_or_caption(hits, max_chars=9000)`
- `rerank_hits(query_text, hits, top_k=3)`

**配置参数**
- `PDF_TOP_K_CHUNKS`: 最终返回的 chunk 数量
- `RERANK_CANDIDATES`: Rerank 前的候选数量
- `PRE_RERANK_MERGE_MAX_CHARS`: 合并后单个 chunk 最大长度

---

#### **功能 4：LLM 对话生成**

**目的**：基于检索到的上下文，用 LLM 生成自然语言答案

**模型**：默认 Qwen3-VL-8B-Instruct（vLLM 部署）；可切换 OpenAI Responses（如 GPT-5）

**Prompt 结构**
```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是一个严谨、实用的多模态助手..."
    },
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "以下是从PDF中检索到的相关上下文...\n\n[上下文]"},
        {"type": "text", "text": "请用阿拉伯语回答"},  // 可选
        {"type": "text", "text": "用户问题：第三方责任险的免赔额是多少？"}
      ]
    }
  ]
}
```

**语言自动检测**
- 查询包含阿拉伯语字符 → 添加"请用阿拉伯语回答"
- 查询包含拉丁字母 → 添加"请用英语回答"

**关键函数**
- `call_vllm_chat(messages, temperature=0.3, max_tokens=3072)`
- `build_user_content(text, pdf_context, lang_instruction)`

---

#### **功能 5：数据管理与校准**

**目的**：让管理员能修正 OCR 错误、编辑 Chunk 文本、重算向量

**界面 (datamng_gr.py)**
- **输入**：app_id、doc_dir、page_no
- **显示**：
  - 页面 PNG 预览
  - OCR 文本、表格、日志
  - 该页所有 Chunks（从 PG 匹配 doc_dir 并过滤 page_no）
- **操作**：
  - 选择某个 Chunk
  - 编辑 chunk_text
  - 点击"保存" / "保存并重算向量"

**核心函数**
- `load_ocr_page_assets(app_id, doc_dir, page_no)` → OCR 产物
- `load_page_chunks_for_review(app_id, clearance, doc_dir, page_no)` → Chunks
- `save_reviewed_chunk(app_id, clearance, chunk_id, new_text, reembed=True)`

**重算向量逻辑**
- 如果 `reembed=True`，调用 `embed_text(new_text)` 生成新向量
- 更新 `chunks.chunk_text` 和 `chunks.embedding`

---

#### **功能 6：对象存储管理**

**MinIO 目录规则**
```
<bucket>/
  <app_id>/
    <doc_dir>/
      source/
        <doc_dir>.pdf  # 原文件
      ocr/
        img/     <doc_dir>_page1.png, page2.png, ...
        text/    <doc_dir>_page1.txt, page2.txt, ...
        tab/     <doc_dir>_page1table.txt, ...
        figure/  <doc_dir>_page1figure.txt, ...
        log/     <doc_dir>_page1log.txt, ...
      text/
        <doc_dir>.txt  # 整本文本（拼接所有页）
```

**关键操作**
- `obj_store.upload_file(object_key, local_path)`
- `obj_store.get_bytes(object_key)` → PNG 二进制
- `obj_store.get_text(object_key)` → UTF-8 字符串
- `obj_store.get_uri(object_key)` → 公网 URL（如 `http://minio:9000/rag-files/...`）

**对象 Key 计算**
```python
def build_ocr_object_keys(app_id, doc_dir, page_no):
    base = f"{app_id}/{doc_dir}/ocr"
    file_base = f"{doc_dir}_page{page_no}"
    return {
        "img": f"{base}/img/{file_base}.png",
        "text": f"{base}/text/{file_base}.txt",
        "tab": f"{base}/tab/{file_base}table.txt",
        "log": f"{base}/log/{file_base}log.txt",
    }
```

---

## 8. Technology Stack

### 后端技术

| 组件 | 技术选型 | 版本 / 备注 |
|------|---------|-------------|
| **LLM** | Qwen3-VL-8B-Instruct | vLLM 部署，OpenAI 兼容 API；可切 OpenAI Responses |
| **Embedding** | qwen3-embedding | vLLM 或 Ollama 部署；维度由 `config.EMBED_DIM` 决定 |
| **Rerank** | 可配置模型 | 外部 HTTP Rerank 服务 |
| **OCR** | PaddleOCR（服务端） | 自建 HTTP 服务，应用侧通过 `OCR_ENDPOINT` 调用 |
| **VLM** | vLLM / OpenAI | 可选，与 OCR 并行或组合使用 |
| **向量数据库** | PostgreSQL 15+ + pgvector | 维度以数据库 schema 为准 |
| **对象存储** | MinIO | S3 兼容 API |
| **应用框架** | FastAPI | 已实现接口 + Redis 会话/任务状态 |
| **缓存/会话** | Redis | API 会话与入库任务状态 |
| **认证** | LDAP + JWT | API 层基础登录与会话 |
| **UI 框架** | Gradio 6.2.0 | 快速原型搭建 |
| **PDF 解析** | PyMuPDF (fitz) | 文本层提取 |
| **Word 解析** | python-docx (openpyxl) | DOCX/XLSX 解析 |

### 依赖库

**核心依赖**（requirements.txt 节选）
```
fastapi==0.128.0
gradio==6.2.0
psycopg==3.3.2          # PostgreSQL 驱动
minio==7.2.20           # MinIO 客户端
PyMuPDF==1.26.7         # PDF 解析
paddleocr==3.3.2        # OCR
paddlepaddle==3.2.2     # OCR 后端
openpyxl==3.1.5         # Excel 解析
requests==2.32.5        # HTTP 客户端
```

### 部署架构

**单机 Docker Compose 部署**
```yaml
services:
  minio:           # 对象存储
  postgres:        # 向量数据库
  vllm:            # LLM 推理服务
  rerank:          # Rerank 服务
  ocr:             # OCR 服务（CPU 版）
  # ocr-gpu:       # OCR 服务（GPU 版，可选）
  gradio-ui:       # 前端界面（规划）
```

**硬件加速**
- **CPU**：所有组件均可运行（性能较慢）
- **CUDA GPU**：推荐用于 vLLM、Rerank、OCR（需 NVIDIA 驱动 + nvidia-docker）

---

## 9. Database Schema & Architecture

### 数据库设计概览

系统采用 **PostgreSQL 15+ + pgvector** 作为向量数据库，设计遵循企业级安全标准，包含：
- **核心数据表**：docs（文档）、doc_versions（版本）、chunks（向量块）
- **安全机制**：RLS（行级安全）+ 多租户隔离 + 密级控制
- **审计机制**：变更日志 + 检索行为追踪

### 核心表结构

#### **1. docs 表（文档主表）**

存储文档元数据，每个文档有唯一的 `doc_id`。

```sql
CREATE TABLE docs (
  doc_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  app_id        text NOT NULL,              -- 应用ID（多租户隔离）
  title         text,                        -- 文档标题（原文件名）
  source_uri    text,                        -- 原始文件路径/MinIO对象Key
  classification smallint NOT NULL DEFAULT 0, -- 密级：0=公开, 1=内部, 2=敏感, 3=机密
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text NOT NULL DEFAULT current_user
);

-- 索引
CREATE INDEX idx_docs_app_id ON docs(app_id);
CREATE INDEX idx_docs_classification ON docs(classification);
```

**字段说明**
- `app_id`：租户标识，如 "sales"、"finance"、"legal"
- `classification`：文档密级，用于细粒度权限控制
- `source_uri`：MinIO 对象存储路径，如 `http://minio:9000/rag-files/appA/contract_abc123/source/contract.pdf`

**示例数据**
```sql
INSERT INTO docs (app_id, title, source_uri, classification)
VALUES ('legal', '供应商合作协议.pdf', 'appA/contract_abc123/source/contract.pdf', 2);
```

---

#### **2. doc_versions 表（文档版本）**

支持文档的多次解析和向量化，每次生成新版本。

```sql
CREATE TABLE doc_versions (
  version_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id        uuid NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
  version_no    int  NOT NULL,               -- 版本号（从 1 开始递增）
  content_hash  text NOT NULL,               -- 内容 SHA256 哈希（用于去重）
  parser_ver    text,                        -- 解析器版本，如 "pymupdf+ocr+vlm"
  embed_model   text NOT NULL,               -- 向量模型，如 "qwen3-embedding:8b"
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text NOT NULL DEFAULT current_user,
  UNIQUE(doc_id, version_no)
);

-- 索引
CREATE INDEX idx_versions_doc_id ON doc_versions(doc_id);
```

**字段说明**
- `version_no`：递增版本号，支持文档重新解析和对比
- `content_hash`：SHA256 哈希，避免重复入库
- `parser_ver`：记录使用的解析器（如 "pymupdf+ocr+vlm" 或 "txt" 或 "docx"）
- `embed_model`：记录向量模型，方便后续模型升级时批量重算

**示例数据**
```sql
INSERT INTO doc_versions (doc_id, version_no, content_hash, parser_ver, embed_model)
VALUES (
  '123e4567-e89b-12d3-a456-426614174000',
  1,
  'a3b2c1d4e5f6...',
  'pymupdf+ocr',
  'qwen3-embedding:8b'
);
```

---

#### **3. chunks 表（向量块）**

存储文档切分后的 Chunk 及其向量，是检索的核心表。

```sql
CREATE TABLE chunks (
  chunk_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id        uuid NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
  version_id    uuid NOT NULL REFERENCES doc_versions(version_id) ON DELETE CASCADE,
  
  app_id        text NOT NULL,              -- 冗余 app_id（加速 RLS 过滤）
  classification smallint NOT NULL DEFAULT 0, -- 冗余密级
  
  chunk_index   int NOT NULL,               -- Chunk 在文档中的序号
  chunk_text    text NOT NULL,              -- Chunk 文本内容（含 [[META ...]] 头）
  chunk_hash    text NOT NULL,              -- Chunk 内容哈希
  
  embedding     vector(4096),               -- 向量（当前初始化脚本为 4096；需与 EMBED_DIM 对齐）
  created_at    timestamptz NOT NULL DEFAULT now(),
  embedding_b   bit                         -- 预留：二值化向量（未使用）
);

-- 索引
CREATE INDEX idx_chunks_app_class ON chunks(app_id, classification);
CREATE INDEX idx_chunks_doc_ver ON chunks(doc_id, version_id);

-- 向量索引（生产环境建议使用 HNSW 或 IVFFlat）
-- CREATE INDEX idx_chunks_embedding ON chunks USING ivfflat (embedding vector_cosine_ops);
```

**字段说明**
- `chunk_index`：Chunk 在文档中的顺序（0-based）
- `chunk_text`：包含元数据的完整文本，如：
  ```
  [[META type=text page=5 caption=""]]
  这是第 5 页的文本内容...
  ```
- `embedding`：向量维度需与 `config.EMBED_DIM` 一致；当前初始化脚本为 4096，需要与配置对齐
- 冗余的 `app_id` 和 `classification`：避免 JOIN docs 表，加速 RLS 过滤

**示例数据**
```sql
INSERT INTO chunks (doc_id, version_id, app_id, classification, chunk_index, chunk_text, chunk_hash, embedding)
VALUES (
  '123e4567-e89b-12d3-a456-426614174000',
  '789e4567-e89b-12d3-a456-426614174111',
  'legal',
  2,
  0,
  '[[META type=text page=1 caption=""]]合同第一条：双方责任...',
  'b4c3d2e1f0a9...',
  '[0.123, -0.456, 0.789, ...]'::vector(4096)
);
```

---

#### **4. audit_mutations 表（变更审计）**

记录所有表的 INSERT/UPDATE/DELETE 操作，用于合规审计和数据恢复。

```sql
CREATE TABLE audit_mutations (
  audit_id      bigserial PRIMARY KEY,
  ts            timestamptz NOT NULL DEFAULT now(),
  actor         text NOT NULL DEFAULT current_user,
  action        text NOT NULL,         -- INSERT/UPDATE/DELETE
  table_name    text NOT NULL,
  row_pk        text,                  -- 被操作行的主键值
  request_id    text,                  -- 应用层传入的请求ID（用于关联）
  old_data      jsonb,                 -- 变更前的数据（JSON格式）
  new_data      jsonb                  -- 变更后的数据（JSON格式）
);

-- 索引
CREATE INDEX idx_audit_mutations_ts ON audit_mutations(ts);
CREATE INDEX idx_audit_mutations_actor ON audit_mutations(actor);
CREATE INDEX idx_audit_mutations_request_id ON audit_mutations(request_id);
```

**触发器自动记录**
```sql
-- 为 docs、doc_versions、chunks 表自动记录变更
CREATE TRIGGER trg_audit_docs
AFTER INSERT OR UPDATE OR DELETE ON docs
FOR EACH ROW EXECUTE FUNCTION audit_row_change('doc_id');
```

**示例审计记录**
```json
{
  "audit_id": 1001,
  "ts": "2026-01-16T14:30:00Z",
  "actor": "rag_writer",
  "action": "UPDATE",
  "table_name": "chunks",
  "row_pk": "123e4567-e89b-12d3-a456-426614174000",
  "request_id": "req_abc123",
  "old_data": {"chunk_text": "原始文本..."},
  "new_data": {"chunk_text": "修正后的文本..."}
}
```

---

#### **5. audit_search 表（检索审计）**

记录所有检索行为，用于分析用户查询模式和数据访问审计。

```sql
CREATE TABLE audit_search (
  search_id     bigserial PRIMARY KEY,
  ts            timestamptz NOT NULL DEFAULT now(),
  actor         text NOT NULL DEFAULT current_user,
  app_id        text NOT NULL,
  clearance     int  NOT NULL,
  request_id    text,
  query_text    text,                  -- 查询文本（可按需脱敏）
  top_k         int NOT NULL,
  filters       jsonb,                 -- 过滤条件（JSON格式）
  hit_chunk_ids uuid[],                -- 命中的 Chunk ID 列表
  hit_doc_ids   uuid[],                -- 命中的文档 ID 列表
  score_min     real,                  -- 最低相似度分数
  score_max     real                   -- 最高相似度分数
);

-- 索引
CREATE INDEX idx_audit_search_ts ON audit_search(ts);
CREATE INDEX idx_audit_search_app_id ON audit_search(app_id);
CREATE INDEX idx_audit_search_actor ON audit_search(actor);
```

**示例检索记录**
```json
{
  "search_id": 2001,
  "ts": "2026-01-16T15:00:00Z",
  "actor": "user_alice",
  "app_id": "legal",
  "clearance": 2,
  "query_text": "第三方责任险的免赔额是多少",
  "top_k": 3,
  "hit_chunk_ids": ["uuid1", "uuid2", "uuid3"],
  "hit_doc_ids": ["doc_uuid1"],
  "score_min": 0.72,
  "score_max": 0.89
}
```

---

### Row Level Security (RLS) 多租户隔离

所有核心表启用 PostgreSQL RLS，根据会话变量自动过滤数据。

#### **RLS 策略**

```sql
-- 启用 RLS
ALTER TABLE docs ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

-- docs 表策略：只能访问自己 app_id 且密级不超过 clearance 的文档
CREATE POLICY docs_rls ON docs
USING (
  app_id = current_setting('app.current_app', true)
  AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
);

CREATE POLICY versions_rls ON doc_versions
USING (
  EXISTS (
    SELECT 1 FROM docs d
    WHERE d.doc_id = doc_versions.doc_id
      AND d.app_id = current_setting('app.current_app', true)
      AND d.classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  )
);

-- chunks 表策略：同样基于 app_id 和 clearance
CREATE POLICY chunks_rls ON chunks
USING (
  app_id = current_setting('app.current_app', true)
  AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
);
```

#### **会话变量设置**

每次查询前，应用层必须设置会话变量：

```python
# Python 代码示例
ctx = RlsContext(app_id="legal", clearance=2, request_id=str(uuid.uuid4()))

# 在数据库连接上设置
cursor.execute("SET app.current_app = %s", (ctx.app_id,))
cursor.execute("SET app.clearance = %s", (ctx.clearance,))
cursor.execute("SET app.request_id = %s", (ctx.request_id,))

# 后续查询自动受 RLS 策略限制
cursor.execute("SELECT * FROM chunks WHERE ...")
```

**隔离效果**
- 销售部门（app_id="sales"）看不到财务部门（app_id="finance"）的数据
- clearance=1 的用户看不到 classification=2 的敏感文档
- 所有隔离在数据库层自动执行，应用层无需额外过滤

---

### 数据库角色与权限

系统定义两个数据库角色，遵循最小权限原则。

#### **角色定义**

```sql
-- 1. rag_reader：只读角色（用于检索）
CREATE ROLE rag_reader LOGIN PASSWORD 'CHANGE_ME_READER';
GRANT SELECT ON docs, doc_versions, chunks TO rag_reader;
GRANT INSERT ON audit_search TO rag_reader;

-- 2. rag_writer：读写角色（用于入库和数据管理）
CREATE ROLE rag_writer LOGIN PASSWORD 'CHANGE_ME_WRITER';
GRANT SELECT, INSERT, UPDATE ON docs, doc_versions, chunks TO rag_writer;
GRANT INSERT ON audit_search, audit_mutations TO rag_writer;
```

#### **权限矩阵**

| 操作 | rag_reader | rag_writer | 说明 |
|------|------------|------------|------|
| **SELECT** docs/chunks | ✅ | ✅ | 检索和查询 |
| **INSERT** chunks | ❌ | ✅ | 文档入库 |
| **UPDATE** chunks | ❌ | ✅ | Chunk 校准 |
| **DELETE** chunks | ❌ | ❌ | 仅管理员可删除 |
| **INSERT** audit_search | ✅ | ✅ | 记录检索日志 |
| **INSERT** audit_mutations | ❌ | ✅ | 记录变更日志 |

**应用层使用**
```python
# 查询场景：使用 rag_reader
pg_config = PgConfig(
    user="rag_reader",
    password="CHANGE_ME_READER",
    ...
)

# 入库场景：使用 rag_writer
pg_config = PgConfig(
    user="rag_writer",
    password="CHANGE_ME_WRITER",
    ...
)
```

---

### 向量索引优化

#### **当前配置（开发环境）**

```sql
-- 基础索引（无向量索引）
CREATE INDEX idx_chunks_app_class ON chunks(app_id, classification);
CREATE INDEX idx_chunks_doc_ver ON chunks(doc_id, version_id);
```

适用场景：小规模数据（< 100万 Chunks），响应时间 < 1 秒

#### **生产环境推荐**

```sql
-- IVFFlat 索引（适合中等规模，100万 - 1000万 Chunks）
CREATE INDEX idx_chunks_embedding ON chunks 
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);

-- HNSW 索引（适合大规模，> 1000万 Chunks）
CREATE INDEX idx_chunks_embedding ON chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

#### **索引选择建议**

| 数据规模 | 索引类型 | 参数建议 | 查询性能 | 索引大小 |
|----------|----------|----------|----------|----------|
| < 10万 | 无索引 | - | 0.5 秒 | 0 |
| 10万 - 100万 | IVFFlat | lists=100 | 0.1 秒 | 中等 |
| 100万 - 1000万 | IVFFlat | lists=1000 | 0.05 秒 | 大 |
| > 1000万 | HNSW | m=16, ef=64 | 0.01 秒 | 极大 |

---

### 数据库扩展

系统依赖两个 PostgreSQL 扩展：

```sql
-- pgvector：向量数据类型和相似度计算
CREATE EXTENSION IF NOT EXISTS vector;

-- pgcrypto：UUID 生成（gen_random_uuid）
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

**pgvector 支持的距离度量**
- `vector_cosine_ops`：余弦相似度（当前使用）
- `vector_l2_ops`：欧几里得距离
- `vector_ip_ops`：内积

---

### ER 图（实体关系）

```
┌──────────────┐
│     docs     │ 1      ∞ ┌──────────────┐
│              │──────────│ doc_versions │
│ • doc_id (PK)│          │              │
│ • app_id     │          │ • version_id │
│ • title      │          │ • doc_id (FK)│
│ • source_uri │          │ • version_no │
│ • class.     │          │ • parser_ver │
└──────┬───────┘          │ • embed_model│
       │                  └──────┬───────┘
       │ 1                       │
       │                         │ 1
       │                         │
       │ ∞                       │ ∞
┌──────▼───────────────────────▼──────┐
│            chunks                   │
│                                     │
│ • chunk_id (PK)                     │
│ • doc_id (FK)                       │
│ • version_id (FK)                   │
│ • app_id (冗余)                      │
│ • classification (冗余)              │
│ • chunk_index                       │
│ • chunk_text                        │
│ • embedding vector(4096)            │
└─────────────────────────────────────┘

        ┌──────────────┐
        │audit_mutations│
        │              │
        │• 记录所有表的  │
        │  变更历史     │
        └──────────────┘

        ┌──────────────┐
        │audit_search  │
        │              │
        │• 记录所有检索  │
        │  行为日志     │
        └──────────────┘
```

---

### 数据流示例

#### **文档入库流程**

```sql
-- 1. 插入文档元数据
INSERT INTO docs (app_id, title, source_uri, classification)
VALUES ('legal', '合同.pdf', 'minio://...', 2)
RETURNING doc_id;  -- 返回 doc_id

-- 2. 创建版本记录
INSERT INTO doc_versions (doc_id, version_no, content_hash, parser_ver, embed_model)
VALUES (doc_id, 1, 'sha256...', 'pymupdf+ocr', 'qwen3-embedding:8b')
RETURNING version_id;

-- 3. 批量插入 Chunks
INSERT INTO chunks (doc_id, version_id, app_id, classification, chunk_index, chunk_text, chunk_hash, embedding)
VALUES
  (doc_id, version_id, 'legal', 2, 0, '[[META...]]文本1', 'hash1', vector1),
  (doc_id, version_id, 'legal', 2, 1, '[[META...]]文本2', 'hash2', vector2),
  ...;
```

#### **向量检索流程**

```sql
-- 1. 设置 RLS 上下文
SET app.current_app = 'legal';
SET app.clearance = '2';

-- 2. 向量检索（自动受 RLS 限制）
SELECT 
  chunk_id,
  doc_id,
  chunk_text,
  1 - (embedding <=> query_embedding::vector) AS similarity
FROM chunks
WHERE app_id = 'legal'  -- RLS 会自动过滤
ORDER BY embedding <=> query_embedding::vector
LIMIT 10;

-- 3. 记录检索审计
INSERT INTO audit_search (app_id, clearance, query_text, top_k, hit_chunk_ids, score_min, score_max)
VALUES ('legal', 2, '查询文本', 10, ARRAY[chunk_ids], 0.72, 0.89);
```

---

### 数据库初始化

系统提供自动初始化脚本，位于 `relateddocker/pgv-docker/init/` 目录：

**初始化顺序**
1. `001_schema.sql`：创建表结构、RLS 策略、审计触发器
2. `002_roles.sql`：创建数据库角色（rag_reader、rag_writer）

**Docker Compose 自动初始化**
```yaml
# docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg15
    volumes:
      - ./relateddocker/pgv-docker/init:/docker-entrypoint-initdb.d
    environment:
      POSTGRES_DB: rag
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
```

容器首次启动时，会自动执行 `/docker-entrypoint-initdb.d/*.sql` 中的脚本。

**手动初始化**
```bash
# 1. 连接到 PostgreSQL
psql -h localhost -p 5432 -U postgres -d rag

# 2. 执行初始化脚本
\i relateddocker/pgv-docker/init/001_schema.sql
\i relateddocker/pgv-docker/init/002_roles.sql

# 3. 验证
\dt  -- 查看表列表
\du  -- 查看角色列表
```

---

## 10. Security & Configuration

### 安全机制

**1. 多租户隔离 (RLS)**
- PostgreSQL Row Level Security 启用
- 每个请求必须传入 `app_id` 和 `clearance`
- 数据库自动过滤不同租户的数据
- 示例：
  ```sql
  CREATE POLICY app_isolation ON docs
  USING (
    app_id = current_setting('app.current_app', true)
    AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );
  ```

**2. 密级控制**
- `clearance` 字段：0=公开, 1=内部, 2=敏感, 3=机密
- 用户只能访问 clearance ≤ 自己权限的文档
- 示例：clearance=1 的用户看不到 clearance=2 的财务文件

**3. API 鉴权（已实现基础版）**
- vLLM 接口：当前未鉴权（内网部署）
- FastAPI：已提供 LDAP 登录 + JWT 会话接口（角色/权限细化待完善）
- MinIO：Access Key + Secret Key

### 配置管理

**环境变量（config.py）**
```python
# PostgreSQL
PG_HOST = "10.55.223.100"
PG_PORT = 5432
PG_USER = "rag_writer"
PG_PASSWORD = "CHANGE_ME_WRITER"

# MinIO
MINIO_ENDPOINT = "localhost:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "Admin123!"
MINIO_BUCKET = "rag-files"

# vLLM
VLLM_BASE_URL = "http://localhost:8000"
MODEL_PATH = "/models/Qwen3-VL-8B-Instruct"

# Embedding (Ollama/vLLM)
EMBED_BASE_URL = "http://10.55.223.100:11434"
EMBED_MODEL = "qwen3-embedding:8b"
EMBED_DIM = 1536

# Rerank
RERANK_BASE_URL = "http://localhost:18010"
RERANK_ENABLED = True

# OCR
OCR_ENDPOINT = "http://127.0.0.1:18000"
OCR_MAX_WORKER = 6

# RLS 默认值
RAG_APP_ID = "appA"
RAG_CLEARANCE = 2
```

**安全最佳实践**
- 生产环境使用强密码和 SSL 连接
- MinIO 和 PostgreSQL 不暴露到公网
- API 网关层添加鉴权和限流
- 定期备份 PostgreSQL 和 MinIO 数据

---

## 11. API Specification (当前实现)

### 核心端点（api_server.py）

**1. 文档上传与解析**
```http
POST /v1/ingest/file
Content-Type: multipart/form-data

Parameters:
  file: (binary)
  parse_modes: string[]   # 例如 ["OCR", "VLM"]
  chunk_mode: string      # "normal chunk" 或 "advanced chunk"
  ocr_lang: string        # "ar" / "en" / "ch"
  rag_app_id: string
  rag_clearance: integer
  callback_url: string    # 可选：处理完成后回调
```

**2. 入库任务状态**
```http
GET /v1/ingest/status?job_id=...
GET /v1/ingest/status/{task_id}
```

**3. 查询问答**
```http
POST /v1/chat/send
Content-Type: application/json

Body:
{
  "session_id": "uuid",
  "text": "第三方责任险的免赔额是多少？",
  "rag_app_id": "legal",
  "rag_clearance": 2
}
```

**4. OCR/资产与校对**
```http
GET /v1/admin/page_assets?rag_app_id=...&doc_dir=...&page_no=...
GET /v1/assets/page_image?rag_app_id=...&doc_dir=...&page_no=...
GET /v1/admin/page_review?rag_app_id=...&doc_id=...&page_no=...
POST /v1/admin/chunk/update
GET /v1/admin/docs/find?rag_app_id=...&doc_dir=...
```

**5. 认证**
```http
POST /api/auth/ldap
GET  /api/auth/me
POST /api/auth/logout
```

---

## 12. Success Criteria

### MVP 成功定义
一个功能完整、可演示的 RAG 原型系统，能够：
1. 支持 5 种格式文档上传（PDF、TXT、图片、Word、Excel）
2. 自动完成解析、分块、向量化、入库全流程
3. 提供准确率 > 80% 的智能问答（基于测试集）
4. 用户能通过 Gradio 界面完成所有核心操作
5. 部署到单台服务器或开发机器（8 核 CPU + 24GB GPU）

### 功能需求 (必须完成)

#### ✅ 文档处理
- ✅ PDF：支持纯文本和图像混合页面
- ✅ TXT：逐行/段落分页，自动识别编码
- ✅ 图片：JPG/PNG OCR 提取
- ✅ Word：按标题层级和字符数分页
- ✅ Excel：按 Sheet 和行数分页

#### ✅ 向量检索
- ✅ 基于 pgvector 的相似度检索
- ✅ 支持 app_id 和 clearance 过滤
- ✅ 返回 top-k chunks 及元数据

#### ✅ Rerank 重排序
- ✅ Rerank 服务可用（模型可配置）
- ✅ 能提升检索准确率 10%+（对比基线）

#### ✅ 对话生成
- ✅ 基于检索上下文生成答案
- ✅ 标注引用来源（页码、文档名）
- ✅ 支持多语言（中文、英文、阿拉伯语）

#### ✅ 数据管理
- ✅ 可视化 OCR 结果（PNG + 文本）
- ✅ 人工编辑 Chunk 并保存
- ✅ 支持重算向量

### 质量指标

| 指标 | 目标值 | 测量方法 |
|------|--------|----------|
| **文档解析准确率** | > 95% | 人工抽查 50 个文档，对比 OCR 结果与真实内容 |
| **检索召回率 (Recall@10)** | > 85% | 测试集 100 个问题，检查 top 10 是否包含正确答案 |
| **检索精准率 (Precision@3)** | > 80% | 测试集 100 个问题，top 3 中正确答案比例 |
| **端到端响应时间** | < 5 秒 | 从查询到返回答案（不含 OCR 解析时间） |
| **文档入库吞吐量** | > 10 页/分钟 | 单机环境，混合 OCR 和文本层 |

### 用户体验目标
- 新用户能在 10 分钟内完成首次文档上传和查询
- 数据管理界面直观易用，无需培训即可修正 Chunk
- 查询结果提供清晰的引用来源（页码 + 预览图）

---

## 13. Implementation Phases

### Phase 1: 核心流程打通（已完成 ✅）
**目标**：完成 PDF → 入库 → 检索 → 对话的端到端流程

**交付物**
- ✅ core.py 核心逻辑模块
- ✅ PostgreSQL + pgvector 数据库搭建
- ✅ MinIO 对象存储配置
- ✅ vLLM 部署 Qwen3-VL-8B
- ✅ PDF 解析（PyMuPDF + OCR）
- ✅ Chunk 分块与向量化
- ✅ 基本的 Gradio 查询界面（nexora_gr.py）

**验证标准**
- 能上传 PDF，自动解析并入库
- 能提问并得到基于 PDF 内容的答案
- 向量检索能召回相关段落

**时间线**：✅ 已完成（2025 年 12 月 - 2026 年 1 月上旬）

---

### Phase 2: 多格式支持与数据管理（进行中 🚧）
**目标**：扩展支持 TXT、图片、Word、Excel，增加数据校准界面

**交付物**
- ✅ TXT 文件入库（txtfunc.py）
- ✅ 图片文件入库（imgfunc.py）
- ✅ Word 文件入库（docxfunc.py）
- ✅ Excel 文件入库（xlsxfunc.py）
- ✅ 统一入库接口 `ingest_file()` + `ingest_pages_common()`
- ✅ 数据管理界面（datamng_gr.py）
  - ✅ 加载 OCR 产物（PNG + 文本）
  - ✅ 按 doc_dir + page_no 过滤 Chunks
  - ✅ 人工编辑 Chunk 并重算向量
- ✅ 文件导入界面（gradio_ui.py）
- ✅ 用户查询界面（user_query_ui.py）

**验证标准**
- 能识别 5 种格式文件并自动路由到对应解析器
- 数据管理界面能加载任意页面并编辑 Chunk
- 非 PDF 文档的查询结果能显示来源页

**时间线**：🚧 进行中（2026 年 1 月中旬），预计 1 月底完成

---

### Phase 3: Rerank 与检索优化（部分完成 ⏳）
**目标**：提升检索精准度，优化用户体验

**交付物**
- ✅ Rerank 服务部署（rerank_client.py）
- ✅ 预合并同页 Chunk 逻辑（merge_hits_by_page_or_caption）
- ⏳ Rerank 参数调优（RERANK_CANDIDATES、PRE_RERANK_MERGE_MAX_CHARS）
- ⏳ 检索性能测试（Recall、Precision、F1）
- ⏳ 缓存机制（Redis，可选）

**验证标准**
- Rerank 后 Precision@3 提升 10% 以上
- 端到端响应时间 < 5 秒
- 用户查询界面显示引用页 PNG 预览

**时间线**：⏳ 部分完成，预计 2 月上旬完成优化

---

### Phase 4: API 服务与工程化（部分完成 ⏳）
**目标**：提供 RESTful API，支持外部系统集成

**交付物**
- ✅ FastAPI 服务（api_server.py）已实现核心接口
  - POST /v1/ingest/file（文档上传）
  - GET /v1/ingest/status（任务状态）
  - POST /v1/chat/send（查询问答）
  - GET /v1/admin/docs/find（文档查找）
  - POST /v1/admin/chunk/update（Chunk 校准）
- ✅ API 鉴权（LDAP + JWT）
- 📋 Docker Compose 一键部署脚本
- 📋 部署文档和用户手册
- 📋 日志和监控（Prometheus + Grafana，可选）

**验证标准**
- API 文档完整（Swagger UI）
- 所有服务能通过 `docker-compose up -d` 一键启动
- 外部系统能通过 HTTP API 调用核心功能

**时间线**：⏳ 持续优化中

---

## 14. Future Considerations (Post-MVP)

### 高级功能

**1. 前端重构**
- 使用 Next.js + React 替代 Gradio
- 更美观的 UI 设计和用户体验
- 支持响应式布局（移动端适配）

**2. 文档版本管理**
- 支持文档更新和版本对比
- 显示修改历史和 Diff
- 回滚到历史版本

**3. 批量导入与任务队列**
- 支持上传文件夹或 ZIP 包
- 后台任务队列（Celery + Redis）
- 进度条和状态通知

**4. 高级检索**
- Hybrid Search（向量 + 全文搜索）
- Filter by metadata（作者、日期、标签）
- 多轮对话上下文管理

**5. 知识图谱可视化**
- 显示文档间的引用关系
- 实体识别和关系抽取
- 交互式图谱浏览

### 集成与扩展

**1. 企业系统集成**
- SSO 单点登录（OAuth2、SAML）
- 与 Confluence、SharePoint、飞书云文档集成
- Webhook 通知（文档更新、查询日志）

**2. 多模型支持**
- 支持切换不同 LLM（GPT-4、Claude、Llama）
- 自定义 Embedding 模型
- A/B 测试不同模型效果

**3. 开发者生态**
- Python SDK / JavaScript SDK
- Slack / Teams Bot 集成
- 插件市场（自定义 Parser、Chunker）

### 运维与监控

**1. Kubernetes 部署**
- Helm Chart 打包
- 自动扩缩容（HPA）
- 多集群管理

**2. 可观测性**
- 日志聚合（ELK / Loki）
- 指标监控（Prometheus + Grafana）
- 链路追踪（Jaeger / Zipkin）

**3. 数据治理**
- 自动备份和恢复
- 数据脱敏和匿名化
- 审计日志（操作记录）

---

## 15. Risks & Mitigations

### 风险 1: OCR 准确率不足
**影响**：扫描件或低质量 PDF 解析错误，导致检索和问答质量下降  
**概率**：高  
**缓解措施**：
- 提供数据管理界面，支持人工修正 OCR 结果
- 优先使用 PDF 文本层（PyMuPDF），仅对图像页使用 OCR
- 引入 VLM 作为补充，提高图像理解能力
- 收集高频错误案例，微调 OCR 模型（长期）

---

### 风险 2: 向量检索召回不准确
**影响**：用户查询无法找到正确答案，影响用户体验  
**概率**：中  
**缓解措施**：
- 引入 Rerank 重排序，提升 Precision@3
- 优化 Chunk 策略（检测表格/图形，保持语义完整性）
- 调优 pgvector 参数（索引类型、距离度量）
- 使用更强的 Embedding 模型（Qwen3-Embedding-8B）
- 收集 Bad Case，持续迭代

---

### 风险 3: GPU 资源不足
**影响**：vLLM、Rerank、OCR 推理速度慢，影响用户体验  
**概率**：中  
**缓解措施**：
- 优先使用 CPU 版 Rerank 和 OCR（功能优先）
- GPU 版本作为可选升级（性能优化）
- 引入缓存机制（Redis）减少重复计算
- 批量处理文档入库任务（离线）
- 规划云端 GPU 实例（如阿里云、AWS）

---

### 风险 4: 多租户数据泄露
**影响**：不同 app_id 的数据互相可见，严重安全问题  
**概率**：低  
**缓解措施**：
- 强制启用 PostgreSQL RLS
- 每个请求必须传入 app_id 和 clearance
- 定期审计 RLS 策略和权限配置
- 在测试环境验证隔离效果（模拟攻击）
- API 层再次校验权限（深度防御）

---

### 风险 5: 项目依赖过多，版本冲突
**影响**：部署困难，维护成本高  
**概率**：中  
**缓解措施**：
- 使用 Docker 容器化所有服务
- 固定 requirements.txt 版本号
- 定期测试依赖更新（CI/CD）
- 拆分核心依赖和可选依赖（如 GPU 库）
- 提供多种部署方式（Docker Compose / K8s / 本地 venv）

---

## 16. Appendix

### 相关文档
- **技术文档**：`./docs/architecture.md`（待创建）
- **部署指南**：`./docs/deployment.md`（待创建）
- **API 文档**：`http://localhost:19001/docs`（FastAPI Swagger，端口以实际启动为准）
- **用户手册**：`./docs/user_guide.md`（待创建）

### 关键依赖

| 依赖项 | 版本 | 用途 | 链接 |
|--------|------|------|------|
| Qwen3-VL-8B | - | LLM 推理 | [ModelScope](https://modelscope.cn/models/Qwen/Qwen3-VL-8B-Instruct) |
| Qwen3-Embedding | 0.6B / 8B | 向量化 | [ModelScope](https://modelscope.cn/models/Qwen/Qwen3-Embedding-0.6B) |
| PaddleOCR | 3.3.2 | OCR 引擎 | [GitHub](https://github.com/PaddlePaddle/PaddleOCR) |
| pgvector | 0.5+ | PostgreSQL 向量插件 | [GitHub](https://github.com/pgvector/pgvector) |
| MinIO | latest | 对象存储 | [Docker Hub](https://hub.docker.com/r/minio/minio) |
| vLLM | 0.6+ | LLM 推理加速 | [GitHub](https://github.com/vllm-project/vllm) |

### 代码统计

```
项目总计：~5,300 行 Python 代码
- core.py: 712 行（核心逻辑）
- nexora_gr.py: 751 行（旧查询界面，待废弃）
- functions/*: ~2,600 行（解析器、存储、分块等）
- UI 层: ~800 行（gradio_ui、user_query_ui、datamng_gr）
- API 层: api_server.py（已实现核心接口）
```

### 项目仓库结构

```
./
├── README.md
├── requirements.txt
├── config.py
├── core.py
├── gradio_ui.py
├── user_query_ui.py
├── datamng_gr.py
├── api_server.py
├── functions/
│   ├── ocrfunc.py
│   ├── pdffunc.py
│   ├── txtfunc.py
│   ├── imgfunc.py
│   ├── docxfunc.py
│   ├── xlsxfunc.py
│   ├── vlmfunc.py
│   ├── chunkfunc.py
│   ├── docgenfunc.py
│   ├── docgen_schema.py
│   ├── rerank_client.py
│   ├── object_store.py
│   └── rag_pg_store.py
├── relateddocker/
│   ├── minio-docker/
│   ├── pgv-docker/
│   ├── vllm-docker/
│   ├── rerank-docker/
│   ├── ocr/
│   └── ocrgpu/
└── docs/
    ├── PRD.md (本文档)
    ├── architecture.md (待创建)
    ├── deployment.md (待创建)
    └── user_guide.md (待创建)
```

---

## 附录：当前状态总结

### ✅ 已完成
- RAG 主流程（PDF → 解析 → 入库 → 检索 → 对话）
- 用户查询 UI（user_query_ui.py）：面向终端用户的问答界面
- 数据管理 UI（datamng_gr.py）：OCR 校对和 Chunk 编辑
- 多格式支持：PDF、TXT、图片、Word、Excel 解析器均已实现
- MinIO 对象存储约定和目录结构
- PostgreSQL + pgvector 向量检索
- Rerank 重排序集成（外部 HTTP 服务）

### 🚧 进行中
- 多格式文档在查询界面的统一展示优化
- Rerank 参数调优和性能测试
- 非 OCR 文档的"文本快照图"生成（方便预览）

### 📋 待完成
- FastAPI API 服务完善与测试
- Docker Compose 一键部署脚本
- 前端 React/Next.js 重构（长期）
- 文档版本管理
- 批量导入和任务队列

### 下一步建议
1. **短期（1-2 周）**：完成 Phase 2 收尾（多格式文档展示优化、Rerank 调优）
2. **中期（1 个月）**：启动 Phase 4（API 服务、部署文档、用户手册）
3. **长期（3 个月）**：规划前端重构和高级功能（知识图谱、版本管理）

---

**文档版本历史**

| 版本 | 日期 | 修订内容 | 作者 |
|------|------|----------|------|
| 1.0 | 2026-01-16 | 初始版本，基于项目 docx 和代码生成 | Claude |

---

**审阅与批准**

| 角色 | 姓名 | 日期 | 签名 |
|------|------|------|------|
| 产品经理 | - | - | - |
| 技术负责人 | - | - | - |
| 架构师 | - | - | - |

---

*本文档根据项目实际情况动态更新，最新版本请参考项目仓库。*
