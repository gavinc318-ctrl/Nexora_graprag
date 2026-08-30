#!/usr/bin/env python3
"""生成 OpenLDAP 的 {SSHA} 密码哈希（slappasswd -s 的等价物）。

用法: python3 gen_ssha.py '你的密码'
输出可直接粘到 LDIF 的 userPassword 字段。
"""
import base64, hashlib, os, sys

if len(sys.argv) != 2:
    sys.exit("用法: python3 gen_ssha.py '<密码>'")

pw = sys.argv[1].encode()
salt = os.urandom(4)
digest = hashlib.sha1(pw + salt).digest()
print("{SSHA}" + base64.b64encode(digest + salt).decode())
