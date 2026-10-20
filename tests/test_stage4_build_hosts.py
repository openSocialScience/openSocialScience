"""`scripts/build_hosts.py` 的参数不许有「跑得通但结果是错的」那种组合。

投影脚本最坏的失败不是报错，是**看起来成功了**：`--out` 不带 `--host` 时四个宿主
依次投进同一个目录，后一个盖掉前一个，四行全是 ✓，而目录里只剩最后一个宿主的形状。
这类组合由程序拒绝，不靠使用者记住。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load():
    """按文件路径加载脚本——它不在包里，import 不到。"""
    spec = importlib.util.spec_from_file_location(
        "build_hosts", ROOT / "scripts" / "build_hosts.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _run(monkeypatch, *argv: str) -> int:
    m = _load()
    monkeypatch.setattr(sys, "argv", ["build_hosts.py", *argv])
    return m.main()


def test_out不带host直接拒绝(monkeypatch, capsys, tmp_path: Path) -> None:
    """四个宿主会互相覆盖同一个目录，而四行全是 ✓——**这种成功比失败更贵**。"""
    out = tmp_path / "out"
    rc = _run(monkeypatch, "--out", str(out))
    assert rc == 2
    assert "✗" in capsys.readouterr().out
    assert not out.exists(), "拒绝了就一个字节都别写"


def test_check和out不能同时给(monkeypatch, capsys, tmp_path: Path) -> None:
    """`--check` 是只算不落盘，`--out` 是落到哪儿。两个一起给，必有一个是误会。"""
    out = tmp_path / "out"
    rc = _run(monkeypatch, "--check", "--out", str(out))
    assert rc == 2
    printed = capsys.readouterr().out
    assert "✗" in printed and "--check" in printed
    assert not out.exists()


def test_host加out是允许的组合(monkeypatch, capsys, tmp_path: Path) -> None:
    out = tmp_path / "dsh-skills"
    rc = _run(monkeypatch, "--host", "dsh", "--out", str(out))
    assert rc == 0
    assert "✓" in capsys.readouterr().out
    t = (out / "social-research" / "SKILL.md").read_text(encoding="utf-8")
    assert "# allowed-tools:" in t, "dsh 不认 allowed-tools，投影要把它注释掉"


def test_check不落盘(monkeypatch, capsys, tmp_path: Path) -> None:
    """`--check` 走 TemporaryDirectory，OUT 那棵树一个目录都不该被建出来。"""
    m = _load()
    fake_out = tmp_path / "build" / "hosts"
    monkeypatch.setattr(m, "OUT", fake_out)
    monkeypatch.setattr(sys, "argv", ["build_hosts.py", "--check"])
    rc = m.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "只算不落盘" in out and "✓" in out
    assert not fake_out.exists(), "--check 不许写 OUT"


def test_不认识的宿主拒绝(monkeypatch, capsys) -> None:
    rc = _run(monkeypatch, "--host", "cursor")
    assert rc == 2
    out = capsys.readouterr().out
    assert "✗" in out and "dsh" in out, "已知清单要一起给"
