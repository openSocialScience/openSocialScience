"""`pyproject.toml` 的 `version` 与 `oss.__version__` 是同一个数字抄了两遍——
钉住它们不漂开。`oss mcp serve` 握手回的 `serverInfo.version`（`docs/user/mcp.md`
的示例）读的正是 `oss.__version__`，两处不一致就是文档说了代码没做的事。
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import oss


def test_pyproject版本号跟oss__version__一致():
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    parsed = tomllib.loads(text)
    assert parsed["project"]["version"] == oss.__version__
