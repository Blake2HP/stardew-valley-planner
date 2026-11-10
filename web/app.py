"""网页版 Demo：一个页面 + 两个接口，调用现有的 generate_strategy / generate_plan，不改业务代码。

运行：
    python -m web                         # http://127.0.0.1:8000，默认用 mock（不消耗 API 额度）
    DEMO_PROVIDER=anthropic python -m web  # 用真实模型（需要 ANTHROPIC_API_KEY）

环境变量：
    DEMO_PROVIDER       mock / anthropic（默认 mock）
    DEMO_MODEL          覆盖默认模型名
    DEMO_RATE_LIMIT     每个 IP 每小时最多生成几次（默认：mock 不限，真实模型 10 次）
    DEMO_MAX_SEASONS    一次最多规划几个季节（默认 4）
    DEMO_MAX_CONCURRENT 同时进行的生成数（默认 2），超过时直接返回“繁忙”，避免费用失控
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import markdown as md
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from stardew_planner import generate_plan, generate_strategy
from stardew_planner.bonuses import load_bonus_rules, resolve_bonuses
from stardew_planner.data_loader import SEASON_ZH, get_guide_store, load_game_data
from stardew_planner.generator import parse_seasons
from stardew_planner.llm_client import ModelRefusal, get_client

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

STATIC_DIR = Path(__file__).resolve().parent / "static"


class Settings:
    def __init__(self) -> None:
        self.provider = os.getenv("DEMO_PROVIDER", "mock").lower()
        self.model = os.getenv("DEMO_MODEL") or None
        default_limit = "0" if self.provider == "mock" else "10"
        self.rate_limit = int(os.getenv("DEMO_RATE_LIMIT", default_limit))  # 0 = 不限
        self.max_seasons = int(os.getenv("DEMO_MAX_SEASONS", "4"))
        self.max_concurrent = int(os.getenv("DEMO_MAX_CONCURRENT", "2"))
        # 单次模型调用的超时（秒）；网页里不让 SDK 再自动重试，避免一个卡住的请求长期占用生成名额
        self.request_timeout = float(os.getenv("DEMO_REQUEST_TIMEOUT", "300"))


# ---------------------------------------------------------------------------
# 请求与限流
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    playstyle: str = Field(min_length=1, max_length=100)
    pacing: str = Field(min_length=1, max_length=50)
    seasons: list[str] = Field(min_length=1)
    year: int = Field(default=1, ge=1, le=10)
    professions: list[str] = Field(default_factory=list, max_length=3)
    fertilizer: str | None = None
    farming_level: int | None = Field(default=None, ge=0, le=14)
    processing_share: float = Field(default=1.0, ge=0, le=1)
    start_gold: int | None = Field(default=None, ge=0, le=100_000_000)
    farm_type: str | None = Field(default=None, max_length=30)
    kegs: int = Field(default=0, ge=0, le=1000)
    jars: int = Field(default=0, ge=0, le=1000)
    bee_houses: int = Field(default=0, ge=0, le=1000)
    greenhouse: bool = False


class RateLimiter:
    """每个 IP 一小时内最多 limit 次（内存实现，重启清零；Demo 够用）。"""

    def __init__(self, limit: int, window: float = 3600) -> None:
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


ALLOWED_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
                "blockquote", "strong", "em", "code", "pre", "hr", "br", "a"}
VOID_TAGS = {"hr", "br"}
_TEXT_ALIGN = re.compile(r"text-align:\s*(left|right|center);?")
_URL_NOISE = re.compile(r"[\x00-\x20\x7f]+")


def _safe_url(url: str | None) -> str | None:
    """只放行 http / https 的绝对地址。HTMLParser 已解码实体，浏览器会忽略的空白和控制字符也先去掉再判断。"""
    cleaned = _URL_NOISE.sub("", url or "")
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return None
    return cleaned if parts.scheme.lower() in ("http", "https") and parts.netloc else None


class _Sanitizer(HTMLParser):
    """按白名单重建 HTML：其余标签和属性全部丢弃（保留文字），图片不渲染，避免加载外部资源。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.dropped_links = 0

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            return
        attrs = dict(attrs)
        extra = ""
        if tag == "a":
            url = _safe_url(attrs.get("href"))
            if url is None:
                self.dropped_links += 1
                return
            extra = f' href="{escape(url, quote=True)}" rel="noopener noreferrer nofollow" target="_blank"'
        elif tag in ("th", "td") and (m := _TEXT_ALIGN.fullmatch((attrs.get("style") or "").strip())):
            extra = f' style="text-align: {m.group(1)};"'
        self.out.append(f"<{tag}{extra}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag == "a" and self.dropped_links:  # 对应的 <a> 没有输出
            self.dropped_links -= 1
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(escape(data, quote=False))


def render_html(markdown_text: str) -> str:
    """Markdown → 安全的 HTML：先转义“<”（不影响 Markdown 的表格和引用），再按白名单清洗。"""
    sanitizer = _Sanitizer()
    sanitizer.feed(md.markdown(markdown_text.replace("<", "&lt;"), extensions=["tables"]))
    sanitizer.close()
    return "".join(sanitizer.out)


def season_summary(result) -> dict:
    f, s = result.finance, result.end_state
    return {
        "season": result.season,
        "season_zh": SEASON_ZH[result.season],
        "year": result.year,
        "attempts": result.attempts,
        "json_parse_method": result.json_parse_method,
        "start_gold": f.start_gold if f else None,
        "end_gold": f.end_gold if f else None,
        "end_gold_estimated": f.end_gold_estimated if f else None,
        "carry": [c.crop.name for c in s.carry] if s else [],
        "season_warnings": result.season_warnings,
        "gift_warnings": result.gift_warnings,
    }


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------

def create_app(settings: Settings | None = None, *, client=None, guide_store=None, warm_up: bool = True) -> FastAPI:
    """client / guide_store 供测试注入；默认按 settings.provider 创建客户端、使用默认攻略库。"""
    settings = settings or Settings()
    state: dict = {"data": None}
    limiter = RateLimiter(settings.rate_limit)
    busy = threading.BoundedSemaphore(max(1, settings.max_concurrent))

    @asynccontextmanager
    async def lifespan(_app):
        state["data"] = load_game_data()
        if warm_up and guide_store is None:
            # 提前加载向量库和 Embedding 模型，避免第一个用户等太久
            threading.Thread(target=get_guide_store, daemon=True).start()
        yield

    app = FastAPI(title="星露谷物语 AI 攻略生成器 Demo", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def data():
        if state["data"] is None:
            state["data"] = load_game_data()
        return state["data"]

    def llm():
        """整个应用共用一个客户端；真实模型带上网页专用的超时，并关闭 SDK 自动重试。"""
        if client is not None:
            return client
        if state.get("client") is None:
            options = {} if settings.provider == "mock" else {"timeout": settings.request_timeout, "max_retries": 0}
            state["client"] = get_client(settings.provider, settings.model, **options)
        return state["client"]

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/options")
    def options():
        rules = load_bonus_rules()
        return {
            "provider": settings.provider,
            "is_mock": settings.provider == "mock",
            "max_seasons": settings.max_seasons,
            "rate_limit_per_hour": settings.rate_limit,
            "seasons": [{"key": k, "name": v} for k, v in SEASON_ZH.items()],
            "professions": [{"key": k, "name": p.name_zh, "level": p.level, "requires": p.requires,
                             "excludes": list(p.excludes), "note": p.note} for k, p in rules.professions.items()],
            "farm_types": [{"key": k, "name": v["name_zh"]} for k, v in data().farm_types.items()],
            "fertilizers": [{"key": k, "name": f.name_zh,
                             "kind": "quality" if f.quality_level else "speed", "source": f.source}
                            for k, f in rules.fertilizers.items()],
        }

    @app.post("/api/generate")
    def generate(req: GenerateRequest, request: Request):
        try:
            plan = parse_seasons(req.seasons, req.year)
            resolve_bonuses(req.professions, req.fertilizer, farming_level=req.farming_level)
            data().resolve_farm_type(req.farm_type)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if len(plan) > settings.max_seasons:
            raise HTTPException(400, f"一次最多规划 {settings.max_seasons} 个季节")
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            raise HTTPException(429, f"生成次数太多了：每小时最多 {settings.rate_limit} 次，请稍后再试")
        if not busy.acquire(blocking=False):
            raise HTTPException(503, "服务器正忙，请稍后再试")

        started = time.perf_counter()
        failed = None
        try:
            try:
                model_client = llm()
            except Exception as e:  # 服务端配置问题（缺 API key、环境变量写错），不是用户输入的问题
                raise HTTPException(500, f"服务端模型配置有误：{type(e).__name__}: {e}"[:300]) from e
            kwargs = dict(
                data=data(), client=model_client, provider=settings.provider, model=settings.model,
                guide_store=guide_store, professions=req.professions, fertilizer=req.fertilizer,
                farming_level=req.farming_level, processing_share=req.processing_share, start_gold=req.start_gold,
                farm_type=req.farm_type, kegs=req.kegs, jars=req.jars, greenhouse=req.greenhouse,
                bee_houses=req.bee_houses,
            )
            if len(plan) == 1:
                results = [generate_strategy(req.playstyle, req.pacing, plan[0][0], plan[0][1], **kwargs)]
                markdown_text = results[0].markdown
            else:
                out = generate_plan(req.playstyle, req.pacing, [s for s, _ in plan], req.year, **kwargs)
                results, markdown_text = out.results, out.markdown
                if out.failed_season is not None:
                    season, year = out.failed_season
                    failed = {"season": season, "season_zh": SEASON_ZH[season], "year": year, "error": out.error}
        except HTTPException:
            raise
        except ModelRefusal as e:
            raise HTTPException(422, str(e)[:300]) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # 模型调用失败等：给前端一个可读的错误
            raise HTTPException(502, f"生成失败：{type(e).__name__}: {e}"[:300]) from e
        finally:
            busy.release()

        return {
            "markdown": markdown_text,
            "html": render_html(markdown_text),
            "seasons": [season_summary(r) for r in results],
            "provider": settings.provider,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "failed": failed,  # 跨季规划中途失败时：哪一季、原因；已完成的季节照常返回
        }

    @app.get("/api/health")
    def health():
        return {"ok": True, "provider": settings.provider}

    return app


app = create_app()
