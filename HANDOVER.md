# Nexora GraphRAG — 交接说明

面向接手本项目的开发者与 AI 助手。目标是让你在**同一台机器、不同账户**下
从零把整套系统跑起来，并理解代码中若干"看起来奇怪"的写法为何如此。

- 交接时间：2026-08-30
- 部署机：`10.55.223.113`（主机名 `rag.corp.aiis.sa`）
- 代码：`Nexora_graprag`（后端，**公开仓库**）、`Nexora_frontend`（前端，私有）

---

## 0. 先读这一段

**不要试图复用上一位开发者的安装成果。** 其家目录权限为 `drwxr-x---`，
`~/opt` 下的 8.8GB（conda 环境、PostgreSQL 数据、MinIO 数据、模型）你无法读取。
按第 2 节从零装一遍，约 30–60 分钟，其中大部分是下载耗时。

**本仓库是公开仓库。** 任何密码、密钥、令牌都不得写入代码或脚本，一律放
`.env`（已 gitignore）。见第 7 节的遗留风险。

---

## 1. 环境约束（决定了整套部署方式）

这台机器有四个硬性限制，是理解全部部署决策的前提：

| 限制 | 实测证据 | 后果 |
|---|---|---|
| **无 sudo** | `sudo -l` → `user may not run sudo` | 装不了任何系统包 |
| **无 Docker，且 rootless 也不可行** | `/etc/subuid` 为空、无 `newuidmap` | 仓库里所有 `docker-compose` 均不可用 |
| **无编译工具链** | 无 `gcc`/`make`/`ldap.h`/`libgomp`/`libGL` | 源码编译路径也堵死 |
| **系统 Python 3.14 且无 pip** | `python3 -V` → 3.14.4 | paddleocr/paddlepaddle/gradio 均无 3.14 轮子 |

**唯一可行路线**：micromamba（conda-forge 预编译包）+ 官方静态二进制，全程用户态。
`relateddocker/` 下的 compose 文件仅作参数参考，不要执行。

无 GPU（`nvidia-smi` 不存在），15GB 内存。所有推理服务跑 CPU。

---

## 2. 从零部署

以下命令按顺序执行。`$HOME` 指你自己的家目录。

### 2.1 micromamba

系统无 `bzip2`，用 Python 解压：

```bash
mkdir -p ~/opt/bin ~/opt/dl && cd ~/opt/dl
curl -fsSL -o micromamba.tar.bz2 https://micro.mamba.pm/api/micromamba/linux-64/latest
python3 - <<'PY'
import tarfile, shutil, os
with tarfile.open("micromamba.tar.bz2", "r:bz2") as tf:
    dst = os.path.expanduser("~/opt/bin/micromamba")
    with open(dst, "wb") as f:
        shutil.copyfileobj(tf.extractfile("bin/micromamba"), f)
    os.chmod(dst, 0o755)
PY
export MAMBA_ROOT_PREFIX=$HOME/opt/mamba
export PATH=$HOME/opt/bin:$PATH
```

### 2.2 后端运行环境

```bash
micromamba create -y -n nexora -c conda-forge \
  python=3.12 postgresql=16 pgvector redis-server python-ldap libgomp libgl zstd
```

各包用途：`python-ldap` 因系统缺 `ldap.h` 无法 pip 编译；`libgl` 提供
`libGL.so.1`——`paddlex[ocr]` 硬性要求非 headless 的 `opencv-contrib-python`，
它依赖此库（换 headless 版本会让 paddlex 的 extra 检查失败，别走这条路）。

```bash
ENV=$HOME/opt/mamba/envs/nexora
cd ~/src/Nexora_graprag
$ENV/bin/pip install -r requirements.txt
```

### 2.3 PostgreSQL

```bash
ENV=$HOME/opt/mamba/envs/nexora
export PGDATA=$HOME/opt/pgdata
mkdir -p ~/opt/logs ~/opt/run
echo '<你的DB管理员密码>' > ~/opt/run/.pgpw && chmod 600 ~/opt/run/.pgpw
$ENV/bin/initdb -D "$PGDATA" -U rag_admin --pwfile=$HOME/opt/run/.pgpw \
  -E UTF8 --locale=C.UTF-8 --auth-local=trust --auth-host=scram-sha-256
cat >> $PGDATA/postgresql.conf <<EOF
listen_addresses = '127.0.0.1'
port = 5432
shared_buffers = 512MB
EOF
sed -i "s|^unix_socket_directories.*|unix_socket_directories = '$HOME/opt/run'|" $PGDATA/postgresql.conf
$ENV/bin/pg_ctl -D "$PGDATA" -l ~/opt/logs/postgres.log -w start
```

