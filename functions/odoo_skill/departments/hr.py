"""HR 部门：员工 / 部门 / 职位 / 薪资合同 / 休假 + 员工附件。

这是 odoo_skill 框架的第一个（也是参考）部门实现。新部门照着这个文件的结构写就行：
一组 tool_* 函数 + ToolDef 包装 + 一个 DepartmentSkill 实例 + register()。
"""

from __future__ import annotations

from typing import Any, Dict, List

from functions.odoo_client import OdooClient
from functions.odoo_skill import AttachmentScope, DepartmentSkill, ToolDef, register


def _fmt_m2o(v):
    """Odoo many2one 返回 [id, name]，取 name。"""
    return v[1] if isinstance(v, (list, tuple)) and len(v) == 2 else v


_CURRENCY_CACHE: Dict[str, str] = {}


def _company_currency(client: OdooClient) -> str:
    """公司本位币代码（薪资金额的货币单位），带进程内缓存。"""
    if "code" not in _CURRENCY_CACHE:
        try:
            rows = client.search_read("res.company", [], fields=["currency_id"], limit=1)
            cur = rows[0].get("currency_id") if rows else None
            _CURRENCY_CACHE["code"] = _fmt_m2o(cur) or "unknown"
        except Exception:
            _CURRENCY_CACHE["code"] = "unknown"
    return _CURRENCY_CACHE["code"]


def tool_search_employees(client: OdooClient, name: str = "", department: str = "",
                          job: str = "", limit: int = 20) -> Dict[str, Any]:
    domain: List = []
    if name:
        domain.append(["name", "ilike", name])
    if department:
        domain.append(["department_id.complete_name", "ilike", department])
    if job:
        domain.append(["job_title", "ilike", job])
    rows = client.search_read(
        "hr.employee", domain,
        fields=["name", "job_title", "department_id", "work_email", "work_phone",
                "parent_id", "employee_type"],
        limit=limit, order="name",
    )
    for r in rows:
        r["department"] = _fmt_m2o(r.pop("department_id", None))
        r["manager"] = _fmt_m2o(r.pop("parent_id", None))
    return {"count": len(rows), "employees": rows}


def tool_get_salary(client: OdooClient, employee: str = "", department: str = "",
                    state: str = "open", limit: int = 50) -> Dict[str, Any]:
    domain: List = []
    if state and state != "all":
        domain.append(["state", "=", state])
    if employee:
        domain.append(["employee_id.name", "ilike", employee])
    if department:
        domain.append(["department_id.complete_name", "ilike", department])
    rows = client.search_read(
        "hr.contract", domain,
        fields=["name", "employee_id", "department_id", "job_id", "wage",
                "state", "date_start", "date_end"],
        limit=limit, order="wage desc",
    )
    cur = _company_currency(client)
    for r in rows:
        r["employee"] = _fmt_m2o(r.pop("employee_id", None))
        r["department"] = _fmt_m2o(r.pop("department_id", None))
        r["job"] = _fmt_m2o(r.pop("job_id", None))
        w = r.pop("wage", 0) or 0
        r["monthly_salary"] = f"{w:,.0f} {cur}"
    return {"count": len(rows), "currency": cur,
            "_note": f"All salary amounts are in {cur}. Report them with the {cur} suffix, not $.",
            "contracts": rows}


def tool_headcount(client: OdooClient, group_by: str = "department_id") -> Dict[str, Any]:
    allowed = {"department_id", "job_id", "employee_type", "parent_id"}
    if group_by not in allowed:
        group_by = "department_id"
    rows = client.read_group("hr.employee", [], ["id"], [group_by])
    out = []
    for r in rows:
        out.append({
            "group": _fmt_m2o(r.get(group_by)) or "(未分配)",
            "headcount": r.get(f"{group_by}_count") or r.get("__count") or 0,
        })
    total = client.search_count("hr.employee", [])
    return {"total_headcount": total, "group_by": group_by, "breakdown": out}


def tool_salary_stats(client: OdooClient, group_by: str = "department_id",
                      state: str = "open") -> Dict[str, Any]:
    allowed = {"department_id", "job_id", "state"}
    if group_by not in allowed:
        group_by = "department_id"
    domain: List = []
    if state and state != "all":
        domain.append(["state", "=", state])
    rows = client.read_group(
        "hr.contract", domain, ["wage_avg:avg(wage)", "wage_sum:sum(wage)", "wage_min:min(wage)", "wage_max:max(wage)"],
        [group_by],
    )
    cur = _company_currency(client)
    out = []
    for r in rows:
        out.append({
            "group": _fmt_m2o(r.get(group_by)) or "(unassigned)",
            "contracts": r.get(f"{group_by}_count") or r.get("__count") or 0,
            "avg_salary": f"{round(r.get('wage_avg') or 0):,} {cur}",
            "total_salary": f"{round(r.get('wage_sum') or 0):,} {cur}",
            "min_salary": f"{round(r.get('wage_min') or 0):,} {cur}",
            "max_salary": f"{round(r.get('wage_max') or 0):,} {cur}",
        })
    return {"group_by": group_by, "contract_state": state, "currency": cur,
            "_note": f"All amounts are in {cur}. Report with the {cur} suffix, not $.",
            "breakdown": out}


