"""跨季规划中途失败：保留已经生成的季节，并说明从哪一季开始缺失。"""

import pytest

from stardew_planner import generate_plan
from stardew_planner.llm_client import MockClient

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from web.app import Settings, create_app  # noqa: E402


class FailOnCall(MockClient):
    """第 fail_at 次调用抛出异常，其余照常返回 mock 结果。"""

    def __init__(self, fail_at):
        super().__init__()
        self.fail_at, self.calls = fail_at, 0

    def complete(self, system, messages):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("API 超时")
        return super().complete(system, messages)


def plan(data, store, client, seasons="spring,summer,fall"):
    return generate_plan("种田", "稳健", seasons, client=client, data=data, guide_store=store)


def test_complete_plan_has_no_failure(data, indexed_store):
    out = plan(data, indexed_store, FailOnCall(fail_at=99))
    assert out.complete and out.failed_season is None and len(out.results) == 3
    assert "生成失败" not in out.markdown


def test_later_failure_keeps_finished_seasons(data, indexed_store):
    client = FailOnCall(fail_at=2)
    out = plan(data, indexed_store, client)
    assert client.calls == 2                                   # 失败后不再调用
    assert not out.complete and out.failed_season == ("summer", 1)
    assert [r.season for r in out.results] == ["spring"] and "API 超时" in out.error
    assert out.planned == [("spring", 1), ("summer", 1), ("fall", 1)]
    assert "第 1 年夏季生成失败" in out.markdown and "缺少：第 1 年夏季、第 1 年秋季" in out.markdown
    assert "## 🌸 星露谷物语 · 第 1 年春季攻略" in out.markdown


def test_failure_in_third_season(data, indexed_store):
    out = plan(data, indexed_store, FailOnCall(fail_at=3))
    assert out.failed_season == ("fall", 1) and len(out.results) == 2
    assert out.states[-1].next_season == "fall"               # 可以从失败的季节接着生成


def test_first_season_failure_still_raises(data, indexed_store):
    with pytest.raises(RuntimeError, match="API 超时"):
        plan(data, indexed_store, FailOnCall(fail_at=1))


def test_web_returns_partial_plan(data, indexed_store):
    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=FailOnCall(fail_at=2), guide_store=indexed_store, warm_up=False)
    with TestClient(app) as c:
        r = c.post("/api/generate", json={"playstyle": "种田", "pacing": "稳健", "seasons": ["spring", "summer"]})
    assert r.status_code == 200
    body = r.json()
    assert [s["season"] for s in body["seasons"]] == ["spring"]
    assert body["failed"]["season"] == "summer" and "API 超时" in body["failed"]["error"]


def test_web_complete_plan_has_no_failed_field(data, indexed_store):
    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    with TestClient(app) as c:
        body = c.post("/api/generate", json={"playstyle": "种田", "pacing": "稳健", "seasons": ["spring", "summer"]}).json()
    assert body["failed"] is None
