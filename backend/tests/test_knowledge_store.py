"""SDK boundary: physical cursors and float32 buffers are not public artifact data."""

import json
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.rag import store


@pytest.mark.parametrize("count", [0, 1])
def test_snapshot_closes_iterator_and_normalizes_physical_rows(monkeypatch, count):
    row = {"pk": 123, "embedding": [np.float32(1.0), np.float32(0.0)], "content": "safe",
           "doc_title": "title", "section": "section", "category": "category", "error_code": "",
           "version": "a"*32, "doc_id": "01", "chunk_id": "b"*64}
    batches = iter([[row], []])
    closed = []
    iterator = SimpleNamespace(next=lambda: next(batches), close=lambda: closed.append(True))
    fields = [{"name": key, "params": {"dim": 2} if key == "embedding" else {}} for key in row]
    def query_iterator(*args, **kwargs):
        assert "pk" in kwargs["output_fields"] and kwargs["consistency_level"] == "Strong"
        return iterator
    client = SimpleNamespace(describe_collection=lambda *args, **kwargs: {"fields": fields},
        list_indexes=lambda *args, **kwargs: ["embedding"],
        describe_index=lambda *args, **kwargs: {"index_type": "HNSW", "metric_type": "COSINE"},
        load_collection=lambda *args, **kwargs: None, get_load_state=lambda *args, **kwargs: {"state": 3},
        query_iterator=query_iterator, query=lambda *args, **kwargs: [{"count(*)": count}])
    monkeypatch.setattr(store, "get_client", lambda: client)
    monkeypatch.setattr(store.settings, "embedding_dim", 2)
    if count == 0:
        with pytest.raises(ValueError, match="incomplete"):
            store.snapshot("candidate", deadline=time.monotonic()+1)
    else:
        result = store.snapshot("candidate", deadline=time.monotonic()+1)
        assert "pk" not in result[0]
        assert json.loads(json.dumps(result)) == result
        assert type(result[0]["embedding"][0]) is float
    assert closed == [True]
