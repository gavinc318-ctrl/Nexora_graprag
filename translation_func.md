# 逐页翻译功能设计（vLLM + MinIO + PG）

本文档给出一个可落地的实现思路，用于：
1) 对 MinIO 中指定文档逐页翻译（lang 可选 ar / ch / en）  
2) 翻译结果逐页写入 `/<doc_dir>/ocr/text_trans/`  
3) 合并全文写入 `/<doc_dir>/text_trans/`  
4) 复用同一个 `doc_id` 写入 PG（新增一个翻译版本）  
5) 操作入口放在 `datamng_gr.py`

---

## 1. 功能边界与输入输出

**输入**
- `rag_app_id`、`doc_dir`
- `target_lang`：`ar | ch | en`
- `version_no`（可选，不传则 `latest_version_no + 1`）

**强制翻译内容**
- `ocr/text`（正文）
- `ocr/tab`（表格）
- `ocr/figure`（图表/图形）

**输出**
- MinIO：
  - `/<rag_app_id>/<doc_dir>/ocr/text_trans/<doc_dir>_page{n}.txt`
  - `/<rag_app_id>/<doc_dir>/ocr/table_trans/<doc_dir>_page{n}table.txt`
  - `/<rag_app_id>/<doc_dir>/ocr/figure_trans/<doc_dir>_page{n}figure.txt`
  - `/<rag_app_id>/<doc_dir}/text_trans/<doc_dir>.txt`
- PG：
  - 在 `doc_versions` 新增一条 version（parser_ver 标记为 `translate/<lang>`）
  - 在 `chunks` 写入翻译后的 chunk（**doc_id 复用原文档**）

---

## 2. 数据流与调用路径

### 2.1 逐页翻译流程（核心）
1. **拉取源文本**  
   读取 MinIO `ocr/text` + `ocr/tab` + `ocr/figure` 逐页文本。
2. **逐页调用 vLLM 翻译**  
   使用 vLLM 的 chat completions，固定 system prompt：  
   - 不要改写结构  
   - 保持段落顺序  
   - 不要杜撰  
3. **保存逐页翻译文本**  
   对每页保存到 `ocr/text_trans/`、`ocr/table_trans/`、`ocr/figure_trans/`。
4. **合并全文**  
   拼成全文写入 `text_trans/`。
5. **入库**  
   - 构造 `pages_trans = [{"page_no": n, "text": trans, "tables": table_trans, "figures": figure_trans, "png_bytes": None}]`  
   - 走 `ingest_pages_common` 的逻辑，但需要：
     - **复用 doc_id**（避免新建 docs 记录）  
     - 在 `doc_versions` 新增版本（parser_ver = `translate/<lang>`）  
     - chunks 文本来自翻译后的 pages  

---

## 3. 代码实现建议

### 3.1 新增函数：`functions/translatefunc.py`

职责：只做翻译与存储，不做 UI。

**建议接口**
```
def translate_doc_pages(
    rag_app_id: str,
    doc_dir: str,
    target_lang: str,
) -> Dict[str, Any]:
    ...
```

**内部步骤**
- `load_ocr_page_assets()` 拉 page text/table/figure  
- 按页调用 `call_vllm_chat()`  
- 写 MinIO `ocr/text_trans` & `text_trans`  
- 调用 `pg_store.insert_doc_version_with_chunks()`（建议新增一个方法）

---

## 4. PG 写入策略（复用 doc_id）

当前 `ingest_pages_common()` 会 **新建 doc_id**。  
因此建议新增 PG 方法：

### 4.1 新增 PG API

在 `functions/rag_pg_store.py` 增加：

```
def add_version_and_chunks(
    self,
    ctx: RlsContext,
    doc_id: uuid.UUID,
    version_no: int,
    parser_ver: str,
    embed_model: str,
    chunks: Sequence[Tuple[int, str, Sequence[float]]],
) -> uuid.UUID:
    """仅新增 version + chunks，复用已有 doc_id"""
```

**流程**
- `INSERT INTO doc_versions (...) RETURNING version_id`
- 批量 INSERT chunks（与 ingest_pdf 相同逻辑）

这样翻译版本可以复用同一个 doc_id。

---

## 5. vLLM 翻译 Prompt 建议

```
SYSTEM:
You are a professional translator. Preserve the original structure and order.
Do not add or remove information. Do not summarize.

USER:
Translate the following text into {LANG}. Keep paragraphs and line breaks:
<<<
{PAGE_TEXT}
>>>
```

可选：对表格块保持 Markdown 不变，仅翻译表头/单元格。

---

## 6. MinIO 路径规则

建议复用现有 base：

```
base = f"{rag_app_id}/{doc_dir}/ocr"
page_key = f"{base}/text_trans/{doc_dir}_page{page_no}.txt"
table_key = f"{base}/table_trans/{doc_dir}_page{page_no}table.txt"
figure_key = f"{base}/figure_trans/{doc_dir}_page{page_no}figure.txt"
full_key = f"{rag_app_id}/{doc_dir}/text_trans/{doc_dir}.txt"
```

---

## 7. datamng_gr.py UI 集成

新增一个“逐页翻译”面板：

字段：
- app_id
- doc_dir
- target_lang (ar/ch/en)
- 开始按钮
- 状态输出（每页进度）

调用：
`translate_doc_pages(...)`

UI 需要显示：
- 已翻译页数 / 总页数
- 最后错误（如果失败）

页面查看区改造：
- 在 Text/Table/Figure 右侧并排显示翻译后的结果
- 翻译结果放入折叠面板（Accordion）
- 展示的是 **最近一次写入的翻译版本**（读取 `ocr/*_trans/` 的同页文件，不区分语言）

---

## 8. 风险点与处理

- **翻译成本/延迟**：页数多会慢，可加分页批次或断点续跑  
- **格式破坏**：提示中明确“保留换行与结构”  
- **embedding 维度**：PG schema 目前是 vector(4096)，需与 `config.EMBED_DIM` 一致  
- **重复翻译**：如果已有 `text_trans`，支持覆盖或跳过  

---

## 9. 预期函数交互示意

```
datamng_gr.py (button)
  -> translate_doc_pages()
      -> load_ocr_page_assets()
      -> call_vllm_chat()
      -> upload text_trans (per page)
      -> upload full text_trans
      -> build chunks + embed_text
      -> add_version_and_chunks()
```
