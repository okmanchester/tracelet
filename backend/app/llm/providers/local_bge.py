# @author: 公众号：IT杨秀才
# @doc: 后端，AI Agent知识进阶，后端、AI大模型、场景题面试大全：https://golangstar.cn/
"""本地 BGE provider：bge-large-zh-v1.5 做 Embedding，bge-reranker-base 做 Rerank。

特点：
- 模型懒加载，进程启动时不占用显存；首次调用时才加载。
- torch / sentence-transformers 延迟导入，未安装本地依赖时给出明确提示。
- 推理放在线程池（asyncio.to_thread），并用锁串行化，避免 8GB 显卡并发 OOM。
- query 与 document 分开编码：BGE 中文检索建议只给 query 加指令前缀。
"""

import asyncio
import inspect
import threading
from pathlib import Path

from app.config import settings
from app.llm.base import LLMConfigurationError
from app.llm.registry import register_embedding_provider, register_rerank_provider

_INSTALL_HINT = "本地模型依赖未安装，请执行：pip install -e \".[local]\""


def _resolve_device(pref: str) -> str:
    """auto -> 有 CUDA 用 CUDA，否则 CPU；显式 cuda 但不可用时直接报错。"""
    pref = (pref or "auto").strip().lower()
    try:
        import torch
    except ImportError as e:
        raise LLMConfigurationError(_INSTALL_HINT) from e
    if pref == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if pref == "cuda" and not torch.cuda.is_available():
        raise LLMConfigurationError(
            "配置了 LOCAL_*_DEVICE=cuda，但 torch 检测不到可用 CUDA；"
            "请安装 CUDA 版 torch 或改为 auto/cpu"
        )
    return pref


def _load_sentence_transformer(path: str, device: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise LLMConfigurationError(_INSTALL_HINT) from e
    if not Path(path).is_dir():
        raise LLMConfigurationError(f"本地 embedding 模型目录不存在: {path}")
    try:
        return SentenceTransformer(path, device=device, local_files_only=True)
    except TypeError:  # 兼容旧版 sentence-transformers 不识别该参数
        return SentenceTransformer(path, device=device)


def _load_cross_encoder(path: str, device: str):
    try:
        import torch
        from sentence_transformers import CrossEncoder
    except ImportError as e:
        raise LLMConfigurationError(_INSTALL_HINT) from e
    if not Path(path).is_dir():
        raise LLMConfigurationError(f"本地 rerank 模型目录不存在: {path}")

    # 不同 ST 版本激活函数参数名不同：activation_fn / default_activation_function。
    params = inspect.signature(CrossEncoder.__init__).parameters
    activation_kw = "activation_fn" if "activation_fn" in params else "default_activation_function"
    kwargs = {"device": device, "local_files_only": True, activation_kw: torch.nn.Sigmoid()}
    try:
        return CrossEncoder(path, **kwargs)
    except TypeError:
        kwargs.pop("local_files_only", None)
        return CrossEncoder(path, **kwargs)


class LocalBGEEmbeddingProvider:
    """SentenceTransformer 本地向量化。"""

    def __init__(
        self,
        *,
        model_path: str,
        device: str = "auto",
        batch_size: int = 16,
        query_prefix: str = "",
        expected_dim: int | None = None,
    ) -> None:
        self.name = "local"
        self.model_path = model_path
        self.device = device
        self.batch_size = max(1, batch_size)
        self.max_batch_size = self.batch_size
        self.query_prefix = query_prefix
        self.expected_dim = expected_dim
        self.fingerprint = f"local:{Path(model_path).name}"
        self._model = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            device = _resolve_device(self.device)
            model = _load_sentence_transformer(self.model_path, device)
            # sentence-transformers 6.x 重命名了该方法，保留旧名兼容
            if hasattr(model, "get_embedding_dimension"):
                dim = model.get_embedding_dimension()
            else:
                dim = model.get_sentence_embedding_dimension()
            if self.expected_dim and dim != self.expected_dim:
                raise LLMConfigurationError(
                    f"embedding 模型维度 {dim} 与 EMBEDDING_DIM={self.expected_dim} 不一致；"
                    "请修改 .env 并重建 Milvus collection（python -m scripts.ingest_knowledge）"
                )
            self._model = model
            return model

    def _encode(self, texts: list[str], *, is_query: bool) -> list[list[float]]:
        model = self._load()
        prepared = [self.query_prefix + t for t in texts] if is_query and self.query_prefix else texts
        with self._infer_lock:
            vectors = model.encode(
                prepared,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return vectors.astype("float32").tolist()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts, is_query=False)

    async def embed_query(self, text: str) -> list[float]:
        vectors = await asyncio.to_thread(self._encode, [text], is_query=True)
        return vectors[0]


class LocalBGERerankProvider:
    """CrossEncoder 本地精排，Sigmoid 输出 0..1 相关性分数。"""

    def __init__(
        self,
        *,
        model_path: str,
        device: str = "auto",
        batch_size: int = 16,
    ) -> None:
        self.name = "local"
        self.model_path = model_path
        self.device = device
        self.batch_size = max(1, batch_size)
        self._model = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            device = _resolve_device(self.device)
            self._model = _load_cross_encoder(self.model_path, device)
            return self._model

    def _predict(self, query: str, documents: list[str], top_n: int | None) -> list[dict]:
        import numpy as np

        model = self._load()
        pairs = [(query, doc) for doc in documents]
        with self._infer_lock:
            raw = model.predict(
                pairs,
                batch_size=self.batch_size,
                show_progress_bar=False,
            )
        scores = np.asarray(raw, dtype=float).reshape(-1).tolist()
        ranked = [
            {"index": i, "score": max(0.0, min(1.0, float(s)))}
            for i, s in enumerate(scores)
        ]
        ranked.sort(key=lambda x: (-x["score"], x["index"]))
        return ranked[:top_n] if top_n and top_n > 0 else ranked

    async def rerank(
        self, query: str, documents: list[str], top_n: int | None = None
    ) -> list[dict]:
        if not documents:
            return []
        return await asyncio.to_thread(self._predict, query, documents, top_n)


def _build_local_embedding() -> LocalBGEEmbeddingProvider:
    return LocalBGEEmbeddingProvider(
        model_path=settings.local_embedding_model_path,
        device=settings.local_embedding_device,
        batch_size=settings.local_embedding_batch_size,
        query_prefix=settings.local_embedding_query_prefix,
        expected_dim=settings.embedding_dim,
    )


def _build_local_rerank() -> LocalBGERerankProvider:
    return LocalBGERerankProvider(
        model_path=settings.local_rerank_model_path,
        device=settings.local_rerank_device,
        batch_size=settings.local_rerank_batch_size,
    )


register_embedding_provider("local", _build_local_embedding)
register_rerank_provider("local", _build_local_rerank)
