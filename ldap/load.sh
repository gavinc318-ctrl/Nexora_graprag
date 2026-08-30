#!/usr/bin/env bash
# =====================================================================
# 把 Nexora 的 LDAP 配置加载到目标服务器
#
#   ./load.sh check              只做只读预检，不写入任何东西
#   ./load.sh tree               加载 02（OU 骨架）+ 03（用户授权条目）
#   ./load.sh bind               加载 04（可选的只读 bind 账号）
#   ./load.sh verify             加载后验证
#
# schema（01）不在此脚本内：它写的是 cn=config，远程通常没权限，
# 需要在 LDAP 服务器本机执行：
#   sudo ldapadd -Y EXTERNAL -H ldapi:/// -f 01-nexora-schema.ldif
#
# 环境变量（都有默认值）：
#   LDAP_URI      默认 ldap://ldap.corp.aiis.sa
#   LDAP_BASE     默认 dc=aiis,dc=sa
#   LDAP_ADMIN_DN 默认 cn=admin,dc=aiis,dc=sa
# =====================================================================
set -uo pipefail
cd "$(dirname "$0")"

URI="${LDAP_URI:-ldap://ldap.corp.aiis.sa}"
BASE="${LDAP_BASE:-dc=aiis,dc=sa}"
ADMIN="${LDAP_ADMIN_DN:-cn=admin,$BASE}"
NEXORA_USERS="ou=Users,ou=Nexora,ou=Apps,$BASE"

say() { printf "  %-42s %s\n" "$1" "$2"; }

check() {
  echo "预检 $URI"
  ldapsearch -x -H "$URI" -b "" -s base namingContexts >/dev/null 2>&1 \
    && say "服务器连通" "✅" || { say "服务器连通" "❌ 连不上"; exit 1; }

  ldapsearch -x -H "$URI" -b "$BASE" -s base dn >/dev/null 2>&1 \
    && say "匿名可读 $BASE" "✅" || say "匿名可读 $BASE" "⚠️  不可读（需带凭据）"

  if ldapsearch -x -H "$URI" -b cn=Subschema -s base objectClasses 2>/dev/null \
       | grep -q "nexoraUser"; then
    say "schema: nexoraUser" "✅ 已存在"
  else
    say "schema: nexoraUser" "❌ 缺失 —— 必须先加载 01-nexora-schema.ldif"
  fi

  if ldapsearch -x -H "$URI" -b "$NEXORA_USERS" -s base dn >/dev/null 2>&1; then
    say "授权树 ou=Users,ou=Nexora" "✅ 已存在"
  else
    say "授权树 ou=Users,ou=Nexora" "❌ 缺失 —— 需加载 02-nexora-tree.ldif"
  fi

  n=$(ldapsearch -x -H "$URI" -b "$NEXORA_USERS" "(objectClass=nexoraUser)" dn 2>/dev/null \
       | grep -c "^dn:")
  say "已授权用户数" "$n"
}

add() {
  f="$1"
  [ -f "$f" ] || { echo "找不到 $f"; exit 1; }
  echo ">>> ldapadd $f  (as $ADMIN)"
  ldapadd -x -H "$URI" -D "$ADMIN" -W -f "$f"
  rc=$?
  case $rc in
    0)  echo "    ✅ 成功" ;;
    68) echo "    ⚠️  条目已存在（Already exists），可忽略" ;;
    21) echo "    ❌ Invalid syntax: objectClass value invalid per syntax"
        echo "       → schema 没装。nexoraUser 未定义时 OpenLDAP 报的是 21 而非 65。"
        echo "       → 到 LDAP 服务器本机执行:"
        echo "         sudo ldapadd -Y EXTERNAL -H ldapi:/// -f 01-nexora-schema.ldif" ;;
    65) echo "    ❌ Object class violation —— 条目缺 MUST 属性（nexoraUser 必须有 uid）" ;;
    32) echo "    ❌ No such object —— 父节点不存在，先加载 02-nexora-tree.ldif" ;;
    49) echo "    ❌ Invalid credentials —— 检查 LDAP_ADMIN_DN 与密码" ;;
    50) echo "    ❌ Insufficient access —— 该账号没有写权限" ;;
    *)  echo "    ❌ ldapadd 退出码 $rc" ;;
  esac
  return $rc
}

verify() {
  echo "验证 $URI"
  ldapsearch -x -H "$URI" -b cn=Subschema -s base objectClasses 2>/dev/null \
    | grep -q nexoraUser && say "schema nexoraUser" "✅" || say "schema nexoraUser" "❌"
  echo
  echo "  已授权用户："
  ldapsearch -x -H "$URI" -b "$NEXORA_USERS" "(objectClass=nexoraUser)" \
      uid aiisClearance nexoraStatus nexoraRole 2>/dev/null \
    | awk '/^uid:/{u=$2} /^aiisClearance:/{c=$2} /^nexoraStatus:/{s=$2} /^nexoraRole:/{r=$2; printf "    uid=%-10s clearance=%-2s status=%-8s role=%s\n",u,c,s,r}'
}

case "${1:-check}" in
  check)  check ;;
  tree)   add 02-nexora-tree.ldif; add 03-nexora-users.ldif ;;
  bind)   grep -q "REPLACE_WITH_HASH" 04-nexora-bind-account.ldif && {
            echo "❌ 04-nexora-bind-account.ldif 里的 userPassword 还是占位符。"
            echo "   先执行: python3 gen_ssha.py '你的密码'  并替换 {SSHA}REPLACE_WITH_HASH"; exit 1; }
          add 04-nexora-bind-account.ldif ;;
  verify) verify ;;
  *) sed -n '3,12p' "$0"; exit 1 ;;
esac