def tool_list_time_off(client: OdooClient, employee: str = "", on_date: str = "",
                       date_from: str = "", date_to: str = "",
                       only_approved: bool = True, limit: int = 50) -> Dict[str, Any]:
    """休假查询。on_date='YYYY-MM-DD' 查覆盖该天的休假；也可用 date_from/date_to 查区间；
    只传 employee 查某人全部休假。only_approved 默认只看已批准(state=validate)。"""
    domain: List = []
    if employee:
        domain.append(["employee_id.name", "ilike", employee])
    if only_approved:
        domain.append(["state", "=", "validate"])
    if on_date:
        domain += [["request_date_from", "<=", on_date], ["request_date_to", ">=", on_date]]
    else:
        if date_from:
            domain.append(["request_date_to", ">=", date_from])
        if date_to:
            domain.append(["request_date_from", "<=", date_to])
    rows = client.search_read(
        "hr.leave", domain,
        fields=["employee_id", "holiday_status_id", "request_date_from",
                "request_date_to", "number_of_days", "state"],
        limit=limit, order="request_date_from desc",
    )
    for r in rows:
        r["employee"] = _fmt_m2o(r.pop("employee_id", None))
        r["leave_type"] = _fmt_m2o(r.pop("holiday_status_id", None))
        r["state"] = "approved" if r.get("state") == "validate" else r.get("state")
    return {"count": len(rows), "filter": {"on_date": on_date or None,
            "date_from": date_from or None, "date_to": date_to or None},
            "time_off": rows}


_TOOLS = [
    ToolDef(
        name="hr_search_employees",
        description="按姓名/部门/职位模糊查询员工列表及其部门、经理、联系方式。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "员工姓名（模糊）"},
                "department": {"type": "string", "description": "部门名（模糊）"},
                "job": {"type": "string", "description": "职位名（模糊）"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        impl=tool_search_employees,
    ),
    ToolDef(
        name="hr_get_salary",
        description="查询员工薪资合同明细（wage=月薪金额）。可按员工或部门过滤。默认只看在职合同(state=open)。",
        parameters={
            "type": "object",
            "properties": {
                "employee": {"type": "string", "description": "员工姓名（模糊）"},
                "department": {"type": "string", "description": "部门名（模糊）"},
                "state": {"type": "string", "enum": ["open", "draft", "close", "all"], "default": "open"},
                "limit": {"type": "integer", "default": 50},
            },
        },
        impl=tool_get_salary,
    ),
    ToolDef(
        name="hr_headcount",
        description="按部门/职位/雇佣类型统计员工人数。",
        parameters={
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": ["department_id", "job_id", "employee_type", "parent_id"], "default": "department_id"},
            },
        },
        impl=tool_headcount,
    ),
    ToolDef(
        name="hr_salary_stats",
        description="按部门/职位统计薪资：平均、总额、最低、最高。默认只统计在职合同。",
        parameters={
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": ["department_id", "job_id", "state"], "default": "department_id"},
                "state": {"type": "string", "enum": ["open", "draft", "close", "all"], "default": "open"},
            },
        },
        impl=tool_salary_stats,
    ),
    ToolDef(
        name="hr_list_time_off",
        description="Query time-off / leave records. For 'who is on leave on <date>' pass on_date='YYYY-MM-DD'. For a range use date_from/date_to. For one person's leaves pass employee. Defaults to approved leaves only.",
        parameters={
            "type": "object",
            "properties": {
                "employee": {"type": "string", "description": "employee name (fuzzy)"},
                "on_date": {"type": "string", "description": "YYYY-MM-DD; returns leaves covering this day"},
                "date_from": {"type": "string", "description": "YYYY-MM-DD range start"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD range end"},
                "only_approved": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 50},
            },
        },
        impl=tool_list_time_off,
    ),
]


HR = DepartmentSkill(
    key="hr",
    display_name="Human Resources (HR)",
    scope_description=(
        "only HR data is connected (employees, departments, jobs, salary contracts, time off, "
        "and files attached to employee records)."
    ),
    allowed_models={
        "hr.employee", "hr.department", "hr.job", "hr.contract",
        "hr.leave", "hr.leave.type", "hr.leave.allocation",
        "resource.calendar", "res.company",
    },
    domain_notes=(
        "- Aggregation: for counts, averages, totals or grouped stats, use the aggregate tools\n"
        "  (hr_headcount / hr_salary_stats); do not pull every row.\n"
        "- Salary: `wage` is the monthly contract amount, denominated in the company currency (SAR, Saudi Riyal).\n"
        "  Always state the currency (SAR) when reporting salary figures. state=open means an active contract,\n"
        "  draft/close are draft/ended. By default only count state=open.\n"
    ),
    attachment_scopes=[AttachmentScope(res_model="hr.employee", label="employee", name_field="name")],
    tools=_TOOLS,
)

register(HR)