初始化库结构（**四个脚本，顺序不可乱**）：

```bash
cd ~/src/Nexora_graprag
# 1) 建库与角色（用 superuser）
PGPASSWORD='<DB管理员密码>' $ENV/bin/psql -h 127.0.0.1 -U rag_admin -d postgres \
  -v ON_ERROR_STOP=1 -f graphfunc/000_graphrag_db_setup.sql
# 2) 扩展 —— 必须用 superuser，graphrag_owner 无权建 extension
PGPASSWORD='<DB管理员密码>' $ENV/bin/psql -h 127.0.0.1 -U rag_admin -d graphrag_db \
  -f graphfunc/001_graphrag_extensions.sql
# 3) 建表 + RLS（用 owner）
PGPASSWORD='<owner密码>' $ENV/bin/psql -h 127.0.0.1 -U graphrag_owner -d graphrag_db \
  -v ON_ERROR_STOP=1 -f graphfunc/002_graphrag_schema.sql
# 4) 权限补丁 —— 不执行则 graphrag_writer 无任何表权限，应用连 SELECT 都被拒
PGPASSWORD='<owner密码>' $ENV/bin/psql -h 127.0.0.1 -U graphrag_owner -d graphrag_db \
  -v ON_ERROR_STOP=1 -f graphfunc/003_graphrag_grants.sql
```

注意事项：
- `000` 里的默认密码务必改掉再执行。
- `001` 中 `pgjwt` 不可用会报错，可忽略（JWT 在应用层用 PyJWT 处理）。
- **`003` 是必须的**，原因见第 6 节。

验证（应为 10 表 + 3 视图、36 条 RLS 策略、向量列均为 `vector(1024)`）：

```bash
PGPASSWORD='<owner密码>' $ENV/bin/psql -h 127.0.0.1 -U graphrag_owner -d graphrag_db -tAc \
"select count(*) from pg_policies where schemaname='public';
 select c.relname||'.'||a.attname||' '||format_type(a.atttypid,a.atttypmod)
 from pg_attribute a join pg_class c on c.oid=a.attrelid
 where format_type(a.atttypid,a.atttypmod) like 'vector%';"
```

### 2.4 MinIO / Ollama

```bash
cd ~/opt/bin
curl -fsSL -o minio https://dl.min.io/server/minio/release/linux-amd64/minio && chmod +x minio
curl -fsSL -o mc     https://dl.min.io/client/mc/release/linux-amd64/mc    && chmod +x mc

cd ~/opt/dl   # Ollama 新版是 .tar.zst
curl -fsSL -o ollama.tar.zst \
  https://github.com/ollama/ollama/releases/latest/download/ollama-linux-amd64.tar.zst
mkdir -p ~/opt/ollama && zstd -d -c ollama.tar.zst | tar -x -C ~/opt/ollama
```

拉 embedding 模型（**1024 维，必须与 schema 的 `vector(1024)` 和
`EMBED_DIM` 三者一致**）：

```bash
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=$HOME/opt/ollama-home/models \
  nohup ~/opt/ollama/bin/ollama serve > ~/opt/logs/ollama.log 2>&1 &
OLLAMA_HOST=127.0.0.1:11434 ~/opt/ollama/bin/ollama pull qwen3-embedding:0.6b
```

建桶：

```bash
~/opt/bin/mc alias set nexora http://127.0.0.1:9000 <ACCESS_KEY> <SECRET_KEY> --api S3v4
~/opt/bin/mc mb --ignore-existing nexora/rag-files
```

### 2.5 Rerank / OCR 旁路服务

```bash
$ENV/bin/pip install --extra-index-url https://download.pytorch.org/whl/cpu \
  torch sentence-transformers gunicorn
```

两者由 `services.sh` 拉起，直接跑 `relateddocker/{rerank-docker,ocr}/` 下的 app。
Rerank 首次启动会下载 `BAAI/bge-reranker-base`（约 1.2GB）；
OCR 首次调用会下载 PP-OCRv5 模型。

