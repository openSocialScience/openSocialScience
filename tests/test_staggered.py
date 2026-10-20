"""分批双重差分（Callaway–Sant'Anna，算法来自 StatsPAI）：查出病之后终于有药了。

验收跑的论文用 TWFE 做了 17 个批次的事件研究，各批效应差异极大（I² = 93%），
执行者把异质性诊断出来了却没换估计量——评审第一条致命问题。

**每一个新方法都必须有一个能独立算出同一答案的对照**（见 `test_did.py` 的教训）。
这里有三重对照：

1. 夹具的真实效应是我们设定的，算出来必须接近它（`did_staggered.csv` 带 `true_effect` 列）
2. 老方法（TWFE）在同一份数据上**必须算错**——否则证明不了为什么要换
3. 无协变量、平衡面板时按批的 ATT 有闭式解，程序算的必须跟手算的**一模一样**
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("statspai", reason="did_cs 需要 StatsPAI：pip install 'opensocialscience[causal]'")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def staggered():
    return pd.read_csv(FIXTURES / "did_staggered.csv")


@pytest.fixture(scope="module")
def clean():
    return pd.read_csv(FIXTURES / "did_clean.csv")


@pytest.fixture(scope="module")
def pretrend():
    return pd.read_csv(FIXTURES / "did_pretrend.csv")


def cs_plan(**kw):
    from oss.core.contracts import AnalysisPlan

    base = dict(plan_id="p", idea_id="i", estimator="did_cs", outcome="y",
                unit="unit", time="year", treat_time="treat_time", cluster_level="unit")
    return AnalysisPlan(**{**base, **kw})


def true_att(df: pd.DataFrame) -> float:
    return float(df.loc[df.post == 1, "true_effect"].mean())


# ══════════════════════════════ 一、真实效应是我设定的


def test_各批效应不同时老方法算错_新方法算对(staggered) -> None:
    """**这是换估计量的全部理由。**早批效应随时间增长，TWFE 把已处理的早批当成晚批的对照，
    汇总系数被拖到真实值以下一大截；Callaway–Sant'Anna 只拿从未处理的单位比，算得对。"""
    from oss.core.analysis import run_plan

    truth = true_att(staggered)
    twfe = run_plan(staggered, cs_plan(estimator="did")).estimates["main"].coef
    cs = run_plan(staggered, cs_plan()).estimates["main"].coef
    assert abs(twfe - truth) > 1.0, f"TWFE {twfe:.3f} 竟然接近真实值 {truth:.3f}——夹具没造出分批偏误"
    assert abs(cs - truth) < 0.3, f"真实 {truth:.3f}，Callaway–Sant'Anna 算出 {cs:.3f}"


def test_各批各自的效应也算对(staggered) -> None:
    """按批的 ATT 进 `estimates["cohort_<年>"]`——分批异质性从一句警告变成可引用的数。"""
    from oss.core.analysis import run_plan

    res = run_plan(staggered, cs_plan())
    by_cohort = staggered[staggered.post == 1].groupby("treat_time").true_effect.mean()
    for g, truth in by_cohort.items():
        e = res.estimates[f"cohort_{g:.0f}"]
        assert abs(e.coef - truth) < 0.4, f"{g:.0f} 批真实 {truth:.3f}，算出 {e.coef:.3f}"
        assert e.se > 0 and e.mde > 0


def test_干净夹具上新旧方法一致(clean) -> None:
    """两批效应相同时 TWFE 没有偏，两种方法该给同一个答案（真实 3.0）。"""
    from oss.core.analysis import run_plan

    cs = run_plan(clean, cs_plan()).estimates["main"].coef
    twfe = run_plan(clean, cs_plan(estimator="did")).estimates["main"].coef
    assert abs(cs - 3.0) < 0.3, cs
    assert abs(cs - twfe) < 0.3, (cs, twfe)


# ══════════════════════════════ 二、独立复核：闭式解


