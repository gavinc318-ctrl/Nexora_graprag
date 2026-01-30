# =========================
# 配置
# =========================
import os
from dotenv import load_dotenv
load_dotenv()

def _env_str(key: str, default: str) -> str:
    val = os.getenv(key)
    return default if val is None or val == "" else val

def _env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default

def _env_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default

def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "y", "on")

DISABLE_MODEL_SOURCE_CHECK = _env_bool("DISABLE_MODEL_SOURCE_CHECK", True)

# LLM/VLM 服务（部署相关）
VLLM_BASE_URL = _env_str("VLLM_BASE_URL", "http://localhost:8000")
CHAT_COMPLETIONS_URL = _env_str("CHAT_COMPLETIONS_URL", f"{VLLM_BASE_URL}/v1/chat/completions")
MODEL_PATH = _env_str("MODEL_PATH", "/models/Qwen3-VL-8B-Instruct")
VLM_PROVIDER = _env_str("VLM_PROVIDER", "vllm")  # vllm | openai

# OpenAI Responses API (if VLM_PROVIDER == "openai")
OPENAI_API_KEY = _env_str("OPENAI_API_KEY", "")
OPENAI_RESPONSES_URL = _env_str("OPENAI_RESPONSES_URL", "https://api.openai.com/v1/responses")
OPENAI_MODEL = _env_str("OPENAI_MODEL", "gpt-5")

MAX_PDF_CONTEXT_CHARS = 8000
PDF_CHUNK_SIZE = 4000
PDF_CHUNK_OVERLAP = 200


# chunk 方式：sliding（固定滑窗） / recursive（递归按段落/标点）
PDF_CHUNK_METHOD = "recursive"

# 弱规则检测表格/图形关键词（可按业务扩展）
TABLE_KEYWORDS = ["Table", "TABLE", "جدول", "表"]
FIGURE_KEYWORDS = ["Figure", "FIGURE", "Chart", "CHART", "شكل", "رسم", "图"]

# 表格/图形块最大入库长度（避免极端页导致 embedding/上下文过大）
SPECIAL_BLOCK_MAX_CHARS = 20000

# 召回后、rerank 前合并同页 chunk 的最大长度
PRE_RERANK_MERGE_MAX_CHARS = 9000
PDF_TOP_K_CHUNKS = 3

TEMPERATURE = _env_float("TEMPERATURE", 0.3)
TOP_P = _env_float("TOP_P", 0.9)
MAX_TOKENS = _env_int("MAX_TOKENS", 3072)
TIMEOUT = _env_int("TIMEOUT", 120)

SYSTEM_PROMPT = (
    "你是一个严谨、实用的多模态助手。"
    "如果用户上传了图片，请基于图片进行回答。"
    "如果用户上传了PDF，请基于提供的PDF摘录内容回答。"
    "如果信息不足，请直接说不足并告诉用户需要什么信息。"
)

# ===== PostgreSQL 连接（部署相关）=====
PG_HOST = _env_str("PG_HOST", "10.55.223.100")
PG_PORT = _env_int("PG_PORT", 5432)
PG_DB = _env_str("PG_DB", "rag")
PG_USER = _env_str("PG_USER", "rag_writer")
PG_PASSWORD = _env_str("PG_PASSWORD", "Nexora@123!")
PG_SSLMODE = _env_str("PG_SSLMODE", "disable")   # 内网开发可 disable；生产建议 require/verify-full
PG_ADMIN_USER = _env_str("PG_ADMIN_USER", "rag_admin")          # 或表 owner
PG_ADMIN_PASSWORD = _env_str("PG_ADMIN_PASSWORD", "Admin@123!")



# ===== RLS（多应用/密级隔离）=====
RAG_APP_ID = _env_str("RAG_APP_ID", "appA")
RAG_CLEARANCE = _env_int("RAG_CLEARANCE", 2)

# ===== Graph（开关/任务）=====
GRAPH_ENABLED = _env_bool("GRAPH_ENABLED", False)
GRAPH_JOB_POLL_INTERVAL = _env_int("GRAPH_JOB_POLL_INTERVAL", 10)

