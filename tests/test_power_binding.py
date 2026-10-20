"""变量绑定与统计功效的测试。

跨学科覆盖（见 PLAN.md 一之二）：**不能只测经济学**。
经济学的识别门是硬的、社会学是软的，只测一个等于另一条分支从没跑过。

fixture 两份：
    tiny.csv       平衡面板 25×4，economics
    cgss_like.csv  重复截面 360×3 轮，sociology
"""

from __future__ import annotations

from pathlib import Path
from statistics import NormalDist

import pytest

from oss.core.binding import build_report, shortlist, tokens, verify
from oss.core.contracts import VariableBinding
from oss.core.power import (
    DEFAULT_BAND,
    GRAY_BANDS,
    analyze,
    band_for,
    compute_power,
    design_effect,
    verdict_for,
    _z,
)
from oss.core.profiler import profile_file

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def econ():
    """平衡面板。经济学场景。"""
    return profile_file(FIXTURES / "tiny.csv")


@pytest.fixture(scope="module")
def socio():
    """重复截面。社会学场景——CGSS 那一类。"""
    return profile_file(FIXTURES / "cgss_like.csv")


# ══════════════════════════════════════════════════════════ 分位数：真 bug 的回归


def test_正态分位数不能自己手抄逼近() -> None:
    """**这是跨模型审稿抓到的真 bug 的回归测试。**

    原本手抄了一版 Acklam 有理逼近，`_z(0.975)` 返回 1.0017 而不是 1.96，
    导致所有功效被系统性高估——0.16 报成 0.51。

    而且它**不报错**，只安静地给出错的数。审稿模型按文件里自报的参数
    重算了一遍才发现对不上。
    """
    for p in (0.025, 0.05, 0.5, 0.9, 0.95, 0.975, 0.99):
        assert abs(_z(p) - NormalDist().inv_cdf(p)) < 1e-9, f"p={p} 处偏离标准库"
    assert abs(_z(0.975) - 1.959964) < 1e-5, "z(0.975) 必须是 1.96"


def test_功效值能被手算复核() -> None:
    """审稿模型能重算，测试也要能重算——否则错了没人知道。"""
    _, eff_n, power = compute_power(0.35, 100, n_clusters=25, icc=0.05)
    d, crit = 0.35 * 0.6, NormalDist().inv_cdf(0.975)
    ncp = d * (eff_n ** 0.5) / 2
    expected = (1 - NormalDist().cdf(crit - ncp)) + NormalDist().cdf(-crit - ncp)
    assert abs(power - expected) < 1e-3


# ══════════════════════════════════════════════════════════════ 功效


def test_效应量先打折再算() -> None:
    """不打折的话研究注定样本不足，而且做完才发现。"""
    adjusted, _, _ = compute_power(1.0, 1000)
    assert adjusted == 0.6


def test_聚类会显著降低有效样本量() -> None:
    """社科数据几乎总是聚类的。不管它就是系统性高估功效。"""
    _, n_no_cluster, p_no = compute_power(0.3, 1000)
    _, n_clustered, p_yes = compute_power(0.3, 1000, n_clusters=20, icc=0.1)
    assert n_clustered < n_no_cluster
    assert p_yes < p_no, "聚类之后功效应当下降"


def test_设计效应的公式() -> None:
    assert design_effect(1, 0.1) == 1.0          # 每簇 1 个，没有聚类效应
    assert design_effect(11, 0.1) == pytest.approx(2.0)
    assert design_effect(5, 0.0) == 1.0          # ICC 为 0，没有簇内相关


# ────────────────────────────────────────── 学科档案：同一个功效，不同命运


def test_同一个功效在不同学科判词不同() -> None:
    """**这是学科档案存在的意义。**用一个值走天下就等于没有它。"""
    # 0.65：心理学判死，社会学还在商量
    assert verdict_for(0.65, band_for("psychology")) == "fail"
    assert verdict_for(0.65, band_for("sociology")) == "gray"

    # 0.85：社会学已经够了，心理学还在商量
    assert verdict_for(0.85, band_for("sociology")) == "pass"
    assert verdict_for(0.85, band_for("psychology")) == "gray"

    # 0.45：社会学还在商量，经济学已经判死
    assert verdict_for(0.45, band_for("sociology")) == "gray"
    assert verdict_for(0.45, band_for("economics")) == "fail"


def test_每个学科的带子都把惯例的08包进去() -> None:
    """0.8 是学科惯例。带子是**围着惯例展开**的，不是取代它——
    否则某个学科会出现「按惯例够了但被判死」这种说不通的结果。"""
    for name, (low, high) in GRAY_BANDS.items():
        assert low <= 0.8 <= high, f"{name} 的带子 [{low},{high}] 没包住惯例 0.8"