def test_按批的效应等于手算的闭式解(clean) -> None:
    """**独立复核。**无协变量、平衡面板、只用从未处理单位当对照时，
    某一批在第 t 期的效应有闭式解：

        (该批 t 期均值 − 该批处理前一期均值) − (从未处理 t 期均值 − 从未处理处理前一期均值)

    按批的 ATT 是它在处理后各期的平均。程序算的必须跟手算的一模一样，对不上就是接错了。"""
    from oss.core.analysis import run_plan

    res = run_plan(clean, cs_plan())
    g_col = clean.treat_time.fillna(0).astype(int)
    m = clean.assign(g=g_col).groupby(["g", "year"]).y.mean()
    last = int(clean.year.max())

    def att(g: int, t: int) -> float:
        return (m[g, t] - m[g, g - 1]) - (m[0, t] - m[0, g - 1])

    for g in (2010, 2014):
        hand = float(np.mean([att(g, t) for t in range(g, last + 1)]))
        assert res.estimates[f"cohort_{g}"].coef == pytest.approx(hand, abs=1e-6), (
            f"{g} 批：程序算 {res.estimates[f'cohort_{g}'].coef}，手算 {hand}")

    cells = [att(g, t) for g in (2010, 2014) for t in range(g, last + 1)]
    assert res.estimates["main"].coef == pytest.approx(float(np.mean(cells)), abs=1e-6)


# ══════════════════════════════ 三、诊断：判得出来，也判得不冤


def test_处理前抢跑的数据被拦住(pretrend) -> None:
    """前趋势夹具：处理组在政策落地前四期就开始动。联合检验必须拒绝。"""
    from oss.core.analysis import run_plan

    res = run_plan(pretrend, cs_plan())
    verdict = res.diagnostics["平行趋势"]
    assert "不成立" in verdict, verdict
    assert "联合检验" in verdict and "p = " in verdict, "要报联合检验的 p 值"
    assert "结论不可信" in verdict, "要说清后果"


def test_单期显著但联合未拒绝时不判不成立() -> None:
    """逐期规则在 3 个处理前期上有约 14% 的误报率。判词以联合检验为准，单期只作提示。"""
    from oss.core.contracts import EventStudyPoint
    from oss.core.staggered import _trend_verdict

    pts = [EventStudyPoint(k=-3, coef=0.7, se=0.3, n=10), EventStudyPoint(k=-2, coef=0.1, se=0.3, n=10),
           EventStudyPoint(k=0, coef=3.0, se=0.3, n=10)]
    v = _trend_verdict(pts, {"pvalue": 0.4, "df": 4}, 3)
    assert "不成立" not in v and "未拒绝" in v and "第 -3 期" in v and "噪声" in v, v
    v2 = _trend_verdict(pts, {"pvalue": 0.01, "df": 4}, 3)
    assert "不成立" in v2 and "p = 0.010" in v2, v2
    assert "不成立" in _trend_verdict(pts, None, 3), "没有联合检验时退回逐期规则"


def test_干净数据不该被误判(staggered) -> None:
    """**别把对的也标红**。分批夹具处理前是平的，窗口内不该报「不成立」。"""
    from oss.core.analysis import run_plan

    res = run_plan(staggered, cs_plan())
    assert "不成立" not in res.diagnostics["平行趋势"], res.diagnostics["平行趋势"]


def test_干净夹具不再被判前趋势(clean) -> None:
    """陷阱集里唯一的干净双胞胎误拦（待办 21）：`did_clean` 处理前是平的，却被判「不成立」。

    根因是逐（批 × 期）格子的联合 Wald——4 个事前期在两批上是 8 个矩条件，而只有 60 个单位，
    协方差近奇异、求逆把统计量推大。聚合到事件期之后只剩 3 个矩条件，这份数据就过了。
    """
    from oss.core.analysis import run_plan

    v = run_plan(clean, cs_plan()).diagnostics["平行趋势"]
    assert "不成立" not in v, v
    assert "联合检验" in v and "p = " in v, v


def test_联合检验按事件期算矩条件不按格子(clean) -> None:
    """**矩条件数必须等于报出来的处理前期数**，不是（批 × 期）格子数。

    格子数随批次数成倍长，聚类数不变——「矩条件多、聚类少」正是这个检验塌掉的地方。
    """
    from oss.core.analysis import run_plan

    res = run_plan(clean, cs_plan())
    pre = [p for p in res.event_study if p.k < 0 and p.se > 0]
    v = res.diagnostics["平行趋势"]
    assert len(pre) == 3, [p.k for p in pre]
    assert f"自由度 {len(pre)}" in v, v
    assert "各批 × 期" not in v, "聚合到事件期之后，检验的不再是逐格子"
    assert f"{clean['unit'].nunique()} 个聚类" in v, "小样本校正的分母自由度要写出来"


