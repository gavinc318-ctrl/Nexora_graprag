"""每接入一个新业务部门，就在这里加一行 import（该模块内部会 register() 自己）。

新增部门的步骤：
1. 复制 hr.py 为模板，写一份 <dept>.py：声明 allowed_models / attachment_scopes /
   scope_description / domain_notes，以及这个部门专属的 ToolDef 查询工具。
2. 在下面加一行 `from . import <dept>  # noqa: F401`。
3. 完事——不用改 odoo_skill/__init__.py、api_server.py 或前端，
   新部门会自动出现在 GET /v1/odoo/departments 里，前端 Data Source 下拉框会自动列出来。
"""

from . import hr  # noqa: F401
