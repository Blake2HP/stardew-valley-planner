"""默认向量库只初始化一次：网页预加载线程和请求同时调用时也一样。"""

import threading
import time

from stardew_planner import data_loader


class SlowStore:
    """模拟建库很慢（加载 Embedding 模型），记录被创建了几次。"""

    created = 0

    def __init__(self):
        SlowStore.created += 1
        time.sleep(0.2)

    def index(self, chunks):
        return {"added": len(chunks), "removed": 0, "total": len(chunks)}


def run_concurrently(fn, n=8):
    barrier = threading.Barrier(n)
    results = []

    def worker():
        barrier.wait()
        results.append(fn())

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_store_is_built_once_under_concurrency(monkeypatch):
    SlowStore.created = 0
    monkeypatch.setattr(data_loader, "GuideStore", SlowStore)
    monkeypatch.setattr(data_loader, "_default_store", None)
    monkeypatch.setattr(data_loader, "_vector_disabled", False)
    results = run_concurrently(data_loader.get_guide_store)
    assert SlowStore.created == 1
    assert len({id(r) for r in results}) == 1                 # 所有调用拿到同一个库


def test_failure_is_recorded_once(monkeypatch):
    calls = []

    class Broken:
        def __init__(self):
            calls.append(1)
            time.sleep(0.1)
            raise RuntimeError("模型下载失败")

    monkeypatch.setattr(data_loader, "GuideStore", Broken)
    monkeypatch.setattr(data_loader, "_default_store", None)
    monkeypatch.setattr(data_loader, "_vector_disabled", False)
    results = run_concurrently(data_loader.get_guide_store)
    assert results == [None] * 8 and len(calls) == 1          # 失败后改用关键词检索，不反复重试
    assert data_loader._vector_disabled is True


def test_fallback_chunks_loaded_once(monkeypatch):
    loads = []

    def slow_load(path):
        loads.append(path)
        time.sleep(0.1)
        return []

    monkeypatch.setattr(data_loader, "load_guides", slow_load)
    monkeypatch.setattr(data_loader, "_fallback_chunks", None)
    run_concurrently(data_loader.get_fallback_chunks)
    assert len(loads) == 1