# ── 尺寸模拟：H0 为真的面板抽一批，数错拒率

_SIM_YEARS = tuple(range(2005, 2025))
_SIM_COHORTS = (2013, 2015, 2017, 2019, 2021)   # 5 批 × 14 个单位 + 50 个从未处理 = 120


def _sim_panel(seed: int, pretrend: float = 0.0) -> pd.DataFrame:
    """分批采纳的面板。`pretrend = 0` 时平行趋势**按构造为真**，联合检验拒了就是错拒。

    形状照着 2026-09-17 那次尺寸模拟：120 个单位、5 批、40% 从未处理。批次多、单位少，
    正是逐格子联合检验塌掉的地方——6 个事前期在这里是 30 个矩条件，而聚类只有 120 个。
    """
    rng = np.random.default_rng(seed)
    assign = [g for g in _SIM_COHORTS for _ in range(14)] + [0] * 50
    unit_fe = rng.normal(0.0, 3.0, len(assign))
    year_fe = {y: 0.3 * (y - _SIM_YEARS[0]) + rng.normal(0.0, 0.4) for y in _SIM_YEARS}
    rows = []
    for u, g in enumerate(assign):
        for y in _SIM_YEARS:
            k = (y - g) if g else 0
            effect = 2.0 * (1.0 + 0.5 * k) if (g and k >= 0) else 0.0     # 效应随处理后年数递增
            drift = pretrend * max(k, -8) if (g and k < 0) else 0.0       # 只在事前期
            rows.append(dict(unit=u, year=y, treat_time=(g if g else np.nan),
                             y=20.0 + unit_fe[u] + year_fe[y] + effect + drift + rng.normal(0.0, 1.2)))
    return pd.DataFrame(rows)


def _sim_rejects(seed: int, pretrend: float) -> bool:
    from oss.core.staggered import run_cs

    return "不成立" in run_cs(_sim_panel(seed, pretrend), cs_plan(leads=6, lags=4)).diagnostics["平行趋势"]


def test_平行趋势成立时的错拒率回到名义水平() -> None:
    """**尺寸**：H0 按构造为真的 100 份面板，5% 水平上的错拒率要落回名义值附近。

    种子写死，所以这条是确定性的，不会时好时坏。逐（批 × 期）格子的联合检验在同一批面板上
    错拒 27 份（事前期 6）；窗口再长更糟——2026-09-17 的 200 份模拟里，事前期 8 是 29%、
    事前期 10 是 41.5%，而说明书上该是 5%。
    """
    bad = sum(_sim_rejects(4000 + i, 0.0) for i in range(100))
    assert bad <= 12, f"100 份平行趋势为真的面板里误拒了 {bad} 份，名义水平是 5 份"


def test_真有事前趋势时仍然全拒() -> None:
    """**功效**：修尺寸不许拿功效换。同一个面板注一条事前趋势，30 份要份份拒。"""
    good = sum(_sim_rejects(4000 + i, 0.25) for i in range(30))
    assert good == 30, f"30 份真有事前趋势的面板只拒了 {good} 份"


def test_逐期效应以处理前一期为参照且只报窗口内(staggered) -> None:
    from oss.core.analysis import run_plan

    res = run_plan(staggered, cs_plan(leads=3, lags=5))
    ks = [p.k for p in res.event_study]
    assert ks == [-3, -2, 0, 1, 2, 3, 4, 5], ks
    assert -1 not in ks, "第 −1 期是参照期，本身没有系数"
    post = [p.coef for p in res.event_study if p.k >= 0]
    assert post == sorted(post) or max(post) - min(post) > 0.5, "早批效应随时间增长，处理后各期该往上走"
    assert all(p.n > 0 for p in res.event_study), "每一期要报进了多少个处理观测"


