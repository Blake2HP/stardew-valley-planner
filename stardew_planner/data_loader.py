"""数据加载模块。

两类数据：
1. 结构化数值（JSON）：作物、加工倍率、季节日历。
2. 非结构化攻略文本（Markdown）：按 `## 标题` 切块，每块可带一行 `标签: a, b`。
   用简单的关键词打分做检索（后续可替换为向量检索）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

SEASON_ZH = {"spring": "春季", "summer": "夏季", "fall": "秋季", "winter": "冬季"}
_SEASON_ALIASES = {
    **{k: k for k in SEASON_ZH},
    **{v: k for k, v in SEASON_ZH.items()},
    "春": "spring", "夏": "summer", "秋": "fall", "冬": "winter", "autumn": "fall",
}


def normalize_season(season: str) -> str:
    key = _SEASON_ALIASES.get(season.strip().lower())
    if key is None:
        raise ValueError(f"未知季节: {season!r}，可选: spring/summer/fall/winter 或 春季/夏季/秋季/冬季")
    return key


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Crop:
    name: str
    name_en: str
    seasons: tuple[str, ...]
    seed_price: int
    sell_price: int
    grow_days: int
    regrow_days: int | None
    category: str  # fruit / vegetable / flower / special
    harvest_yield: float = 1.0
    seed_source: str = "皮埃尔杂货店"


@dataclass(frozen=True)
class ArtisanRule:
    machine: str
    product: str
    input_category: str
    multiplier: float
    bonus: int
    fixed_price: int | None
    processing_days: float

    def price_for(self, crop: Crop) -> int:
        if self.fixed_price is not None:
            return self.fixed_price
        return int(crop.sell_price * self.multiplier + self.bonus)


@dataclass(frozen=True)
class CalendarEvent:
    day: int
    name: str
    type: str
    tip: str


@dataclass(frozen=True)
class GuideChunk:
    source: str
    title: str
    text: str
    tags: tuple[str, ...]


@dataclass
class GameData:
    crops: list[Crop]
    artisan_rules: list[ArtisanRule]
    calendar: dict[str, list[CalendarEvent]]
    guide_chunks: list[GuideChunk] = field(default_factory=list)

    # ---- 结构化查询 ----
    def crops_in_season(self, season: str) -> list[Crop]:
        season = normalize_season(season)
        return [c for c in self.crops if season in c.seasons]

    def events_in_season(self, season: str) -> list[CalendarEvent]:
        return sorted(self.calendar.get(normalize_season(season), []), key=lambda e: e.day)

    def artisan_rules_for(self, crop: Crop) -> list[ArtisanRule]:
        return [r for r in self.artisan_rules if r.input_category == crop.category]

    # ---- 非结构化检索 ----
    def retrieve_guides(self, query: str, season: str | None = None, k: int = 4) -> list[GuideChunk]:
        """关键词检索：标签命中 query 记 3 分，季节标签命中记 2 分，query 中的词出现在正文记 1 分。"""
        season_zh = SEASON_ZH[normalize_season(season)] if season else None
        terms = [t for t in re.split(r"[\s,，、/；;。]+", query) if len(t) >= 2]

        def score(chunk: GuideChunk) -> int:
            s = sum(3 for tag in chunk.tags if tag in query)
            if season_zh and season_zh in chunk.tags:
                s += 2
            body = chunk.title + chunk.text
            s += sum(1 for t in terms if t in body)
            return s

        ranked = sorted(self.guide_chunks, key=score, reverse=True)
        return [c for c in ranked[:k] if score(c) > 0]


# ---------------------------------------------------------------------------
# 加载函数
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_crops(path: Path) -> list[Crop]:
    raw = _read_json(path)["crops"]
    return [Crop(**{**item, "seasons": tuple(item["seasons"])}) for item in raw]


def load_artisan_rules(path: Path) -> list[ArtisanRule]:
    return [ArtisanRule(**item) for item in _read_json(path)["rules"]]


def load_calendar(path: Path) -> dict[str, list[CalendarEvent]]:
    raw = _read_json(path)
    return {
        season: [CalendarEvent(**e) for e in events]
        for season, events in raw.items()
        if not season.startswith("_")
    }


_TAG_LINE = re.compile(r"^\s*(?:标签|tags)\s*[:：]\s*(.+)$", re.IGNORECASE)


def parse_guide_markdown(text: str, source: str) -> list[GuideChunk]:
    chunks: list[GuideChunk] = []
    for section in re.split(r"^##\s+", text, flags=re.MULTILINE):
        section = section.strip()
        if not section:
            continue
        title, _, body = section.partition("\n")
        tags: tuple[str, ...] = ()
        body_lines = []
        for line in body.splitlines():
            m = _TAG_LINE.match(line)
            if m and not tags:
                tags = tuple(t.strip() for t in re.split(r"[,，、]", m.group(1)) if t.strip())
            else:
                body_lines.append(line)
        chunks.append(GuideChunk(source, title.strip(), "\n".join(body_lines).strip(), tags))
    return chunks


def load_guides(guide_dir: Path) -> list[GuideChunk]:
    chunks: list[GuideChunk] = []
    for path in sorted(guide_dir.glob("*.md")) + sorted(guide_dir.glob("*.txt")):
        chunks.extend(parse_guide_markdown(path.read_text(encoding="utf-8"), path.name))
    return chunks


def load_game_data(data_dir: str | Path = DEFAULT_DATA_DIR) -> GameData:
    data_dir = Path(data_dir)
    return GameData(
        crops=load_crops(data_dir / "crops.json"),
        artisan_rules=load_artisan_rules(data_dir / "artisan.json"),
        calendar=load_calendar(data_dir / "calendar.json"),
        guide_chunks=load_guides(data_dir / "guides"),
    )
