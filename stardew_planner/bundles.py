"""社区中心献祭（--route community_center）：本季收获的作物里，先留出收集包要用的那几个，不拿去卖。

数据见 data/bundles.json（wiki Bundles，标准收集包）。规则：
- 只管 crops.json 里有的作物；工艺室的采集类收集包（葡萄等）通常靠野外采集，只做说明、不强制；
- “必须交”的物品：所在收集包的格数等于物品数（每样都要），例如春季作物收集包的防风草、染料收集包的向日葵；
  物品比格数多的（高品质作物收集包 4 选 3、每样 5 个金星）只做提示；
- 预留：本季这种作物的期望收获个数 ≥ 需要的个数时算留下了，按直接卖价从收入里扣掉（不卖）；
- 校验：本季是这种作物当年最后一个能种的季节（例如玉米夏秋都能种，秋季才报），季末还没留下时报
  “⚠️ 献祭校验”，交给模型修正。之前季节已经留下的不再要求。
"""

from __future__ import annotations

from dataclasses import dataclass

from .bonuses import Bonuses
from .data_loader import SEASON_ZH, Crop

SEASON_DAYS = 28
ROUTES = ("community_center",)
FORAGING_ROOM = "Crafts Room"


@dataclass(frozen=True)
class BundleItem:
    name_en: str
    count: int
    quality: str = "normal"


@dataclass(frozen=True)
class Bundle:
    name: str
    name_en: str
    room: str
    room_en: str
    slots: int
    items: tuple[BundleItem, ...]

    @property
    def needs_all(self) -> bool:
        return self.slots >= len(self.items)


@dataclass(frozen=True)
class CropNeed:
    bundle: Bundle
    crop: Crop
    count: int
    quality: str
    required: bool  # 必须交（收集包每样都要）；False 表示可以从几样里挑

    @property
    def label(self) -> str:
        q = "金星" if self.quality == "gold" else ""
        return f"{self.crop.name} ×{self.count}{q}（{self.bundle.name}）"


@dataclass
class Reservation:
    need: CropNeed
    harvested: float   # 本季期望收获个数（金星需求时为期望金星及以上个数）
    reserved: bool
    value: int = 0     # 留下这些作物少卖的钱


def resolve_route(route: str | None) -> str | None:
    if route in (None, "", "none"):
        return None
    key = str(route).strip().lower().replace("-", "_")
    if key in ("community_center", "社区中心", "cc"):
        return "community_center"
    raise ValueError(f"不支持的路线：{route}（可选：community_center）")


def crop_needs(bundles: list[Bundle], crops: list[Crop]) -> list[CropNeed]:
    by_en = {c.name_en: c for c in crops}
    out = []
    for b in bundles:
        for item in b.items:
            crop = by_en.get(item.name_en)
            if crop is None:
                continue
            out.append(CropNeed(b, crop, item.count, item.quality,
                                required=b.needs_all and b.room_en != FORAGING_ROOM))
    return out


def season_needs(needs: list[CropNeed], season: str, year: int, done: set[tuple[str, str]]) -> list[CropNeed]:
    """本季能种、当年已开售、之前还没留下的需求。done 是已经留下的 (收集包英文名, 作物英文名)。"""
    return [n for n in needs if season in n.crop.seasons and year >= n.crop.available_year
            and (n.bundle.name_en, n.crop.name_en) not in done]


def is_last_chance(crop: Crop, season: str) -> bool:
    """本季是不是这种作物当年最后一个能种的季节（冬季之后是下一年）。"""
    order = ["spring", "summer", "fall", "winter"]
    later = order[order.index(season) + 1:]
    return not any(s in crop.seasons for s in later)


def reserve(needs: list[CropNeed], harvests: dict[str, float], gold_share: float, bonuses: Bonuses) -> list[Reservation]:
    """harvests：作物名 → 本季期望收获个数；gold_share：每次收获里金星及以上的比例（只影响第一个产出）。"""
    out = []
    for n in needs:
        got = harvests.get(n.crop.name, 0.0)
        if n.quality == "gold":
            got = got / max(n.crop.harvest_yield, 1e-9) * gold_share
        ok = got + 1e-9 >= n.count
        price = bonuses.crop_price(n.crop, "gold" if n.quality == "gold" else "normal")
        out.append(Reservation(n, got, ok, n.count * price if ok else 0))
    return out


def route_warnings(reservations: list[Reservation], season: str) -> list[str]:
    """必须交、本季是最后机会、却没留下的：报给模型修正。"""
    out = []
    for r in reservations:
        n = r.need
        if not n.required or r.reserved or not is_last_chance(n.crop, season):
            continue
        have = f"本季期望只收获约 {r.harvested:.1f} 个" if r.harvested > 1e-9 else "本季没有种"
        out.append(f"⚠️ 献祭校验：{n.label}需要留 {n.count} 个，{have}；{SEASON_ZH[season]}是今年最后能种{n.crop.name}的季节，"
                   f"请在种植清单里安排足够的{n.crop.name}，并在日程里写明留给收集包")
    return out


def describe_route(needs: list[CropNeed], season: str, done_labels: list[str]) -> str:
    """Prompt【数据参考】里的献祭一节。"""
    lines = []
    required = [n for n in needs if n.required]
    optional = [n for n in needs if not n.required]
    if required:
        lines.append("本季要留给社区中心的作物（不要卖，程序已从收入里扣掉它们的售价）：")
        for n in required:
            when = "本季是今年最后机会" if is_last_chance(n.crop, season) else "之后的季节也能补"
            lines.append(f"- {n.label}：{when}")
    if optional:
        lines.append("可选：" + "、".join(n.label for n in optional) + "（收集包可以从几样里挑，金星要靠品质）")
    if done_labels:
        lines.append("之前季节已经留下：" + "、".join(done_labels))
    if not lines:
        return "（本季没有需要种植的献祭作物）"
    lines.append("在收获那天的目标里写明“留X给收集包”，例如“收防风草，留 1 个给收集包”。")
    return "\n".join(lines)
