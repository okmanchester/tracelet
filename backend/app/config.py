# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""应用配置：从环境变量 / .env 读取，集中管理。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ----- Provider 选择：可插拔，改 .env 后重启生效 -----
    llm_chat_provider: str = "deepseek"    # deepseek | dashscope | openai_compat
    embedding_provider: str = "local"      # local | dashscope | openai_compat
    rerank_provider: str = "local"         # local | dashscope
    llm_timeout_seconds: float = 60.0      # Chat 请求超时（秒）

    # ----- Chat: DeepSeek（OpenAI 兼容）-----
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_small: str = "deepseek-flash"
    deepseek_model_large: str = "deepseek-v4-pro"

    # ----- Embedding: 本地 BGE / 维度与 Milvus collection 一致 -----
    embedding_dim: int = 1024
    local_embedding_model_path: str = "D:/WorkSpace/AIModels/BAAI/bge-large-zh-v1.5"
    local_embedding_device: str = "auto"   # auto | cpu | cuda
    local_embedding_batch_size: int = 16
    local_embedding_query_prefix: str = "为这个句子生成表示以用于检索相关文章："

    # ----- Rerank: 本地 BGE cross-encoder -----
    local_rerank_model_path: str = "D:/WorkSpace/AIModels/BAAI/bge-reranker-base"
    local_rerank_device: str = "auto"      # auto | cpu | cuda
    local_rerank_batch_size: int = 16

    # ----- 可选备用：DashScope -----
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model_small: str = "qwen-turbo"
    dashscope_model_large: str = "qwen-plus"
    dashscope_embedding_model: str = "text-embedding-v3"
    dashscope_rerank_model: str = "gte-rerank-v2"

    # ----- 可选备用：任意 OpenAI 兼容厂商 -----
    openai_compat_api_key: str = ""
    openai_compat_base_url: str = ""
    openai_compat_model_small: str = ""
    openai_compat_model_large: str = ""
    openai_compat_embedding_model: str = ""

    # ----- MySQL -----
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    mysql_user: str = "devsupport"
    mysql_password: str = "devsupport123"
    mysql_db: str = "devsupport"

    # ----- Milvus -----
    milvus_uri: str = "http://localhost:19531"
    milvus_collection: str = "knowledge_chunk"

    # ----- Redis -----
    redis_url: str = "redis://localhost:6380/0"

    # ----- 认证 -----
    jwt_secret: str = "change-me-in-production-please"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    # ----- 业务阈值 -----
    intent_confidence_threshold: float = 0.6   # 意图置信度低于此值触发澄清追问
    rag_score_threshold: float = 0.3           # rerank 分达标即判文档命中
    rag_vec_hit_threshold: float = 0.45        # 向量余弦达标即判文档命中（与上者取其一）
    tool_timeout_seconds: float = 3.0          # 单次工具调用超时
    semantic_cache_sim_threshold: float = 0.95  # 语义缓存命中所需的最低相似度

    # ----- 其它 -----
    app_env: str = "dev"
    log_level: str = "INFO"
    conversation_lease_seconds: int = Field(default=90, ge=3)
    conversation_heartbeat_seconds: int = Field(default=20, ge=1)
    conversation_run_seconds: int = Field(default=300, ge=1)
    cache_timeout_seconds: float = Field(default=1.0, gt=0)
    effect_worker_enabled: bool = True

    @property
    def mysql_dsn_async(self) -> str:
        return (
            f"mysql+aiomysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def mysql_dsn_sync(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