**OCR 必须单 worker。** 实测其峰值 RSS 约 8GB，本机 15GB 内存下开两个即被
OOM killer 杀掉（表现为 OCR 静默返回空文本、入库得到 0 chunks）。
`services.sh` 已固定 `-w 1`，`.env` 中 `OCR_MAX_WORKER=1` 控制客户端并发。

---

## 3. 配置 `.env`

在仓库根目录建 `.env`（已 gitignore）。真实凭据请向交接人索取。

```bash
# ---------- LLM（外部服务，非本机部署）----------
VLM_PROVIDER=vllm
VLLM_BASE_URL=http://vllm.corp.aiis.sa:8000
MODEL_PATH=qwen3.5-9b
# qwen3.5-9b 是推理型模型：不关 thinking 则 content 返回 None
VLLM_ENABLE_THINKING=false
# 该模型 max_model_len 仅 4096，上下文 + 生成必须落在此之内
MAX_PDF_CONTEXT_CHARS=2000
MAX_TOKENS=1200
TIMEOUT=180

# ---------- PostgreSQL ----------
PG_HOST=127.0.0.1
PG_PORT=5432
PG_DB=graphrag_db
PG_USER=graphrag_writer
PG_PASSWORD=<填>
PG_SSLMODE=disable
PG_ADMIN_USER=graphrag_owner
PG_ADMIN_PASSWORD=<填>

# ---------- 多租户 / Graph ----------
RAG_APP_ID=appA
RAG_CLEARANCE=2
GRAPH_ENABLED=true

# ---------- MinIO ----------
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=<填>
MINIO_SECRET_KEY=<填>
MINIO_BUCKET=rag-files

# ---------- Embedding ----------
EMBED_ENGINE=ollama
EMBED_BASE_URL=http://127.0.0.1:11434
EMBED_MODEL=qwen3-embedding:0.6b
EMBED_DIM=1024

# ---------- Rerank / OCR ----------
RERANK_ENABLED=true
RERANK_BASE_URL=http://127.0.0.1:18010
OCR_ENDPOINT=http://127.0.0.1:18000
OCR_MAX_WORKER=1

# ---------- Redis ----------
# api_server.py 只认 REDIS_URL；密码含 '@' 必须百分号编码（@ → %40, ! → %21）
REDIS_URL=redis://:<URL编码后的密码>@127.0.0.1:6379/0
# services.sh 启动 redis-server 用的裸密码
REDIS_PASSWORD=<填>

# ---------- LDAP / JWT ----------
LDAP_HOST=ldap://ldap.corp.aiis.sa:389
LDAP_BASE_DN=dc=aiis,dc=sa
# 该库允许匿名 bind + 搜索，留空即走匿名；若启用专用账户见 ldap/04-*.ldif
LDAP_BIND_DN=
LDAP_BIND_PASSWORD=
JWT_SECRET_KEY=<填一个随机串>

DISABLE_MODEL_SOURCE_CHECK=True
```

---

## 4. 启停与验证

```bash
cd ~/src/Nexora_graprag
./services.sh start     # 基础服务：pg / minio / redis / ollama / rerank / ocr
./services.sh status
./services.sh logs ocr  # 查看某个服务日志
./start.sh              # 应用层：API + 4 个 Gradio UI
```

端口分配：

| 端口 | 服务 | | 端口 | 服务 |
|---|---|---|---|---|
| 5432 | PostgreSQL | | 19000 | FastAPI |
| 9000 / 9001 | MinIO API / 控制台 | | 7867 | 文件导入 UI |
| 6379 | Redis | | 7861 | 数据管理 UI |
| 11434 | Ollama | | 7862 | 用户问答 UI |
| 18000 | OCR | | 7863 | Graph 管理 UI |
| 18010 | Rerank | | 5173 | 前端 dev server |

冒烟验证：

```bash
curl -s http://127.0.0.1:19000/health
# 检索 + 生成全链路（需库中已有文档）
curl -s -X POST http://127.0.0.1:19000/v1/chat/send -H 'Content-Type: application/json' \
  -d '{"text":"测试问题","session_id":"smoke","rag_app_id":"appA"}'
```

---

## 5. 前端

```bash
micromamba create -y -n nexora-fe -c conda-forge nodejs=20
export PATH=$HOME/opt/mamba/envs/nexora-fe/bin:$PATH
cd ~/src/Nexora_frontend && npm install && npm run dev
```

