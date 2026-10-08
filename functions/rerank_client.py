import json
import subprocess
import time
from typing import Any, Dict, List, Optional

import requests
import config


def _is_rerank_alive() -> bool:
    try:
        r = requests.get(config.RERANK_HEALTH_URL, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def ensure_rerank_service() -> None:
    """
    在主程序启动时调用：
    - 如果 rerank 已经在跑：直接返回
    - 如果没在跑：按 config.RERANK_AUTO_START 拉起 docker compose
    """
    if not getattr(config, "RERANK_ENABLED", False):
        return

    # OpenAI 打分重排不依赖本地 Docker 服务
    if getattr(config, "RERANK_PROVIDER", "local") == "openai":
        print("[rerank] provider=openai (LLM scoring, no local service needed).")
        return

    if _is_rerank_alive():
        print("[rerank] service is alive.")
        return

    if not getattr(config, "RERANK_AUTO_START", False):
        raise RuntimeError("[rerank] service not alive and auto-start disabled")

    compose_file = getattr(config, "RERANK_COMPOSE_FILE", "").strip()
    if not compose_file:
        raise RuntimeError("[rerank] RERANK_COMPOSE_FILE is empty")

    print(f"[rerank] service not alive, starting via docker compose: {compose_file}")

    # 兼容 docker compose / docker-compose
    cmds = [
        ["docker", "compose", "-f", compose_file, "up", "-d", "--build"],
        ["docker-compose", "-f", compose_file, "up", "-d", "--build"],
    ]

    last_err: Optional[str] = None
    for cmd in cmds:
        try:
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            break
        except Exception as e:
            last_err = str(e)
    else:
        raise RuntimeError(f"[rerank] failed to start docker compose: {last_err}")

    # 等待服务起来
    for _ in range(30):
        if _is_rerank_alive():
            print("[rerank] started OK.")
            return
        time.sleep(1)

    raise RuntimeError("[rerank] started docker compose but health check still failed")


def rerank_results(query: str, docs: List[str], top_k: int) -> List[Dict[str, Any]]:
    """
    返回 rerank 结果：results=[{doc, score, rank}, ...]
    provider=openai 时用 LLM 打分（跨语言好）；否则走本地 bge-reranker 服务。
    """
    if not docs:
        return []
    if getattr(config, "RERANK_PROVIDER", "local") == "openai":
        return _rerank_results_openai(query, docs, top_k)

    payload = {"query": query, "documents": docs, "top_k": top_k}
    r = requests.post(
        config.RERANK_API_URL,
        json=payload,
        timeout=getattr(config, "RERANK_TIMEOUT", 30),
    )
    r.raise_for_status()
    data = r.json()
    return data.get("results") or []


def _rerank_results_openai(query: str, docs: List[str], top_k: int) -> List[Dict[str, Any]]:
    """
    用 OpenAI Chat Completions 给每段候选打 0.0–1.0 相关性分。
    评分标准写进 prompt，使 RERANK_MIN_SCORE 阈值有明确含义。
    任何异常都退化为「原序、score=None」——不清空候选，避免问答彻底丢上下文。
    """
    max_chars = int(getattr(config, "RERANK_DOC_MAX_CHARS", 600))
    passages = [(d or "")[:max_chars].replace("\n", " ").strip() for d in docs]

    listing = "\n".join(f"[{i}] {p}" for i, p in enumerate(passages))
    sys_prompt = (
        "You are a multilingual search-relevance judge. The query and passages may be in "
        "different languages (e.g. English query, Arabic passage) — judge by meaning, not language.\n"
        "Score every passage 0.0–1.0 for how well it helps answer the query:\n"
        "  1.0 = directly contains the answer;  0.5 = related/partial;  0.0 = unrelated.\n"
        'Return ONLY JSON: {"scores":[{"i":<index>,"score":<float>}, ...]} covering every index.'
    )
    user_prompt = f"Query: {query}\n\nPassages:\n{listing}"

    payload = {
        "model": getattr(config, "OPENAI_RERANK_MODEL", config.OPENAI_MODEL),
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}

    try:
        r = requests.post(
            config.OPENAI_CHAT_URL,
            json=payload,
            headers=headers,
            timeout=getattr(config, "RERANK_TIMEOUT", 30),
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"] or "{}"
        rows = json.loads(content).get("scores") or []
        by_idx: Dict[int, float] = {}
        for row in rows:
            try:
                by_idx[int(row["i"])] = float(row["score"])
            except (KeyError, TypeError, ValueError):
                continue
        if not by_idx:
            raise ValueError("no usable scores in LLM response")
    except Exception as e:
        print(f"[rerank] openai scoring failed, passthrough: {type(e).__name__}: {e}")
        return [{"doc": d, "score": None, "rank": i + 1} for i, d in enumerate(docs)]

    ranked = sorted(
        range(len(docs)),
        key=lambda i: by_idx.get(i, 0.0),
        reverse=True,
    )[: max(top_k, 0) or len(docs)]
    return [
        {"doc": docs[i], "score": by_idx.get(i, 0.0), "rank": r + 1}
        for r, i in enumerate(ranked)
    ]


def rerank(query: str, docs: List[str], top_k: int) -> List[int]:
    """
    返回 rerank 后的 doc 下标顺序（从高到低）。
    """
    results = rerank_results(query=query, docs=docs, top_k=top_k)
    # 用 doc 文本匹配回 index（简单可靠；如担心重复文本，可改为传 id）
    order: List[int] = []
    used = set()
    for item in results:
        d = item.get("doc")
        if d is None:
            continue
        try:
            i = docs.index(d)
            if i not in used:
                used.add(i)
                order.append(i)
        except ValueError:
            continue

    # 兜底：把没返回的补到后面
    for i in range(len(docs)):
        if i not in used:
            order.append(i)

    return order


def rerank_hits(query: str, hits: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    """
    hits: pg_store.search_chunks 的返回列表，每个含 chunk_text
    """
    if not hits:
        return hits

    docs = [h.get("chunk_text", "") for h in hits]
    results = rerank_results(query=query, docs=docs, top_k=top_k)
    min_score = getattr(config, "RERANK_MIN_SCORE", None)

    order: List[int] = []
    scores_by_index: Dict[int, float] = {}
    used = set()
    for item in results:
        d = item.get("doc")
        score = item.get("score")
        if d is None:
            continue
        if min_score is not None and score is not None and float(score) < float(min_score):
            continue
        try:
            i = docs.index(d)
            if i not in used:
                used.add(i)
                order.append(i)
                if score is not None:
                    scores_by_index[i] = float(score)
        except ValueError:
            continue

    # 只有在未设置阈值时，才把没返回的补到后面
    if min_score is None:
        for i in range(len(docs)):
            if i not in used:
                order.append(i)

    # 注意：设置了 min_score 且全部候选都低于阈值时，这里【必须返回空】。
    # 曾经的实现会 order = list(range(len(docs))) 回退成全量返回，
    # 使阈值形同虚设——像 "hello" 这种与语料完全无关的输入
    # （rerank 得分 0.000）也会被塞进 LLM 上下文，诱发幻觉。
    # 返回空后 chat_send 不注入 pdf_context，模型即正常对话。

    out: List[Dict[str, Any]] = []
    for i in order:
        if not (0 <= i < len(hits)):
            continue
        h = dict(hits[i])
        if i in scores_by_index:
            h["rerank_score"] = scores_by_index[i]
        out.append(h)
    return out
