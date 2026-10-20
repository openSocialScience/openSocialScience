"""测量骨架 Task 2：环节 4 判「证据做不做得出来」，不算功效。

因果算功效（能不能测出效应）、描述判精度（能分辨多大的差别）；**测量研究的可行性不是样本量问题，
是手头的数据支不支持你要给的那种效度证据**（测量骨架设计稿第四节）。三条判据：题目数够不够、
跨组比较有没有分组变量、有没有可对账的外部指标。

外加两处堵洞：声明了测量类型就必填测量块（实施计划「零之二」第 ① 条），`indicators` 的列名要在数据里。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from oss.api import errors
from oss.api import power as power_api
from oss.api import profile as profile_api
from oss.cli.main import app
from oss.core.analysis import validate_plan
from oss.core.contracts import AnalysisPlan, Idea, IdeaSet, MeasurementSpec
from oss.core.findings import code_of
from oss.core.profiler import profile_file

FIX = Path(__file__).parent / "fixtures"
runner = CliRunner()


# ─────────────────────────── 夹具


def _frame(n: int = 200, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "q1": rng.integers(1, 6, n), "q2": rng.integers(1, 6, n), "q3": rng.integers(1, 6, n),
        "urban": rng.integers(0, 2, n), "income": rng.normal(size=n),
        "one_group": np.ones(n, dtype=int),
    })


def _run(tmp_path: Path, df: pd.DataFrame, ideas: dict[str, str]) -> Path:
    run = tmp_path / "r"
    run.mkdir()
    df.to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    IdeaSet(ideas=[Idea(idea_id=i, title="T", research_question="Q", research_type=t)
                   for i, t in ideas.items()]).save(run / "ideas.json")
    return run


def _plan(run: Path, **measurement) -> None:
    """落一份测量类计划：只写测量块要用的那几栏，别的留缺省。"""
    AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="q1",
                 research_type="measurement",
                 measurement=MeasurementSpec(**measurement)).save(run / "plan.json")


# ═══════════════════════ 一、题目数够不够：两道题的量表给不出结构效度


def test_两道题的量表做不了结构效度_要停(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "measurement"})
    _plan(run, kind="instrument", construct="对陌生人的一般信任", indicators=["q1", "q2"],
          validity_evidence=["structural"])
    with pytest.raises(errors.Refused) as ei:
        power_api.power(str(run), idea="i1")
    assert [code_of(p) for p in ei.value.problems] == ["plan.measurement.too_few_indicators"], ei.value.problems
    assert not (run / "power.json").exists(), "停了还落了一份 power.json"
    # oss power 的 CLI 只打 summary 那一行：理由不拼进去，照着改的人看不见该改什么
    assert str(ei.value.problems[0]) in ei.value.summary


def test_三道题的量表放行(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "measurement"})
    _plan(run, kind="instrument", construct="对陌生人的一般信任", indicators=["q1", "q2", "q3"],
          validity_evidence=["structural"])
    out = power_api.power(str(run), idea="i1")
    assert out["data"]["mode"] == "evidence" and out["data"]["verdict"] == "pass"


def test_用尺子不卡题目数_那道门只管造尺子(tmp_path: Path) -> None:
    """`kind=application` 的两道题不停——这一条是有意的（门只管造尺子），代价记在实施计划「零之二」第 ② 条。"""
    run = _run(tmp_path, _frame(), {"i1": "measurement"})
    _plan(run, kind="application", construct="对陌生人的一般信任", indicators=["q1", "q2"])
    assert power_api.power(str(run), idea="i1")["data"]["verdict"] == "pass"


# ═══════════════════════ 二、跨组比较有没有分组变量


def test_跨组比较的分组变量不在数据里_要停(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "measurement"})
    _plan(run, kind="application", construct="对陌生人的一般信任", indicators=["q1", "q2", "q3"],
          groups_compared=["province"])
    with pytest.raises(errors.Refused) as ei:
        power_api.power(str(run), idea="i1")
    assert [code_of(p) for p in ei.value.problems] == ["plan.measurement.group_missing"], ei.value.problems
    assert "province" in ei.value.problems[0]


def test_分组变量只有一组_要停(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "measurement"})
    _plan(run, kind="application", construct="对陌生人的一般信任", indicators=["q1", "q2", "q3"],
          groups_compared=["one_group"])
    with pytest.raises(errors.Refused) as ei:
        power_api.power(str(run), idea="i1")
    assert [code_of(p) for p in ei.value.problems] == ["plan.measurement.group_missing"], ei.value.problems


def test_分组变量在数据里且不止一组_放行(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "measurement"})
    _plan(run, kind="application", construct="对陌生人的一般信任", indicators=["q1", "q2", "q3"],
          groups_compared=["urban"])
    assert power_api.power(str(run), idea="i1")["data"]["verdict"] == "pass"


# ═══════════════════════ 三、有没有可对账的外部指标：警告 + 处方，不停


def test_声明了效标关联却没有别的变量可对账_只警告不停(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame()[["q1", "q2", "q3"]], {"i1": "measurement"})
    _plan(run, kind="instrument", construct="对陌生人的一般信任", indicators=["q1", "q2", "q3"],
          validity_evidence=["criterion"])
    out = power_api.power(str(run), idea="i1")
    assert out["data"]["verdict"] == "pass", "只该警告，不该停"
    hit = [w for w in out["data"]["warnings"] if code_of(w) == "plan.measurement.no_criterion_var"]
    assert hit, out["data"]["warnings"]
    assert "criterion" in hit[0]


def test_有别的变量可对账_不警告(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "measurement"})
    _plan(run, kind="instrument", construct="对陌生人的一般信任", indicators=["q1", "q2", "q3"],
          validity_evidence=["criterion"])
    out = power_api.power(str(run), idea="i1")
    assert not [w for w in out["data"]["warnings"] if code_of(w) == "plan.measurement.no_criterion_var"]


# ═══════════════════════ 四、功效那一路对测量类关掉


def test_测量类不再出功效判词(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "measurement", "i2": "causal"})
    _plan(run, kind="application", construct="对陌生人的一般信任", indicators=["q1", "q2", "q3"])
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert rep["mode"] == "evidence"
    # 不算效应量：折价后的效应量、最小可测效应、功效三个数都不出
    assert rep["effect_size_raw"] == 0.0 and rep["effect_size_adjusted"] == 0.0
    assert rep["mde"] == 0.0 and rep["power"] == 0.0
    assert "功效" not in out["summary"], out["summary"]
    # CLI 那一行也不许打「功效 0.00」——描述类踩过一次的坑
    cli = runner.invoke(app, ["power", "--run", str(run), "--idea", "i1"])
    assert cli.exit_code == 0 and "功效 0.00" not in cli.output, cli.output
    assert "PASS  证据做不做得出来" in cli.output, cli.output
    # 没给效应量出处也不封顶灰区——那条封顶是功效那一路的
    assert not [w for w in out["data"]["warnings"] if "效应量没给出处" in w]


def test_同一份数据的因果题照旧算功效(tmp_path: Path) -> None:
    """分路只加了一支：别的类型一行不动。"""
    run = _run(tmp_path, _frame(), {"i1": "measurement", "i2": "causal"})
    out = power_api.power(str(run), idea="i2", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "power" and out["data"]["power"] > 0


def test_还没有测量块的_三条核不了但不停(tmp_path: Path) -> None:
    """环节 4 常常还没有计划：三条判据核不了，说出来，不停。"""
    run = _run(tmp_path, _frame(), {"i1": "measurement"})
    out = power_api.power(str(run), idea="i1")
    assert out["data"]["mode"] == "evidence"
    assert any("plan.measurement" in w for w in out["data"]["warnings"]), out["data"]["warnings"]


# ═══════════════════════ 五、第一个绕行洞：声明了测量类型就必填测量块


def _profile():
    return profile_file(FIX / "tiny.csv")


def _base(**kw) -> AnalysisPlan:
    return AnalysisPlan(plan_id="p", idea_id="i1", estimator="ols_fe", outcome="income",
                        treatment="treat", **kw)


def test_声明了测量类型却不写测量块_拒跑() -> None:
    problems, _ = validate_plan(_base(research_type="measurement"), _profile())
    assert "plan.measurement.spec_missing" in {code_of(p) for p in problems}, problems


def test_按最轻的说法声明照样拦_只写类型别的一个字不写() -> None:
    """「往轻里声明」躲不掉：整块不写就是这一条要拦的形状。"""
    problems, _ = validate_plan(
        AnalysisPlan(plan_id="p", idea_id="i1", estimator="describe", outcome="income",
                     research_type="measurement"), _profile())
    assert "plan.measurement.spec_missing" in {code_of(p) for p in problems}, problems


def test_写了测量块的不拦() -> None:
    plan = _base(research_type="measurement",
                 measurement=MeasurementSpec(kind="application", construct="社会信任",
                                             indicators=["income", "edu_years", "urban"]))
    problems, _ = validate_plan(plan, _profile())
    assert "plan.measurement.spec_missing" not in {code_of(p) for p in problems}, problems


def test_别的研究类型不受这一条影响() -> None:
    problems, _ = validate_plan(_base(research_type="descriptive"), _profile())
    assert "plan.measurement.spec_missing" not in {code_of(p) for p in problems}, problems


# ═══════════════════════ 五之二、第八个绕行洞：测量放进 also 也要写测量块


def test_附带类型里有测量_也要写测量块() -> None:
    """跟上一条同一个形状，只是换了一栏声明：主类型写 causal、测量搁进 `also`。"""
    problems, _ = validate_plan(_base(research_type="causal", also=["measurement"]), _profile())
    assert "plan.measurement.spec_missing" in {code_of(p) for p in problems}, problems


def test_附带类型没有测量_不受影响() -> None:
    problems, _ = validate_plan(_base(research_type="causal", also=["descriptive"]), _profile())
    assert "plan.measurement.spec_missing" not in {code_of(p) for p in problems}, problems


def test_附带类型里有测量_写了测量块就不拦() -> None:
    plan = _base(research_type="causal", also=["measurement"],
                 measurement=MeasurementSpec(kind="application", construct="社会信任",
                                             indicators=["income", "edu_years", "urban"]))
    problems, _ = validate_plan(plan, _profile())
    assert "plan.measurement.spec_missing" not in {code_of(p) for p in problems}, problems


# ═══════════════════════ 六、indicators 的列名要在数据里


def test_indicators的列名不在数据里_要报() -> None:
    plan = _base(research_type="measurement",
                 measurement=MeasurementSpec(kind="application", construct="社会信任",
                                             indicators=["income", "q_nope"]))
    problems, _ = validate_plan(plan, _profile())
    hit = [p for p in problems if code_of(p) == "plan.columns.not_in_data" and "q_nope" in p]
    assert hit, problems
    assert "测量块的指标" in hit[0]


def test_indicators的列名都在数据里_不报() -> None:
    plan = _base(research_type="measurement",
                 measurement=MeasurementSpec(kind="application", construct="社会信任",
                                             indicators=["income", "edu_years"]))
    problems, _ = validate_plan(plan, _profile())
    assert not [p for p in problems if code_of(p) == "plan.columns.not_in_data"], problems


# ═══════════════════════ 七、第九个绕行洞：主类型不是测量，环节 4 那三条照样核
#
# 原先环节 4 只按主类型分路：主类型写 causal、测量搁进 also，三条判据一条都不核——
# 跟计划门上刚堵掉的第八个洞完全同形。修法照计划门那三条的口径：**判据挂在测量块本身**。


def _causal_plan(run: Path, *, also: list[str] | None = None, **measurement) -> None:
    """主类型是因果的计划，测量块照给——考「主类型不是测量时那三条还核不核」。"""
    AnalysisPlan(plan_id="p", idea_id="i1", estimator="ols_fe", outcome="q1", treatment="urban",
                 research_type="causal", also=also or [],
                 measurement=MeasurementSpec(**measurement) if measurement else None,
                 ).save(run / "plan.json")


def test_附带类型里有测量_题目数不够照样停(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "causal"})
    _causal_plan(run, also=["measurement"], kind="instrument", construct="对陌生人的一般信任",
                 indicators=["q1", "q2"], validity_evidence=["structural"])
    with pytest.raises(errors.Refused) as ei:
        power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert [code_of(p) for p in ei.value.problems] == ["plan.measurement.too_few_indicators"], ei.value.problems
    assert not (run / "power.json").exists(), "停了还落了一份 power.json"


def test_附带类型里有测量_分组变量不在数据里照样停(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "causal"})
    _causal_plan(run, also=["measurement"], kind="application", construct="对陌生人的一般信任",
                 indicators=["q1", "q2", "q3"], groups_compared=["province"])
    with pytest.raises(errors.Refused) as ei:
        power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert [code_of(p) for p in ei.value.problems] == ["plan.measurement.group_missing"], ei.value.problems


def test_不写also只写测量块_照样拦(tmp_path: Path) -> None:
    """「换个说法照样拦」：连 `also` 都不声明，只要写了测量块，那三条就得对得上——
    判据挂在块本身，才不是「少填一栏就绕过去」。"""
    run = _run(tmp_path, _frame(), {"i1": "causal"})
    _causal_plan(run, kind="instrument", construct="对陌生人的一般信任",
                 indicators=["q1", "q2"], validity_evidence=["structural"])
    with pytest.raises(errors.Refused) as ei:
        power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert [code_of(p) for p in ei.value.problems] == ["plan.measurement.too_few_indicators"], ei.value.problems


def test_附带的测量三条都过_主类型那一路的报告不变(tmp_path: Path) -> None:
    """只加测量那类的检查，主类型走的还是整套功效——契约里 `also` 就是这个说法。"""
    run = _run(tmp_path, _frame(), {"i1": "causal"})
    _causal_plan(run, also=["measurement"], kind="application", construct="对陌生人的一般信任",
                 indicators=["q1", "q2", "q3"], groups_compared=["urban"])
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "power" and out["data"]["power"] > 0
    assert "附带的测量" in out["summary"], out["summary"]


def test_附带的测量没有对账指标_照样警告不停(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame()[["q1", "q2", "q3"]], {"i1": "causal"})
    _causal_plan(run, also=["measurement"], kind="instrument", construct="对陌生人的一般信任",
                 indicators=["q1", "q2", "q3"], validity_evidence=["criterion"])
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "power"
    assert [w for w in out["data"]["warnings"] if code_of(w) == "plan.measurement.no_criterion_var"]


def test_声明了附带测量却没有测量块_说一声不停(tmp_path: Path) -> None:
    """计划门那边会拒（spec_missing），环节 4 这里只说「三条核不了」——不是「过了」。"""
    run = _run(tmp_path, _frame(), {"i1": "causal"})
    _causal_plan(run, also=["measurement"])
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "power"
    assert any("测量块" in w for w in out["data"]["warnings"]), out["data"]["warnings"]


def test_没有测量块也没声明测量_一行不动(tmp_path: Path) -> None:
    run = _run(tmp_path, _frame(), {"i1": "causal"})
    _causal_plan(run)
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "power" and "附带的测量" not in out["summary"]
    assert not [w for w in out["data"]["warnings"] if "测量" in w], out["data"]["warnings"]