def test_每一批的成员写进诊断(staggered) -> None:
    """评审看批次表能想到「这些是后共产主义国家」——它得先看得见名单。"""
    from oss.core.analysis import run_plan

    res = run_plan(staggered, cs_plan())
    members = res.diagnostics["批次成员"]
    assert "2006 批" in members and "2012 批" in members, members
    assert "50 个单位" in members, members
    assert "共 50 个" in members, "超过上限要说总数，不能只列前几个"


def test_批次异质性用同一把尺子(staggered) -> None:
    from oss.core.analysis import run_plan

    res = run_plan(staggered, cs_plan())
    het = res.diagnostics["批次异质性"]
    # 两批，判不可靠——要说判不了，不能硬判
    assert "判不可靠" in het or "差异大" in het, het
    assert "2006 批" in het and "2012 批" in het, het


def test_没有从未处理的单位时改用尚未处理并写明(clean) -> None:
    """**StatsPAI 在这里有个坑**：晚批处理后没有任何对照，那些格子被记成「效应 0、标准误 ∞」，
    它的头条数把这些 0 平均了进去——3.77 变成 0.84，不报错。包装层必须绕开它，并把
    「哪一批整个没算」写出来。"""
    from oss.core.analysis import run_plan

    only_treated = clean[clean.treat_time.notna()]
    res = run_plan(only_treated, cs_plan())
    main = res.estimates["main"]
    # 只有 2010 批在 2010–2013 年能拿 2014 批当对照，闭式解：
    m = only_treated.groupby(["treat_time", "year"]).y.mean()
    hand = float(np.mean([(m[2010, t] - m[2010, 2009]) - (m[2014, t] - m[2014, 2009])
                          for t in (2010, 2011, 2012, 2013)]))
    assert main.coef == pytest.approx(hand, abs=1e-6), f"程序 {main.coef}，手算 {hand}——算不了的格子被当成 0 平均进去了？"
    assert "cohort_2014" not in res.estimates, "2014 批没有对照，不该有它的效应"
    lost = res.diagnostics["算不了的格子"]
    assert "2014 批" in lost and "整个没算" in lost, lost
    assert "2010 批在 2014–2020" in lost, lost
    assert "没有从未被处理的单位" in res.diagnostics["对照组"], res.diagnostics["对照组"]
    assert "尚未被处理" in res.diagnostics["识别假设"], res.diagnostics["识别假设"]
    assert "尚未被处理" in res.diagnostics["估计量"]


def test_全都能算时不报算不了的格子(staggered) -> None:
    from oss.core.analysis import run_plan

    res = run_plan(staggered, cs_plan())
    assert "算不了的格子" not in res.diagnostics


def test_不平衡面板也能算(clean) -> None:
    from oss.core.analysis import run_plan

    holes = clean.sample(frac=0.9, random_state=1)
    res = run_plan(holes, cs_plan())
    assert abs(res.estimates["main"].coef - 3.0) < 0.5
    assert res.n < len(clean)


def test_识别假设写进诊断(clean) -> None:
    """论文里那句「平行趋势只需对从未处理的单位成立」不该靠执行者自己写。"""
    from oss.core.analysis import run_plan

    res = run_plan(clean, cs_plan())
    assert "从未被处理" in res.diagnostics["识别假设"]
    assert "Callaway" in res.diagnostics["估计量"] and "StatsPAI" in res.diagnostics["估计量"]


def test_后端不给自己的表述时识别假设不许出现空括号() -> None:
    """StatsPAI 在非平衡面板下不返回 model_info["parallel_trends"]。

    原先无条件拼 `（{label}）。原文：{statement}`，两个都空就拼出
    「……不限制处理前趋势（）。原文：」——括号空着、「原文：」后面什么都没有。
    验收跑 r20260826_edufert 与 r20260907_edufert_zh 交付的 results.json 里就是这个样子，
    而九条标准第 5 条（设计的边界写进局限）靠的正是这一句：**没有原文可引，
    执行者要么略过这一节，要么自己编一句英文**。
    """
    from oss.core.staggered import _identification_note

    got = _identification_note("nevertreated", {})
    assert "（）" not in got and "原文：" not in got, got
    assert "从未被处理" in got, got
    assert "未随本次估计返回" in got, "要说清是后端没给，不是我们漏了"

    full = _identification_note("nevertreated", {"label": "PT", "statement": "Parallel trends hold."})
    assert "（PT）" in full and "原文：Parallel trends hold." in full, full


