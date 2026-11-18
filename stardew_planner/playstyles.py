"""预设的玩法风格、节奏偏好和给新手看的优劣势说明（data/playstyles.json）。

这些说明只用来帮玩家挑选，不进 Prompt：玩家可以照样输入任意文字作为玩法和节奏。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .data_loader import DEFAULT_DATA_DIR


@dataclass(frozen=True)
class Preset:
    name: str
    summary: str
    pros: tuple[str, ...]
    cons: tuple[str, ...]
    suited_for: str

    def as_dict(self) -> dict:
        return {"name": self.name, "summary": self.summary, "pros": list(self.pros), "cons": list(self.cons),
                "suited_for": self.suited_for}

    def describe(self) -> str:
        lines = [f"【{self.name}】{self.summary}"]
        lines += [f"  ＋ {p}" for p in self.pros]
        lines += [f"  － {c}" for c in self.cons]
        lines.append(f"  适合：{self.suited_for}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Presets:
    playstyles: tuple[Preset, ...] = ()
    pacings: tuple[Preset, ...] = ()


def load_presets(data_dir: str | Path = DEFAULT_DATA_DIR) -> Presets:
    """文件不存在时返回空（网页和命令行就只显示输入框，不显示说明）。"""
    path = Path(data_dir) / "playstyles.json"
    if not path.exists():
        return Presets()
    raw = json.loads(path.read_text(encoding="utf-8"))

    def build(items) -> tuple[Preset, ...]:
        return tuple(Preset(i["name"], i["summary"], tuple(i["pros"]), tuple(i["cons"]), i["suited_for"])
                     for i in items or ())

    return Presets(build(raw.get("playstyles")), build(raw.get("pacings")))


def describe_presets(presets: Presets) -> str:
    """命令行 --list-playstyles 的输出。"""
    out = ["玩法风格（-p / --playstyle，也可以自己写）：", ""]
    out += [p.describe() + "\n" for p in presets.playstyles]
    out += ["节奏偏好（-c / --pacing，也可以自己写）：", ""]
    out += [p.describe() + "\n" for p in presets.pacings]
    return "\n".join(out).rstrip() + "\n"