def test_没给学科时用默认带子() -> None:
    assert band_for("") == DEFAULT_BAND
    assert band_for("不存在的学科") == DEFAULT_BAND


def test_三档判词() -> None:
    assert verdict_for(0.3, (0.5, 0.9)) == "fail"
    assert verdict_for(0.95, (0.5, 0.9)) == "pass"
    assert verdict_for(0.7, (0.5, 0.9)) == "gray"


def test_报告带全设计效应的可复核参数() -> None:
    """审稿模型说过：不给 ICC 和每簇样本量，设计效应无法验证。"""
    r = analyze("i1", 0.3, 400, discipline="sociology", n_clusters=20, icc=0.08)
    assert r.n_raw == 400
    assert r.n_effective < r.n_raw
    assert r.n_clusters == 20
    assert r.icc == 0.08
    assert r.design_effect > 1.0


def test_玩具数据撑不起中等效应() -> None:
    """诚实的结果：100 观测 / 25 聚类，中等效应量下功效不足。

    **这才是对的**——系统该说「你这份数据做不了」，
    不该为了让链条好看就调松阈值。
    """
    r = analyze("i1", 0.35, 100, discipline="economics", n_clusters=25)
    assert r.verdict == "fail"
    assert r.power < 0.5


# ══════════════════════════════════════════════════════════════ 绑定


def test_词元切分不含单字符() -> None:
    assert tokens("urban_inc-2020") == {"urban", "inc", "2020"}
    assert tokens("") == set()


def test_英文构念能靠词元匹配收窄(econ) -> None:
    sl = shortlist("income", econ)
    assert sl.matches[0].column == "income"
    assert sl.matches[0].score > 0, "直接命中应当有正分"
    assert not sl.rule_failed


def test_规则给不出候选时要如实说不能拿几列充数(econ) -> None:
    """**这条测试原先断言的是相反的行为，是 GSS 真跑把它推翻的。**

    原先的想法是「规则层只负责收窄不负责否决，命中太少就补几列上去，
    让模型去做跨语言的桥接」。听起来合理，实际产出的是噪声：
    补上去的那几列按「有没有标签、缺失率高低」排序，
    **跟要绑的是哪个构念完全无关**——GSS 真跑时五个构念拿到了同一份名单，
    而真正对的列一个都没出现。

    **噪声比空白更糟**：一份跟构念无关的名单会诱导模型在里面选一个，
    而它本该看到「规则帮不上忙，你自己看全表」。

    原先那个顾虑仍然成立——规则不该替模型做否决。
    新的做法用 `rule_failed` + 一句说明来满足它：**这是「我不知道」，不是「绑不上」。**
    """
    sl = shortlist("受访者对量子引力的看法", econ)
    assert sl.rule_failed
    assert not sl.matches, "给不出就别给，不要按缺失率补几列充数"
    assert "全表" in sl.note, sl.note


def test_标识符文本列与权重列都不进候选(econ) -> None:
    names = {c.column for c in shortlist("id", econ).matches}
    assert "unit_id" not in names, "个体标识不该被当成实质变量的候选"


# ───────────────────────────────────────────────── 回验：模型只有推荐权


def test_模型说的列不存在就判死(econ) -> None:
    """**它说 income_gini，数据里没有，就判 missing。**不是警告，是判死。"""
    b = verify("基尼系数", "outcome", "income_gini", econ)
    assert b.status == "missing"
    assert not b.is_usable
    assert "最接近的是 income" in b.note, "列名写错时该给个最接近的，省得用户自己翻"


def test_没给列也判死(econ) -> None:
    assert verify("某构念", "treatment", None, econ).status == "missing"


def test_组内变异为零要说出后果(econ) -> None:
    """列在那儿，但固定效应会把它整个吃掉——回归里它是常数。"""
    b = verify("恒定量", "control", "const_col", econ)
    assert "恒定" in b.note or "变异" in b.note


def test_派生变量逐列核过(econ) -> None:
    ok = verify("人均收入", "outcome", None, econ, derivation="income / age")
    assert ok.status == "derived" and "income" in ok.note

    bad = verify("人均收入", "outcome", None, econ, derivation="income / population")
    assert bad.status == "missing"
    assert "population" in bad.note, "构造式里缺哪一列要点名"


def test_低置信度判成代理变量(econ) -> None:
    assert verify("收入", "outcome", "income", econ, confidence=0.4).status == "proxy"


# ─────────────────────────────────────────────── 汇总：只有核心角色致命


