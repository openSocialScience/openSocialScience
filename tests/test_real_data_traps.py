"""真数据踩到的坑，逐条钉死。

**这些测试全部来自 2026-08-24 拿 GSS 真跑一次的记录**
（`docs/runs/gss20260824-transcript.md`，问题清单见 `docs/design/PLAN.md` 九之一）。

写它们的顺序很重要：**先让它们红，再改代码**。

一条从没红过的测试等于没有测试——它可能因为断言太松、或者根本没走到那个分支
而永远是绿的，而你无从知道。这跟「排版漂亮但数字是手打的论文」是同一个毛病：
看起来通过了，实际什么都没验证。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import PlanRejected, run_ols_fe
from oss.core.contracts import AnalysisPlan
from oss.core.profiler import profile_file

FIXTURES = Path(__file__).parent / "fixtures"
GSS_LIKE = FIXTURES / "gss_like.csv"


@pytest.fixture(scope="module")
def prof():
    return profile_file(GSS_LIKE)


@pytest.fixture(scope="module")
def frame():
    return pd.read_csv(GSS_LIKE)


def plan(**kw) -> AnalysisPlan:
    base = dict(
        plan_id="p", idea_id="i", estimator="ols_fe",
        outcome="sei", treatment="paeduc", controls=["educ", "age", "female"],
        fixed_effects=["region"], cluster_level="region_year",
    )
    return AnalysisPlan(**{**base, **kw})


# ══════════════════════════════════ 夹具自检：坑确实造进去了

def test_夹具确实带着回收的编号(frame) -> None:
    """同一个编号在多期出现，但期与期之间是不同的人——出生年会变。"""
    per_unit = frame.groupby("id").year.nunique()
    assert (per_unit > 1).mean() > 0.5, "编号没有跨期重复，坑①没造进去"
    varying = frame.groupby("id").cohort.nunique() > 1
    assert varying.mean() > 0.5, "同一编号内出生年不变，那它就真是个体标识了"


def test_夹具确实带着APC恒等式(frame) -> None:
    d = (frame.year - frame.age - frame.cohort).abs()
    assert (d == 0).sum() > 200, "APC 恒等式没造进去"
    assert 0 < (d == 1).sum() < 30, "要有少数行差 1，共线才是「不精确」的"


# ═══════════════════════════ 发现① 结构判定对回收编号没有正确路径

def test_编号每期回收的重复截面不能判成面板(prof) -> None:
    """**PLAN.md 九之一 ①。**

    GSS 的 `id` 是受访者在当年问卷内的序号，每轮从 1 重编。
    于是「个体出现在多期的比例」虚高（本夹具 96%），
    从 `repeated < 0.2` 那条防线的**另一侧**穿过去，被判成面板。

    判成面板会授权对 `id` 上个体固定效应——把不同的人当成同一个人做组内比较。
    **系数是垃圾，但一行报错都不会有。**
    """
    assert prof.structure.kind == "repeated_cross_section", (
        f"判成了 {prof.structure.kind}（unit_id={prof.structure.unit_id!r}）"
    )


def test_判成重复截面时不能留下个体标识(prof) -> None:
    """留着 `unit_id` 等于给下游一个「可以上个体固定效应」的暗示。"""
    assert prof.structure.unit_id is None


def test_结构判定要说清依据了什么证据(prof) -> None:
    """「依据列 X 与 Y 判定」没法核。要写出跑了什么检验、得到什么。"""
    note = prof.structure.detection_note
    assert any(k in note for k in ("回收", "每期", "出生", "不变")), note


def test_去掉编号后每期几十行不能判成时间序列(frame, tmp_path) -> None:
    """**PLAN.md 九之一 ① 的另一半。**

    `if time and not unit` 分支完全没有检查每期行数。
    时间序列每期约 1 行；这里每期约 50 行，是重复截面。
    """
    p = tmp_path / "no_id.csv"
    frame.drop(columns=["id"]).to_csv(p, index=False)
    assert profile_file(p).structure.kind == "repeated_cross_section"


# ═══════════════════════════ 发现② 近共线安静穿过秩检验

def test_APC近共线必须出现在诊断里(frame) -> None:
    """**PLAN.md 九之一 ②。**

    年份固定效应 + `age` + `cohort` 三者线性相关，但因少数行差 1 而不精确共线，
    `matrix_rank` 满秩、秩检验放行，回归照跑照出星号。

    **不要求拒绝**——把这个规格作为对照跑是正当的（真跑时它就是 R6）。
    要求的是**报出来**：诊断里必须有共线的度量。
    """
    res = run_ols_fe(frame, plan(controls=["educ", "age", "cohort", "female"],
                                 fixed_effects=["year", "region"]))
    keys = " ".join(res.diagnostics)
    assert any(k in keys for k in ("VIF", "共线", "条件数")), (
        f"诊断里没有任何共线度量，只有：{list(res.diagnostics)}"
    )


def test_共线诊断要点名是哪个变量(frame) -> None:
    """「存在共线」没法行动，「age 与年份固定效应+cohort 线性相关」才有用。"""
    res = run_ols_fe(frame, plan(controls=["educ", "age", "cohort", "female"],
                                 fixed_effects=["year", "region"]))
    text = " ".join(f"{k} {v}" for k, v in res.diagnostics.items())
    assert "age" in text or "cohort" in text, text


def test_不共线的规格不该被误报(frame) -> None:
    """以队列为时间轴、不放年份固定效应，就没有 APC 问题。别把好规格也标红。"""
    res = run_ols_fe(frame, plan(controls=["educ", "age", "cohort", "female"],
                                 fixed_effects=["region"]))
    assert res.estimates["main"].se > 0


# ═══════════════════════════ 发现⑨ 秩诊断归因错

def test_样本筛选把控制变量变成常数时要归因给筛选(frame) -> None:
    """**PLAN.md 九之一 ⑨。**

    `female` 因为 `sample_filter="female == 1"` 变成常数，
    现在却被报成「被固定效应（region）吃光了」。

    点名对了（有用），归因错了——**照它的提示改会改错地方**：
    使用者会去动固定效应，而该动的是筛选条件。
    """
    with pytest.raises(PlanRejected) as exc:
        run_ols_fe(frame, plan(sample_filter="female == 1"))
    msg = str(exc.value)
    assert "female" in msg, "要点名是哪个变量"
    assert "样本筛选" in msg, f"该归因给样本筛选，实际说的是：{msg}"


# ═══════════════════════════ 发现⑦ 权重列认不出来

def test_名字不含weight的权重列也要认出来(prof) -> None:
    """**PLAN.md 九之一 ⑦。**

    `_WEIGHT_HINTS` 按词元匹配，GSS 的 `wtssall` / `wtssps` 整个是一个词元，
    一条都不命中。后果是抽样权重被当成实质变量的候选列。
    """
    assert prof.weight_column == "wtssps", (
        f"判成了 {prof.weight_column!r}"
    )


def test_列太少时不下结论但要把这个洞说出来() -> None:
    """`_tracks_anything` 的残余风险：只有一两列实质变量时核验不了。

    **不下结论是对的**（「没有一列稳定」在只有一列时不说明任何问题），
    但不能静悄悄地就当面板用——那个洞要写进 warnings。
    """
    frame = pd.DataFrame({
        "person_id": list(range(25)) * 4,
        "year": sum(([y] * 25 for y in (2016, 2018, 2020, 2022)), []),
        "income": range(100),
    })
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    prof = profile_table(table(frame))
    assert prof.structure.kind.startswith("panel"), "证据不足时不该改判"
    assert any("追踪" in w for w in prof.warnings), (
        f"核验没做成却没有任何提示。warnings：{prof.warnings}"
    )


def test_名字像权重但取值不像的不能误认() -> None:
    """**宁可漏认，不可错认。**

    把实质变量误判成权重，它就会从绑定候选里被整个排除掉，
    而使用者不会知道为什么——这比漏认一个权重列更难查。

    `wtp`（willingness to pay）有零值、`wt_kg`（体重）取值离散，都不该被当成抽样权重。
    """
    import numpy as np
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    rng = np.random.default_rng(0)
    frame = pd.DataFrame({
        "wtp": np.concatenate([np.zeros(20), rng.uniform(1, 100, 80)]),  # 有零
        "wt_kg": rng.integers(50, 55, 100).astype(float),                # 只有 5 个取值
        "wtssps": rng.gamma(9, 1 / 9, 100),                              # 真权重
        "y": rng.normal(size=100),
    })
    prof = profile_table(table(frame))
    assert prof.weight_column == "wtssps", f"认成了 {prof.weight_column!r}"
    kinds = {v.name: v.semantic_type for v in prof.variables}
    assert kinds["wtp"] != "weight", "有零值的不该判成权重"
    assert kinds["wt_kg"] != "weight", "取值离散的不该判成权重"


def test_中文列名的线索词要能命中() -> None:
    """**中文没有分隔符，按词元匹配永远命中不了。**

    `抽样权重` 切出来是一整个词元，跟 hint `权重` 不相等；
    裸子串那条分支又要求 hint 长度 ≥ 4，而中文词只有两三个字。
    结果是三张 hint 表里的中文线索词一条都不会命中——**而且不报错**。

    对一个面向中文社科数据的项目，这是要害。
    发现于修「权重列认不出来」（PLAN.md 九之一 ⑦）时的顺带核查。
    """
    from oss.core.profiler import _TIME_HINTS, _UNIT_HINTS, _WEIGHT_HINTS, _name_hits

    assert _name_hits("抽样权重", _WEIGHT_HINTS)
    assert _name_hits("个体编号", _UNIT_HINTS)
    assert _name_hits("调查年份", _TIME_HINTS)
    assert _name_hits("出生年份", _TIME_HINTS)
    # 不该误伤：英文仍然按词元匹配
    assert not _name_hits("resident_income", _UNIT_HINTS), "英文不能退回裸子串"


def test_中文列名的画像能认出结构与权重() -> None:
    """端到端：一份列名全中文的重复截面。"""
    import numpy as np
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    rng = np.random.default_rng(7)
    n = 300
    frame = pd.DataFrame({
        "个体编号": list(range(1, 101)) * 3,
        "调查年份": sum(([y] * 100 for y in (2015, 2017, 2019)), []),
        "抽样权重": rng.gamma(9, 1 / 9, n),
        "教育年限": rng.integers(6, 20, n).astype(float),
        "职业地位": rng.normal(45, 12, n),
        "出生队列": rng.integers(1950, 1995, n).astype(float),
    })
    prof = profile_table(table(frame))
    assert prof.weight_column == "抽样权重", f"权重列认成了 {prof.weight_column!r}"
    assert prof.structure.time_id == "调查年份", f"时间列认成了 {prof.structure.time_id!r}"


# ═══════════════════════════ 发现③ 设计效应假定处理变量在簇内不变

def test_个体层处理变量的组内相关应当接近零(frame) -> None:
    """**PLAN.md 九之一 ③。**

    设计效应是 Moulton 因子 `1 + (m−1)·ρx·ρu`。原先隐含取 `ρx = 1`，
    也就是假定处理变量在簇内完全不变——政策在县一级开关、实验在班级一级随机，
    那类设计才是这样。

    父亲教育年限在同一个区-年里变异很大，`ρx` 接近 0。
    """
    from oss.core.power import cluster_icc

    rho = cluster_icc(frame.paeduc, frame.region_year)
    assert rho < 0.15, f"个体层变量的 ρx 算出来是 {rho:.3f}，太高了"


def test_簇层变量的组内相关应当接近一(frame) -> None:
    """对照：`region` 在 `region_year` 内按定义完全不变，ρx 必须接近 1。

    没有这条对照，上一条测试可能只是因为 `cluster_icc` 恒返回 0 而通过。
    """
    from oss.core.power import cluster_icc

    rho = cluster_icc(frame.region, frame.region_year)
    assert rho > 0.9, f"簇层变量的 ρx 算出来是 {rho:.3f}，太低了"


def test_设计效应必须随处理变量的组内相关缩放() -> None:
    """`ρx = 0` 时聚类不带来精度损失；`ρx = 1` 时才是满额的设计效应。"""
    from oss.core.power import design_effect

    full = design_effect(cluster_size=350, icc=0.02, treatment_icc=1.0)
    none = design_effect(cluster_size=350, icc=0.02, treatment_icc=0.0)
    assert full > 7.0, f"簇层处理变量的设计效应应当很大，实际 {full:.2f}"
    assert none == 1.0, f"个体层处理变量不该被打折，实际 {none:.2f}"


def test_功效报告必须写明ρx是算出来的还是假定的() -> None:
    """**判词会被写进论文局限，来源必须可核。**

    一个假定出来的 ρx 和一个从数据算出来的 ρx，
    对「这个研究功效够不够」的含义完全不同。
    """
    from oss.core.power import analyze

    assumed = analyze("i1", 0.15, 5000, discipline="sociology", n_clusters=20, icc=0.02)
    assert assumed.treatment_icc == 1.0
    assert "假定" in assumed.treatment_icc_source
    assert "假定" in assumed.reason, "判词里也要说，不能只在字段里"

    computed = analyze(
        "i1", 0.15, 5000, discipline="sociology", n_clusters=20, icc=0.02,
        treatment_icc=0.01, treatment_icc_source="从数据算出（paeduc 在 region_year 内）",
    )
    assert computed.power > assumed.power, "个体层处理变量的功效应当更高"
    assert "算出" in computed.reason


# ═══════════════════════ 回归验证跑出来的新问题 ㉔：多项式也会被误报

def test_二次项与一次项的高VIF要认成良性(frame) -> None:
    """**跟交互项是同一类问题（⑳）。**

    `age` 与 `age²` 天然高度相关，这是可以靠中心化消除的良性共线，
    不影响任何一个系数的可解释性。但良性判定只认 `*` 交互、不认多项式，
    于是**凡是带二次项的模型都会拉警报**——而警报天天响就等于没有警报。

    发现于回归验证：GSS v2 主规格报出「偏高：age=35、age2=34」。
    """
    df = frame.assign(age2=frame.age ** 2 / 100)
    res = run_ols_fe(df, plan(controls=["educ", "age", "age2"]))
    text = res.diagnostics["共线诊断（VIF）"]
    assert "正常" in text, f"二次项造成的高 VIF 该被认成良性：{text}"


def test_真正的共线不能被当成良性(frame) -> None:
    """别把良性判定放得太宽——APC 那种必须照样报。"""
    res = run_ols_fe(frame, plan(controls=["educ", "age", "cohort"],
                                 fixed_effects=["year", "region"]))
    text = res.diagnostics["共线诊断（VIF）"]
    assert "正常" not in text and "严重" in text, text


# ═══════════ 真数据（QoG 国家-年面板）跑出来的两个结构判定问题

def _panel_with_named_units() -> pd.DataFrame:
    """国家-年面板，外加一列「该国哪一年民主化」——**在国内是常数**。

    这是 QoG 那份真数据的最小复刻。社科里这种形态极常见：
    国家-年、省-年、企业-年，个体标识是**名字**而不是编号，
    而且表里常常带着几列「该单位的某个事件发生在哪一年」。
    """
    rng = np.random.default_rng(3)
    countries = [f"Country{i:03d}" for i in range(40)]
    years = list(range(1960, 2021))
    dem = {c: (rng.choice([1975, 1985, 1992, 2001]) if i % 3 else np.nan)
           for i, c in enumerate(countries)}
    rows = []
    for c in countries:
        for y in years:
            rows.append(dict(country=c, year=y, dem_year=dem[c],
                             lifexp=60 + 0.2 * (y - 1960) + rng.normal(0, 2),
                             log_gdppc=float(rng.normal(9, 1))))
    return pd.DataFrame(rows)


def test_国家名要能被认成个体标识() -> None:
    """**社科面板的个体标识常常是名字，不是编号。**

    线索词表里原先只有 `id` / `pid` / `编号` 这类，
    于是 `country` / `province` / `city` 一个都认不出来——
    而国家-年、省-年面板是社科最常见的形态之一。
    """
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    prof = profile_table(table(_panel_with_named_units()))
    assert prof.structure.kind.startswith("panel"), (
        f"判成了 {prof.structure.kind}，unit_id={prof.structure.unit_id!r}")
    assert prof.structure.unit_id == "country"


def test_个体内不变的年份列不能当时间轴() -> None:
    """**`dem_year` 是属性，不是时间轴。**

    它在每个国家内是常数（该国民主化的那一年）。
    但规则偏好「唯一值少」的列，而它比 `year` 少，于是被选中当了时间轴——
    整份面板因此被判成重复截面。

    判据是机械的：**时间轴必须在个体内变化**。
    """
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    prof = profile_table(table(_panel_with_named_units()))
    assert prof.structure.time_id == "year", (
        f"时间列选成了 {prof.structure.time_id!r}——它在个体内是常数，是属性不是时间轴")


def test_不要把真正的时间列误伤(frame) -> None:
    """GSS 那份重复截面里 `year` 仍然该是时间列。别为了修上面那条把这条弄坏。"""
    from oss.core.profiler import profile_file

    assert profile_file(FIXTURES / "gss_like.csv").structure.time_id == "year"


# ═══════════ 真数据（GSS 三轮面板）：宽表面板认不出来

def test_宽表面板要被认出来并提示重塑() -> None:
    """**真面板数据很多是宽表**：每人一行，各轮的变量用后缀并排。

    画像会判它 `cross_section`——**技术上没错，但完全没用**：
    拿着真面板数据的人会照这个做截面分析，**把面板结构整个扔掉**，
    而个体固定效应、个体内变化这些面板才有的东西全用不上。

    判据是机械的：多个变量共享同一个词干、只差一个轮次后缀。
    """
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    rng = np.random.default_rng(9)
    n = 300
    frame = pd.DataFrame({"pid": range(n)} | {
        f"{b}_{w}": rng.normal(size=n)
        for b in ("happy", "health", "income", "educ") for w in (1, 2, 3)
    })
    prof = profile_table(table(frame))
    w = " ".join(prof.warnings)
    assert "宽表" in w, prof.warnings
    assert "重塑" in w or "长表" in w, "要说清该怎么办"
    assert "happy" in w or "4" in w, "要给出是哪些变量/多少组"


def test_普通截面不该被误判成宽表面板() -> None:
    """**别把对的也标红。**`edu_years`、`hh_size2` 这类名字不是轮次后缀。"""
    from oss.core.profiler import profile_table
    from tests.test_profiler import table

    rng = np.random.default_rng(10)
    frame = pd.DataFrame({
        "id": range(200), "edu_years": rng.normal(size=200),
        "hh_size2": rng.normal(size=200), "income": rng.normal(size=200),
        "age2": rng.normal(size=200),
    })
    assert not any("宽表" in w for w in profile_table(table(frame)).warnings)


def test_长表面板不该被提示重塑(frame) -> None:
    """已经是长表的不需要提示。"""
    from oss.core.profiler import profile_file

    assert not any("宽表" in w for w in profile_file(FIXTURES / "gss_like.csv").warnings)


def test_面板流失要报出来() -> None:
    """**面板特有的问题：有人中途退出，而退出往往不是随机的。**

    GSS 三轮面板真跑时丢了 51.8% 的样本，但没有任何诊断报这件事——
    只报了「因缺失丢弃 51.8%」，看不出其中多少是**整个人不见了**、
    多少是这个人某一轮没答某道题。两者含义完全不同：
    前者是流失（可能有选择性），后者是条目缺失。
    """
    from oss.core.analysis import run_ols_fe
    from oss.core.contracts import AnalysisPlan

    rng = np.random.default_rng(11)
    rows = []
    for i in range(200):
        waves = (1, 2, 3) if i < 120 else ((1, 2) if i < 170 else (1,))
        for w in waves:
            rows.append(dict(pid=i, wave=w, y=float(rng.normal()),
                             x=float(rng.normal())))
    res = run_ols_fe(pd.DataFrame(rows), AnalysisPlan(
        plan_id="p", idea_id="i", outcome="y", treatment="x",
        fixed_effects=["pid", "wave"], cluster_level="pid"))
    a = res.diagnostics.get("面板流失", "")
    assert "全部" in a or "轮" in a, a
    assert "120" in a or "60%" in a, f"要给出有多少人答满了全部轮次：{a}"


def test_截面不该报面板流失(frame) -> None:
    from oss.core.analysis import run_ols_fe
    from oss.core.contracts import AnalysisPlan

    res = run_ols_fe(frame, AnalysisPlan(
        plan_id="p", idea_id="i", outcome="sei", treatment="paeduc",
        controls=["educ"], fixed_effects=["region"], cluster_level="region_year"))
    assert "面板流失" not in res.diagnostics
