"""三轮互盲审稿暴露的三个工具层缺口。

审稿意见不该只用来改稿——**该用来修工具，让这一类问题不能再发生**。
改稿只救这一篇，修工具救所有篇。

三条都经过核实，全部属实（见 `docs/runs/gss20260824_v2-*review*.json`）：

**A. 分箱估计不落盘。**全文最强的结论（「最晚队列两组已无实质差别」）
唯一依据是一张图，而图上的数字不在任何产出物里。
后果：这句话**没有占位符可用**，只能用散文写，而散文正是主张核验管不住的地方。
两位审稿人独立指出了这一条。

**B. 主张核验只看主规格的诊断。**R3 那个补充检验的诊断里写着「严重共线」，
正文没提，而检查只扫顶层，扫不到。

**C. 功效用的人数与实际分析人数不一致**（49,891 vs 47,047，差 2,844），
没有任何东西核这一致性。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from oss.core.analysis import run_ols_fe
from oss.core.contracts import (
    AnalysisPlan,
    AnalysisResults,
    Estimate,
    PowerReport,
    RobustnessRun,
)
from oss.core.manuscript import check, render

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def frame():
    return pd.read_csv(FIXTURES / "gss_like.csv")


def plan(**kw) -> AnalysisPlan:
    base = dict(
        plan_id="p", idea_id="i", outcome="sei", treatment="paeduc",
        controls=["educ", "age"], fixed_effects=["region"], cluster_level="region_year",
    )
    return AnalysisPlan(**{**base, **kw})


# ══════════════════════════════ A 分箱估计要落进产出物


def test_分箱估计要落进结果文件(frame) -> None:
    """**图上的数字必须能被复核。**

    一张图是给人看形状的；但论文引用它做出事实断言时，
    读者要能查到每一箱的点估计、标准误和样本量。
    图里有、文件里没有，就等于「相信我」。
    """
    res = run_ols_fe(frame, plan(
        binned={"by": "cohort", "split": "female", "treatment": "paeduc", "bins": 4}))
    assert res.binned, "分箱结果没落盘"
    rows = next(iter(res.binned.values()))
    r = rows[0]
    assert r.slope is not None and r.se > 0 and r.n > 0 and r.center is not None


def test_不声明分箱就不算(frame) -> None:
    """没在计划里声明的分析不该偷偷跑——**事前锁定的意义就在这**。"""
    assert not run_ols_fe(frame, plan()).binned


def test_分箱结果能被论文引用() -> None:
    """引不了就等于没落盘。论文要能写「最晚一箱的斜率是多少」。"""
    from oss.core.contracts import BinnedSlope

    res = AnalysisResults(
        plan_id="p", n=300,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)},
        binned={"female=0.0": [
            BinnedSlope(group="female=0.0", bin_index=1, center=1920.0,
                        slope=1.03, se=0.11, n=90),
            BinnedSlope(group="female=0.0", bin_index=2, center=1980.0,
                        slope=0.64, se=0.09, n=110),
        ]},
    )
    text = ("# T\n## 摘要\na\n## 引言\nb\n## 数据与方法\nc\n## 结果\n"
            "最早一箱 {{bin:female=0.0/first.slope}}，"
            "最晚一箱 {{bin:female=0.0/last.slope}}"
            "（标准误 {{bin:female=0.0/last.se}}，{{bin:female=0.0/last.n}} 个观测）。"
            "\n## 讨论\nd")
    problems, _ = check(text, res)
    assert not problems, problems
    out = render(text, res)
    assert "1.030" in out and "0.640" in out and "110" in out


def test_引用不存在的分箱要被拒绝并列出可用的() -> None:
    from oss.core.contracts import BinnedSlope

    res = AnalysisResults(
        plan_id="p", n=10,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)},
        binned={"female=0.0": [BinnedSlope(group="female=0.0", bin_index=1,
                                           center=1.0, slope=1.0, se=0.1, n=5)]},
    )
    text = ("# T\n## 摘要\na\n## 引言\nb\n## 数据与方法\nc\n## 结果\n"
            "{{bin:male/last.slope}}\n## 讨论\nd")
    problems, _ = check(text, res)
    assert any("female=0.0" in p for p in problems), problems


# ══════════════════════════════ B 稳健性各自的诊断也要核


def test_稳健性里的严重共线没提也要拦() -> None:
    """R3 的诊断里写着「严重共线」，正文没提——而检查只扫顶层，扫不到。"""
    res = AnalysisResults(
        plan_id="p", n=100,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)},
        diagnostics={"共线诊断（VIF）": "最大 VIF 2.0，无共线问题"},
        robustness={"R3 限制年龄": RobustnessRun(
            label="R3 限制年龄", n=80,
            estimates={"main": Estimate(term="x", coef=0.9, se=0.1, p=0.002)},
            diagnostics={"共线诊断（VIF）": "**严重**：age2=127、age=127"})},
    )
    text = ("# T\n## 摘要\na\n## 引言\nb\n## 数据与方法\nc\n## 结果\n"
            "{{result:main.coef}}\n## 讨论\nd")
    problems, _ = check(text, res)
    assert any("R3 限制年龄" in p for p in problems), (
        f"要点名是哪个检验出的问题：{problems}")


def test_稳健性共线正常就不拦() -> None:
    res = AnalysisResults(
        plan_id="p", n=100,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)},
        robustness={"R1": RobustnessRun(
            label="R1", n=90, estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.01)},
            diagnostics={"共线诊断（VIF）": "最大 VIF 1.5，无共线问题"})},
    )
    text = ("# T\n## 摘要\na\n## 引言\nb\n## 数据与方法\nc\n## 结果\n"
            "{{result:main.coef}}\n## 讨论\nd")
    assert not check(text, res)[0]


# ══════════════════════════════ C 人数对不上要报出来


def test_功效用的人数与实际分析人数不一致要警告() -> None:
    """49,891 vs 47,047，差 2,844。**纯机械可判，之前没人核。**"""
    res = AnalysisResults(
        plan_id="p", n=47047,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)})
    pw = PowerReport(idea_id="i", n_raw=49891, power=0.77, verdict="gray",
                     gray_band=(0.4, 0.8), discipline="sociology")
    text = ("# T\n## 摘要\na\n## 引言\nb\n## 数据与方法\nc\n## 结果\n"
            "{{result:main.coef}}\n## 讨论\n事前功效落在灰区。")
    _, warns = check(text, res, pw)
    assert any("47047" in w and "49891" in w for w in warns), warns


def test_人数一致就不警告() -> None:
    res = AnalysisResults(
        plan_id="p", n=47047,
        estimates={"main": Estimate(term="x", coef=1.0, se=0.1, p=0.001)})
    pw = PowerReport(idea_id="i", n_raw=47047, power=0.77, verdict="gray",
                     gray_band=(0.4, 0.8), discipline="sociology")
    text = ("# T\n## 摘要\na\n## 引言\nb\n## 数据与方法\nc\n## 结果\n"
            "{{result:main.coef}}\n## 讨论\n事前功效落在灰区。")
    _, warns = check(text, res, pw)
    assert not any("人数" in w or "样本量" in w for w in warns), warns