def test_控制变量缺了不致命(econ) -> None:
    r = build_report("i1", [
        verify("处理", "treatment", "treat", econ),
        verify("结果", "outcome", "income", econ),
        verify("某控制", "control", "nonexistent", econ),
    ], econ)
    assert r.core_ok, "控制变量缺失不该让整个 idea 判死"


def test_核心变量缺了就做不了(econ) -> None:
    r = build_report("i1", [
        verify("处理", "treatment", "nonexistent", econ),
        verify("结果", "outcome", "income", econ),
    ], econ)
    assert not r.core_ok
    assert any("核心变量" in b for b in r.blocking)


def test_没有核心变量说明idea还没成形(econ) -> None:
    r = build_report("i1", [verify("某控制", "control", "age", econ)], econ)
    assert not r.core_ok
    assert any("还没成形" in b for b in r.blocking)


def test_处理变量组内变异为零也做不了(econ) -> None:
    """绑上了不等于能用——固定效应会把它吃掉。"""
    r = build_report("i1", [
        verify("处理", "treatment", "const_col", econ),
        verify("结果", "outcome", "income", econ),
    ], econ)
    assert not r.core_ok


# ══════════════════════════════════════════════ 社会学场景：重复截面


def test_社会学fixture是重复截面(socio) -> None:
    """CGSS 这类数据每人只出现一次。**误判成面板会一路产出做不了的 DID 设计。**"""
    assert socio.structure.kind == "repeated_cross_section"
    assert socio.structure.unit_id is None


def test_社会学数据的绑定(socio) -> None:
    b = verify("父亲受教育年限", "treatment", "father_edu_years", socio)
    assert b.is_usable
    assert verify("职业地位", "outcome", "occupation_isei", socio).is_usable


def test_社会学的样本量撑得起中等效应(socio) -> None:
    """360 个观测、12 个省聚类。社会学的带子更宽，判定应当比经济学宽松。"""
    n = socio.file.n_rows
    econ_r = analyze("i1", 0.35, n, discipline="economics", n_clusters=12)
    socio_r = analyze("i1", 0.35, n, discipline="sociology", n_clusters=12)
    assert socio_r.power == econ_r.power, "功效是算出来的，跟学科无关"
    # 但判词可能不同——学科决定的是阈值，不是功效本身
    assert socio_r.gray_band[0] < econ_r.gray_band[0]


def test_权重列在社会学数据里被检出(socio) -> None:
    """调查数据不加权的估计通常是有偏的。"""
    assert socio.weight_column == "sample_weight"


# ═════════════════ 跨模型审稿指出的：效应量类型必须声明


def test_效应量类型不声明就必须警告() -> None:
    """**跨模型审稿抓到的第二个真问题**（第一个是 `_z(0.975)` 返回 1.0017）。

    判词原文：「未说明 effect_size 的度量类型（Cohen's d / r / 标准化回归系数），
    却直接套用两样本均值差功效公式 d·√n_eff/2；若是 r 或 beta，功效将完全不同。」

    这条完全成立，而且是最危险的那一类：**传一个相关系数进来，
    功效算出来是错的，而且不报错**——跟当年手抄的分位数逼近一模一样。
    """
    from oss.core.power import analyze

    rep = analyze("i1", 0.3, 5000, discipline="sociology")
    assert rep.effect_metric, "产出物里必须写明效应量是什么度量"
    assert "d" in rep.reason or "Cohen" in rep.reason, "判词里也要说"


def test_不支持的效应量类型要拒绝而不是硬算() -> None:
    """**换个度量就该换公式。**不换公式还硬算，是安静地给出错的数。"""
    from oss.core.power import analyze

    with pytest.raises(ValueError, match="度量"):
        analyze("i1", 0.3, 5000, discipline="sociology", effect_metric="odds_ratio")


def test_相关系数要先转成d再算() -> None:
    """r 与 d 有确定的换算关系 d = 2r/√(1−r²)，不能当成同一个数。"""
    from oss.core.power import analyze

    as_d = analyze("i1", 0.3, 2000, discipline="sociology", effect_metric="d")
    as_r = analyze("i1", 0.3, 2000, discipline="sociology", effect_metric="r")
    assert as_r.power != as_d.power, "r=0.3 与 d=0.3 不是同一个效应量"
    assert as_r.effect_metric == "r"


def test_判词要写明公式与已知的近似() -> None:
    """审稿意见：正态近似、忽略簇不平衡——这些不改也行，但必须写出来。"""
    from oss.core.power import analyze

    rep = analyze("i1", 0.2, 5000, discipline="sociology", n_clusters=140, icc=0.02)
    for key in ("正态", "簇"):
        assert key in rep.reason, f"判词里没提「{key}」：{rep.reason}"
