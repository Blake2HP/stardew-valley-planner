"""网页版 Demo：一个页面 + 几个接口，调用现有的 generate_strategy / generate_plan，不改业务代码。

/api/import-save 只解析上传的存档、返回表单默认值，不保存文件（DEMO_MAX_SAVE_MB 限制大小，默认 20MB）。

/api/saves/local 和 /api/saves/local/load 读取运行这个服务的电脑上的星露谷存档，会暴露服务器的文件，所以默认只在
本机运行时开放：python -m web 只监听 127.0.0.1 / localhost / ::1 时自动开启，并且只接受本机发来的请求；
DEMO_ALLOW_LOCAL_SAVES=1 强制开启（不限来源），=0 强制关闭。读取时只认扫描到的存档文件夹名，防止目录穿越。

运行：
    python -m web                         # http://127.0.0.1:8000，默认用 mock（不消耗 API 额度）
    DEMO_PROVIDER=anthropic python -m web  # 用真实模型（需要 ANTHROPIC_API_KEY）

环境变量：
    DEMO_PROVIDER       mock / anthropic（默认 mock）
    DEMO_MODEL          覆盖默认模型名
    DEMO_RATE_LIMIT     每个 IP 每小时最多生成几次（默认：mock 不限，真实模型 10 次）
    DEMO_MAX_SEASONS    一次最多规划几个季节（默认 4）
    DEMO_MAX_CONCURRENT 同时进行的生成数（默认 2），超过时直接返回“繁忙”，避免费用失控
    DEMO_MAX_SAVE_MB    上传存档的大小上限（默认 20）
    DEMO_ALLOW_LOCAL_SAVES  1 / 0：强制开启或关闭“读取本机存档”（默认只在本机监听时开启）
    DEMO_SAVES_DIR      本机存档根目录（默认按操作系统找）
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Literal
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
from stardew_planner.achievements import parse_progress, resolve_goals
from stardew_planner.coop import MAX_PLAYERS, ROLE_WORDS, parse_roles
from stardew_planner.data_loader import SEASON_ZH, get_guide_store, load_game_data
from stardew_planner.generator import parse_seasons
from stardew_planner.options import PlanOptions
from stardew_planner.livestock import parse_animals
from stardew_planner.llm_client import ModelRefusal, get_client
from stardew_planner.playstyles import load_presets
from stardew_planner.save_parser import (
    SAVE_DIR_HINTS, SaveParseError, discover_local_saves, find_local_save, parse_save,
)

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
        self.max_save_bytes = int(float(os.getenv("DEMO_MAX_SAVE_MB", "20")) * 1024 * 1024)
        # 读取本机存档：off / local（只接受本机请求）/ on（不限来源）
        flag = os.getenv("DEMO_ALLOW_LOCAL_SAVES", "").strip().lower()
        if flag in ("1", "true", "yes", "on"):
            self.local_saves = "on"
        elif flag in ("0", "false", "no", "off"):
            self.local_saves = "off"
        else:
            self.local_saves = "local" if os.getenv("DEMO_HOST", "") in LOOPBACK else "off"
        self.saves_dirs = [d for d in os.getenv("DEMO_SAVES_DIR", "").split(os.pathsep) if d] or None


LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# 不让网页用户改的规划参数（用默认值）：自动修正轮数直接影响模型调用次数和费用
SERVER_SIDE_OPTIONS = {"revise_rounds"}


# ---------------------------------------------------------------------------
# 请求与限流
# ---------------------------------------------------------------------------

class LocalSaveRequest(BaseModel):
    folder: str = Field(min_length=1, max_length=200)


class InGroundCrop(BaseModel):
    crop: str = Field(min_length=1, max_length=20)
    tiles: int = Field(ge=1, le=10000)
    ready_day: int = Field(ge=1, le=28 * 8)
    place: Literal["field", "greenhouse", "island"] = "field"


class GenerateRequest(BaseModel):
    playstyle: str = Field(min_length=1, max_length=100)
    pacing: str = Field(min_length=1, max_length=50)
    seasons: list[str] = Field(min_length=1)
    year: int = Field(default=1, ge=1, le=10)
    start_day: int = Field(default=1, ge=1, le=28)  # 季中重新规划：今天是第一个季节的第几天
    professions: list[str] = Field(default_factory=list, max_length=3)
    fertilizer: str | None = None
    farming_level: int | None = Field(default=None, ge=0, le=14)
    processing_share: float = Field(default=1.0, ge=0, le=1)
    start_gold: int | None = Field(default=None, ge=0, le=100_000_000)
    farm_type: str | None = Field(default=None, max_length=30)
    kegs: int = Field(default=0, ge=0, le=1000)
    jars: int = Field(default=0, ge=0, le=1000)
    bee_houses: int = Field(default=0, ge=0, le=1000)
    animals: str | None = Field(default=None, max_length=100)
    owned_animals: str | None = Field(default=None, max_length=200)  # 已经养着的成年动物（存档导入）
    in_ground: list[InGroundCrop] = Field(default_factory=list, max_length=200)  # 地里已有的作物（存档导入）
    route: Literal["community_center"] | None = None
    location: Literal["pelican_town", "ginger_island"] = "pelican_town"
    greenhouse: bool = False
    players: int = Field(default=1, ge=1, le=MAX_PLAYERS)
    roles: dict[str, str] = Field(default_factory=dict, max_length=MAX_PLAYERS)  # {"P1": "种田"}，空值忽略
    achievements: list[str] = Field(default_factory=list, max_length=60)  # 成就 key 或名字
    achievement_progress: str | None = Field(default=None, max_length=1000)  # 如 "防风草=15,万人迷=12"


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
_NESTED_ITEM = re.compile(r"^  (?=- )", re.M)  # 联机日程的子列表缩进 2 格；Python-Markdown 要 4 格才算嵌套
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
    text = _NESTED_ITEM.sub("    ", markdown_text.replace("<", "&lt;"))
    sanitizer.feed(md.markdown(text, extensions=["tables"]))
    sanitizer.close()
    return "".join(sanitizer.out)


def season_summary(result) -> dict:
    f, s = result.finance, result.end_state
    return {
        "season": result.season,
        "season_zh": SEASON_ZH[result.season],
        "year": result.year,
        "start_day": result.start_day,
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
        presets = load_presets()
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
            "playstyles": [p.as_dict() for p in presets.playstyles],
            "pacings": [p.as_dict() for p in presets.pacings],
            "max_players": MAX_PLAYERS,
            "roles": list(ROLE_WORDS),  # 常用分工（程序认得的关键词），也可以自己填
            "achievements": [{"key": a.key, "name": a.name, "name_en": a.name_en, "description": a.description,
                              "category": a.category, "checked": a.checked}
                             for a in (data().achievement_rules.achievements if data().achievement_rules else ())],
        }

    def local_saves_allowed(request: Request) -> None:
        """没开启、或者 local 模式下请求不是本机发来的：当作接口不存在（404），不透露任何信息。"""
        mode = settings.local_saves
        client_ip = request.client.host if request.client else ""
        if mode == "off" or (mode == "local" and client_ip not in LOOPBACK):
            raise HTTPException(404, "Not Found")

    def import_result(save) -> dict:
        return {
            "defaults": save.form_defaults(data().achievement_rules),
            "summary": save.summary_lines(data()),
            "warnings": save.warnings,
        }

    @app.get("/api/saves/guide")
    def saves_guide():
        """“找不到存档文件？”指引：各系统的默认存档位置（不涉及服务器文件，公网也能用）。"""
        return {"dirs": SAVE_DIR_HINTS}

    @app.get("/api/saves/local")
    def local_saves(request: Request):
        local_saves_allowed(request)
        return {"saves": [s.as_dict() for s in discover_local_saves(settings.saves_dirs)]}

    @app.post("/api/saves/local/load")
    def load_local_save(req: LocalSaveRequest, request: Request):
        local_saves_allowed(request)
        try:
            found = find_local_save(req.folder, settings.saves_dirs)  # 只认扫描到的文件夹名，防止目录穿越
            if found.path.stat().st_size > settings.max_save_bytes:
                raise HTTPException(413, f"存档文件太大：最多 {settings.max_save_bytes / 1024 / 1024:g}MB")
            save = parse_save(found.path, data())
        except SaveParseError as e:
            raise HTTPException(400, str(e)) from e
        return import_result(save)

    @app.post("/api/import-save")
    async def import_save(request: Request):
        """请求体是存档文件本身（application/octet-stream）。只在内存里解析，返回表单默认值和摘要，不落盘。"""
        limit = settings.max_save_bytes
        too_big = HTTPException(413, f"存档文件太大：最多 {limit / 1024 / 1024:g}MB")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise too_big
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise too_big
            chunks.append(chunk)
        if not size:
            raise HTTPException(400, "没有收到存档文件")
        try:
            save = parse_save(b"".join(chunks), data())
        except SaveParseError as e:
            raise HTTPException(400, str(e)) from e
        return import_result(save)

    @app.post("/api/generate")
    def generate(req: GenerateRequest, request: Request):
        try:
            plan = parse_seasons(req.seasons, req.year)
            resolve_bonuses(req.professions, req.fertilizer, farming_level=req.farming_level)
            data().resolve_farm_type(req.farm_type)
            roles = {k: v for k, v in req.roles.items() if v.strip()}
            if any(len(v) > 30 for v in roles.values()):
                raise ValueError("每个玩家的分工最多 30 个字")
            roles = parse_roles(roles, req.players)
            parse_animals(req.owned_animals, data().animals)
            goals = resolve_goals(req.achievements, data().achievement_rules)
            parse_progress(req.achievement_progress, data().achievement_rules, data().crops)
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
            options = PlanOptions.pick(  # 和 PlanOptions 字段同名的请求字段直接取；需要整理的另外给
                req, animals=req.animals or None, owned_animals=req.owned_animals or None,
                in_ground=[c.model_dump() for c in req.in_ground] or None, roles=roles,
                achievements=[g.achievement.name for g in goals], achievement_progress=req.achievement_progress or None,
            )
            kwargs = dict(data=data(), client=model_client, provider=settings.provider, model=settings.model,
                          guide_store=guide_store, options=options)
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
            "players": req.players,
            "roles": roles,
            "achievements": [g.achievement.name for g in goals],
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "failed": failed,  # 跨季规划中途失败时：哪一季、原因；已完成的季节照常返回
        }

    @app.get("/api/health")
    def health():
        return {"ok": True, "provider": settings.provider}

    return app


app = create_app()
