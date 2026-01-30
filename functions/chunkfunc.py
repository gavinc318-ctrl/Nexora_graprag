import config
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

def recursive_chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """递归切分：优先按段落/换行/标点，最后兜底硬切，并附加 overlap。"""
    if not text:
        return []

    seps = ["\n\n", "\n", "。", "！", "？", ".", "؛", ";", " ", ""]  # 中/英/阿常见分隔
    chunks: List[str] = [text]

    def pack_by_sep(t: str, sep: str) -> List[str]:
        if sep == "":
            return [t[i : i + chunk_size] for i in range(0, len(t), chunk_size)]
        parts = t.split(sep)
        out: List[str] = []
        buf = ""
        for p in parts:
            if p == "":
                continue
            piece = p + sep
            if len(buf) + len(piece) <= chunk_size:
                buf += piece
            else:
                if buf.strip():
                    out.append(buf)
                buf = piece
        if buf.strip():
            out.append(buf)
        return out

    for sep in seps:
        new_chunks: List[str] = []
        for c in chunks:
            c = c.strip()
            if not c:
                continue
            if len(c) <= chunk_size:
                new_chunks.append(c)
            else:
                new_chunks.extend(pack_by_sep(c, sep))
        chunks = new_chunks

    # overlap：给后一块拼接前一块尾部（避免断句损失上下文）
    if overlap > 0 and len(chunks) > 1:
        final: List[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = final[-1]
            cur = chunks[i]
            final.append((prev[-overlap:] + cur).strip())
        chunks = final

    return [c.strip() for c in chunks if c.strip()]

# =========================
# 文本切块（支持 sliding / recursive）
# =========================
def sliding_chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """固定长度滑窗切分（你原来的实现）。"""
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_size)
        chunks.append(text[start:end])
        if end == n:
            break
        start = max(0, end - overlap)
    return chunks

def build_chunks_with_meta(
    text_blocks: List[Dict[str, Any]],
    special_blocks: List[Dict[str, Any]],
    chunk_size: int,
    overlap: int,
) -> List[str]:
    """把页面 blocks 转成最终 chunks（带 [[META]] 头）。"""
    chunks: List[str] = []

    # text blocks：按页递归切分，更利于后续“同页合并”
    for b in text_blocks:
        raw = (b.get("text") or "").strip()
        if not raw:
            continue
        sub = chunk_text(raw, chunk_size, overlap)
        for s in sub:
            header = build_meta_header({"type": "text", "page": b.get("page")})
            chunks.append(header + "\n" + s)

    # special blocks：整块入库
    for b in special_blocks:
        raw = (b.get("text") or "").strip()
        if not raw:
            continue
        header = build_meta_header({"type": b.get("type"), "page": b.get("page"), "caption": b.get("caption", "")})
        # 控制极端长度，避免 embedding / 上下文爆炸
        max_len = int(getattr(config, "SPECIAL_BLOCK_MAX_CHARS", 12000))
        if len(raw) > max_len:
            raw = raw[:max_len] + "\n...(truncated)"
        chunks.append(header + "\n" + raw)

    return chunks

def build_chunks_with_meta1(
    pages: List[Dict[str, Any]],
    chunk_size: int,
    overlap: int,
) -> List[str]:
    """把页面 blocks 转成最终 chunks（带 [[META]] 头）。"""
    chunks: List[str] = []

    # text blocks：按页递归切分，更利于后续“同页合并”
    for b in pages:
        raw = (b.get("text") or "").strip()
        if not raw:
            continue
        sub = chunk_text(raw, chunk_size, overlap)
        for s in sub:
            header = build_meta_header({"type": "text", "page": b.get("page_no")})
            chunks.append(header + "\n" + s)

    # table blocks：整块入库
    for b in pages:
        raw = (b.get("tables") or "").strip()
        if not raw:
            continue
        #header = build_meta_header({"type": "Tables", "page": b.get("page"), "caption": b.get("caption", "")})
        header = build_meta_header({"type": "Tables", "page": b.get("page_no")})
        # 控制极端长度，避免 embedding / 上下文爆炸
        max_len = int(getattr(config, "SPECIAL_BLOCK_MAX_CHARS", 12000))
        if len(raw) > max_len:
            raw = raw[:max_len] + "\n...(truncated)"
        chunks.append(header + "\n" + raw)

    # Figures blocks：整块入库
    for b in pages:
        raw = (b.get("figures") or "").strip()
        if not raw:
            continue
        #header = build_meta_header({"type": "Figures", "page": b.get("page"), "caption": b.get("caption", "")})
        header = build_meta_header({"type": "Figures", "page": b.get("page_no")})
        # 控制极端长度，避免 embedding / 上下文爆炸
        max_len = int(getattr(config, "SPECIAL_BLOCK_MAX_CHARS", 12000))
        if len(raw) > max_len:
            raw = raw[:max_len] + "\n...(truncated)"
        chunks.append(header + "\n" + raw)
    return chunks

def chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    method = getattr(config, "PDF_CHUNK_METHOD", "sliding")
    if method == "recursive":
        return recursive_chunk_text(text, chunk_size, overlap)
    return sliding_chunk_text(text, chunk_size, overlap)


# =========================
# 轻量级：检测表格/图形并标记（弱规则）
# =========================

def build_meta_header(meta: Dict[str, Any]) -> str:
    # 只放短字段，避免影响 embedding 太多
    parts = []
    for k in ["type", "page", "caption"]:
        if k in meta and meta[k] not in (None, "", []):
            v = str(meta[k]).replace("]", "").replace("\n", " ").strip()
            if len(v) > 120:
                v = v[:120] + "…"
            parts.append(f"{k}={v}")
    return f"{config.META_PREFIX} " + " ".join(parts) + "]]"


def parse_meta_header(text: str) -> Dict[str, Any]:
    """从 chunk_text 第一行解析 [[META ...]]。没有则返回空 dict。"""
    if not text:
        return {}
    first = text.splitlines()[0].strip()
    if not (first.startswith(config.META_PREFIX) and first.endswith("]]")):
        return {}
    inside = first[len(config.META_PREFIX):].rstrip("]]").strip()
    meta: Dict[str, Any] = {}
    for token in inside.split():
        if "=" in token:
            k, v = token.split("=", 1)
            meta[k.strip()] = v.strip()
    # page 转 int
    if "page" in meta:
        try:
            meta["page"] = int(str(meta["page"]))
        except Exception:
            pass
    return meta


def merge_hits_by_page_or_caption(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """召回后、rerank 前：把同页/同 caption 的 chunk 合并，增强上下文。"""
    if not hits:
        return hits

    merged: List[Dict[str, Any]] = []
    seen = {}

    for h in hits:
        txt = h.get("chunk_text") or ""
        meta = parse_meta_header(txt)
        t = meta.get("type", "")
        page = meta.get("page", None)
        caption = meta.get("caption", "")

        if t in ("table", "figure"):
            key = f"{t}:{page}:{caption}" if caption else f"{t}:{page}"
        else:
            key = f"text:{page}"

        if key not in seen:
            new_h = dict(h)
            seen[key] = new_h
            merged.append(new_h)
        else:
            # 合并文本（去掉重复 META 头）
            cur = seen[key]
            cur_txt = cur.get("chunk_text") or ""
            add_txt = txt
            # 去掉 add 的 META 头
            add_lines = add_txt.splitlines()
            if add_lines and add_lines[0].startswith(config.META_PREFIX):
                add_txt = "\n".join(add_lines[1:]).strip()

            cur["chunk_text"] = (cur_txt.rstrip() + "\n\n" + add_txt).strip()

    # 控制合并后 chunk_text 长度，避免 rerank 请求过大
    max_chars = int(getattr(config, "PRE_RERANK_MERGE_MAX_CHARS", 14000))
    for h in merged:
        if h.get("chunk_text") and len(h["chunk_text"]) > max_chars:
            h["chunk_text"] = h["chunk_text"][:max_chars] + "\n...(merged truncated)"

    return merged



def normalize_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s