- `.env.development` 中 `VITE_API_BASE_URL` 留空，走 `vite.config.ts` 的 proxy
  转发到 `localhost:19000`，不产生跨域。
- 用域名访问必须在 `server.allowedHosts` 登记（Vite 5.4.12+ 的 Host 校验），
  当前已放行 `rag.corp.aiis.sa`。
- `nginx.conf` 中 `proxy_pass` 指向 `10.55.223.100:9000`，**该地址不可达且端口错误**
  （后端在 19000）。配反向代理时不要照抄。

---

## 6. 代码中已修复的缺陷

理解这些能避免"优化"时把修复改回去。全部见分支
`fix/rootless-deploy-and-runtime-fixes` 的四个提交。

**依赖**：`requirements.txt` 原缺 `python-dotenv`（config.py 直接 import，
一启动即 ImportError）、缺 `python-docx`（代码 import docx）、
`paddlex` 漏写 `[ocr]` extras（PaddleOCR 初始化失败）。

**数据库**：`chunks.embedding` 原为 `vector(1536)`，与实际模型输出 1024 维不符；
`entity` 表原本**没有 `embedding` 列**，但 `graph_pg_store` 读写它；
`chunks`/`entity` 原本没有任何向量索引。

**权限（`003_graphrag_grants.sql`）**：`000` 脚本在建表之前执行，其
`GRANT ... ON ALL TABLES` 作用于当时尚不存在的表，且 `ALTER DEFAULT PRIVILEGES`
未加 `FOR ROLE`、只对执行者 `rag_admin` 生效，而表由 `graphrag_owner` 创建——
两者均落空。**重建数据库时务必执行 `003`。**

**`functions/vlmfunc.py`**：推理型模型（Qwen3.x）默认开 thinking，输出全在
`message.reasoning`，`content` 为 `None`。现按 `VLLM_ENABLE_THINKING` 注入
`chat_template_kwargs={"enable_thinking": false}`，并做两级兜底。

**`graphfunc/graph_pg_store.py`**：六处 `%s::jsonb` 直接绑 Python list/dict，
psycopg3 会将 list 适配成 PG 数组字面量 `{a,b}`，转 jsonb 即报语法错误。
统一经 `_jsonb()` 序列化。**新增 jsonb 参数时务必沿用。**

**`functions/rerank_client.py`**：原逻辑在"全部候选均低于 `RERANK_MIN_SCORE`"时
回退为返回全量结果，使阈值完全失效——与语料无关的输入（如 `hello`，
rerank 得分 0.000）也会被塞进 LLM 上下文。现改为返回空，
`chat_send` 随之不注入 `pdf_context`，模型正常对话。

**`api_server.py`**：上传用 `NamedTemporaryFile` 落盘，只保留扩展名，
导致 `doc_dir` 变成 `tmpXXXXXXXX_<uuid>`。现改为在独立临时目录内以原始
文件名落盘，并用 `_safe_upload_name()` 净化——该值会成为 MinIO object key
前缀，必须剥离路径穿越与控制字符。

---

## 7. 已知问题与待办

按优先级排列。

### 高：公开仓库中的明文凭据（尚未处理）

`8b89c93` 提交引入，至今仍在公开历史中：

| 位置 | 内容 |
|---|---|
| `config.py:92,95` | PG 密码 / PG 管理员密码默认值 |
| `config.py:154` | MinIO 凭据默认值 |
| `api_server.py:50` | Redis 密码（在 `REDIS_URL` 默认值里） |
| `api_server.py:264` | LDAP bind 密码默认值 |
| `api_server.py:347` | JWT 密钥默认值 |

其中 LDAP bind 密码对应旧库 `10.55.223.101` 上一个**仍然有效**的账号，
可列出全部 24 个用户。建议依次：① 轮换全部密码；
② 把默认值从代码中去掉，改为缺失即启动失败；③ 再考虑清理 git 历史。

### 高：`/v1/auth/refresh` 不存在，登录 8 小时后掉线

前端 `main.tsx` 启动 `setupTokenRefresh()`，token 临近过期时调该接口 →
404 → `localStorage.clear()` 跳转登录页。JWT 有效期正好 8 小时。
补一个后端路由即可（约十几行）。

### 中：Graph 查询侧实体抽取对问句失效

