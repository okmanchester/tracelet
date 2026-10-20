"""A process with already loaded weights cannot certify changed model files."""

from types import SimpleNamespace

import pytest

from app import knowledge
from app.llm import registry


def test_changed_local_artifacts_require_process_restart(tmp_path, monkeypatch):
    root = tmp_path / "model"
    root.mkdir()
    weights = root / "weights.bin"
    weights.write_bytes(b"original synthetic weights")
    monkeypatch.setattr(knowledge.settings, "embedding_provider", "local")
    monkeypatch.setattr(knowledge.settings, "local_embedding_model_path", str(root))
    monkeypatch.setattr(registry, "get_embedding_provider", lambda: SimpleNamespace(fingerprint="same-model-name"))
    before = knowledge.embedding_config()
    assert len(before["artifact"]) == 64
    weights.write_bytes(b"new synthetic weights with different content")
    with pytest.raises(knowledge.KnowledgeError, match="changed_restart_required"):
        knowledge.embedding_config()
