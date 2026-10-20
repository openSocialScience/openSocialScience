"""描述骨架第 1 批（精度代替功效）的审查修补。

九条：换题后按旧计划的类型判；精度模式认 --cluster-col；分组列缺值不虚增各组有效样本量；
组差用分组列在簇内的 ρx 与各组自己的权重；退化输入；计划指纹认描述块；基准出处不许空；
帮助与 MCP 跟上；有意义差别按结局原单位给（--effect-metric raw）。
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.api import errors
from oss.api import power as power_api
from oss.api import profile as profile_api
from oss.core import power as power_mod
from oss.core.contracts import Idea, IdeaSet

Z = 1.959963984540054


def _run(tmp_path: Path, df: pd.DataFrame, ideas: dict[str, str]) -> Path:
    run = tmp_path / "r"
    run.mkdir()
    df.to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    IdeaSet(ideas=[Idea(idea_id=i, title="T", research_question="Q", research_type=t)
                   for i, t in ideas.items()]).save(run / "ideas.json")
    return run


def _plan(run: Path, **kw) -> None:
    (run / "plan.json").write_text(json.dumps({"plan_id": "p", **kw}, ensure_ascii=False), encoding="utf-8")


def _bind_outcome(run: Path, idea: str, col: str) -> None:
    (run / "binding.json").write_text(json.dumps({
        "idea_id": idea, "core_ok": True,
        "bindings": [{"construct_name": "结局", "role": "outcome", "column": col, "status": "bound"}]},
        ensure_ascii=False), encoding="utf-8")


def _flat(n: int = 300, seed: int = 1) -> pd.DataFrame:
    return pd.DataFrame({"y": np.random.default_rng(seed).normal(size=n)})


# ══════════════════════════════ 1. 换题后盘上的旧计划不算新题的类型


def test_计划是别的题的_研究类型按ideas里这一条取_描述旧计划不把因果新题拉进精度(tmp_path: Path) -> None:
    run = _run(tmp_path, _flat(), {"i1": "descriptive", "i2": "causal"})
    _plan(run, idea_id="i1", research_type="descriptive")          # 换题（oss anchor switch）后留在盘上的 i1 计划
    out = power_api.power(str(run), idea="i2", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "power", out["data"]["warnings"]
    assert any("i1" in w and "plan.json" in w for w in out["data"]["warnings"]), out["data"]["warnings"]


def test_计划是别的题的_因果旧计划不把描述新题拉回功效(tmp_path: Path) -> None:
    run = _run(tmp_path, _flat(), {"i1": "descriptive", "i2": "causal"})
    _plan(run, idea_id="i2", research_type="causal")
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert out["data"]["mode"] == "precision"


def test_计划没写idea_id的老计划照旧认(tmp_path: Path) -> None:
    run = _run(tmp_path, _flat(), {"i1": "causal"})
    _plan(run, research_type="descriptive")
    assert power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")["data"]["mode"] == "precision"


# ══════════════════════════════ 2. 精度模式认 --cluster-col


def _county_frame(n: int = 1200, k: int = 40, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    county = np.repeat(np.arange(k), n // k)
    return pd.DataFrame({"y": rng.normal(size=n) + rng.normal(size=k)[county], "county": county,
                         "urban": (county % 2)})


def test_精度模式给了cluster_col就按它数簇_整群设计效应大于1_半宽变宽(tmp_path: Path) -> None:
    run = _run(tmp_path, _county_frame(), {"i1": "descriptive"})
    off = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    on = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", cluster_col="county")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert rep["n_clusters"] == 40 and rep["design_effect"] > 1.0
    assert rep["design_effect"] == pytest.approx(1 + (1200 / 40 - 1) * 0.05)
    assert on["data"]["precision_halfwidth"] > off["data"]["precision_halfwidth"]
    assert on["data"]["precision_halfwidth"] == pytest.approx(
        off["data"]["precision_halfwidth"] * math.sqrt(rep["design_effect"]), abs=2e-4)


def test_没给整群信息_画像有疑似抽样单位就提示加cluster_col_不自动用(tmp_path: Path) -> None:
    run = _run(tmp_path, _county_frame(), {"i1": "descriptive"})
    prof = json.loads((run / "profile.json").read_text(encoding="utf-8"))
    assert prof["survey"]["psu_candidates"] == ["county"]          # 夹具本身先要让画像认出区县
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    assert any("--cluster-col county" in w for w in out["data"]["warnings"]), out["data"]["warnings"]
    assert json.loads((run / "power.json").read_text(encoding="utf-8"))["design_effect"] == 1.0


def test_计划写了survey_psu也提示(tmp_path: Path) -> None:
    df = _flat(600).assign(site=np.repeat(np.arange(20), 30))
    run = _run(tmp_path, df, {"i1": "descriptive"})
    _plan(run, idea_id="i1", research_type="descriptive", survey={"psu": "site"})
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    # F3：计划写了 survey.psu 就按它算整群（原先只提示加 --cluster-col site），警告里说按计划取了整群列 site
    assert any("整群列 site" in w for w in out["data"]["warnings"]), out["data"]["warnings"]
    assert json.loads((run / "power.json").read_text(encoding="utf-8"))["n_clusters"] == 20


def test_每簇一行就说整群没算(tmp_path: Path) -> None:
    df = _flat(300).assign(rid=np.arange(300))
    run = _run(tmp_path, df, {"i1": "descriptive"})
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", cluster_col="rid")
    assert any("每簇一行" in w and "整群没算" in w for w in out["data"]["warnings"]), out["data"]["warnings"]


def test_功效模式的cluster_col照旧(tmp_path: Path) -> None:
    """因果题没有处理列时 --cluster-col 不数簇——功效模式一行不动。"""
    run = _run(tmp_path, _county_frame(), {"i1": "causal"})
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", cluster_col="county")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert out["data"]["mode"] == "power" and rep["n_clusters"] == 1200


# ══════════════════════════════ 3. 分组列缺值不虚增各组的有效样本量


def test_分组列缺值的行不给别的组虚增有效样本量(tmp_path: Path) -> None:
    urban = [0.0] * 100 + [1.0] * 50 + [np.nan] * 150
    run = _run(tmp_path, _flat(300).assign(urban=urban), {"i1": "descriptive"})
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col="urban")
    d = out["data"]
    assert d["min_cell_label"] == "urban=1" and d["min_cell_n_eff"] == pytest.approx(50)
    assert d["precision_halfwidth"] == pytest.approx(Z * math.sqrt(1 / 100 + 1 / 50), abs=1e-4)   # ≈ 0.340


def test_精度的组差按各组自己的行数算_不按非缺失行里的占比摊全样本() -> None:
    half, eff, cell = power_mod.precision(300, group_sizes=[100, 50])
    assert half == pytest.approx(Z * math.sqrt(1 / 100 + 1 / 50), rel=1e-6) and cell == pytest.approx(50)
    assert eff == pytest.approx(300)


# ══════════════════════════════ 4. 组差用分组列在簇内的 ρx、各组自己的权重


def _gap_frame(level: str, n: int = 1200, k: int = 40, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    county = np.repeat(np.arange(k), n // k)
    g = (county % 2) if level == "cluster" else np.tile([0, 1], n // 2)
    return pd.DataFrame({"y": rng.normal(size=n) + rng.normal(size=k)[county], "county": county, "g": g})


def _gap(tmp_path: Path, df: pd.DataFrame, **kw) -> dict:
    run = _run(tmp_path, df, {"i1": "descriptive"})
    power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col="g", **kw)
    return json.loads((run / "power.json").read_text(encoding="utf-8"))


def test_分组在簇内交替_ρx约为0_组差半宽接近不分簇的值(tmp_path: Path) -> None:
    rep = _gap(tmp_path, _gap_frame("alternate"), cluster_col="county")
    assert rep["treatment_icc"] == pytest.approx(0.0, abs=0.02)
    assert rep["precision_halfwidth"] == pytest.approx(Z * math.sqrt(1 / 600 + 1 / 600), rel=0.02)
    assert "ρx" in rep["reason"] and "从数据算出" in rep["treatment_icc_source"]


def test_分组是簇一级的_ρx为1_组差照原公式(tmp_path: Path) -> None:
    rep = _gap(tmp_path, _gap_frame("cluster"), cluster_col="county")
    assert rep["treatment_icc"] == pytest.approx(1.0)
    de = 1 + (1200 / 40 - 1) * 0.05
    assert rep["precision_halfwidth"] == pytest.approx(Z * math.sqrt(de / 600 + de / 600), abs=1e-4)


def test_只知道簇数不知道簇列_组差按ρx为1算并说明是保守的(tmp_path: Path) -> None:
    rep = _gap(tmp_path, _gap_frame("alternate"), clusters=40)
    de = 1 + (1200 / 40 - 1) * 0.05
    assert rep["treatment_icc"] == 1.0 and "保守" in rep["treatment_icc_source"]
    assert rep["precision_halfwidth"] == pytest.approx(Z * math.sqrt(de / 600 + de / 600), abs=1e-4)
    assert "保守" in rep["reason"]


def test_总体水平与最小分组仍取ρx为1(tmp_path: Path) -> None:
    rep = _gap(tmp_path, _gap_frame("alternate"), cluster_col="county")
    de = 1 + (1200 / 40 - 1) * 0.05
    assert rep["design_effect"] == pytest.approx(de, abs=1e-4)
    assert rep["min_cell_n_eff"] == pytest.approx(600 / de, abs=0.1)


def test_加权时各组按自己的权重算Kish_权重不均的那组有效样本量更小(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    n = 800
    g = np.repeat([0, 1], n // 2)
    w = np.where(g == 0, 1.0, rng.choice([0.2, 5.0], n))            # 第 0 组等权，第 1 组权重很不均
    df = pd.DataFrame({"y": rng.normal(size=n), "g": g, "weight": w})
    rep = _gap(tmp_path, df)
    assert rep["weight_column"] == "weight"
    w1 = w[g == 1]
    kish1 = w1.sum() ** 2 / (w1 ** 2).sum()
    assert rep["min_cell_label"] == "g=1" and rep["min_cell_n_eff"] == pytest.approx(kish1, abs=0.1)
    assert rep["precision_halfwidth"] == pytest.approx(Z * math.sqrt(1 / 400 + 1 / kish1), abs=1e-4)


def test_组差是排序第二类对参照组_不是行数最大的两组_跟describe同一对比(tmp_path: Path) -> None:
    """三组时行数最大的两组（g=2 与 g=30）不是排序的头两类（g=2 与 g=10）：精度要判 describe 报的那一对。
    取值 2 / 10 / 30 顺带核按数值排（按字符串排，10 会跑到 2 前面）。"""
    g = np.repeat([2, 10, 30], [300, 50, 250])
    rep = _gap(tmp_path, pd.DataFrame({"y": np.random.default_rng(9).normal(size=600), "g": g}))
    assert rep["precision_halfwidth"] == pytest.approx(Z * math.sqrt(1 / 300 + 1 / 50), abs=1e-4)
    assert "（g=10 对 g=2）" in rep["reason"] and rep["min_cell_label"] == "g=10", rep["reason"]
    half, _, _ = power_mod.precision(600, group_sizes=[300, 50, 250])
    assert half == pytest.approx(Z * math.sqrt(1 / 300 + 1 / 50), rel=1e-6)


def test_精度核心函数_各组权重设计效应与组差ρx() -> None:
    half, _, cell = power_mod.precision(1200, n_clusters=40, group_sizes=[600, 600], gap_rho_x=0.0)
    assert half == pytest.approx(Z * math.sqrt(2 / 600), rel=1e-6)
    assert cell == pytest.approx(600 / (1 + 29 * 0.05))
    half_w, _, cell_w = power_mod.precision(800, group_sizes=[400, 400], weight_deff=3.0,
                                            group_weight_deffs=[1.0, 2.0])
    assert half_w == pytest.approx(Z * math.sqrt(1 / 400 + 2 / 400), rel=1e-6) and cell_w == pytest.approx(200)


# ══════════════════════════════ 5. 退化输入


def test_样本量不大于0就拒(tmp_path: Path) -> None:
    run = _run(tmp_path, _flat(), {"i1": "descriptive", "i2": "causal"})
    for idea in ("i1", "i2"):
        with pytest.raises(errors.BadInput):
            power_api.power(str(run), idea=idea, effect=0.3, effect_source="测试", n=-5)
    with pytest.raises(ValueError):
        power_mod.precision(0)


def test_精度模式有意义的最小差别是0就拒(tmp_path: Path) -> None:
    run = _run(tmp_path, _flat(), {"i1": "descriptive"})
    with pytest.raises(errors.BadInput, match="不能是 0"):
        power_api.power(str(run), idea="i1", effect=0.0, effect_source="测试")
    with pytest.raises(ValueError):
        power_mod.analyze_precision("i", 0.0, 400)


def _no_nonfinite(text: str) -> dict:
    def boom(tok: str):
        raise AssertionError(f"power.json 里出现了 {tok}")
    return json.loads(text, parse_constant=boom)


def test_power_json里没有无穷与非数(tmp_path: Path) -> None:
    urban = [0.0] * 100 + [1.0] * 50 + [np.nan] * 150
    run = _run(tmp_path, _flat(300).assign(urban=urban, rid=np.arange(300)), {"i1": "descriptive"})
    power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col="urban", cluster_col="rid")
    rep = _no_nonfinite((run / "power.json").read_text(encoding="utf-8"))
    assert rep["precision_halfwidth"] > 0 and all(
        math.isfinite(v) for v in (rep["precision_halfwidth"], rep["min_cell_n_eff"], rep["treatment_icc"]))


# ══════════════════════════════ 6. 计划指纹认描述块


_OLD_KEYS = ("estimator", "outcome", "treatment", "controls", "fixed_effects", "cluster_level",
             "sample_filter", "interactions", "instruments", "running", "cutoff", "bandwidth",
             "unit", "time", "treat_time", "leads", "lags")


def _old_sha(raw: dict) -> str:
    keep = {k: raw.get(k) for k in _OLD_KEYS}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def test_描述块不同的两份describe计划指纹不同(tmp_path: Path) -> None:
    from oss.core.loop import plan_sha
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    base = {"plan_id": "p", "estimator": "describe", "outcome": "y"}
    a.write_text(json.dumps({**base, "description": {"kind": "gap", "groups": ["urban"]}}), encoding="utf-8")
    b.write_text(json.dumps({**base, "description": {"kind": "gap", "groups": ["east"]}}), encoding="utf-8")
    assert plan_sha(a) != plan_sha(b)


def test_描述块里只改写给人看的字不算换设计(tmp_path: Path) -> None:
    from oss.core.loop import plan_sha
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    base = {"plan_id": "p", "estimator": "describe", "outcome": "y"}
    desc = {"kind": "gap", "groups": ["urban"], "meaningful_difference": "5 个百分点"}
    a.write_text(json.dumps({**base, "description": desc}, ensure_ascii=False), encoding="utf-8")
    b.write_text(json.dumps({**base, "description": {
        **desc, "meaningful_difference": "5 个百分点（某政策线）",
        "benchmarks": [{"quantity": "q", "value": 1.0, "source": "s"}]}}, ensure_ascii=False), encoding="utf-8")
    assert plan_sha(a) == plan_sha(b)


@pytest.mark.parametrize("extra", [{}, {"description": None}])
def test_没有描述块的计划指纹跟老算法逐位一样(tmp_path: Path, extra: dict) -> None:
    from oss.core.loop import plan_sha
    raw = {"plan_id": "p", "estimator": "did", "outcome": "y", "unit": "unit", "time": "year",
           "treat_time": "treat_time", "cluster_level": "unit", **extra}
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(raw), encoding="utf-8")
    assert plan_sha(p) == _old_sha(raw)


# ══════════════════════════════ 7. 契约


@pytest.mark.parametrize("kw", [{"source": ""}, {"source": "  "}, {"quantity": ""}, {"quantity": " \n"}])
def test_基准的出处与量都不许空(kw: dict) -> None:
    from pydantic import ValidationError

    from oss.core.contracts import Benchmark
    base = {"quantity": "城镇就业人员周平均工作时间", "value": 46.3, "source": "某年鉴表 1-50"}
    with pytest.raises(ValidationError):
        Benchmark(**{**base, **kw})


def test_计划的权重栏说七种估计量都真加权() -> None:
    from oss.core.contracts import AnalysisPlan
    desc = AnalysisPlan.model_fields["weights"].description
    assert "七种估计量都真加权" in desc and "describe" in desc and "六种" not in desc


def test_计划的估计量栏写了describe() -> None:
    from oss.core.contracts import AnalysisPlan
    desc = AnalysisPlan.model_fields["estimator"].description
    assert "describe" in desc and "分组描述" in desc and "不带控制变量" in desc


# ══════════════════════════════ 8. 帮助与 MCP


def test_MCP的oss_power认得group_col_说明写了精度模式() -> None:
    from oss.mcp.tools import TOOLS
    t = TOOLS["oss_power"]
    assert "group_col" in t.properties
    assert "精度" in t.description and "precision_low" in t.description and "有意义的最小差别" in t.description
    assert "raw" in t.properties["effect_metric"]["description"]


def test_命令行power的帮助写了描述类的读法() -> None:
    """直接读 click 的选项对象：渲染出来的帮助会被框线折行，拿子串比不牢。"""
    from typer.main import get_command

    from oss.cli.main import app
    cmd = get_command(app).commands["power"]
    opts = {p.name: p.help or "" for p in cmd.params}
    assert "有意义的最小差别" in opts["effect"] and "raw" in opts["effect"]
    assert "raw" in opts["effect_metric"] and "精度" in opts["cluster_col"]
    assert "精度" in cmd.help and "precision_low" in cmd.help and "有意义的最小差别" in cmd.help


# ══════════════════════════════ 9. 有意义的差别按结局原单位给


def _prop_frame(n: int = 1000, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"long": (rng.random(n) < 0.3).astype(float), "grp": rng.integers(0, 2, n),
                         "weight": rng.uniform(0.5, 2.0, n)})


def _wsd(y: np.ndarray, w: np.ndarray) -> float:
    m = np.average(y, weights=w)
    return float(np.sqrt(np.average((y - m) ** 2, weights=w)))


def test_raw按结局加权标准差折成标准差单位_判词写两种单位(tmp_path: Path) -> None:
    df = _prop_frame()
    run = _run(tmp_path, df, {"i1": "descriptive"})
    _bind_outcome(run, "i1", "long")
    out = power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    sd = _wsd(df["long"].to_numpy(), df["weight"].to_numpy())
    assert rep["effect_metric"] == "raw" and rep["effect_size_raw"] == 0.05
    assert rep["effect_size_adjusted"] == pytest.approx(0.05 / sd, abs=1e-4)
    head = f"有意义的最小差别 0.05（结局原单位）= {0.05 / sd:.3f} 个标准差（结局加权标准差 {sd:.3f}）"
    assert head in rep["reason"], rep["reason"]
    assert out["data"]["mode"] == "precision"


def test_d模式有结局标准差时也给原单位的半宽(tmp_path: Path) -> None:
    df = _prop_frame()
    run = _run(tmp_path, df, {"i1": "descriptive"})
    _bind_outcome(run, "i1", "long")
    power_api.power(str(run), idea="i1", effect=0.1, effect_source="测试")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    sd = _wsd(df["long"].to_numpy(), df["weight"].to_numpy())
    half = rep["precision_halfwidth"]
    assert f"半宽 {half:.3f} 个标准差 ≈ {half * sd:.3f}（结局原单位）" in rep["reason"], rep["reason"]


def test_raw的结局列不在数据里就拒_说改用标准差单位(tmp_path: Path) -> None:
    run = _run(tmp_path, _prop_frame(), {"i1": "descriptive"})
    _bind_outcome(run, "i1", "nope")
    with pytest.raises(errors.BadInput, match="标准差"):
        power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试")


def test_raw没绑结局也拒(tmp_path: Path) -> None:
    run = _run(tmp_path, _prop_frame(), {"i1": "descriptive"})
    with pytest.raises(errors.BadInput, match="标准差"):
        power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试")


def test_raw的结局从本题的计划取(tmp_path: Path) -> None:
    run = _run(tmp_path, _prop_frame(), {"i1": "descriptive"})
    _plan(run, idea_id="i1", research_type="descriptive", outcome="long")
    out = power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试")
    assert out["data"]["mode"] == "precision"


def test_功效模式不认raw(tmp_path: Path) -> None:
    run = _run(tmp_path, _prop_frame(), {"i1": "causal"})
    _bind_outcome(run, "i1", "long")
    with pytest.raises(errors.BadInput, match="raw"):
        power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试")
    with pytest.raises(ValueError):
        power_mod.to_cohens_d(0.05, "raw")                          # raw 不进 EFFECT_METRICS


# ══════════════════════════════ 10. 精度只数分析真会用到的行（终审 M1、M3）


def _survey_frame(seed: int = 7, k: int = 40, m: int = 30, miss_y: float = 0.0) -> pd.DataFrame:
    """跟终审探针同一个形状：区县整群、城乡在区县内不全同、城镇的权重更不均。"""
    rng = np.random.default_rng(seed)
    n = k * m
    county = np.repeat(np.arange(1, k + 1), m)
    urban = (rng.random(n) < 0.3 + 0.4 * (county % 2)).astype(float)
    u = rng.normal(0, 0.6, k)[county - 1]
    y = (rng.random(n) < 1 / (1 + np.exp(-(-0.3 + 0.5 * urban + u)))).astype(float)
    if miss_y:
        y[rng.random(n) < miss_y] = np.nan
    return pd.DataFrame({"online": y, "urban": urban, "county": county,
                         "weight": rng.lognormal(0, 0.5, n) * (1 + urban), "age": rng.integers(18, 61, n)})


def _anova_icc(v: np.ndarray, g: np.ndarray) -> float:
    """独立重算的组内相关（单因素方差分解），不走 power_mod.cluster_icc。"""
    labs, inv = np.unique(g, return_inverse=True)
    k, n = len(labs), len(v)
    nj = np.bincount(inv)
    mj = np.bincount(inv, weights=v) / nj
    msb = (nj * (mj - v.mean()) ** 2).sum() / (k - 1)
    msw = ((v - mj[inv]) ** 2).sum() / (n - k)
    m0 = (n - (nj ** 2).sum() / n) / (k - 1)
    return float(min(max((msb - msw) / (msb + (m0 - 1) * msw), 0.0), 1.0))


def _kish(w: np.ndarray) -> float:
    return float(w.sum() ** 2 / (w ** 2).sum())


def _expected_gap_half(df: pd.DataFrame, icc: float = 0.05) -> float:
    """城乡差的半宽，只用 df 里的行重算：簇数、每簇行数、组差的 ρx、各组的 Kish 都按这批行。"""
    m = len(df) / df["county"].nunique()
    rho = _anova_icc((df["urban"] == 1.0).astype(float).to_numpy(), df["county"].to_numpy())
    de_gap = 1 + (m - 1) * icc * rho
    k0, k1 = (_kish(df.loc[df["urban"] == v, "weight"].to_numpy()) for v in (0.0, 1.0))
    return Z * math.sqrt(de_gap / k0 + de_gap / k1)


def test_给了n又给分组就拒_整群按n折分组按全文件数两头对不上(tmp_path: Path) -> None:
    """终审 M1 的复现：1200 行 40 个区县，--n 300 配 --group-col urban。原先整群按 m = 300/40 折、各格照数全文件，
    半宽从 0.1439 掉到 0.1319，最小分组的有效样本量 349.9 比 n 还大。"""
    run = _run(tmp_path, _survey_frame(), {"i1": "descriptive"})
    _bind_outcome(run, "i1", "online")
    with pytest.raises(errors.BadInput) as e:
        power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试",
                        group_col="urban", cluster_col="county", n=300)
    msg = str(e.value)
    assert "prep.py" in msg and "sample_filter" in msg and "两头对不上" in msg, msg
    # 不带分组的 --n 照旧
    power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试",
                    cluster_col="county", n=300)
    assert json.loads((run / "power.json").read_text(encoding="utf-8"))["n_raw"] == 300


def test_结局缺失的行不算进精度_跟describe用同一批行(tmp_path: Path) -> None:
    """终审 M3 的复现：结局缺三成，原先按全文件 1200 行算，半宽 0.1461；describe 只用结局不缺的行，按那批行是 0.1699。"""
    df = _survey_frame(seed=11, miss_y=0.30)
    run = _run(tmp_path, df, {"i1": "descriptive"})
    _bind_outcome(run, "i1", "online")
    out = power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试",
                          group_col="urban", cluster_col="county")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    obs = df.dropna(subset=["online"])
    assert rep["n_raw"] == len(obs), rep["n_raw"]
    assert out["data"]["precision_halfwidth"] == pytest.approx(_expected_gap_half(obs), abs=2e-4)
    assert f"结局 online 缺失 {len(df) - len(obs)} 行" in rep["reason"], rep["reason"]


def test_权重缺失或不为正的行不算(tmp_path: Path) -> None:
    df = _survey_frame()
    df.loc[:99, "weight"] = np.nan
    df.loc[100:149, "weight"] = 0.0
    run = _run(tmp_path, df, {"i1": "descriptive"})
    _bind_outcome(run, "i1", "online")
    out = power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试",
                          group_col="urban", cluster_col="county", weight_col="weight")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    kept = df[df["weight"] > 0]
    assert rep["n_raw"] == len(kept)
    assert out["data"]["precision_halfwidth"] == pytest.approx(_expected_gap_half(kept), abs=2e-4)
    assert "权重 weight 缺失或不为正 150 行" in rep["reason"], rep["reason"]


def test_计划的sample_filter筛掉的行不算(tmp_path: Path) -> None:
    df = _survey_frame()
    run = _run(tmp_path, df, {"i1": "descriptive"})
    # F3：有本题的计划时权重按计划取（计划不写 weights 就不加权）；这里要测筛选，计划照 oss run 的样子加权
    _plan(run, idea_id="i1", research_type="descriptive", estimator="describe", outcome="online", weights="weight",
          sample_filter="age >= 30", description={"kind": "gap", "groups": ["urban"]})
    out = power_api.power(str(run), idea="i1", effect=0.05, effect_metric="raw", effect_source="测试",
                          group_col="urban", cluster_col="county")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    kept = df.query("age >= 30")
    assert rep["n_raw"] == len(kept), rep["n_raw"]
    assert out["data"]["precision_halfwidth"] == pytest.approx(_expected_gap_half(kept), abs=2e-4)
    assert "`age >= 30`" in rep["reason"] and f"筛掉 {len(df) - len(kept)} 行" in rep["reason"], rep["reason"]


# ══════════════════════════════ 11. 不写 --idea 时算哪道题（终审 M2）


_EST = {"unit_quantity": "上网比例之差", "target_population": "成年人", "assumptions": ["覆盖"], "link": "加权"}


def test_换题后不写idea_按锚点当前的题算_route不开mismatch(tmp_path: Path) -> None:
    """终审 M2 的复现：i1（描述）真跑过、用户授权换到 i2（因果）、i2 的因果计划写好了，不写 --idea 的 oss power
    原先算的是 ideas[0] = i1 的精度，还说 i2 的计划是「旧计划」；route 开 power_mode_mismatch，处方「重跑 oss power」
    重跑还是 i1，永远消不掉。"""
    from oss.api import anchor as anchor_api
    from oss.api.analysis import run as run_api
    from oss.core import route as route_mod

    run = _run(tmp_path, _survey_frame(), {"i1": "descriptive", "i2": "causal"})
    _bind_outcome(run, "i1", "online")
    survey = {"psu": "county", "weight_use": "descriptive", "design_source": "码本第 3 页"}
    _plan(run, idea_id="i1", research_type="descriptive", estimator="describe", outcome="online",
          weights="weight", survey=survey, estimand=_EST, description={"kind": "gap", "groups": ["urban"]})
    run_api(str(run))
    anchor_api.switch(str(run), to="i2", why="用户换题", authorized_by="user")
    _plan(run, idea_id="i2", research_type="causal", estimator="ols_fe", outcome="online", treatment="urban",
          weights="weight", survey={**survey, "weight_use": "model"}, estimand=_EST)
    out = power_api.power(str(run), effect=0.2, effect_source="测试")["data"]
    assert out["idea_id"] == "i2" and out["mode"] == "power", out
    assert not any("旧计划" in w for w in out["warnings"]), out["warnings"]
    assert "power_mode_mismatch" not in {i.id for i in route_mod.build(run).items}


def test_不写idea_锚点先于盘上的计划_都没有按ideas第一条(tmp_path: Path) -> None:
    run = _run(tmp_path, _flat(), {"i1": "descriptive", "i2": "causal"})
    assert power_api.power(str(run), effect=0.3, effect_source="测试")["data"]["idea_id"] == "i1"
    _plan(run, idea_id="i2", research_type="causal")
    out = power_api.power(str(run), effect=0.3, effect_source="测试")["data"]
    assert out["idea_id"] == "i2" and out["mode"] == "power", out
    _plan(run, idea_id="i1", research_type="descriptive")      # 盘上的计划指 i1，锚点在 i2
    (run / "run_state.json").write_text(json.dumps({"anchor": {
        "direction": "d", "idea_id": "i2", "plan_sha": "", "source": "model", "research_type": "",
        "frozen_at": "2026-09-15T00:00:00Z"}}), encoding="utf-8")
    assert power_api.power(str(run), effect=0.3, effect_source="测试")["data"]["idea_id"] == "i2"


# ══════════════════════════════ 12. 主要比较按计划的 description.kind 定（终审 M5）


def _kind_run(tmp_path: Path, kind: str, **desc) -> tuple[Path, dict]:
    rng = np.random.default_rng(13)
    df = pd.DataFrame({"y": rng.normal(size=600), "g": np.repeat([0, 1, 2], [300, 100, 200]),
                       "year": np.repeat([2010, 2015, 2020], [300, 100, 200])})
    run = _run(tmp_path, df, {"i1": "descriptive"})
    _plan(run, idea_id="i1", research_type="descriptive", estimator="describe", outcome="y",
          description={"kind": kind, **desc})
    col = "year" if kind == "trend" else "g"
    power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col=col)
    return run, json.loads((run / "power.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", ["level", "distribution"])
def test_计划是level或distribution_有分组也按总体水平判_仍报最小分组(tmp_path: Path, kind: str) -> None:
    """describe 在 level / distribution 的主结果是全样本均值：精度要判的是它，不是头两组之差。"""
    _, rep = _kind_run(tmp_path, kind, groups=["g"])
    assert rep["precision_halfwidth"] == pytest.approx(Z / math.sqrt(600), abs=1e-4), rep["reason"]
    assert rep["min_cell_label"] == "g=1" and rep["min_cell_n_eff"] == pytest.approx(100)
    assert "总体水平" in rep["reason"] and kind in rep["reason"], rep["reason"]


def test_计划是gap_照旧按组差(tmp_path: Path) -> None:
    _, rep = _kind_run(tmp_path, "gap", groups=["g"])
    assert rep["precision_halfwidth"] == pytest.approx(Z * math.sqrt(1 / 300 + 1 / 100), abs=1e-4)
    assert "（g=1 对 g=0）" in rep["reason"], rep["reason"]


def test_计划是trend_按首两期之差近似_判词写明(tmp_path: Path) -> None:
    _, rep = _kind_run(tmp_path, "trend", over="year")
    assert rep["precision_halfwidth"] == pytest.approx(Z * math.sqrt(1 / 300 + 1 / 100), abs=1e-4)
    assert "趋势的精度按首两期之差近似" in rep["reason"], rep["reason"]


# ══════════════════════════════ 13. 描述类的关联计划（不是 describe）按 treatment 分组（终审 M4）


def _assoc_run(tmp_path: Path, treatment: str) -> tuple[dict, dict]:
    rng = np.random.default_rng(17)
    df = pd.DataFrame({"y": rng.normal(size=600), "urban": np.repeat([0, 1], [400, 200]), "x": rng.normal(size=600)})
    run = _run(tmp_path, df, {"i1": "causal"})
    _plan(run, idea_id="i1", research_type="descriptive", estimator="ols_fe", outcome="y", treatment=treatment)
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试")
    return out["data"], json.loads((run / "power.json").read_text(encoding="utf-8"))


def test_描述类关联计划_二值treatment按两组之差算(tmp_path: Path) -> None:
    """因果降为描述的 ols_fe、treatment 是二值：按总体水平算的半宽顶多是组差的一半，判词会松一倍。"""
    d, rep = _assoc_run(tmp_path, "urban")
    assert d["mode"] == "precision"
    assert rep["precision_halfwidth"] == pytest.approx(Z * math.sqrt(1 / 400 + 1 / 200), abs=1e-4), rep["reason"]
    assert "计划是描述类的关联（ols_fe），按 treatment 列 urban 的两组之差算精度" in d["warnings"], d["warnings"]


def test_描述类关联计划_连续treatment按总体水平_判词写读作标准化系数(tmp_path: Path) -> None:
    d, rep = _assoc_run(tmp_path, "x")
    assert rep["precision_halfwidth"] == pytest.approx(Z / math.sqrt(600), abs=1e-4)
    assert "标准化系数" in rep["reason"] and "每变一个标准差" in rep["reason"], rep["reason"]
    assert not any("两组之差" in w for w in d["warnings"])


# ══════════════════════════════ 14. 没有整群信息时不写聚类（终审 M8）


def test_截面没给簇列_不写聚类不写保守_整群的提醒只一条(tmp_path: Path) -> None:
    """原先 power.json 记 n_clusters = 1200（每行一簇），判词写「1200 个观测聚类到 1200 个单位」「ρx 取 1——保守」，
    设计效应却是 1.00；警告两条说的是同一件事。"""
    run = _run(tmp_path, _county_frame(), {"i1": "descriptive"})
    out = power_api.power(str(run), idea="i1", effect=0.3, effect_source="测试", group_col="urban")
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert rep["n_clusters"] is None, rep["n_clusters"]
    assert "聚类到" not in rep["reason"] and "保守" not in rep["reason"], rep["reason"]
    about = [w for w in out["data"]["warnings"] if "整群" in w]
    assert len(about) == 1 and "--cluster-col county" in about[0], out["data"]["warnings"]


# ══════════════════════════════ 15. 分组的格与标签（终审 M11、M6）


def test_分类型分组列_没出现的类不成空格() -> None:
    """parquet 读进来的分类列，pandas 2.2 的 groupby 缺省把没出现的类也算成一格（行数 0）。"""
    df = pd.DataFrame({"y": np.zeros(500), "area": pd.Categorical(["农村"] * 300 + ["城市"] * 200,
                                                                 categories=["郊区", "农村", "城市"])})
    warns: list[str] = []
    sizes, labels, _, _, _ = power_api._group_cells(df, ["area"], "", None, "", warns)
    assert labels == ["area=农村", "area=城市"] and sizes == [300, 200], (labels, sizes)


def test_分组标签跟describe用同一个函数() -> None:
    """标签两边要逐字对得上：原先 power 这边抄了一份 _cell_value，跟 analysis._level_text 各写一遍。"""
    from oss.core import analysis
    assert power_api._level_text is analysis._level_text
    assert not hasattr(power_api, "_cell_value")


# ══════════════════════════════ 16. MCP、帮助与契约的措辞（终审 M7、M10）


def test_MCP的oss_power有出处度量与权重列() -> None:
    from oss.mcp.tools import TOOLS
    props = TOOLS["oss_power"].properties
    for k in ("effect_source", "effect_metric", "weight_col"):
        assert k in props and props[k]["description"].strip(), k
    assert "raw" in props["effect_metric"]["description"]


def test_MCP的oss_power给了出处就不封顶(tmp_path: Path) -> None:
    from oss.mcp.tools import TOOLS
    run = _run(tmp_path, _flat(1200), {"i1": "descriptive"})
    d = TOOLS["oss_power"].fn(run=str(run), idea="i1", effect=0.3, effect_source="某政策线", weight_col="")["data"]
    assert d["verdict"] == "pass" and d["weight_column"] == "", d


def test_设计锁的估计量清单跟工具箱一致() -> None:
    """**判据挂在算得出来的事实上，不挂在写死的数字上。**

    原先写的是「七个」，加了 logit / ologit 之后得改成「九个」——
    而那意味着每加一个估计量就要改一次测试，改的时候很容易顺手把
    清单本身对不对给放过去。现在按 `SUPPORTED_ESTIMATORS` 的实际长度核，
    数字和名字都不许漏。
    """
    from oss.core.analysis import SUPPORTED_ESTIMATORS
    from oss.core.contracts import DesignLock

    desc = DesignLock.model_fields["estimator"].description
    # 中文数字过十之后不是一个字，按十进制拼——**判据仍然挂在实际长度上**
    n = len(SUPPORTED_ESTIMATORS)
    d = "零一二三四五六七八九"
    cn = d[n] if n < 10 else ("十" + (d[n % 10] if n % 10 else ""))
    assert f"那{cn}个" in desc, f"清单有 {len(SUPPORTED_ESTIMATORS)} 个，描述里没说「那{cn}个」：{desc}"
    missing = [e for e in SUPPORTED_ESTIMATORS if e not in desc]
    assert not missing, f"设计锁的描述里漏了：{missing}"


def test_命令行power的group_col帮助写全() -> None:
    from typer.main import get_command

    from oss.cli.main import app
    opts = {p.name: p.help or "" for p in get_command(app).commands["power"].params}
    h = opts["group_col"]
    for k in ("逗号", "至多两列", "参照组", "最小分组", "--n"):
        assert k in h, (k, h)


def test_MCP说明书的oss_power一行写了精度() -> None:
    doc = (Path(__file__).resolve().parents[1] / "docs" / "user" / "mcp.md").read_text(encoding="utf-8")
    line = next(x for x in doc.splitlines() if x.startswith("| `oss_power`"))
    assert "精度" in line and "描述" in line, line


# ══════════════════════════════ 17. 有本题的计划：没给的按计划取，分析样本跟 oss run 同一套选行（F1 复审）


_SURVEY = {"strata": "stratum", "psu": "county", "weight_use": "descriptive", "design_source": "码本"}
_NOT_SURVEY = {"not_survey_reason": "测试：不按调查处理"}
_GC = {"group_col": "urban", "cluster_col": "county"}
_SAY = "按本题计划的分组列 urban、整群列 county、权重 weight 算"


def _review_frame(seed: int = 7, k: int = 40, m: int = 30) -> pd.DataFrame:
    """F1 复审的夹具（f1_frame.make）：40 个区县分 4 层，城乡在区县内不全同，权重对数正态。"""
    rng = np.random.default_rng(seed)
    n = k * m
    county = np.repeat(np.arange(1, k + 1), m).astype(float)
    urban = (rng.random(n) < 0.3 + 0.4 * (county % 2)).astype(float)
    u = rng.normal(0, 0.6, k)[(county - 1).astype(int)]
    y = (rng.random(n) < 1 / (1 + np.exp(-(-0.3 + 0.5 * urban + u)))).astype(float)
    return pd.DataFrame({"online": y, "urban": urban, "county": county, "stratum": (county - 1) // 10 + 1,
                         "weight": rng.lognormal(0, 0.5, n), "age": rng.integers(18, 61, n).astype(float),
                         "year": rng.choice([2010.0, 2012.0, 2014.0], n),
                         "area": np.where(urban == 1, "城市", "农村")})


def _blank(col: str, frac: float, seed: int):
    """复审脚本的 blank：把一列随机挖掉一部分。返回改数据的函数，参数化用。"""
    def change(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.loc[np.random.default_rng(seed).random(len(df)) < frac, col] = np.nan
        return df
    return change


def _same(df: pd.DataFrame) -> pd.DataFrame:
    return df


def _design_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """分层缺 5%、区县（初级抽样单位）缺 3%。"""
    return _blank("county", 0.03, 5)(_blank("stratum", 0.05, 4)(df))


def _dplan(kind: str = "gap", groups: tuple = ("urban",), over: str = "", weights: str = "weight",
           survey: dict | None = None, **extra) -> dict:
    d: dict = {"kind": kind, "groups": list(groups), "benchmarks": [{"quantity": "q", "value": 0.7, "source": "s"}]}
    if over:
        d["over"] = over
    p = {"idea_id": "i1", "research_type": "descriptive", "estimator": "describe", "outcome": "online",
         "weights": weights, "estimand": _EST, "description": d, **extra}
    p["survey"] = _SURVEY if survey is None else survey
    if not p["survey"]:
        del p["survey"]
    return p


_ASSOC = {"idea_id": "i1", "research_type": "descriptive", "estimator": "ols_fe", "outcome": "online",
          "treatment": "urban", "controls": ["age"], "weights": "weight", "survey": _SURVEY, "estimand": _EST}


def _review_run(tmp_path: Path, df: pd.DataFrame, plan: dict) -> Path:
    run = _run(tmp_path, df, {"i1": "descriptive"})
    _bind_outcome(run, "i1", "online")
    _plan(run, **plan)
    return run


def _spy(monkeypatch: pytest.MonkeyPatch) -> dict:
    """记下 analyze_precision 拿到的样本量与各格（复审脚本的探针）。"""
    cap: dict = {}
    orig = power_mod.analyze_precision

    def spy(idea_id, effect, n, **kw):
        cap.clear()
        cap.update(kw, n=n)
        return orig(idea_id, effect, n, **kw)

    monkeypatch.setattr(power_mod, "analyze_precision", spy)
    return cap


def _oss_run_rows(run: Path) -> tuple[int, list[tuple[str, int]]]:
    """oss run 的分析样本：直接调 run_plan（只比选行，不过 validate_plan 那道计划校验）。"""
    from oss.core import analysis
    from oss.core.contracts import AnalysisPlan
    from oss.core.loaders import load_any
    res = analysis.run_plan(load_any(str(run / "data.csv")).frame, AnalysisPlan.load(run / "plan.json"))
    return res.n, [(g.label, g.n) for g in res.groups]


_FRAME_CASES = [
    pytest.param(_blank("online", 0.2, 3), _dplan(), _GC, id="A_missing_outcome"),
    pytest.param(_same, _dplan(sample_filter="age >= 25"), _GC, id="C_sample_filter"),
    pytest.param(_same, _dplan(groups=("area",)), {"group_col": "area", "cluster_col": "county"},
                 id="D_string_group"),
    pytest.param(_design_gaps, _dplan(), _GC, id="E1_strata_psu_missing_with_cluster_col"),
    pytest.param(_design_gaps, _dplan(), {"group_col": "urban"}, id="E2_strata_psu_missing_no_cluster_col"),
    pytest.param(_blank("urban", 0.10, 6), _dplan(kind="level"), {"cluster_col": "county"},
                 id="F1_level_groups_missing_no_group_col"),
    pytest.param(_blank("urban", 0.10, 6), _dplan(kind="level"), _GC, id="F2_level_groups_missing_with_group_col"),
    pytest.param(_blank("year", 0.05, 8), _dplan(kind="trend", groups=(), over="year"), {"cluster_col": "county"},
                 id="G1_trend_over_missing_no_group_col"),
    pytest.param(_blank("year", 0.05, 8), _dplan(kind="trend", groups=(), over="year"),
                 {"group_col": "year", "cluster_col": "county"}, id="G2_trend_over_missing_group_col_year"),
    pytest.param(_blank("weight", 0.05, 9), _dplan(weights="", survey={}), {"group_col": "urban"},
                 id="H_unweighted_plan_profile_weight_missing"),
    pytest.param(_blank("weight", 0.05, 9), _dplan(weights="", survey=_NOT_SURVEY), {"group_col": "urban"},
                 id="H2_unweighted_not_survey_profile_weight_missing"),
    pytest.param(_blank("county", 0.03, 10), _dplan(survey={}, cluster_level="county"), _GC,
                 id="I_cluster_level_missing"),
    pytest.param(_blank("county", 0.03, 10), _dplan(survey=_NOT_SURVEY, cluster_level="county"), _GC,
                 id="I2_cluster_level_missing_not_survey"),
    pytest.param(_blank("age", 0.10, 11), _ASSOC, {"cluster_col": "county"}, id="K_assoc_control_missing"),
]


@pytest.mark.parametrize(("change", "plan", "kw"), _FRAME_CASES)
def test_有计划时精度的行跟oss_run同一批(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change, plan: dict,
                                 kw: dict) -> None:
    """F1 复审 f1_frame 的表：原先精度数了 oss run 去掉的行（分层或抽样单位缺失；没给分组列时分组或分期缺失；
    关联计划的控制变量缺失），也去掉了 oss run 留着的行（不加权的计划碰上画像的权重列；聚类层缺失）。"""
    cap = _spy(monkeypatch)
    run = _review_run(tmp_path, change(_review_frame()), plan)
    out = power_api.power(str(run), idea="i1", effect=0.2, effect_source="测试", **kw)["data"]
    n, cells = _oss_run_rows(run)
    assert cap["n"] == n, (cap["n"], n, out["reason_lines"])
    got = list(zip(cap.get("group_labels") or [], cap.get("group_sizes") or []))
    if got and cells and cells[0][0] != "全样本":
        assert got == cells, (got, cells)
    assert not any("oss run 也用不上" in x for x in out["reason_lines"]), out["reason_lines"]
    if n < 1200:
        assert any("跟 oss run 同一套选行" in x for x in out["reason_lines"]), out["reason_lines"]


def test_有计划时oss_run会拒的行_精度也拒_理由照抄(tmp_path: Path) -> None:
    """F1 复审 B：权重有 0，oss run 整个拒跑；原先精度去掉那几行照算，还说「这些行 oss run 也用不上」。"""
    from oss.core import analysis
    df = _review_frame()
    df.loc[:9, "weight"] = 0.0
    run = _review_run(tmp_path, df, _dplan())
    with pytest.raises(analysis.PlanRejected) as ref:
        _oss_run_rows(run)
    with pytest.raises(errors.BadInput) as e:
        power_api.power(str(run), idea="i1", effect=0.2, effect_source="测试", **_GC)
    assert ref.value.problems[0] in str(e.value), (ref.value.problems, str(e.value))


def test_有计划时没给的按计划取_分组整群权重_警告与判词各说一次(tmp_path: Path) -> None:
    """F1 复审 S1 的根子：不带 --group-col / --cluster-col 重跑，组差计划被悄悄改判成总体水平、不算整群。"""
    run = _review_run(tmp_path, _review_frame(), _dplan())
    auto = power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试")["data"]
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert rep["n_clusters"] == 40 and auto["min_cell_label"] == "urban=1" and auto["weight_column"] == "weight"
    assert auto["verdict"] == "fail" and auto["precision_halfwidth"] == pytest.approx(0.1439, abs=1e-4)
    assert sum(_SAY in w for w in auto["warnings"]) == 1, auto["warnings"]
    assert sum(_SAY in x for x in auto["reason_lines"]) == 1, auto["reason_lines"]
    full = power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试", group_col="urban",
                           cluster_col="county", weight_col="weight")["data"]
    assert full["precision_halfwidth"] == auto["precision_halfwidth"]
    assert not any("按本题计划" in w for w in full["warnings"]), full["warnings"]


def test_有计划时显式给的照给的(tmp_path: Path) -> None:
    run = _review_run(tmp_path, _review_frame(), _dplan())
    d = power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试", group_col="area",
                        cluster_col="stratum", weight_col="")["data"]
    rep = json.loads((run / "power.json").read_text(encoding="utf-8"))
    assert rep["n_clusters"] == 4 and d["min_cell_label"].startswith("area="), (rep["n_clusters"], d)
    assert d["weight_design_effect"] == 1.0 and d["weight_column"] == ""
    assert not any("按本题计划" in w for w in d["warnings"]), d["warnings"]


def test_计划不加权_画像有权重列也不按权重折(tmp_path: Path) -> None:
    run = _review_run(tmp_path, _review_frame(), _dplan(weights="", survey=_NOT_SURVEY))
    d = power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试", group_col="urban")["data"]
    assert d["weight_design_effect"] == 1.0 and d["weight_column"] == "", d
    assert any("按本题计划" in w and "不加权" in w for w in d["warnings"]), d["warnings"]


def test_计划没写抽样单位_整群列取cluster_level(tmp_path: Path) -> None:
    run = _review_run(tmp_path, _review_frame(), _dplan(survey=_NOT_SURVEY, cluster_level="county"))
    d = power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试")["data"]
    assert json.loads((run / "power.json").read_text(encoding="utf-8"))["n_clusters"] == 40
    assert any("整群列 county" in w for w in d["warnings"]), d["warnings"]


def test_计划是别的题的_不按它取分组整群权重(tmp_path: Path) -> None:
    run = _run(tmp_path, _review_frame(), {"i1": "descriptive", "i2": "descriptive"})
    _bind_outcome(run, "i1", "online")
    _plan(run, **{**_dplan(), "idea_id": "i2"})
    d = power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试")["data"]
    assert json.loads((run / "power.json").read_text(encoding="utf-8"))["n_clusters"] is None
    assert d["min_cell_label"] == "" and d["weight_column"] == "weight"
    assert d["precision_halfwidth"] == pytest.approx(0.0641, abs=1e-4)
    assert not any("按本题计划" in w for w in d["warnings"]), d["warnings"]


def test_按计划取的分组也不许跟n一起用(tmp_path: Path) -> None:
    run = _review_run(tmp_path, _review_frame(), _dplan())
    with pytest.raises(errors.BadInput) as e:
        power_api.power(str(run), idea="i1", effect=0.12, effect_source="测试", n=500)
    assert "两头对不上" in str(e.value) and "urban" in str(e.value), str(e.value)


# ══════════════════════════════ 18. 锚点指向 ideas.json 里没有的题（F1 复审 M2-3）


def test_锚点指向ideas里没有的题_警告点名(tmp_path: Path) -> None:
    run = _run(tmp_path, _flat(), {"i1": "descriptive", "i2": "causal"})
    (run / "run_state.json").write_text(json.dumps({"anchor": {"idea_id": "i9"}}), encoding="utf-8")
    d = power_api.power(str(run), effect=0.3, effect_source="测试")["data"]
    assert d["idea_id"] == "i1"
    assert any("i9" in w and "ideas.json" in w for w in d["warnings"]), d["warnings"]
