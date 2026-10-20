"""交互项与稳健性：契约层的两个缺口。

都来自 GSS 真跑（PLAN.md 九之一 ④⑤）：

**④ `AnalysisPlan` 表达不了交互项。**
九个候选里六个的核心设计写不出来——凡是「效应随某个维度变化」的问题都是交互。
只能在数据里预先把列乘好，**于是没有任何校验能确认乘对了**。

**⑤ `results.json` 只存一个规格。**
七项稳健性跑完就被下一次覆盖，论文里想报具体数值只能手打，
而手打会被校验拦下——**诚实的写法被机制堵死**。

第二条还牵出一个更深的问题：`robustness` 现在是一串散文
（"R1 换结果变量：以职业声望替代 SEI"），**没有任何东西能执行它、核对它**。
而这个字段存在的全部理由是「事前锁定，事后不许改」。
锁一份没人能核的散文，等于没锁。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import PlanRejected, run_ols_fe, validate_plan
from oss.core.contracts import AnalysisPlan
from oss.core.profiler import profile_file

FIXTURES = Path(__file__).parent / "fixtures"
GSS_LIKE = FIXTURES / "gss_like.csv"


@pytest.fixture(scope="module")
def frame():
    return pd.read_csv(GSS_LIKE)


@pytest.fixture(scope="module")
def prof():
    return profile_file(GSS_LIKE)


def plan(**kw) -> AnalysisPlan:
    base = dict(
        plan_id="p", idea_id="i", estimator="ols_fe",
        outcome="sei", treatment="paeduc", controls=["educ", "age"],
        fixed_effects=["region"], cluster_level="region_year",
    )
    return AnalysisPlan(**{**base, **kw})


# ════════════════════════════════════════════ ④ 交互项


def test_交互项可以写进计划(frame) -> None:
    """**PLAN.md 九之一 ④。**

    「教育扩张是否削弱了家庭背景的影响」这个问题的核心设计是
    `背景 × 学历 × 队列` 的三重交互。现在的契约只有单个 treatment 列名，
    写不出来。
    """
    res = run_ols_fe(frame, plan(
        treatment="paeduc*educ",
        interactions=["paeduc*educ"],
        controls=["educ", "age"],
    ))
    assert "main" in res.estimates
    assert res.estimates["main"].term == "paeduc*educ"


def test_交互项的系数与手工乘出来的完全一致(frame) -> None:
    """**独立复核**：现场构造的交互项，必须跟预先乘好的列给出同一个系数。

    上一阶段的教训——手抄的数值算法错了不报错，只安静地给出错的数。
    所以每个新公式都要配一个能对照的实现。
    """
    manual = frame.assign(px=frame.paeduc * frame.educ)
    a = run_ols_fe(manual, plan(treatment="px", controls=["paeduc", "educ", "age"]))
    b = run_ols_fe(frame, plan(
        treatment="paeduc*educ", interactions=["paeduc*educ"],
        controls=["paeduc", "educ", "age"],
    ))
    assert a.estimates["main"].coef == pytest.approx(b.estimates["main"].coef, abs=1e-9)
    assert a.estimates["main"].se == pytest.approx(b.estimates["main"].se, abs=1e-9)


def test_交互项分量写错要被拒绝(prof) -> None:
    """**这是交互项进契约的全部意义。**

    预先在数据里乘好的话，`paeduc * eduq`（打错了）会静静地产出一列 NaN，
    或者更糟——乘到另一个存在但不相干的列上。没有任何校验能发现。
    """
    problems, _ = validate_plan(plan(interactions=["paeduc*eduq"]), prof)
    assert problems
    assert "eduq" in problems[0]
    assert "educ" in problems[0], "要给出最接近的列名"


def test_交互项缺低阶项要警告(prof) -> None:
    """交互项不带主效应，系数就不可解读——这是最常见的规格错误之一。

    三重交互 `a*b*c` 需要 a、b、c 三个主效应和 a*b、a*c、b*c 三个二阶项。
    **警告不拒绝**：偶尔有人是故意的，但绝大多数是忘了。
    """
    _, warnings = validate_plan(
        plan(interactions=["paeduc*educ*age"], controls=["educ"]), prof
    )
    joined = " ".join(warnings)
    assert "paeduc" in joined and "age" in joined, f"要点名缺了哪些项：{warnings}"


def test_低阶项齐全时不该警告(prof) -> None:
    """别把对的规格也标红。"""
    _, warnings = validate_plan(plan(
        interactions=["paeduc*educ", "paeduc*age", "educ*age", "paeduc*educ*age"],
        controls=["paeduc", "educ", "age"],
    ), prof)
    assert not any("低阶" in w or "主效应" in w for w in warnings), warnings


# ════════════════════════════════════════════ ⑤ 稳健性


def test_稳健性条目是可执行的规格不是散文() -> None:
    """**`robustness` 存在的理由是「事前锁定、事后不许改」。**

    锁一份没人能执行、没人能核对的散文，等于没锁——
    真跑的时候我照样可以想跑什么跑什么，事后再写一段话说"我跑了这些"。
    """
    p = AnalysisPlan(
        plan_id="p", idea_id="i", outcome="sei", treatment="paeduc",
        robustness=[{"label": "R1 结果换职业声望", "outcome": "prestige"}],
    )
    assert p.robustness[0].label == "R1 结果换职业声望"
    assert p.robustness[0].outcome == "prestige"
    assert p.robustness[0].executable


def test_散文式条目要被标成不可执行() -> None:
    """旧格式（一串字符串）仍然收，但**必须标出来它跑不了**——
    静默接受等于假装锁上了。"""
    p = AnalysisPlan(
        plan_id="p", idea_id="i", outcome="sei", treatment="paeduc",
        robustness=["R1 换个结果变量试试"],
    )
    assert p.robustness[0].label == "R1 换个结果变量试试"
    assert not p.robustness[0].executable


def test_跑一次就把主规格和所有稳健性都跑了(frame) -> None:
    """稳健性跟主结果**同一次跑完**，才谈得上「事前锁定」。

    分开跑的话，`results.json` 被后一次覆盖——这正是 GSS 真跑时
    七项稳健性报不出数值的原因。
    """
    res = run_ols_fe(frame, plan(robustness=[
        {"label": "R1 聚类换到 year", "cluster_level": "year"},
        # 注意不能用 `year >= 2008`——夹具里 paeduc 在 2000/2004 整段缺失，
        # 主规格实际已经只用 2008 年之后的数据，那样筛一行都不会掉。
        # **块状缺失会让「筛掉早期」这类稳健性变成空操作**，这本身值得记一笔。
        {"label": "R2 只保留 2012 年之后", "sample_filter": "year >= 2012"},
    ]))
    assert set(res.robustness) == {"R1 聚类换到 year", "R2 只保留 2012 年之后"}
    r1 = res.robustness["R1 聚类换到 year"]
    assert r1.estimates["main"].coef == pytest.approx(res.estimates["main"].coef, abs=1e-9), \
        "只换聚类层级，点估计不该变"
    assert r1.estimates["main"].se != res.estimates["main"].se, "标准误应当变"
    assert res.robustness["R2 只保留 2012 年之后"].n < res.n


def test_跑不动的稳健性要记下来不能静默跳过(frame) -> None:
    """一项稳健性跑不出来是信息，不是可以忽略的意外。"""
    res = run_ols_fe(frame, plan(robustness=[
        {"label": "R9 筛到没样本", "sample_filter": "year >= 3000"},
    ]))
    assert "R9 筛到没样本" in res.robustness
    assert res.robustness["R9 筛到没样本"].error, "跑不动要留下原因"


def test_散文条目不会被当成规格执行(frame) -> None:
    res = run_ols_fe(frame, plan(robustness=["R1 换个结果变量试试"]))
    entry = res.robustness["R1 换个结果变量试试"]
    assert entry.error and "散文" in entry.error