# ===== Graph entity extraction prompts =====
GRAPH_ENTITY_PROMPTS = {
    "strict": (
        "You are extracting high-value entities from a document chunk.\n"
        "Rules:\n"
        "- Return at most 3 entities.\n"
        "- Only extract high-value entities: terms, regulations, organizations, systems, products, metrics.\n"
        "- Output JSON array only. No extra text.\n"
        "- Each entity: {\"name\":..., \"type\":..., \"aliases\":[], \"confidence\":\"high|medium|low\"}\n"
    ),
    "medium_it": (
        "You are extracting useful IT/telecom entities from a document chunk.\n"
        "Rules:\n"
        "- Return at most 5 entities.\n"
        "- Prefer medium-salience professional terms: protocols, standards, systems, platforms, products, metrics, organizations.\n"
        "- Skip generic words unless they are domain terms.\n"
        "- Output JSON array only. No extra text.\n"
        "- Each entity: {\"name\":..., \"type\":..., \"aliases\":[], \"confidence\":\"high|medium|low\"}\n"
    ),
    "loose": (
        "You are extracting entities from a document chunk or a user query.\n"
        "The content may describe daily life, personal activities, events, or plans.\n"
        "Rules:\n"
        "- Return at most 8 entities.\n"
        "- Be lenient: include meaningful entities from everyday life, such as:\n"
        "  • activities and sports (e.g. running, swimming, walking)\n"
        "  • data or records (e.g. running data, sleep records, exercise logs)\n"
        "  • plans or schedules (e.g. training plan, future plan)\n"
        "  • events, tasks, habits, or goals\n"
        "  • domain concepts, datasets, metrics, organizations, products if present\n"
        "- If the text is short or informal, extract the main noun phrases the user is referring to.\n"
        "- Output JSON array only. No extra text.\n"
        "- Each entity: {\"name\":..., \"type\":..., \"aliases\":[], \"confidence\":\"high|medium|low\"}\n"
    )
}

GRAPH_ENTITY_PROMPT_DEFAULT = "strict"

# ===== 入库元数据（可选，不写也有默认值）=====
DEFAULT_CLASSIFICATION = 1
PARSER_VER = "pymupdf+ocr+vlm"




# ===== S3 对象存储设置（部署相关）=====
MINIO_ENDPOINT = _env_str("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = _env_str("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = _env_str("MINIO_SECRET_KEY", "Admin123!")
MINIO_BUCKET = _env_str("MINIO_BUCKET", "rag-files")


# =========================
#  Qwen3 Embedding 配置 vllm or ollama（部署相关）
# =========================
EMBED_ENGINE = _env_str("EMBED_ENGINE", "ollama")
if EMBED_ENGINE == "vllm":
    _default_embed_base = "http://127.0.0.1:11434"
    _default_embed_model = "/models/Qwen3-Embedding-0.6B"
else:
    _default_embed_base = "http://10.55.223.100:11434"
    _default_embed_model = "qwen3-embedding:8b"
EMBED_BASE_URL = _env_str("EMBED_BASE_URL", _default_embed_base)
EMBED_MODEL = _env_str("EMBED_MODEL", _default_embed_model)
EMBED_DIM = _env_int("EMBED_DIM", 1536)
# ===== 向量维度（必须与 chunks.embedding 的 vector(N) 一致）=====




# =========================
# Rerank（Docker 服务）配置
# =========================
RERANK_ENABLED = _env_bool("RERANK_ENABLED", True)

# rerank 对外暴露端口（避免与 vLLM 的 8000 冲突）
RERANK_BASE_URL = _env_str("RERANK_BASE_URL", "http://localhost:18010")
RERANK_HEALTH_URL = f"{RERANK_BASE_URL}/health"
RERANK_API_URL = f"{RERANK_BASE_URL}/rerank"

# 启动主程序时，如果 rerank 没起来，是否自动拉起 docker compose
RERANK_AUTO_START = _env_bool("RERANK_AUTO_START", False)

# docker-compose 文件路径（你按实际目录放置）
# 例如你之前创建的是 /home/usr/rerank-docker/docker-compose.yml
RERANK_COMPOSE_FILE = _env_str("RERANK_COMPOSE_FILE", "/home/usr/rerank-docker/docker-compose.yml")

# rerank 二次排序：召回候选数量（先从 PG 取更多，再用 rerank 排 top_k）
RERANK_CANDIDATES = 10

# Graph/Vector 双路召回：候选数量（静态配额）
GRAPH_CHUNK_CANDIDATES = _env_int("GRAPH_CHUNK_CANDIDATES", 20)
VECTOR_CHUNK_CANDIDATES = _env_int("VECTOR_CHUNK_CANDIDATES", 40)

# rerank 分数阈值（None 表示不做阈值过滤）
RERANK_MIN_SCORE = 0.4

# rerank 请求超时
RERANK_TIMEOUT = _env_int("RERANK_TIMEOUT", 30)

# =========================
# OCR（Docker 服务）配置
# =========================
OCR_MAX_WORKER = _env_int("OCR_MAX_WORKER", 6)
OCR_MAX_TABLES = _env_int("OCR_MAX_TABLES", 3)
OCR_ENDPOINT = _env_str("OCR_ENDPOINT", "http://127.0.0.1:18000")
# =========================
# chunk服务配置
# =========================
TABLE_KEYWORDS = ["Table", "TABLE", "جدول"]
FIGURE_KEYWORDS = ["Figure", "FIGURE", "Chart", "CHART", "شكل", "رسم"]
META_PREFIX = "[[META"



# =========================
# DOCX 文件控制参数
# # =========================
SOFT_LIMIT_CHARS = 1200
HARD_LIMIT_CHARS = 2000
NEW_PAGE_ON_HEADING_LEQ = 3
MAX_HEADING_LEVEL_IN_PATH = 6
