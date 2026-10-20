"""政策冲击对比法与逐年展开图：让系统能做「因果」而不只是「关联」。

**现在两篇论文都必须写「本文不是因果推断」——不是因为问题不允许，
是因为工具做不到。**评审团砍掉的那个研究想法，理由之一就是「工具箱也不支持」。

**每一个新方法都必须有一个能独立算出同一答案的对照。**
教训来自更早的一次：手抄的正态分位数逼近返回 1.0017 而不是 1.96，
功效被系统性高估三倍，**而且不报错**。新方法只会更容易出这种事。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TRUE_EFFECT = 3.0


@pytest.fixture(scope="module")
def clean():
    return pd.read_csv(FIXTURES / "did_clean.csv")


@pytest.fixture(scope="module")
def pretrend():
    return pd.read_csv(FIXTURES / "did_pretrend.csv")


def did_plan(**kw):
    from oss.core.contracts import AnalysisPlan

    base = dict(
        plan_id="p", idea_id="i", estimator="did", outcome="y",
        unit="unit", time="year", treat_time="treat_time",
        cluster_level="unit",
    )
    return AnalysisPlan(**{**base, **kw})


# ══════════════════════════════ 数值正确性：算出来的必须等于我设定的


def test_政策冲击对比法能还原真实效应(clean) -> None:
    """**夹具里的真实效应是 3.0，算出来必须接近它。**

    这是最硬的一条检验：数是我设定的，算错了立刻现形。
    """
    from oss.core.analysis import run_plan

    res = run_plan(clean, did_plan())
    got = res.estimates["main"].coef
    assert abs(got - TRUE_EFFECT) < 0.3, f"真实效应 {TRUE_EFFECT}，算出来 {got}"


def test_两组两期时结果等于手算的双重差分(clean) -> None:
    """**独立复核。**两组两期的情形有闭式解：

        (处理组后期均值 − 处理组前期均值) − (对照组后期均值 − 对照组前期均值)

    程序算的必须跟手算的一模一样。对不上就是公式写错了。
    """
    from oss.core.analysis import run_plan

    df = clean[(clean.year.isin([2008, 2012]))
               & (clean.treat_time.isna() | (clean.treat_time == 2010))].copy()
    res = run_plan(df, did_plan())

    g = df.assign(treated=df.treat_time.notna())
    m = g.groupby(["treated", "year"]).y.mean()
    manual = ((m[True, 2012] - m[True, 2008]) - (m[False, 2012] - m[False, 2008]))
    assert res.estimates["main"].coef == pytest.approx(manual, abs=1e-9), (
        f"程序算 {res.estimates['main'].coef}，手算 {manual}")


def test_分批实施要警告双向固定效应可能有偏(clean) -> None:
    """**这是这个方法最有名的坑。**

    政策分批铺开、且各批效应不同时，双向固定效应会把「早处理组」
    当成「晚处理组」的对照，算出来的东西可能连符号都不对。
    工具不该假装没这回事。
    """
    from oss.core.analysis import validate_plan
    from oss.core.profiler import profile_file

    prof = profile_file(FIXTURES / "did_clean.csv")
    _, warns = validate_plan(did_plan(), prof)
    joined = " ".join(warns)
    assert "分批" in joined or "staggered" in joined.lower(), warns
    assert "有偏" in joined, "要说清后果，不能只说「注意」"


# ══════════════════════════════ 分批按数据判，不按估计量的名字判


def _staggered_gate(path: Path, **kw) -> list[str]:
    """把计划连同**真要跑的那份数据**交给门，返回警告。"""
    from oss.core.analysis import validate_plan
    from oss.core.profiler import profile_file

    _, warns = validate_plan(did_plan(**kw), profile_file(path), pd.read_csv(path))
    return list(warns)


def test_分批写成ols_fe加post也要警告() -> None:
    """**换个写法就绕过去。**估计量写 `ols_fe`、处理写一个 post 哑变量、上单位与时间
    两个固定效应——算的跟 `did` 一模一样，坑也一模一样，而门原先认的是
    「计划里写的估计量名字」，一道都不响（考卷 A：主系数 2.129，真实平均处理效应 3.56）。
    """
    from oss.core.findings import code_of

    warns = _staggered_gate(FIXTURES / "did_staggered.csv", estimator="ols_fe",
                            treatment="post", fixed_effects=["unit", "year"])
    assert any(code_of(w) == "plan.did.staggered" for w in warns), warns


def test_连treat_time都没写的ols_fe分批也认得出() -> None:
    """判据是数据里各单位的首次处理期，不是计划里写没写 `treat_time`。"""
    from oss.core.findings import code_of

    warns = _staggered_gate(FIXTURES / "did_staggered.csv", estimator="ols_fe",
                            treatment="post", fixed_effects=["unit", "year"], treat_time="")
    assert any(code_of(w) == "plan.did.staggered" for w in warns), warns


def test_同时处理的ols_fe不误报分批(tmp_path) -> None:
    """所有单位同一期开始被处理：那是标准的两期双重差分，没有分批偏误可言。"""
    from oss.core.findings import code_of

    df = pd.read_csv(FIXTURES / "did_staggered.csv")
    df = df[df.treat_time.isna() | (df.treat_time == 2006)]
    path = tmp_path / "one_cohort.csv"
    df.to_csv(path, index=False)
    warns = _staggered_gate(path, estimator="ols_fe", treatment="post",
                            fixed_effects=["unit", "year"])
    assert not any(code_of(w) == "plan.did.staggered" for w in warns), warns


def test_不是面板就不判分批() -> None:
    """重复截面上「各单位的首次处理期」没有意义——**宁可漏，不可误报**。"""
    from oss.core.findings import code_of

    warns = _staggered_gate(FIXTURES / "gss_like.csv", estimator="ols_fe", outcome="sei",
                            treatment="female", fixed_effects=["year", "region"],
                            cluster_level="region_year", unit="", time="", treat_time="")
    assert not any(code_of(w) == "plan.did.staggered" for w in warns), warns


def test_处理变量不是01就不判分批() -> None:
    """连续的处理强度不是「何时开始被处理」，首次处理期无从谈起。"""
    from oss.core.findings import code_of

    warns = _staggered_gate(FIXTURES / "did_staggered.csv", estimator="ols_fe",
                            treatment="x", fixed_effects=["unit", "year"], treat_time="")
    assert not any(code_of(w) == "plan.did.staggered" for w in warns), warns


def test_只上一个固定效应就不判分批() -> None:
    """分批偏误说的是**双向**固定效应；只吸收单位、不吸收时间的不是那回事。"""
    from oss.core.findings import code_of

    warns = _staggered_gate(FIXTURES / "did_staggered.csv", estimator="ols_fe",
                            treatment="post", fixed_effects=["unit"], time="", treat_time="")
    assert not any(code_of(w) == "plan.did.staggered" for w in warns), warns


# ══════════════════════════════ 逐年展开：处理前平不平


def test_逐年展开能算出每一期的效应(clean) -> None:
    from oss.core.analysis import run_plan

    res = run_plan(clean, did_plan(estimator="event_study", leads=4, lags=4))
    assert res.event_study, "逐年展开的结果没落盘"
    ks = sorted(p.k for p in res.event_study)
    assert min(ks) <= -2 and max(ks) >= 3
    assert -1 not in ks, "第 -1 期是参照期，本身没有系数"


def test_处理前的系数应当接近零(clean) -> None:
    """干净的数据里，政策实施**之前**两组走势平行，所以处理前的系数该接近 0。"""
    from oss.core.analysis import run_plan

    res = run_plan(clean, did_plan(estimator="event_study", leads=4, lags=4))
    pre = [p for p in res.event_study if p.k < 0]
    assert pre, "没有处理前的期"
    assert max(abs(p.coef) for p in pre) < 1.0, [(p.k, p.coef) for p in pre]


def test_处理后的系数应当接近真实效应(clean) -> None:
    from oss.core.analysis import run_plan

    res = run_plan(clean, did_plan(estimator="event_study", leads=4, lags=4))
    post = [p for p in res.event_study if p.k >= 0]
    avg = float(np.mean([p.coef for p in post]))
    assert abs(avg - TRUE_EFFECT) < 0.5, [(p.k, round(p.coef, 2)) for p in post]


# ══════════════════════════════ 平行趋势检验：拦住假效应


def test_处理前就分化的数据要被检验拦住(pretrend) -> None:
    """**这份夹具里的「效应」是假的。**

    处理组在政策实施前就比对照组涨得快，双重差分会把这个趋势当成效应。
    **表格上看不出来，图上一眼可见**——而检验必须报出来。
    """
    from oss.core.analysis import run_plan

    res = run_plan(pretrend, did_plan(estimator="event_study", leads=4, lags=4))
    verdict = res.diagnostics.get("平行趋势", "")
    assert "不成立" in verdict or "站不住" in verdict, verdict
    assert "结论不可信" in verdict or "假" in verdict, "要说清后果"


def test_干净的数据不该被误判(clean) -> None:
    """**别把对的也标红**——警报天天响就等于没有警报。"""
    from oss.core.analysis import run_plan

    res = run_plan(clean, did_plan(estimator="event_study", leads=4, lags=4))
    assert "不成立" not in res.diagnostics.get("平行趋势", "")


def test_假效应的量级确实被抬高了(clean, pretrend) -> None:
    """确认这份夹具真的造出了假效应——否则上面那条测试是空的。"""
    from oss.core.analysis import run_plan

    a = run_plan(clean, did_plan()).estimates["main"].coef
    b = run_plan(pretrend, did_plan()).estimates["main"].coef
    assert b > a + 0.8, f"干净 {a:.2f}，有前趋势 {b:.2f}——夹具没造出假效应"


# ══════════════════════════════ 伴随的逐年展开：一个题目一个目录


def _fe_plan(**kw):
    from oss.core.contracts import AnalysisPlan
    base = dict(plan_id="p", idea_id="i", estimator="ols_fe", outcome="y", treatment="post",
                controls=["x"], fixed_effects=["unit", "year"], cluster_level="unit",
                unit="unit", time="year", treat_time="treat_time", leads=3, lags=3)
    return AnalysisPlan(**{**base, **kw})


def test_ols_fe计划写了treat_time就自动附带逐年展开(clean) -> None:
    """验证跑里主规格是带交互项的 ols_fe，事件研究只能另开目录跑——
    审计 FAIL 留在那边，主目录的门看不见它、判了「none」。伴随的逐年展开进同一份结果。"""
    from oss.core.analysis import run_plan

    res = run_plan(clean, _fe_plan())
    assert res.event_study, "写了 treat_time 的 ols_fe 应当附带逐年展开"
    for key in ("平行趋势", "对照组", "处理前平衡", "逐年展开", "批次异质性"):
        assert key in res.diagnostics, key
    assert res.estimates["main"].term == "post", "main 仍是主规格的，不是伴随规格的"
    post = [p for p in res.event_study if p.k >= 0]
    assert abs(sum(p.coef for p in post) / len(post) - TRUE_EFFECT) < 0.5


def test_没写treat_time的ols_fe不附带(clean) -> None:
    from oss.core.analysis import run_plan

    res = run_plan(clean, _fe_plan(unit="", time="", treat_time=""))
    assert not res.event_study and "逐年展开" not in res.diagnostics


def test_稳健性各项不再各自附带逐年展开(clean) -> None:
    from oss.core.analysis import run_plan
    from oss.core.contracts import RobustnessSpec

    res = run_plan(clean, _fe_plan(robustness=[
        RobustnessSpec(label="R1 去掉控制", controls=[], executable=True)]))
    assert res.event_study
    assert "逐年展开" not in res.robustness["R1 去掉控制"].diagnostics


def test_主规格带稳健性清单也要算批次异质性(clean) -> None:
    """原先靠 `if not plan.robustness` 判「内层」，判反了：主规格从来算不到，
    稳健性各算一遍——验证跑里执行者写「工具没有产出批次异质性诊断」就是这么来的。"""
    from oss.core.analysis import run_plan
    from oss.core.contracts import RobustnessSpec

    res = run_plan(clean, did_plan(robustness=[RobustnessSpec(label="R1", controls=["x"], executable=True)]))
    assert "批次异质性" in res.diagnostics
    assert "批次异质性" not in res.robustness["R1"].diagnostics


# ══════════════════════════════ 诊断按估计样本算，不按全文件算


def test_对照组与平衡诊断只看筛选后的样本(clean) -> None:
    """验收跑的论文不得不写一段解释「诊断说 159 个从未处理国，估计样本其实是 66 个」。"""
    from oss.core.analysis import run_plan

    full = run_plan(clean, did_plan())
    assert "20 个单位从未被处理" in full.diagnostics["对照组"], full.diagnostics["对照组"]
    part = run_plan(clean, did_plan(sample_filter="unit < 30"))
    assert "20 个单位从未被处理" not in part.diagnostics["对照组"], part.diagnostics["对照组"]
