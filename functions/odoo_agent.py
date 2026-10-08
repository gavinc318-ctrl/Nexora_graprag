"""向后兼容的重导出层。

原来的"Odoo HR 问答 Agent"已经拆成通用的 functions/odoo_skill 框架（多部门可插拔）+
functions/odoo_skill/departments/hr.py（HR 部门的具体实现）。这个文件只是保留旧的
import 路径，新代码请直接 `from functions.odoo_skill import odoo_ask, list_departments`。
"""

from functions.odoo_skill import odoo_ask, list_departments, get_department  # noqa: F401
