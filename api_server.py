"""FastAPI 接口层：封装 core.py 能力，并把会话状态存入 Redis。

本服务面向两类前端：
- **用户问答端**：对齐 code/user_query_ui.py（聊天 + RAG 命中来源）
- **数据管理端**：对齐 code/datamng_gr.py（按 doc_dir/page_no 拉 OCR 产物、按页加载 chunks、编辑 chunk 并写回）

运行：
  export REDIS_URL="redis://:YourStrongPassword@localhost:6379/0"
  uvicorn api_server:app --host 0.0.0.0 --port 19001
"""

import ldap
from ldap import LDAPError, INVALID_CREDENTIALS
import os
import json
import asyncio
import shutil
import time
import uuid
import tempfile
import requests
from typing import Any, Dict, List, Optional

import redis.asyncio as redis
from fastapi import FastAPI, UploadFile, File, Form, Body, HTTPException, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import jwt
from datetime import datetime, timedelta

import config
from core import (
    AppState,
    chat_send,
    clear_db,
    ingest_file,
    load_ocr_page_assets,
    load_page_chunks_for_review,
    pg_store,
    RlsContext,
    save_reviewed_chunk,
    graph_job_worker,
)


# =========================
# Redis 配置
# =========================
REDIS_URL = os.getenv("REDIS_URL", "redis://:Admin@123!@localhost:6379/0")
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))
SESSION_KEY_PREFIX = os.getenv("SESSION_KEY_PREFIX", "nexora:sess:")
INGEST_JOB_TTL_SECONDS = int(os.getenv("INGEST_JOB_TTL_SECONDS", "86400"))
INGEST_KEY_PREFIX = os.getenv("INGEST_KEY_PREFIX", "nexora:ingest:")
INGEST_CALLBACK_TIMEOUT = float(os.getenv("INGEST_CALLBACK_TIMEOUT", "10"))


