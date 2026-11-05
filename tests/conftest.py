import hashlib

import numpy as np
import pytest

from stardew_planner import data_loader
from stardew_planner.data_loader import load_game_data

chromadb = pytest.importorskip("chromadb")
from chromadb.api.types import EmbeddingFunction  # noqa: E402
from chromadb.config import Settings  # noqa: E402


class BigramEmbedding(EmbeddingFunction):
    """测试用的确定性 Embedding：把中文字符二元组哈希到固定维度。

    不下载模型、不联网，结果稳定；共享词语越多的文本，余弦距离越小。
    """

    DIM = 256

    def __init__(self) -> None:
        pass

    def __call__(self, input):
        vectors = []
        for text in input:
            v = np.zeros(self.DIM, dtype=np.float32)
            chars = [c for c in text if not c.isspace()]
            for a, b in zip(chars, chars[1:]):
                v[int(hashlib.md5((a + b).encode()).hexdigest(), 16) % self.DIM] += 1.0
            norm = np.linalg.norm(v)
            vectors.append(v / norm if norm else v)
        return vectors

    @staticmethod
    def name() -> str:
        return "test-bigram"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return BigramEmbedding()


def make_store(path):
    client = chromadb.PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))
    return data_loader.GuideStore(client=client, embedding_function=BigramEmbedding())


@pytest.fixture(scope="session")
def data():
    return load_game_data()


@pytest.fixture(scope="session")
def crops(data):
    return {c.name: c for c in data.crops}


@pytest.fixture(scope="session")
def indexed_store(tmp_path_factory):
    """已写入 data/guides 全部片段的向量库（临时目录，测试结束即丢弃）。"""
    store = make_store(tmp_path_factory.mktemp("chroma_session"))
    store.index(data_loader.load_guides(data_loader.DEFAULT_GUIDE_DIR))
    return store


@pytest.fixture
def empty_store(tmp_path):
    return make_store(tmp_path / "chroma")


@pytest.fixture(autouse=True)
def _isolate_default_store(monkeypatch, indexed_store):
    """测试中不读写 data/.chroma_db，也不下载 Embedding 模型。"""
    monkeypatch.setattr(data_loader, "_default_store", indexed_store)
    monkeypatch.setattr(data_loader, "_vector_disabled", False)
