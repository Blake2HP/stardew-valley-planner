"""本机存档查找：默认目录、扫描与排序、SaveGameInfo 容错、命令行 --list-saves / --save auto / 选编号、
网页本地接口的开关和目录穿越防护。全部在 tmp_path 里构造，不读真实存档。"""

import os
import sys
from pathlib import Path

import pytest

from stardew_planner.save_parser import (
    SaveParseError, discover_local_saves, find_local_save, get_default_save_dirs,
)

FIXTURE = Path(__file__).parent / "fixtures" / "Farmer_123456789"


def info_xml(name="Blake", farm="星露", year=1, season=0, day=15, money=3200) -> str:
    return (f'<?xml version="1.0"?><Farmer><name>{name}</name><farmName>{farm}</farmName><money>{money}</money>'
            f"<yearForSaveGame>{year}</yearForSaveGame><seasonForSaveGame>{season}</seasonForSaveGame>"
            f"<dayOfMonthForSaveGame>{day}</dayOfMonthForSaveGame></Farmer>")


def make_save(root: Path, folder: str, mtime: float, info: str | None = None, main: bytes | None = None) -> Path:
    d = root / folder
    d.mkdir(parents=True)
    path = d / folder
    path.write_bytes(main if main is not None else FIXTURE.read_bytes())
    if info is not None:
        (d / "SaveGameInfo").write_text(info, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def saves_root(tmp_path):
    root = tmp_path / "Saves"
    make_save(root, "Old_1", 1_000, info_xml(farm="老", year=1, season=0, day=3, money=500))
    make_save(root, "New_2", 3_000, info_xml(farm="星露", year=2, season=1, day=15, money=3200))
    make_save(root, "Broken_3", 2_000, "<Farmer><name>半截")          # SaveGameInfo 损坏
    make_save(root, "NoInfo_4", 500)                                     # 没有 SaveGameInfo
    (root / "Empty_5").mkdir()                                           # 没有主存档：不算存档
    (root / "Other_6").mkdir()
    (root / "Other_6" / "SaveGameInfo").write_text(info_xml(), encoding="utf-8")
    (root / "stray.txt").write_text("x", encoding="utf-8")               # 根目录下的普通文件
    return root


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------

def test_discover_sorts_by_last_played(saves_root):
    saves = discover_local_saves([saves_root])
    assert [s.folder for s in saves] == ["New_2", "Broken_3", "Old_1", "NoInfo_4"]
    new = saves[0]
    assert (new.farmer, new.farm_name, new.year, new.season, new.day, new.money) == ("Blake", "星露", 2, "summer", 15, 3200)
    assert new.label == "星露农场 - 第2年夏季第15天 (3,200g)，Blake"
    assert new.path == saves_root / "New_2" / "New_2"


def test_broken_or_missing_info_falls_back_to_folder(saves_root):
    by = {s.folder: s for s in discover_local_saves([saves_root])}
    assert not by["Broken_3"].info_ok and by["Broken_3"].label == "Broken_3（没有读到存档摘要）"
    assert not by["NoInfo_4"].info_ok and by["NoInfo_4"].year is None


def test_missing_dirs_are_skipped(tmp_path, saves_root):
    assert discover_local_saves([tmp_path / "nope", saves_root])[0].folder == "New_2"
    assert discover_local_saves([tmp_path / "nope"]) == []


def test_info_with_doctype_is_ignored(tmp_path):
    root = tmp_path / "Saves"
    make_save(root, "Evil_1", 1, '<!DOCTYPE x [<!ENTITY a "b">]><Farmer><name>&a;</name></Farmer>')
    (s,) = discover_local_saves([root])
    assert not s.info_ok


def test_default_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert get_default_save_dirs() == [tmp_path / "StardewValley" / "Saves"]
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert get_default_save_dirs() == [tmp_path / ".config" / "StardewValley" / "Saves",
                                       tmp_path / ".var/app/com.valvesoftware.Steam/.config/StardewValley/Saves"]
    monkeypatch.setattr(sys, "platform", "darwin")
    assert get_default_save_dirs() == [tmp_path / ".config" / "StardewValley" / "Saves"]


@pytest.mark.parametrize("name", ["..", ".", "../New_2", "..\\New_2", "New_2/New_2", "C:New_2", "", "Missing_9"])
def test_find_local_save_rejects_traversal(saves_root, name):
    with pytest.raises(SaveParseError):
        find_local_save(name, [saves_root])


def test_find_local_save(saves_root):
    assert find_local_save("Old_1", [saves_root]).farm_name == "老"


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def test_list_saves(saves_root, capsys):
    import main
    main.main(["--list-saves", "--saves-dir", str(saves_root)])
    out = capsys.readouterr().out
    assert "找到 4 个存档" in out and "1. 星露农场 - 第2年夏季第15天 (3,200g)，Blake" in out
    assert str(saves_root / "New_2" / "New_2") in out


def test_list_saves_none_found(tmp_path, capsys):
    import main
    main.main(["--list-saves", "--saves-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "没有找到星露谷存档" in out and "%APPDATA%" in out and "com.valvesoftware.Steam" in out


def test_resolve_save_arg(saves_root):
    import io

    import main
    saves = discover_local_saves([saves_root])
    assert main.resolve_save_arg("auto", saves, out=io.StringIO()) == str(saves[0].path)
    assert main.resolve_save_arg("x/y", saves) == "x/y"
    assert main.resolve_save_arg("", saves, ask=lambda _: "3", out=io.StringIO()) == str(saves[2].path)
    for answer in ("0", "9", "abc"):
        with pytest.raises(ValueError, match="编号应为 1-4"):
            main.resolve_save_arg("", saves, ask=lambda _, a=answer: a, out=io.StringIO())

    def eof(_):
        raise EOFError
    with pytest.raises(ValueError, match="--save auto"):
        main.resolve_save_arg("", saves, ask=eof, out=io.StringIO())
    with pytest.raises(ValueError, match="没有找到星露谷存档"):
        main.resolve_save_arg("auto", [])


def test_save_auto_end_to_end(saves_root, capsys):
    import main
    main.main(["--save", "auto", "--saves-dir", str(saves_root), "--provider", "mock", "--revise-rounds", "0"])
    out, err = capsys.readouterr()
    assert "[自动选择最近玩的存档] 星露农场" in err and "[存档解析摘要]" in err
    assert "第 2 年夏季（第 12–28 天）攻略" in out


def test_bare_save_flag_asks(saves_root, capsys, monkeypatch):
    import main
    monkeypatch.setattr("builtins.input", lambda: "2")
    main.main(["--save", "--saves-dir", str(saves_root), "--provider", "mock", "--revise-rounds", "0"])
    err = capsys.readouterr().err
    assert "请输入编号（1-4）" in err and "2. Broken_3（没有读到存档摘要）" in err


# ---------------------------------------------------------------------------
# 网页本地接口
# ---------------------------------------------------------------------------

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from stardew_planner.llm_client import MockClient  # noqa: E402
from web.app import Settings, create_app  # noqa: E402


def web_client(indexed_store, mode, saves_root, client_host="testclient"):
    settings = Settings()
    settings.provider, settings.rate_limit = "mock", 0
    settings.local_saves, settings.saves_dirs = mode, [str(saves_root)]
    app = create_app(settings, client=MockClient(), guide_store=indexed_store, warm_up=False)
    return TestClient(app, client=(client_host, 50000))


def test_local_saves_off_by_default(indexed_store, saves_root, monkeypatch):
    monkeypatch.delenv("DEMO_ALLOW_LOCAL_SAVES", raising=False)
    monkeypatch.delenv("DEMO_HOST", raising=False)
    assert Settings().local_saves == "off"                     # 直接用 uvicorn 启动、没说明监听地址：关闭
    monkeypatch.setenv("DEMO_HOST", "0.0.0.0")
    assert Settings().local_saves == "off"                     # 公网监听：关闭
    monkeypatch.setenv("DEMO_HOST", "127.0.0.1")
    assert Settings().local_saves == "local"                   # python -m web 默认：本机
    monkeypatch.setenv("DEMO_ALLOW_LOCAL_SAVES", "0")
    assert Settings().local_saves == "off"
    monkeypatch.setenv("DEMO_ALLOW_LOCAL_SAVES", "1")
    assert Settings().local_saves == "on"
    with web_client(indexed_store, "off", saves_root) as c:
        assert c.get("/api/saves/local").status_code == 404
        assert c.post("/api/saves/local/load", json={"folder": "New_2"}).status_code == 404


def test_local_mode_only_answers_loopback(indexed_store, saves_root):
    with web_client(indexed_store, "local", saves_root, client_host="203.0.113.5") as c:
        assert c.get("/api/saves/local").status_code == 404     # 别人的请求：当作不存在
    with web_client(indexed_store, "local", saves_root, client_host="127.0.0.1") as c:
        saves = c.get("/api/saves/local").json()["saves"]
    assert [s["folder"] for s in saves][:2] == ["New_2", "Broken_3"] and "path" not in saves[0]   # 不返回服务器路径


def test_load_local_save(indexed_store, saves_root):
    with web_client(indexed_store, "on", saves_root) as c:
        r = c.post("/api/saves/local/load", json={"folder": "New_2"})
    assert r.status_code == 200 and r.json()["defaults"]["start_day"] == 12


@pytest.mark.parametrize("folder", ["..", "../New_2", "..\\..\\Windows", "New_2/../Old_1", "Missing_9"])
def test_load_local_save_blocks_traversal(indexed_store, saves_root, folder):
    with web_client(indexed_store, "on", saves_root) as c:
        r = c.post("/api/saves/local/load", json={"folder": folder})
    assert r.status_code == 400


def test_save_guide_is_always_available(indexed_store, saves_root):
    with web_client(indexed_store, "off", saves_root) as c:
        dirs = c.get("/api/saves/guide").json()["dirs"]
        page = c.get("/").text
    assert "Windows" in dirs and "Steam Deck / Flatpak" in dirs
    assert "找不到存档文件？" in page and "没有后缀名" in page and 'id="localSave"' in page
