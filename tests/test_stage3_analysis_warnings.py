"""探索撞名那条拒绝也要带上 ⚠ 警告行。

`emit_explore` 的编号是从留痕数出来的：留痕被截断或删过就会撞号，
这时**宁可拒绝也不能覆盖**。但这条拒绝是从 `emit_explore` 里抛的，
不经过 `run()` 里那两个带 `warnings` 的 `except`——散文那一侧
`⚠ …` 本来就打在 `✗` 之前，异常不把它带上，这条路径上那几行就凭空少了。

**少一行警告不会有任何报错**，只会让「分批实施用 did_cs」这类提醒在
某一条路径上安静消失——这正是散文冻结拦不到的那一类回归（golden 里
没有撞名这个用例）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oss.api import analysis as analysis_api
from oss.api import errors
from oss.api import profile as profile_api
from oss.cli.main import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def _explorable(tmp_path: Path) -> tuple[Path, Path]:
    """一个能跑探索的运行目录 + 一份探针计划。

    数据用 `did_clean.csv`：它是**分批实施**的，校验必然吐一条 ⚠——
    这条测试要的就是那一条。
    """
    run = tmp_path / "r"
    run.mkdir()
    shutil.copy(FIXTURES / "did_clean.csv", run / "data.csv")
    profile_api.profile(str(run / "data.csv"), run=str(run))
    (run / "ideas.json").write_text(json.dumps({"direction": "政策与产出", "ideas": []},
                                               ensure_ascii=False), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "y",
        "unit": "unit", "time": "year", "treat_time": "treat_time",
        "cluster_level": "unit"}), encoding="utf-8")
    (run / "route.json").write_text(json.dumps({"plan_sha": "s", "items": [
        {"id": "pretrend_fail", "priority": "must", "trigger": "t", "kind": "disclose"}]}),
        encoding="utf-8")
    probe = run / "probe.json"
    probe.write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "y",
        "unit": "unit", "time": "year", "treat_time": "treat_time",
        "cluster_level": "unit", "sample_filter": "year >= 2005"}), encoding="utf-8")
    return run, probe


def _drop_explore_rows(run: Path) -> None:
    """把留痕里的 explore 行删掉——模拟「trace 被截断」，下一次会撞上同一个编号。"""
    f = run / "trace.jsonl"
    kept = [l for l in f.read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l).get("step") != "explore"]
    f.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


def _first_explore_then_truncate(tmp_path: Path) -> tuple[Path, Path]:
    run, probe = _explorable(tmp_path)
    res = analysis_api.run(str(run), explore=True, for_id="pretrend_fail",
                           explore_plan=str(probe))
    assert Path(res["data"]["path"]).name == "pretrend_fail_1.results.json"
    assert res["data"]["warnings"], "did_clean 是分批实施的，第一次跑就该有 ⚠"
    _drop_explore_rows(run)
    return run, probe


def test_撞名的拒绝带着警告一起抛(tmp_path):
    run, probe = _first_explore_then_truncate(tmp_path)
    with pytest.raises(errors.Refused) as e:
        analysis_api.run(str(run), explore=True, for_id="pretrend_fail",
                         explore_plan=str(probe))
    assert e.value.code == 1 and "探索结果已存在" in e.value.summary
    assert e.value.data["warnings"], "警告丢了：散文那一侧的 ⚠ 行会跟着一起消失"
    # 拒绝就不许覆盖：上一次探索的证据原样还在，也没多出第二份
    assert sorted(p.name for p in (run / "explore").iterdir()) == [
        "pretrend_fail_1.results.json"]


def test_散文里的警告行仍在拒绝行之前(tmp_path):
    run, probe = _first_explore_then_truncate(tmp_path)
    r = runner.invoke(app, ["run", "--explore", "--for", "pretrend_fail",
                            "--plan", str(probe), "--run", str(run)])
    assert r.exit_code == 1, r.output
    lines = r.output.splitlines()
    warn = next(i for i, l in enumerate(lines) if l.startswith("  ⚠ "))
    cross = next(i for i, l in enumerate(lines) if l.startswith("✗ 探索结果已存在"))
    assert warn < cross, r.output
