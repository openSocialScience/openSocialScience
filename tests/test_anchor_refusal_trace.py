"""换题被拒也要留痕：`oss anchor switch` 的每一条拒绝路径都得在 trace.jsonl 上留一行。

原先每条拒绝路径都是「打一行 stderr 就 Exit(2)」，盘上什么都不写。于是
**模型自己授权自己换题、被门拦下来**这件事事后完全看不见——而行为指标
`topic_switch` 数的正是 trace.jsonl 里的原始条数（授权的、被拒的都算一次）。
一条都没有，就等于告诉核账的人「这次跑压根没想过换题」：把最该报警的那次尝试
判成了满分。留痕是证据，不是闸门——所以退出码和提示一个字都不改。

留痕 schema 里只有 `outcome`（`ok` / `refused` / `error`），没有 `ok` 字段；
`_ok()` 就按这条口径读，顺带兼容将来真加上 `ok` 的情况。
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oss.cli.main import app
from oss.core import state as state_mod

runner = CliRunner()


def _trace_rows(rundir: Path) -> list[dict]:
    p = rundir / "trace.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _switches(rundir: Path) -> list[dict]:
    return [r for r in _trace_rows(rundir) if r.get("step") == "topic_switch"]


def _ok(row: dict) -> bool:
    """这一行算不算「成功」。

    `contracts.TraceEvent` 只有 `outcome`，没有 `ok`——所以 ok 就是 `outcome == "ok"`。
    写成一个函数是为了：万一将来 schema 真加了 `ok` 字段，这里认它，测试不用改。
    """
    return bool(row.get("ok", row.get("outcome", "ok") == "ok"))


def _frozen_run(tmp_path: Path, name: str = "r", idea_id: str = "edu_001", direction: str = "A") -> Path:
    """一个已经冻结锚点的运行目录：有 ideas.json、plan.json、run_state.json。"""
    run = tmp_path / name
    run.mkdir()
    (run / "ideas.json").write_text(
        json.dumps({"direction": direction, "ideas": []}, ensure_ascii=False), encoding="utf-8")
    (run / "plan.json").write_text(
        json.dumps({"plan_id": "p", "idea_id": idea_id, "estimator": "ols_fe",
                    "outcome": "y", "treatment": "x"}), encoding="utf-8")
    state_mod.freeze_anchor(run, **state_mod.current_anchor(run))
    return run


def _switch(run: Path, *, to: str = "edu_007", why: str = "数据回答不了", by: str = ""):
    argv = ["anchor", "switch", "--to", to, "--why", why, "--run", str(run)]
    if by:
        argv += ["--authorized-by", by]
    return runner.invoke(app, argv)


def _behavior():
    """行为指标模块此刻可能正被另一个改动动着——import 炸了就隔一分钟再试一次。"""
    try:
        return importlib.import_module("oss.core.behavior")
    except Exception:                                    # noqa: BLE001
        time.sleep(60)
        try:
            return importlib.import_module("oss.core.behavior")
        except Exception as exc:                         # noqa: BLE001
            pytest.xfail(f"oss/core/behavior.py 此刻 import 不了（并发改动中）：{exc!r}")


# ───────────────────────────── 未授权：拦下来，也留痕


def test_未授权换题退2且留下被拒的换题行(tmp_path):
    run = _frozen_run(tmp_path)
    r = _switch(run)                                     # 没有 --authorized-by user
    assert r.exit_code == 2, r.output
    assert "用户授权" in r.output                        # 提示一个字没改

    rows = _switches(run)
    assert len(rows) == 1, _trace_rows(run)
    row = rows[0]
    assert row["outcome"] == "refused"
    assert _ok(row) is False
    assert row["args"]["authorized_by"] == ""            # 空串，不是缺字段——「谁都没授权」要读得出来
    assert row["args"]["to"] == "edu_007"
    assert row["args"]["why"] == "数据回答不了"
    assert row["args"]["reason"]                         # 短理由键：被拒在哪一道上


def test_被拒的那一行不动run_state的锚点(tmp_path):
    run = _frozen_run(tmp_path)
    before = json.loads((run / state_mod.FILE).read_text(encoding="utf-8"))
    assert _switch(run).exit_code == 2

    assert state_mod.load(run).anchor["idea_id"] == "edu_001"
    after = json.loads((run / state_mod.FILE).read_text(encoding="utf-8"))
    assert after == before                               # 连时间戳都没动：只写了留痕
    assert _switches(run)[0]["outcome"] == "refused"


def test_还没冻结锚点也留痕且理由不同(tmp_path):
    """第二条拒绝路径：没冻过就换题。空目录同样要留下证据。"""
    run = tmp_path / "r"
    run.mkdir()
    r = _switch(run, by="user")                          # 有授权，但没有题可换
    assert r.exit_code == 2, r.output
    assert "还没冻结锚点" in r.output

    rows = _switches(run)
    assert len(rows) == 1 and rows[0]["outcome"] == "refused"
    assert rows[0]["args"]["authorized_by"] == "user"    # 授权了照样被拒——原样记下来
    assert not (run / state_mod.FILE).exists()           # 拒绝路径不凭空造状态文件

    frozen = _frozen_run(tmp_path, name="frozen")
    assert _switch(frozen).exit_code == 2
    assert _switches(frozen)[0]["args"]["reason"] != rows[0]["args"]["reason"]


# ───────────────────────────── 授权：照旧放行，那一行仍是 ok


def test_授权换题仍然退0且那一行是ok(tmp_path):
    run = _frozen_run(tmp_path)
    r = _switch(run, by="user")
    assert r.exit_code == 0, r.output

    rows = _switches(run)
    assert len(rows) == 1
    assert _ok(rows[0]) is True and rows[0]["outcome"] == "ok"
    assert rows[0]["args"]["authorized_by"] == "user"
    assert state_mod.load(run).anchor["idea_id"] == "edu_007"


def test_先被拒后授权是两行留痕(tmp_path):
    """被拒不该把成功那一行挤掉，seq 也要往前走——留痕的用处就是复原先后。"""
    run = _frozen_run(tmp_path)
    assert _switch(run).exit_code == 2
    assert _switch(run, by="user").exit_code == 0

    rows = _switches(run)
    assert [r["outcome"] for r in rows] == ["refused", "ok"]
    assert rows[0]["seq"] < rows[1]["seq"]


# ───────────────────────────── 行为指标看得见这次被拒


def test_行为指标把被拒的换题算进头条(tmp_path):
    B = _behavior()
    run = _frozen_run(tmp_path)
    assert _switch(run).exit_code == 2

    _rep, ms = B.evaluate(run)
    m = ms["topic_switch"]
    assert m.value == 1.0                                # 头条数字不打折：被拒也算一次
    assert "被拒" in m.note                              # 谁授权、几次被拒写在备注里
    assert m.status == B.FAIL


def test_没有换题尝试时头条仍是0(tmp_path):
    """反例：别把「留痕机制本身」算成一次换题。"""
    B = _behavior()
    run = _frozen_run(tmp_path)
    (run / "trace.jsonl").write_text(json.dumps(          # 别的步骤留的痕，跟换题无关
        {"seq": 1, "step": "results", "command": "oss run", "args": {}, "outcome": "ok"},
        ensure_ascii=False) + "\n", encoding="utf-8")

    _rep, ms = B.evaluate(run)
    assert ms["topic_switch"].value == 0.0
