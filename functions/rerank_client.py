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
    返回 rerank 服务的结果：results=[{doc, score, rank}, ...]
    """
    if not docs:
        return []
    payload = {"query": query, "documents": docs, "top_k": top_k}
    r = requests.post(
        config.RERANK_API_URL,
        json=payload,
        timeout=getattr(config, "RERANK_TIMEOUT", 30),
    )
    r.raise_for_status()
    data = r.json()
    return data.get("results") or []


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
        except ValueError:
            continue

    # 只有在未设置阈值时，才把没返回的补到后面
    if min_score is None:
        for i in range(len(docs)):
            if i not in used:
                order.append(i)
    elif not order:
        # 阈值过高导致无命中时，回退原始顺序避免空结果
        order = list(range(len(docs)))

    return [hits[i] for i in order if 0 <= i < len(hits)]
