"""反向代理后的真实 IP：只信任指定代理传来的 X-Forwarded-For，限流按真实用户计算。"""

import pytest

pytest.importorskip("fastapi")
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # noqa: E402

import web.__main__ as web_main  # noqa: E402
from stardew_planner.llm_client import MockClient  # noqa: E402
from web.app import Settings, create_app  # noqa: E402

BODY = {"playstyle": "种田", "pacing": "稳健", "seasons": ["spring"]}


def app_with_limit(indexed_store):
    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 1
    return create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)


def post(client, ip):
    return client.post("/api/generate", json=BODY, headers={"X-Forwarded-For": ip}).status_code


def test_trusted_proxy_limits_each_user_separately(indexed_store):
    # TestClient 的连接地址是 "testclient"，把它当成受信任的代理
    app = ProxyHeadersMiddleware(app_with_limit(indexed_store), trusted_hosts=["testclient"])
    with TestClient(app) as c:
        assert post(c, "1.1.1.1") == 200
        assert post(c, "2.2.2.2") == 200          # 另一个用户，不受第一个人的次数影响
        assert post(c, "1.1.1.1") == 429          # 同一个用户第二次才被限流


def test_untrusted_sender_cannot_spoof_ip(indexed_store):
    # 连接方不在信任列表里：X-Forwarded-For 被忽略，换 IP 也绕不过限流
    app = ProxyHeadersMiddleware(app_with_limit(indexed_store), trusted_hosts=["10.0.0.5"])
    with TestClient(app) as c:
        assert post(c, "1.1.1.1") == 200
        assert post(c, "2.2.2.2") == 429


@pytest.mark.parametrize("argv, env, expected", [
    ([], None, None),                                              # 交给 uvicorn 默认（只信任 127.0.0.1）
    (["--forwarded-allow-ips", "10.0.0.5"], None, "10.0.0.5"),
    ([], "10.0.0.6,10.0.0.7", "10.0.0.6,10.0.0.7"),
])
def test_cli_passes_trusted_proxies(monkeypatch, argv, env, expected):
    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: seen.update(kw))
    if env is None:
        monkeypatch.delenv("DEMO_FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("DEMO_FORWARDED_ALLOW_IPS", env)
    web_main.main(argv)
    assert seen["proxy_headers"] is True and seen["forwarded_allow_ips"] == expected