def _sess_key(session_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{session_id}"

def _ingest_key(job_id: str) -> str:
    return f"{INGEST_KEY_PREFIX}{job_id}"


def _safe_upload_name(filename: Optional[str]) -> str:
    """把客户端传来的文件名净化成可安全落盘的名字。

    core.ingest_file 用 Path(file_path).stem 拼 doc_dir（doc_dir = "<stem>_<uuid>"），
    而 doc_dir 会成为 MinIO 的 object key 前缀，所以这里必须去掉路径分隔符与
    控制字符，否则会产生越权路径或非法 key。
    """
    name = os.path.basename((filename or "").strip().replace("\\", "/"))
    name = name.replace("\x00", "")
    # 仅剔除路径分隔与控制字符，保留中文/阿拉伯文等 Unicode 文件名
    name = "".join(ch for ch in name if ch not in '/\\' and ord(ch) >= 32)
    name = name.strip(". ")                     # 防 "." ".." 及尾随点
    if not name:
        return "upload"
    stem, ext = os.path.splitext(name)
    if not stem:                                # 形如 ".pdf"
        stem, ext = "upload", name
    return stem[:100] + ext[:20]                # doc_dir 还要再拼 36 字符的 uuid



def state_to_dict(state: AppState) -> Dict[str, Any]:
    return {"api_messages": state.api_messages, "ui_messages": state.ui_messages}


def dict_to_state(d: Dict[str, Any]) -> AppState:
    return AppState(
        api_messages=d.get("api_messages") or [],
        ui_messages=d.get("ui_messages") or [],
    )


async def load_state(r: redis.Redis, session_id: str) -> AppState:
    raw = await r.get(_sess_key(session_id))
    if not raw:
        return AppState.new()
    return dict_to_state(json.loads(raw))


async def save_state(r: redis.Redis, session_id: str, state: AppState) -> None:
    raw = json.dumps(state_to_dict(state), ensure_ascii=False)
    await r.set(_sess_key(session_id), raw, ex=SESSION_TTL_SECONDS)


async def clear_state(r: redis.Redis, session_id: str) -> None:
    await r.delete(_sess_key(session_id))


async def save_ingest_state(r: redis.Redis, job_id: str, payload: Dict[str, Any]) -> None:
    """保存文件处理任务状态到 Redis"""
    raw = json.dumps(payload, ensure_ascii=False)
    await r.set(_ingest_key(job_id), raw, ex=INGEST_JOB_TTL_SECONDS)


async def load_ingest_state(r: redis.Redis, job_id: str) -> Optional[Dict[str, Any]]:
    """从 Redis 加载文件处理任务状态"""
    raw = await r.get(_ingest_key(job_id))
    if not raw:
        return None
    return json.loads(raw)


def _notify_callback(callback_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """发送 webhook 回调通知"""
    try:
        resp = requests.post(callback_url, json=payload, timeout=INGEST_CALLBACK_TIMEOUT)
        return {
            "ok": True,
            "status_code": resp.status_code,
            "text": (resp.text or "")[:500],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _run_ingest_job(
    r: redis.Redis,
    job_id: str,
    tmp_path: str,
    filename: str,
    parse_modes: List[str],
    chunk_mode: str,
    ocr_lang: str,
    graph_enabled: bool,
    rag_app_id: str,
    rag_clearance: int,
    callback_url: Optional[str],
) -> None:
    """后台任务：处理文件入库"""
    
    # 更新状态为 running
    await save_ingest_state(
        r,
        job_id,
        {
            "status": "running",
            "job_id": job_id,
            "filename": filename,
            "started_at": int(time.time()),
        },
    )

    try:
        # 在线程池中运行同步的 ingest_file 函数
        result = await asyncio.to_thread(
            ingest_file,
            file_path=tmp_path,
            parse_modes=parse_modes,
            chunk_mode=chunk_mode,
            ocr_lang_choice=ocr_lang,
            graph_enabled=graph_enabled,
            rag_app_id=rag_app_id,
            rag_clearance=rag_clearance,
        )
        
        # 更新状态为 done
        payload = {
            "status": "done",
            "job_id": job_id,
            "filename": filename,
            "result": result,
            "finished_at": int(time.time()),
        }
    except Exception as e:
        # 更新状态为 error
        payload = {
            "status": "error",
            "job_id": job_id,
            "filename": filename,
            "error": f"{type(e).__name__}: {e}",
            "finished_at": int(time.time()),
        }
    finally:
        # 删除临时文件所在的整个目录
        try:
            shutil.rmtree(os.path.dirname(tmp_path), ignore_errors=True)
        except Exception:
            pass

    # 发送 webhook 回调（如果提供了 callback_url）
    if callback_url:
        payload["callback"] = await asyncio.to_thread(_notify_callback, callback_url, payload)

    # 保存最终状态到 Redis
    await save_ingest_state(r, job_id, payload)


# =========================
# FastAPI 应用初始化
# =========================
app = FastAPI(title="Nexora API")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=True)
    asyncio.create_task(graph_job_worker(config.RAG_APP_ID, config.RAG_CLEARANCE))


@app.on_event("shutdown")
async def shutdown():
    try:
        await app.state.redis.close()
    except Exception:
        pass


# =========================
# 认证接口 - LDAP
# =========================

@app.post("/api/auth/ldap")
async def ldap_login(
    username: str = Body(...),
    password: str = Body(...),
):
    """
    真实 LDAP 认证
    
    流程：
    1. 用 bind 账号搜索 People 树中的用户 DN
    2. 使用用户 DN + 密码进行 LDAP bind 认证
    3. 查询 Nexora 应用树获取 aiisClearance
    4. 生成 JWT Token
    """
    
    # LDAP 配置
    LDAP_HOST = os.getenv("LDAP_HOST", "ldap://10.55.223.101:389")
    LDAP_BASE_DN = os.getenv("LDAP_BASE_DN", "dc=aiis,dc=sa")
    LDAP_BIND_DN = os.getenv("LDAP_BIND_DN", "cn=nexora-bind,dc=aiis,dc=sa")
    LDAP_BIND_PASSWORD = os.getenv("LDAP_BIND_PASSWORD", "Goodday")
    
    # LDAP Filter Escape - 防止注入
    def escape_ldap_filter(value: str) -> str:
        replacements = {
            '\\': '\\5c',
            '*': '\\2a',
            '(': '\\28',
            ')': '\\29',
            '\x00': '\\00'
        }
        for char, escaped in replacements.items():
            value = value.replace(char, escaped)
        return value
    
    try:
        # Step 1: 连接 LDAP 并用 bind 账号搜索用户 DN
        conn = ldap.initialize(LDAP_HOST)
        conn.protocol_version = ldap.VERSION3
        conn.set_option(ldap.OPT_REFERRALS, 0)
        
        # Bind 账号登录
        conn.simple_bind_s(LDAP_BIND_DN, LDAP_BIND_PASSWORD)
        
        # 搜索 People 树中的用户
        search_base = f"ou=People,{LDAP_BASE_DN}"
        search_filter = f"(&(objectClass=posixAccount)(uid={escape_ldap_filter(username)}))"
        
        result = conn.search_s(search_base, ldap.SCOPE_SUBTREE, search_filter, ['uid'])
        
        if not result:
            conn.unbind_s()
            return {
                "ok": False,
                "error": "User not found"
            }
        
        user_dn = result[0][0]
        
        # Step 2: 使用用户 DN + 密码进行认证
        try:
            test_conn = ldap.initialize(LDAP_HOST)
            test_conn.protocol_version = ldap.VERSION3
            test_conn.simple_bind_s(user_dn, password)
            test_conn.unbind_s()
        except INVALID_CREDENTIALS:
            conn.unbind_s()
            return {
                "ok": False,
                "error": "Invalid credentials"
            }
        
        # Step 3: 查询 Nexora 应用树获取 clearance
        nexora_base = f"ou=Users,ou=Nexora,ou=Apps,{LDAP_BASE_DN}"
        nexora_filter = f"(&(objectClass=nexoraUser)(uid={escape_ldap_filter(username)}))"
        
        nexora_result = conn.search_s(
            nexora_base, 
            ldap.SCOPE_SUBTREE, 
            nexora_filter,
            ['aiisClearance', 'nexoraStatus', 'nexoraRole']
        )
        
        if not nexora_result or 'aiisClearance' not in nexora_result[0][1]:
            conn.unbind_s()
            return {
                "ok": False,
                "error": "No clearance assigned to this user"
            }
        
        # 获取 clearance
        clearance_value = nexora_result[0][1].get('aiisClearance', [b'0'])[0]
        clearance = int(clearance_value.decode('utf-8') if isinstance(clearance_value, bytes) else clearance_value)
        
        # 验证 clearance 范围
        if not (0 <= clearance <= 3):
            conn.unbind_s()
            return {
                "ok": False,
                "error": f"Invalid clearance level: {clearance}"
            }
        
        # 获取其他属性
        nexora_status = nexora_result[0][1].get('nexoraStatus', [b'active'])[0]
        nexora_role = nexora_result[0][1].get('nexoraRole', [b'user'])[0]
        
        if isinstance(nexora_status, bytes):
            nexora_status = nexora_status.decode('utf-8')
        if isinstance(nexora_role, bytes):
            nexora_role = nexora_role.decode('utf-8')
        
        conn.unbind_s()
        
        # Step 4: 生成 JWT Token
        payload = {
            "sub": username,
            "dn": user_dn,
            "clearance": clearance,
            "nexoraStatus": nexora_status,
            "nexoraRole": nexora_role,
            "exp": datetime.utcnow() + timedelta(hours=8),
            "iat": datetime.utcnow()
        }
        
        secret_key = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
        token = jwt.encode(payload, secret_key, algorithm="HS256")
        
        return {
            "ok": True,
            "token": token,
            "user": {
                "username": username,
                "dn": user_dn,
                "clearance": clearance,
                "nexoraStatus": nexora_status,
                "nexoraRole": nexora_role
            }
        }
        
    except ldap.SERVER_DOWN:
        return {
            "ok": False,
            "error": "LDAP server is not reachable"
        }
    except ldap.INVALID_CREDENTIALS:
        return {
            "ok": False,
            "error": "Invalid bind credentials"
        }
    except LDAPError as e:
        return {
            "ok": False,
            "error": f"LDAP error: {str(e)}"
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"Authentication failed: {str(e)}"
        }


@app.post("/v1/auth/ldap")
async def ldap_login_v1(
    username: str = Body(...),
    password: str = Body(...),
):
    """v1 路径别名，调用相同的认证逻辑"""
    return await ldap_login(username, password)


@app.get("/api/auth/me")
async def get_current_user():
    """获取当前用户信息（Mock）"""
    return {
        "user": {
            "username": "admin@company.com",
            "email": "admin@company.com",
            "displayName": "admin",
            "roles": ["admin"],
            "clearance": 3,
            "knowledgeBases": ["legal", "finance", "hr", "public"],
            "department": "IT Department"
        }
    }


@app.get("/v1/auth/me")
async def get_current_user_v1():
    """v1 路径别名"""
    return await get_current_user()


@app.post("/api/auth/logout")
async def logout():
    """退出登录"""
    return {"ok": True}


@app.post("/v1/auth/logout")
async def logout_v1():
    """v1 路径别名"""
    return await logout()


# =========================
# 知识库列表接口
# =========================

@app.get("/api/knowledge-bases")
async def get_knowledge_bases():
    """
    获取所有可用的知识库列表
    
    实际部署时，应该根据用户的 clearance 过滤
    """
    knowledge_bases = [
        {
            "id": "legal",
            "name": "legal",
            "description": "Company legal contracts and policy documents",
            "requiredClearance": 2,
            "icon": "⚖️",
            "documentCount": 1250
        },
        {
            "id": "finance",
            "name": "finance",
            "description": "Financial statements and audit documents",
            "requiredClearance": 3,
            "icon": "💰",
            "documentCount": 856
        },
        {
            "id": "hr",
            "name": "hr",
            "description": "Employee Handbook, HR Policies",
            "requiredClearance": 1,
            "icon": "👥",
            "documentCount": 432
        },
        {
            "id": "public",
            "name": "public",
            "description": "Public information, profile",
            "requiredClearance": 0,
            "icon": "📢",
            "documentCount": 324
        }
    ]
    
    return {
        "ok": True,
        "knowledgeBases": knowledge_bases
    }


# =========================
# 健康检查
# =========================

@app.get("/health")
async def health():
    return {"Nexora is online!": True}


# =========================
# 异步文件入库接口
# =========================

@app.post("/v1/ingest/file")
async def api_ingest_file(
    request: Request,
    file: UploadFile = File(...),
    parse_modes: List[str] = Form(default=["OCR", "VLM"]),
    chunk_mode: str = Form(default="advanced chunk"),
    ocr_lang: str = Form(default="ar"),
    graph_enabled: bool = Form(default=config.GRAPH_ENABLED),
    rag_app_id: str = Form(default=config.RAG_APP_ID),
    rag_clearance: int = Form(default=config.RAG_CLEARANCE),
    callback_url: Optional[str] = Form(default=None),
):
    print(f"=== Received upload request ===")
    print(f"File: {file.filename}, Size: {file.size if hasattr(file, 'size') else 'unknown'}")
    print(f"rag_app_id: {rag_app_id} (type: {type(rag_app_id)})")
    print(f"rag_clearance: {rag_clearance} (type: {type(rag_clearance)})")
    print(f"ocr_lang: {ocr_lang}")
    print(f"chunk_mode: {chunk_mode}")
    print(f"parse_modes: {parse_modes} (type: {type(parse_modes)})")
    print(f"graph_enabled: {graph_enabled} (type: {type(graph_enabled)})")
    
    """
    异步文件入库接口：PDF/DOCX/XLSX/TXT/JPG/PNG
    
    立即返回 job_id，后台异步处理文件
    
    参数：
    - file: 上传的文件
    - parse_modes: 解析模式，默认 ["OCR", "VLM"]
    - chunk_mode: 分块模式，默认 "advanced chunk"
    - ocr_lang: OCR 语言，默认 "ar"
    - rag_app_id: 知识库 ID
    - rag_clearance: 权限级别
    - callback_url: 可选的 webhook 回调地址
    
    返回：
    {
      "ok": true,
      "job_id": "uuid",
      "status": "queued"
    }
    """
    r: redis.Redis = request.app.state.redis
    
    # 保存上传的文件到临时目录
    print(f"我收到一个文件请求{file.filename}")
    # 注意：不能用 NamedTemporaryFile —— 它只保留扩展名，文件名会变成 tmpXXXXXXXX，
    # 导致 core.ingest_file 生成的 doc_dir 成了 "tmpXXXXXXXX_<uuid>" 而非原文件名。
    # 改为在独立临时目录内用原始文件名落盘，目录在任务结束时整体删除。
    tmp_dir = tempfile.mkdtemp(prefix="nexora_ingest_")
    tmp_path = os.path.join(tmp_dir, _safe_upload_name(file.filename))
    with open(tmp_path, "wb") as tmp:
        await file.seek(0)
        shutil.copyfileobj(file.file, tmp)

    # 生成任务 ID
    job_id = str(uuid.uuid4())
    
    # 保存初始状态到 Redis
    await save_ingest_state(
        r,
        job_id,
        {
            "status": "queued",
            "job_id": job_id,
            "filename": file.filename,
            "callback_url": callback_url,
            "created_at": int(time.time()),
        },
    )

    # 创建后台任务处理文件
    asyncio.create_task(
        _run_ingest_job(
            r=r,
            job_id=job_id,
            tmp_path=tmp_path,
            filename=file.filename,
            parse_modes=parse_modes,
            chunk_mode=chunk_mode,
            ocr_lang=ocr_lang,
            graph_enabled=graph_enabled,
            rag_app_id=rag_app_id,
            rag_clearance=rag_clearance,
            callback_url=callback_url,
        )
    )

    # 立即返回任务 ID
    return {
        "ok": True,
        "job_id": job_id,
        "task_id": job_id,  # 兼容前端使用 task_id 的情况
        "status": "queued",
        "message": "File uploaded successfully, processing started in background"
    }


@app.get("/v1/ingest/status")
async def api_ingest_status(job_id: str, request: Request):
    """
    查询文件处理任务状态
    
    参数：
    - job_id: 任务 ID（从 /v1/ingest/file 返回）
    
    返回：
    - status: queued / running / done / error
    - job_id: 任务 ID
    - filename: 文件名
    - result: 处理结果（status=done 时）
    - error: 错误信息（status=error 时）
    """
    r: redis.Redis = request.app.state.redis
    payload = await load_ingest_state(r, job_id)
    
    if not payload:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 计算进度百分比（简化版）
    status = payload.get("status")
    if status == "queued":
        progress = 0
    elif status == "running":
        progress = 50  # 可以根据实际情况细化
    elif status == "done":
        progress = 100
    else:  # error
        progress = 0
    
    payload["progress"] = progress
    
    # 如果处理完成，提取关键信息
    if status == "done" and "result" in payload:
        result = payload["result"]
        if isinstance(result, dict):
            payload["doc_dir"] = result.get("doc_name")
            payload["page_count"] = result.get("pages")
    
    return {"ok": True, **payload}


# 兼容前端使用 /v1/ingest/status/{task_id} 的路径
@app.get("/v1/ingest/status/{task_id}")
async def api_ingest_status_path(task_id: str, request: Request):
    """查询任务状态（路径参数版本）"""
    return await api_ingest_status(job_id=task_id, request=request)


# =========================
# 聊天接口
# =========================

class ChatSendIn(BaseModel):
    session_id: str
    text: str
    rag_app_id: Optional[str] = None
    rag_clearance: Optional[int] = None
    graph_enabled: Optional[bool] = None


@app.post("/v1/chat/send")
async def api_chat_send(payload: ChatSendIn = Body(...), request: Request = None):
    r: redis.Redis = request.app.state.redis
    state = await load_state(r, payload.session_id)

    res = chat_send(
        state=state,
        user_text=payload.text,
        graph_enabled=payload.graph_enabled,
        rag_app_id=payload.rag_app_id,
        rag_clearance=payload.rag_clearance,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "bad request"))

    state = res["state"]
    await save_state(r, payload.session_id, state)

    return {
        "ok": True,
        "session_id": payload.session_id,
        "answer": res.get("answer", ""),
        "sources": res.get("sources", []),
        "hits": res.get("hits", []),
    }


@app.post("/v1/chat/clear")
async def api_chat_clear(request: Request, session_id: str = Body(..., embed=True)):
    r: redis.Redis = request.app.state.redis
    await clear_state(r, session_id)
    return {"ok": True, "session_id": session_id}


@app.post("/v1/admin/clear_db")
async def api_clear_db(rag_app_id: str = Body(..., embed=True)):
    res = clear_db(rag_app_id)
    if not res.get("ok"):
        raise HTTPException(status_code=500, detail=res.get("error", "clear db failed"))
    return res


# =========================
# Admin：OCR 资产 & Chunk 校对
# =========================


@app.get("/v1/admin/page_assets")
async def api_admin_page_assets(
    rag_app_id: str,
    doc_dir: str,
    page_no: int,
):
    """返回某页 OCR 产物（text/tab/log + png_bytes）。"""
    import base64

    assets = load_ocr_page_assets(rag_app_id, doc_dir, page_no)
    png = assets.get("png_bytes") or b""
    assets["png_base64"] = base64.b64encode(png).decode("ascii") if png else ""
    assets.pop("png_bytes", None)
    return {"ok": True, **assets}


@app.get("/v1/assets/page_image")
async def api_page_image(
    rag_app_id: str,
    doc_dir: str,
    page_no: int,
):
    """直接返回某页 PNG（便于浏览器预览/缓存）。"""
    assets = load_ocr_page_assets(rag_app_id, doc_dir, page_no)
    png = assets.get("png_bytes") or b""
    if not png:
        raise HTTPException(status_code=404, detail="image not found")
    return Response(content=png, media_type="image/png")


@app.get("/v1/admin/page_review")
async def api_admin_page_review(
    rag_app_id: str,
    rag_clearance: int,
    doc_dir: str,
    page_no: int,
):
    """聚合接口：OCR 产物 + 本页 chunks（按 [[META page=...]] 过滤）。"""
    assets = load_ocr_page_assets(rag_app_id, doc_dir, page_no)
    review = load_page_chunks_for_review(rag_app_id, rag_clearance, doc_dir, page_no)
    if not review.get("ok"):
        raise HTTPException(status_code=404, detail=review.get("error", "not found"))
    assets.pop("png_bytes", None)
    return {
        "ok": True,
        "assets": assets,
        "review": review,
    }


class ChunkUpdateIn(BaseModel):
    rag_app_id: str = config.RAG_APP_ID
    rag_clearance: int = config.RAG_CLEARANCE
    chunk_id: str
    new_chunk_text: str
    reembed: bool = False


@app.post("/v1/admin/chunk/update")
async def api_admin_chunk_update(payload: ChunkUpdateIn = Body(...)):
    res = save_reviewed_chunk(
        rag_app_id=payload.rag_app_id,
        rag_clearance=payload.rag_clearance,
        chunk_id=payload.chunk_id,
        new_chunk_text=payload.new_chunk_text,
        reembed=payload.reembed,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "update failed"))
    return res


@app.get("/v1/admin/docs/find")
async def api_admin_find_docs(
    rag_app_id: str,
    rag_clearance: int,
    doc_dir: str,
    limit: int = 20,
):
    """按 doc_dir 在 PG docs 中查找文档候选。"""
    ctx = RlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))
    docs = pg_store.find_docs_by_doc_dir(ctx=ctx, doc_dir=doc_dir, limit=limit)
    return {"ok": True, "docs": docs}
