"""网页版 Demo（web/app.py）：页面、接口、参数校验、限流与 HTML 转义。全部用 mock，不消耗 API 额度。"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("markdown")

from fastapi.testclient import TestClient  # noqa: E402

from stardew_planner.llm_client import MockClient  # noqa: E402
from web.app import Settings, create_app, render_html  # noqa: E402

BODY = {"playstyle": "种田极限赚钱", "pacing": "速通极限", "seasons": ["spring"]}


def make_client(indexed_store, client=None, **overrides):
    settings = Settings()
    settings.provider = "mock"
    settings.rate_limit = 0
    for k, v in overrides.items():
        setattr(settings, k, v)
    app = create_app(settings, client=client or MockClient(), guide_store=indexed_store, warm_up=False)
    return TestClient(app)


def test_index_page(indexed_store):
    with make_client(indexed_store) as c:
        r = c.get("/")
        assert r.status_code == 200 and "星露谷物语 AI 攻略生成器" in r.text


def test_options(indexed_store):
    with make_client(indexed_store) as c:
        o = c.get("/api/options").json()
    assert o["is_mock"] and o["max_seasons"] == 4
    assert {p["key"] for p in o["professions"]} == {"tiller", "artisan", "agriculturist"}
    kinds = {f["key"]: f["kind"] for f in o["fertilizers"]}
    assert kinds["speed_gro"] == "speed" and kinds["deluxe_fertilizer"] == "quality"


def test_generate_single_season(indexed_store):
    with make_client(indexed_store) as c:
        r = c.post("/api/generate", json=BODY)
    assert r.status_code == 200
    data = r.json()
    assert "第 1 年春季攻略" in data["markdown"] and "<table>" in data["html"]
    (s,) = data["seasons"]
    assert (s["season"], s["start_gold"], s["end_gold"], s["attempts"]) == ("spring", 500, 2424, 1)


def test_generate_multi_season_with_bonuses(indexed_store):
    body = {**BODY, "seasons": ["spring", "summer", "fall"], "professions": ["artisan"],
            "fertilizer": "deluxe_fertilizer", "farming_level": 10, "processing_share": 0.5}
    with make_client(indexed_store) as c:
        data = c.post("/api/generate", json=body).json()
    assert [s["season"] for s in data["seasons"]] == ["spring", "summer", "fall"]
    assert data["seasons"][1]["carry"] == ["玉米"]
    assert data["seasons"][1]["start_gold"] == data["seasons"][0]["end_gold"]   # 资金衔接
    assert "跨季连续规划" in data["markdown"] and "耕种等级 10" in data["markdown"]


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"seasons": ["spring", "fall"]}, "季节必须连续"),
        ({"professions": ["artisan"], "farming_level": 6}, "耕种等级 6 还不能选择工匠"),
        ({"fertilizer": "magic_dust"}, "未知肥料"),
        ({"seasons": ["spring", "summer", "fall"]}, "最多规划 2 个季节"),
    ],
)
def test_bad_requests(indexed_store, patch, message):
    with make_client(indexed_store, max_seasons=2) as c:
        r = c.post("/api/generate", json={**BODY, **patch})
    assert r.status_code == 400 and message in r.json()["detail"]


@pytest.mark.parametrize("patch", [{"playstyle": ""}, {"farming_level": 20}, {"processing_share": 1.5}, {"seasons": []}])
def test_schema_validation(indexed_store, patch):
    with make_client(indexed_store) as c:
        assert c.post("/api/generate", json={**BODY, **patch}).status_code == 422


def test_rate_limit(indexed_store):
    with make_client(indexed_store, rate_limit=1) as c:
        assert c.post("/api/generate", json=BODY).status_code == 200
        r = c.post("/api/generate", json=BODY)
    assert r.status_code == 429 and "每小时最多 1 次" in r.json()["detail"]


class BrokenClient:
    def complete(self, system, messages):
        raise RuntimeError("API 超时")


def test_model_failure_returns_502(indexed_store):
    with make_client(indexed_store, client=BrokenClient()) as c:
        r = c.post("/api/generate", json=BODY)
    assert r.status_code == 502 and "API 超时" in r.json()["detail"]


def test_html_from_model_is_escaped():
    html = render_html('# 标题\n\n<script>alert(1)</script>\n\n[点我](javascript:alert(1))\n\n> 引用\n\n| a | b |\n|---|---|\n| 1 | 2 |')
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "javascript:" not in html
    assert "<blockquote>" in html and "<table>" in html     # Markdown 的引用和表格不受影响


@pytest.mark.parametrize(
    "url",
    ["java&#115;cript:alert(1)", "JaVaScRiPt:alert(1)", "java&#x09;script:alert(1)", "&#106;avascript:alert(1)",
     "data:text/html,x", "vbscript:x", "//evil.example/x", "/relative"],
)
def test_unsafe_links_lose_href_but_keep_text(url):
    html = render_html(f"[点我]({url})")
    assert "href" not in html and "<a" not in html and "点我" in html


def test_external_images_are_not_rendered():
    html = render_html("开头 ![a](http://evil.example/t.png) 结尾")
    assert "<img" not in html and "evil.example" not in html and "开头" in html and "结尾" in html


def test_safe_links_are_kept_and_hardened():
    html = render_html("[wiki](https://stardewvalleywiki.com/Crops?a=1&b=2)")
    assert 'href="https://stardewvalleywiki.com/Crops?a=1&amp;b=2"' in html
    assert 'rel="noopener noreferrer nofollow"' in html and html.count("</a>") == 1


def test_attributes_cannot_be_injected():
    html = render_html('[x](https://a.example/"onmouseover="alert(1))')
    assert 'onmouseover="' not in html


def test_table_alignment_survives():
    html = render_html("| a | b |\n|:--|--:|\n| 1 | 2 |")
    assert "text-align: left;" in html and "text-align: right;" in html


# ---------------------------------------------------------------------------
# 模型客户端：超时、拒答、配置错误
# ---------------------------------------------------------------------------

def test_real_provider_gets_web_timeout(indexed_store, monkeypatch):
    import web.app as web_app

    seen = []

    def fake_get_client(provider, model, **options):
        seen.append((provider, options))
        return MockClient()

    monkeypatch.setattr(web_app, "get_client", fake_get_client)
    settings = Settings()
    settings.provider, settings.rate_limit, settings.request_timeout = "anthropic", 0, 42.0
    app = create_app(settings, guide_store=indexed_store, warm_up=False)
    with TestClient(app) as c:
        assert c.post("/api/generate", json=BODY).status_code == 200
        assert c.post("/api/generate", json=BODY).status_code == 200
    assert seen == [("anthropic", {"timeout": 42.0, "max_retries": 0})]     # 只创建一次


class RefusingClient:
    def complete(self, system, messages):
        from stardew_planner.llm_client import ModelRefusal

        raise ModelRefusal("cyber")


def test_refusal_returns_422(indexed_store):
    with make_client(indexed_store, client=RefusingClient()) as c:
        r = c.post("/api/generate", json=BODY)
    assert r.status_code == 422 and "模型拒绝生成" in r.json()["detail"]


def test_client_config_error_returns_500_and_frees_slot(indexed_store, monkeypatch):
    import web.app as web_app

    def broken(provider, model, **options):
        raise ValueError("环境变量 ANTHROPIC_TIMEOUT 必须是数字")

    monkeypatch.setattr(web_app, "get_client", broken)
    settings = Settings()
    settings.provider, settings.rate_limit, settings.max_concurrent = "anthropic", 0, 1
    app = create_app(settings, guide_store=indexed_store, warm_up=False)
    with TestClient(app) as c:
        first = c.post("/api/generate", json=BODY)
        second = c.post("/api/generate", json=BODY)
    assert first.status_code == 500 and "服务端模型配置有误" in first.json()["detail"]
    assert second.status_code == 500                                        # 名额已释放，不是 503