# ══════════════════════════════ 四、接线：稳健性、契约、校验、Bacon 分解


def test_稳健性按did_cs跑而不是退回OLS(staggered) -> None:
    from oss.core.analysis import run_plan
    from oss.core.contracts import RobustnessSpec

    res = run_plan(staggered, cs_plan(
        controls=["x"], robustness=[RobustnessSpec(label="R1 去控制", controls=[], executable=True)]))
    rb = res.robustness["R1 去控制"]
    assert not rb.error, rb.error
    assert "Callaway" in rb.diagnostics["估计量"], "稳健性没按主计划的估计量跑"
    assert abs(rb.estimates["main"].coef - true_att(staggered)) < 0.3


def test_结果能落盘再读回(staggered) -> None:
    from oss.core.analysis import run_plan
    from oss.core.contracts import AnalysisResults

    res = run_plan(staggered, cs_plan())
    back = AnalysisResults.model_validate_json(res.model_dump_json())
    assert back.estimates["main"].coef == res.estimates["main"].coef
    assert len(back.event_study) == len(res.event_study)


def test_分批警告现在指向药(clean) -> None:
    """警告只描述病不给药，执行者就会「诊断出来了却没换估计量」。"""
    from oss.core.analysis import validate_plan
    from oss.core.profiler import profile_file

    prof = profile_file(FIXTURES / "did_clean.csv")
    _, warns = validate_plan(cs_plan(estimator="did"), prof)
    joined = " ".join(warns)
    assert "did_cs" in joined, warns
    _, warns_cs = validate_plan(cs_plan(), prof)
    assert not any("分批实施" in w for w in warns_cs), "已经用了 did_cs 就不该再警告分批"


def test_没装StatsPAI时校验给安装命令(monkeypatch) -> None:
    from oss.core import staggered
    from oss.core.analysis import validate_plan
    from oss.core.profiler import profile_file

    monkeypatch.setattr(staggered, "available", lambda: False)
    prof = profile_file(FIXTURES / "did_clean.csv")
    problems, _ = validate_plan(cs_plan(), prof)
    assert any("StatsPAI" in p and "pip install" in p for p in problems), problems


def test_TWFE主规格附带Bacon分解说明为什么有偏(staggered) -> None:
    """用了 `did` 的人要看得见：汇总系数里有多大比例来自「已处理单位当对照」的坏比较。"""
    from oss.core.analysis import run_plan

    res = run_plan(staggered, cs_plan(estimator="did"))
    bacon = res.diagnostics["Bacon 分解"]
    assert "已处理" in bacon and "权重" in bacon, bacon
    assert "不可直接当作平均效应" in bacon, "坏比较权重 19% 时要说清后果"
    assert "从未处理" in bacon


def test_只有一批时不附带Bacon分解(clean) -> None:
    from oss.core.analysis import run_plan

    one = clean[clean.treat_time.isna() | (clean.treat_time == 2010)]
    res = run_plan(one, cs_plan(estimator="did"))
    assert "Bacon 分解" not in res.diagnostics


def test_分批估计算出非数时拒绝而不是静默(staggered, monkeypatch) -> None:
    """**判不了就说判不了。**NaN 写进 results.json 就会变成论文里的「nan」。"""
    import statspai as sp
    from oss.core import staggered as mod
    from oss.core.analysis import PlanRejected

    real = sp.callaway_santanna

    class Fake:
        estimate, se, pvalue, n_obs = float("nan"), float("nan"), float("nan"), 0
        model_info: dict = {}
        diagnostics: dict = {}

        def tidy(self):
            return pd.DataFrame(columns=["term", "estimate", "std_error", "p_value", "type"])

    monkeypatch.setattr(sp, "callaway_santanna", lambda *a, **k: Fake())
    monkeypatch.setattr(sp, "aggte", lambda r, **k: r)
    with pytest.raises(PlanRejected) as exc:
        mod.run_cs(staggered, cs_plan())
    assert "非数" in str(exc.value)
    monkeypatch.setattr(sp, "callaway_santanna", real)
