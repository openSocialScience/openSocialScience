"""`oss/core/envfile.py`：`.env` 该到哪儿找，全项目唯一的定义。

事故形状：用户在 `~/Desktop/ossrun-d/r1` 这类自己的研究目录里跑 `oss`，
`.env` 从没放在那儿或它的上级——`oss doctor` 说「审稿 未配置」，
`oss review` 拿不到 key。三处历史代码（`oss/cli/main.py` 的 `_load_env`、
`oss/hosts/checks.py` 的 `_dotenv`、`oss/api/sim.py` 的 `dotenv_near`）
各自抄了一遍「cwd 往上找第一份」，都要挨这一刀。

这里只测 `oss/core/envfile.py` 自己的规则；三处调用方各自的测试文件
（`test_stage4_cli_hosts.py`、`test_stage4_hosts.py`、`test_sim_api.py`）
覆盖「真的用上了这个模块」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oss.core import envfile


def _write_env(path: Path, **kv: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(f"{k}={v}" for k, v in kv.items()) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _干净环境(tmp_path, monkeypatch):
    """每个测试都在一个没有真 .env 的临时天地里跑。

    pytest 进程在 import 阶段已经把仓库根的 .env 读进了环境（`oss.cli.main`
    的 `_load_env()` 是 import 时调用的）——这里把测试用到的几个键先删掉，
    否则「真仓库的值」会盖过测试布置的假值，把测试变成看真配置脸色。
    """
    for key in ("REVIEWER_BASE_URL", "REVIEWER_MODEL", "REVIEWER_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(envfile, "USER_ENV", home / ".config" / "oss" / ".env")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    # 源码检出这一档默认关掉——指向一个没有 pyproject.toml 的空目录，
    # 真仓库（正好满足条件）绝不会被不相关的测试意外读到。
    empty_root = tmp_path / "不是仓库"
    empty_root.mkdir()
    monkeypatch.setattr(envfile, "_source_checkout_root", lambda: empty_root)
    return {"tmp_path": tmp_path, "home": home, "cwd": cwd}


# ─────────────────────────────────────────────── candidates() / load_env()


def test_用户级文件会被读到(_干净环境):
    _write_env(envfile.USER_ENV, REVIEWER_API_KEY="user-key")
    envfile.load_env()
    import os
    assert os.environ.get("REVIEWER_API_KEY") == "user-key"


def test_cwd的env赢用户级(_干净环境, monkeypatch):
    cwd = _干净环境["cwd"]
    _write_env(cwd / ".env", REVIEWER_API_KEY="cwd-key")
    _write_env(envfile.USER_ENV, REVIEWER_API_KEY="user-key")
    envfile.load_env()
    import os
    assert os.environ.get("REVIEWER_API_KEY") == "cwd-key"


def test_预先设好的环境变量赢两处文件(_干净环境, monkeypatch):
    cwd = _干净环境["cwd"]
    _write_env(cwd / ".env", REVIEWER_API_KEY="cwd-key")
    _write_env(envfile.USER_ENV, REVIEWER_API_KEY="user-key")
    monkeypatch.setenv("REVIEWER_API_KEY", "已经在环境里的值")
    envfile.load_env()
    import os
    assert os.environ.get("REVIEWER_API_KEY") == "已经在环境里的值"


def test_源码检出候选只在pyproject名字对的时候才算(_干净环境, monkeypatch):
    fake_repo = _干净环境["tmp_path"] / "fake-repo"
    fake_repo.mkdir()
    _write_env(fake_repo / ".env", REVIEWER_API_KEY="checkout-key")
    monkeypatch.setattr(envfile, "_source_checkout_root", lambda: fake_repo)

    # 没有 pyproject.toml：不算候选
    assert envfile._source_checkout_env() is None
    envfile.load_env()
    import os
    assert "REVIEWER_API_KEY" not in os.environ

    # pyproject.toml 存在，但名字不对：仍然不算
    (fake_repo / "pyproject.toml").write_text(
        '[project]\nname = "some-other-package"\n', encoding="utf-8")
    assert envfile._source_checkout_env() is None

    # 名字对了才算
    (fake_repo / "pyproject.toml").write_text(
        '[project]\nname = "opensocialscience"\n', encoding="utf-8")
    assert envfile._source_checkout_env() == fake_repo / ".env"
    envfile.load_env()
    assert os.environ.get("REVIEWER_API_KEY") == "checkout-key"


def test_三档全没有就什么都不设(_干净环境):
    envfile.load_env()
    import os
    assert os.environ.get("REVIEWER_API_KEY") is None


def test_candidates去重按解析后的路径(_干净环境, monkeypatch):
    """cwd 的 .env 跟用户级 .env 解析到同一份真实文件时，只算一次。"""
    cwd = _干净环境["cwd"]
    real = _干净环境["tmp_path"] / "真身.env"
    _write_env(real, REVIEWER_API_KEY="共享")
    (cwd / ".env").symlink_to(real)
    monkeypatch.setattr(envfile, "USER_ENV", real)
    assert len(envfile.candidates()) == 1


# ─────────────────────────────────────────────── search_paths()


def test_search_paths只给路径不需要文件存在(_干净环境):
    paths = envfile.search_paths()
    assert all(isinstance(p, Path) for p in paths)
    assert (_干净环境["cwd"] / ".env") in paths
    assert envfile.USER_ENV in paths


def test_search_paths不包含源码检出除非它是真检出(_干净环境, monkeypatch):
    paths = envfile.search_paths()
    assert len(paths) == 2, "不是可编辑开发安装时，不该报一条误导性的 site-packages 路径"

    fake_repo = _干净环境["tmp_path"] / "fake-repo2"
    fake_repo.mkdir()
    (fake_repo / "pyproject.toml").write_text(
        '[project]\nname = "opensocialscience"\n', encoding="utf-8")
    monkeypatch.setattr(envfile, "_source_checkout_root", lambda: fake_repo)
    paths = envfile.search_paths()
    assert fake_repo / ".env" in paths


# ─────────────────────────────────────────────── search_paths_for_message()


def test_search_paths_for_message第一档说清楚是往上各级不是只查cwd自己(_干净环境):
    """`search_paths()` 第一项是 `cwd/.env`，字面意思容易让人以为只查 cwd
    自己那一份——消息版要把「往上找」这件事说出来。"""
    cwd = _干净环境["cwd"]
    msgs = envfile.search_paths_for_message()
    assert msgs[0] == f"{cwd} 往上各级的 .env"
    assert str(envfile.USER_ENV) in msgs[1:]


def test_search_paths_for_message只给路径不给值(_干净环境, monkeypatch):
    monkeypatch.setenv("REVIEWER_API_KEY", "不该出现的假值")
    msgs = envfile.search_paths_for_message()
    assert all("不该出现的假值" not in m for m in msgs)


def test_search_paths_for_message带上源码检出当它是真检出(_干净环境, monkeypatch):
    fake_repo = _干净环境["tmp_path"] / "fake-repo3"
    fake_repo.mkdir()
    (fake_repo / "pyproject.toml").write_text(
        '[project]\nname = "opensocialscience"\n', encoding="utf-8")
    monkeypatch.setattr(envfile, "_source_checkout_root", lambda: fake_repo)
    msgs = envfile.search_paths_for_message()
    assert str(fake_repo / ".env") in msgs


# ─────────────────────────────────────────────── doctor 的「未配置」消息


def test_doctor未配置时列出找过的路径且不带假key值(_干净环境, monkeypatch):
    from typer.testing import CliRunner
    from oss.cli.main import app

    monkeypatch.setenv("REVIEWER_API_KEY", "FAKE_SECRET_对不该出现在输出里")
    monkeypatch.delenv("REVIEWER_BASE_URL", raising=False)
    monkeypatch.delenv("REVIEWER_MODEL", raising=False)

    r = CliRunner().invoke(app, ["doctor", "--host", "dsh"])
    assert "未配置 REVIEWER_BASE_URL" in r.output
    assert "FAKE_SECRET_对不该出现在输出里" not in r.output
    assert str(envfile.USER_ENV) in r.output
