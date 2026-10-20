"""执行者是谁：问四处，都问不到就拒绝。

跨模型审稿的全部意义在于「审的和被审的不是同一家」。**不知道被审的是谁，
就没有跨家族可言**——所以宁可拒绝，不可猜。宿主换了，`EXECUTOR_MODEL` 从哪来也换了：
Claude Code 里用户可能设过 ANTHROPIC_MODEL，dsh 里一个模型名变量都没有。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss import hosts
from oss.host.reviewer import Reviewer, SameFamilyRefused
from oss.hosts import executor as ex


def _run_state(tmp_path: Path, model: str) -> Path:
    (tmp_path / "run_state.json").write_text(json.dumps(
        {"run_id": "r1", "stage": "8-review", "models": {"executor": model}},
        ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_第一顺位是EXECUTOR_MODEL(tmp_path: Path) -> None:
    _run_state(tmp_path, "deepseek-v4-flash")
    got = ex.resolve("claude-code", tmp_path,
                     env={"EXECUTOR_MODEL": "claude-opus-4", "ANTHROPIC_MODEL": "claude-x"})
    assert (got.model, got.source) == ("claude-opus-4", "EXECUTOR_MODEL")


def test_第二顺位是宿主自己暴露的变量(tmp_path: Path) -> None:
    _run_state(tmp_path, "deepseek-v4-flash")
    got = ex.resolve("claude-code", tmp_path, env={"ANTHROPIC_MODEL": "claude-sonnet-4"})
    assert (got.model, got.source) == ("claude-sonnet-4", "claude-code:ANTHROPIC_MODEL")


def test_合成档案也走第二顺位(tmp_path: Path) -> None:
    prof = hosts.HostProfile(name="fake", label="F", skills_dir="~/x",
                             executor_model_env=("MY_MODEL", "MY_OTHER"))
    got = ex.resolve(prof, None, env={"MY_OTHER": "glm-4-flash"})
    assert (got.model, got.source) == ("glm-4-flash", "fake:MY_OTHER")


def test_第三顺位是上一次审稿记下的(tmp_path: Path) -> None:
    _run_state(tmp_path, "deepseek-v4-flash")
    got = ex.resolve("dsh", tmp_path, env={})
    assert (got.model, got.source) == ("deepseek-v4-flash", "run_state.json")


def test_dsh没有模型变量所以直接落到run_state(tmp_path: Path) -> None:
    _run_state(tmp_path, "deepseek-v4-flash")
    assert hosts.load("dsh").executor_model_env == ()
    got = ex.resolve("dsh", tmp_path, env={"ANTHROPIC_MODEL": "claude-x"})
    assert got.model == "deepseek-v4-flash", "别家宿主的变量不许被 dsh 借用"


def test_都问不到就是空并且不猜(tmp_path: Path) -> None:
    got = ex.resolve("dsh", tmp_path, env={})
    assert (got.model, got.source) == ("", "none")


def test_认不出的宿主不许借别家的变量(tmp_path: Path) -> None:
    """回落到缺省宿主等于**替一个不认识的宿主借了 claude-code 的变量**：
    `ANTHROPIC_MODEL` 说的不是正在跑这套技能的模型，拿它当答案就是猜。
    宁可返回空串交给 check_family 拒绝。"""
    got = ex.resolve("dshh", tmp_path, env={"ANTHROPIC_MODEL": "claude-x"})
    assert (got.model, got.source) == ("", "none")


def test_resolve探测宿主时吃调用方注入的env而不回落到真实环境(monkeypatch) -> None:
    """`_profile()` 内部认宿主要靠 `detect()`；`detect()` 不认 `env=` 的话，
    就会去读真实 `os.environ`，等于**替调用方注入的假环境借了真实进程的变量**——
    跟本模块开头那条「不回落」的脾气自相矛盾。真实环境里随便设一个会被误认的
    OSS_HOST，用来证明传入的 env 才是唯一依据。"""
    monkeypatch.setenv("OSS_HOST", "dsh")
    got = ex.resolve(None, None, env={"OSS_HOST": "cursor", "ANTHROPIC_MODEL": "A1"})
    assert (got.model, got.source) == ("", "none")


def test_认不出的宿主在提示里被点名() -> None:
    """只说「认不出的宿主」，用户不知道是自己拼错了还是 OSS_HOST 设错了。"""
    h = ex.hint("dshh")
    assert "认不出" in h
    assert "dshh" in h, "得点名是哪个名字认不出"
    assert "dsh" in h, "已知清单要一起给，拼错的人才知道该改成什么"


def test_hint探测宿主时也吃调用方注入的env(monkeypatch) -> None:
    monkeypatch.setenv("OSS_HOST", "dsh")
    h = ex.hint(None, env={"OSS_HOST": "cursor"})
    assert "认不出的宿主 'cursor'" in h


def test_提示里点名宿主和三条怎么设() -> None:
    h = ex.hint("dsh")
    assert "DeepSeek Harness" in h
    assert "EXECUTOR_MODEL" in h and "run_state.json" in h
    assert "KEY" in h, "dsh 剥凭据这件事必须在这儿说，否则用户会往 export 上撞"


def test_拒绝信息带上提示(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REQUIRE_CROSS_MODEL", "1")   # 拒绝只在严格档下发生
    monkeypatch.delenv("EXECUTOR_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    rev = Reviewer(base_url="http://x", model="deepseek-chat", api_key="k",
                   host="dsh", rundir=tmp_path)
    with pytest.raises(SameFamilyRefused) as exc:
        rev.check_family()
    msg = str(exc.value)
    assert "判不出模型家族" in msg.splitlines()[0], "第一行是 doctor 摘要用的，不许变"
    assert "DeepSeek Harness" in msg and "EXECUTOR_MODEL" in msg


def test_认不出的宿主下拒绝信息点名那个宿主(monkeypatch, tmp_path: Path) -> None:
    """`OSS_HOST=cursor`：探测照样返回 cursor，判不判得出档案是 _profile 的事。
    没有档案 → 问不到执行者 → 拒绝，而拒绝信息里得说清是哪个宿主认不出。"""
    monkeypatch.setenv("REQUIRE_CROSS_MODEL", "1")   # 拒绝只在严格档下发生
    monkeypatch.delenv("EXECUTOR_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    rev = Reviewer(base_url="http://x", model="deepseek-chat", api_key="k",
                   host="cursor", rundir=tmp_path)
    with pytest.raises(SameFamilyRefused) as exc:
        rev.check_family()
    msg = str(exc.value)
    assert "判不出模型家族" in msg.splitlines()[0], "第一行是 doctor 摘要用的，不许变"
    assert "认不出的宿主 'cursor'" in msg


def test_显式传空串仍然不会被链条填上(monkeypatch, tmp_path: Path) -> None:
    """阶段 0 就定下的契约：调用方说「我不知道」，就不能拿到别处的值。"""
    monkeypatch.setenv("EXECUTOR_MODEL", "claude-opus-4")
    r = Reviewer(executor_model="", host="claude-code", rundir=tmp_path)
    assert r.executor_model == "" and r.executor_source == "explicit"


def test_审稿命令把运行目录传给了解析() -> None:
    """否则第三顺位永远用不上——run_state.json 就在那个目录里。"""
    src = (Path(__file__).resolve().parents[1] / "oss" / "cli" / "main.py").read_text(
        encoding="utf-8")
    assert "rundir=rundir.path" in src


def test_api层现造reviewer时带上了运行目录(monkeypatch, tmp_path: Path) -> None:
    """`review(reviewer=None)` 现造的那一个也必须知道运行目录在哪。

    **MCP 的 oss_review 走的正是这条路**——它不注入 reviewer。不传运行目录的话，
    执行者身份的第三顺位（run_state.json 里上一次审稿记下的）永远问不到，
    而在 dsh 那种一个模型名变量都没有的宿主里，第三顺位就是唯一的一条路。
    """
    from oss.api import review as review_api

    run = tmp_path / "run1"
    run.mkdir()
    doc = run / "paper.md"
    doc.write_text("正文。\n", encoding="utf-8")

    captured: dict = {}

    class _Capture:
        def __init__(self, *a, **k) -> None:
            captured.update(k)
            raise RuntimeError("stop")   # 构造完就打断：这条测的是参数，不是审稿

    # _default_reviewer 是在函数体里 import 的，所以要换源头那个名字
    monkeypatch.setattr("oss.host.reviewer.Reviewer", _Capture)
    with pytest.raises(RuntimeError, match="stop"):
        review_api.review(str(run), files=[str(doc)])
    assert captured["rundir"] == Path(run)


# ─────────────────────────────────────────────── 空壳退休

def test_阶段0的假宿主壳已经删掉() -> None:
    """`ClaudeCodeHost.review()` 返回 `FAKE_阶段 0 不调用真实审稿模型`，
    没有任何调用方，却摆在 `oss/host/` 里像是一条可选路径。**留着的假路比没有更糟。**"""
    root = Path(__file__).resolve().parents[1]
    assert not (root / "oss" / "host" / "claude_code.py").exists()
    base = (root / "oss" / "host" / "base.py").read_text(encoding="utf-8")
    assert "class Host(" not in base, "没有实现者的 Protocol 是空承诺"
    assert "class RunDir" in base, "RunDir 要留着"
    main = (root / "oss" / "cli" / "main.py").read_text(encoding="utf-8")
    assert "ClaudeCodeHost" not in main
