"""只读 Odoo XML-RPC 客户端（供 AI Agent 查询企业业务数据）。

设计原则：
- **只读**：只放行 search / read / read_group / fields_get 等查询方法，
  任何 create/write/unlink 一律拒绝。
- **模型白名单**：即使连接用的 Odoo 账号本身已按部门授权，这里再做一层限制。
- 连接参数从 config（.env）读取；uid 缓存，鉴权失败会重试一次。
"""

from __future__ import annotations

import threading
import xmlrpc.client
from typing import Any, Dict, List, Optional

import config

# 只读方法白名单
_READ_METHODS = {
    "search", "search_read", "search_count", "read", "read_group", "fields_get",
    "name_search", "name_get", "default_get",
}

# 默认（HR）模型白名单——仅作为 get_client() 不传 allowed_models 时的兼容兜底；
# 各部门的实际白名单由 functions/odoo_skill/departments/*.py 里的 DepartmentSkill.allowed_models 声明。
# 注意：ir.attachment 是全系统共用的附件表（res_model/res_id 可指向任意模型的任意记录），
# 放开这个模型本身并不能限定"只读某部门的附件"——真正的范围限制在 functions/odoo_skill 的
# list_attachments / read_attachment 里做（强制 domain res_model in 该部门声明的 attachment_scopes），
# 并且通用逃生口 odoo_query 显式拒绝直接查 ir.attachment，防止绕过这层限制读到无关附件。
_ALLOWED_MODELS_HR = {
    "hr.employee", "hr.department", "hr.job", "hr.contract",
    "hr.leave", "hr.leave.type", "hr.leave.allocation",
    "resource.calendar", "res.company", "ir.attachment",
}


class OdooError(RuntimeError):
    pass


class OdooClient:
    def __init__(
        self,
        url: Optional[str] = None,
        db: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        allowed_models: Optional[set] = None,
        timeout: int = 30,
    ):
        self.url = (url or getattr(config, "ODOO_URL", "")).rstrip("/")
        self.db = db or getattr(config, "ODOO_DB", "")
        self.username = username or getattr(config, "ODOO_USER", "")
        self.password = password or getattr(config, "ODOO_PASSWORD", "")
        self.allowed_models = allowed_models or set(_ALLOWED_MODELS_HR)
        self.timeout = timeout

        if not (self.url and self.db and self.username and self.password):
            raise OdooError("Odoo 连接参数不完整（需 ODOO_URL/ODOO_DB/ODOO_USER/ODOO_PASSWORD）")

        transport = xmlrpc.client.Transport()
        # xmlrpc.client 没有直接的 timeout 参数，靠底层 socket 默认；生产可换 httpx
        self._common = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/common", allow_none=True
        )
        self._object = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object", allow_none=True
        )
        self._uid: Optional[int] = None
        self._lock = threading.Lock()

    # ---------- 内部 ----------
    def _authenticate(self) -> int:
        uid = self._common.authenticate(self.db, self.username, self.password, {})
        if not uid:
            raise OdooError("Odoo 鉴权失败：用户名或密码错误")
        return int(uid)

    def _ensure_uid(self) -> int:
        if self._uid is None:
            with self._lock:
                if self._uid is None:
                    self._uid = self._authenticate()
        return self._uid

    def execute_kw(
        self,
        model: str,
        method: str,
        args: List[Any],
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if method not in _READ_METHODS:
            raise OdooError(f"方法 {method!r} 不允许（本客户端只读）")
        if model not in self.allowed_models:
            raise OdooError(f"模型 {model!r} 不在允许范围内")

        kwargs = kwargs or {}
        uid = self._ensure_uid()
        try:
            return self._object.execute_kw(self.db, uid, self.password, model, method, args, kwargs)
        except xmlrpc.client.Fault as e:
            # session 失效等：重认证重试一次
            self._uid = None
            uid = self._ensure_uid()
            try:
                return self._object.execute_kw(self.db, uid, self.password, model, method, args, kwargs)
            except xmlrpc.client.Fault as e2:
                raise OdooError(f"Odoo 调用失败: {e2.faultString}") from e2

    # ---------- 便捷方法 ----------
    def search_read(
        self,
        model: str,
        domain: Optional[List] = None,
        fields: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0,
        order: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        kw: Dict[str, Any] = {"fields": fields or [], "limit": limit, "offset": int(offset or 0)}
        if order:
            kw["order"] = order
        return self.execute_kw(model, "search_read", [domain or []], kw)

    def search_count(self, model: str, domain: Optional[List] = None) -> int:
        return self.execute_kw(model, "search_count", [domain or []])

    def read_group(
        self,
        model: str,
        domain: Optional[List],
        fields: List[str],
        groupby: List[str],
        lazy: bool = False,
    ) -> List[Dict[str, Any]]:
        return self.execute_kw(
            model, "read_group", [domain or [], fields, groupby], {"lazy": lazy}
        )

    def fields_get(self, model: str) -> Dict[str, Any]:
        return self.execute_kw(
            model, "fields_get", [], {"attributes": ["string", "type", "help", "relation", "selection"]}
        )

    def ping(self) -> Dict[str, Any]:
        ver = self._common.version()
        uid = self._ensure_uid()
        return {"ok": True, "server_version": ver.get("server_version"), "uid": uid}


_clients_by_scope: Dict[frozenset, OdooClient] = {}
_clients_lock = threading.Lock()


def get_client(allowed_models: Optional[set] = None) -> OdooClient:
    """按模型白名单缓存的客户端。同一组 allowed_models 复用同一个连接（含已鉴权的 uid）；
    不同部门声明不同的 allowed_models 时各自拿到独立作用域的客户端，互不越权。
    不传 allowed_models 时用默认的 HR 白名单（兼容旧调用方）。
    """
    scope = frozenset(allowed_models) if allowed_models is not None else frozenset(_ALLOWED_MODELS_HR)
    client = _clients_by_scope.get(scope)
    if client is None:
        with _clients_lock:
            client = _clients_by_scope.get(scope)
            if client is None:
                client = OdooClient(allowed_models=set(scope))
                _clients_by_scope[scope] = client
    return client