入库侧正常（能抽出实体、建共现边），但查询侧对问句返回 `[]`——
`config.GRAPH_ENTITY_PROMPTS` 三个提示词都写着 "from a document chunk"，
模型判定问句不该抽取。因此 Graph 增强在问答时实际未生效。
底层机制是好的：`_find_entities_hybrid` 实测能用"证监会"召回"中国证监会"
（ILIKE + 向量融合打分）。改 prompt 即可。

### 中：PDF 解析没有"纯文本层"模式

`functions/pdffunc.py:371` 只有 `not use_vlm and use_ocr` 一条分支走纯 OCR，
其余组合一律落入 VLM 分支——不勾任何选项反而强制调用 VLM。

### 中：MinIO 已暴露到局域网且仍用默认凭据

当前监听 `0.0.0.0:9000/9001`，凭据为 MinIO 默认账号。
建议建独立服务账号（仅授权 `rag-files` 桶）并改掉默认账号密码。

### 低：rerank 阈值硬编码 / 无 rerank 时无保护

`RERANK_MIN_SCORE = 0.4` 写死在 `config.py`。当前语料少、分数两极分化
（无关 0.000 vs 相关 0.998），阈值合适；语料变多后需按真实问题重新标定，
建议改为环境变量。另外该阈值是唯一的相关性闸门——`RERANK_ENABLED=false`
或 rerank 服务不可用时，检索会退回"来什么塞什么"，可在向量层补一个
距离上限兜底（余弦距离 > 1.0 即视为无关）。

### 低：前端存在未对接的死代码

`src/api/knowledge.ts` 整个文件（`/v1/documents/*` 共 5 个接口）未被任何
页面 import，`chat.ts` 中的会话管理接口（`/v1/chat/new|list|history` 等）
同样如此，后端也没有这些路由。做文档列表/删除功能时需先补后端。

---

## 8. 外部依赖

| 服务 | 地址 | 说明 |
|---|---|---|
| vLLM | `vllm.corp.aiis.sa:8000` (10.55.223.112) | 模型 `qwen3.5-9b`，`max_model_len=4096` |
| LDAP | `ldap.corp.aiis.sa:389` (10.55.223.111) | 已导入 Nexora schema 与授权树 |
| 内网 DNS | `dns01.corp.aiis.sa` (10.55.223.110) | 解析 `*.corp.aiis.sa` |

**LDAP 现状**：`ldap/` 目录下的 `01`（schema）、`02`（授权树）、`03`（用户授权条目）
已在服务器上执行完毕，`04`（只读 bind 账号）未执行——当前走匿名 bind。
已授权用户：`gcheng`(clearance=3, admin)、`gzhang`(clearance=2, user)。

**授权条目是登录的必要条件**：登录流程第 3 步会在
`ou=Users,ou=Nexora,ou=Apps` 下按 uid 查 `aiisClearance`，查不到即拒绝并
返回 `No clearance assigned to this user`——即使 `ou=People` 中密码正确。
新增可登录用户需往该子树加条目，用 `ldap/load.sh` 或参照 `03-nexora-users.ldif`。

另有一台旧 LDAP `10.55.223.101`，配置完整（24 个 People 用户、5 个已授权），
与新库用户**完全不重叠**。若需迁移那批用户，需先搬 `ou=People` 子树。

---

## 9. 数据迁移（可选）

上一位开发者的数据仅为测试内容，通常从零开始即可。确需迁移时：

```bash
# PostgreSQL
pg_dump -h 127.0.0.1 -U graphrag_owner graphrag_db | gzip > graphrag_db.sql.gz
gunzip -c graphrag_db.sql.gz | psql -h 127.0.0.1 -U graphrag_owner -d graphrag_db

# MinIO
mc mirror <源alias>/rag-files <新alias>/rag-files
```

注意跨账户时源目录不可读，需由原账户先导出到双方均可访问的位置。

---

## 10. 交接检查清单

- [ ] 从交接人处获取全部凭据（PG / MinIO / Redis / JWT），**不要沿用仓库中的默认值**
- [ ] 按第 2 节完成部署，`./services.sh status` 全绿
- [ ] 执行 `graphfunc/003_graphrag_grants.sql`，确认 `graphrag_writer` 可读写
- [ ] 确认 `EMBED_DIM` / schema `vector(N)` / 模型实际维度三者一致
- [ ] `/health` 与 `/v1/chat/send` 冒烟通过
- [ ] 前端 `npm run dev` 起得来，能登录
- [ ] 处理第 7 节"高"优先级两项
