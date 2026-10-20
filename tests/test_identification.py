"""因果设计的审计：机械能判的那一半。

刚做完的政策冲击对比让系统**能说出「是它造成的」**。
按项目一贯的顺序——**先装门，再抬天花板**——门必须跟上。

平行趋势检验已经是一道门，但它只看「处理前平不平」。
还有几件同样要紧、同样**机械可判**的事，之前一件都没查：

| 查什么 | 不查的后果 |
|---|---|
| 有没有**从未被处理**的对照组 | 全都最终被处理时，比较完全依赖「早晚之差」，而那正是分批偏误最重的场合 |
| 处理组与对照组处理前**可不可比** | 两组本来就是两种国家，「效应」里混着它们的固有差异 |
| **各批次的效应差多少** | 差得大就说明分批偏误是真的在起作用，不是理论担忧 |
| 面板构成在处理前后**变没变** | 处理后样本里少了一批国家的话，效应可能只是构成变了 |

判断那一半（为什么是这些地方先改、对照组合不合适、有没有溢出）
交给 skill 与跨模型，不在这里。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import run_plan
from oss.core.contracts import AnalysisPlan

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def clean():
    return pd.read_csv(FIXTURES / "did_clean.csv")


def plan(**kw) -> AnalysisPlan:
    base = dict(
        plan_id="p", idea_id="i", estimator="did", outcome="y",
        unit="unit", time="year", treat_time="treat_time", cluster_level="unit",
    )
    return AnalysisPlan(**{**base, **kw})


# ══════════════════════════════ 对照组是谁


def test_要报出对照组的构成(clean) -> None:
    """**「跟谁比」是这套方法的第一个问题**，而之前根本没报。"""
    res = run_plan(clean, plan())
    d = res.diagnostics.get("对照组", "")
    assert "从未" in d and "20" in d, d


def test_全都最终被处理时要警告(clean) -> None:
    """没有「从未被处理」的单位时，比较完全依赖各批次的早晚之差——
    **而那正是分批偏误最重的场合**。"""
    df = clean.copy()
    df.loc[df.treat_time.isna(), "treat_time"] = 2018.0
    res = run_plan(df, plan())
    d = res.diagnostics.get("对照组", "")
    assert "没有从未被处理" in d or "全部" in d, d
    assert "偏误" in d or "谨慎" in d, "要说清后果"


# ══════════════════════════════ 两组处理前可不可比


def test_处理前的可观测差异要报出来(clean) -> None:
    """两组本来就是两种单位的话，「效应」里混着它们的固有差异。

    判据是标准化差值：`(处理组均值 − 对照组均值) / 合并标准差`。
    经验上超过 0.25 就算明显不平衡。
    """
    res = run_plan(clean, plan(controls=["x"]))
    b = res.diagnostics.get("处理前平衡", "")
    assert "x" in b, b
    assert "标准化差值" in b or "SMD" in b, b


def test_明显不平衡要标出来(clean) -> None:
    df = clean.copy()
    df.loc[df.treat_time.notna(), "x"] = df.loc[df.treat_time.notna(), "x"] + 2.0
    res = run_plan(df, plan(controls=["x"]))
    b = res.diagnostics.get("处理前平衡", "")
    assert "不平衡" in b, b


def test_平衡的不该被误报(clean) -> None:
    """**别把对的也标红。**"""
    res = run_plan(clean, plan(controls=["x"]))
    assert "不平衡" not in res.diagnostics.get("处理前平衡", "")


# ══════════════════════════════ 各批次的效应差多少


def test_要分批估计并报出差异(clean) -> None:
    """分批偏误是**理论担忧**；各批效应差多少是**实证征兆**。

    只警告「可能有偏」而不给征兆，使用者无从判断这个担忧在自己数据上重不重。
    """
    res = run_plan(clean, plan())
    h = res.diagnostics.get("批次异质性", "")
    assert "2010" in h and "2014" in h, h


def test_批次太少时要说判不可靠而不是给判词(clean) -> None:
    """**又一次「判不了却给了判词」。**

    干净夹具只有 2 批、两批真实效应都是 3.0，却被 I² 判成「差异大」——
    因为批次太少时 I² 本身不稳定（元分析里通常要 5–10 个才谈得上）。

    判不了就说判不了。这条纪律在这个项目里已经用过很多次：
    没摘要 → 核不了；文本太少 → 判不了；后端不可用 → 不是通过。
    """
    res = run_plan(clean, plan())
    h = res.diagnostics.get("批次异质性", "")
    assert "判不可靠" in h, h
    assert "3.238" in h or "2.757" in h or "个单位" in h, "要把各批的估计值给出来让人自己判断"


def test_批次够多且效应真的差很大时要报出来() -> None:
    """批次够多（≥4）时，真的异质性必须被抓到。

    上面那条测的是「批次太少不下结论」，这条测的是「够多时不能漏报」——
    **两条都要有，否则「判不可靠」会变成万能挡箭牌。**
    """
    rng = np.random.default_rng(7)
    rows = []
    effects = {2006: 1.0, 2009: 5.0, 2012: -2.0, 2015: 8.0}   # 各批效应差很大
    for u in range(120):
        t0 = None if u % 5 == 0 else sorted(effects)[u % 4]
        for y in range(2000, 2021):
            eff = effects[t0] if (t0 and y >= t0) else 0.0
            rows.append(dict(unit=u, year=y,
                             treat_time=(float(t0) if t0 else np.nan),
                             y=float(eff + rng.normal(0, 1.0))))
    res = run_plan(pd.DataFrame(rows), plan())
    h = res.diagnostics.get("批次异质性", "")
    assert "差异大" in h, h
    assert "I²" in h, "要给出异质性的度量"


def test_批次小而多时不能把噪声当异质性(clean) -> None:
    """**真数据上暴露的问题。**

    QoG 那份数据有 28 个批次，其中 **17 个只有一个国家**。
    一两个国家算出来的效应标准误极大，把它们的散布当成「分批偏误的证据」是错的——
    **那是噪声，不是异质性**。而这个判据会在任何「批次多而小」的数据上无脑报警。

    判据要改成元分析里的异质性检验：把各批的**精度**算进去，
    问「散布是不是超过了纯抽样噪声能解释的范围」。
    """
    rng = np.random.default_rng(5)
    rows = []
    for u in range(60):
        t0 = 2006 + (u % 20)          # 20 个批次，每批 3 个单位
        for y in range(2000, 2021):
            rows.append(dict(unit=u, year=y, treat_time=float(t0),
                             y=float(3.0 * (y >= t0) + rng.normal(0, 3))))
    df = pd.DataFrame(rows)
    df.loc[df.unit % 5 == 0, "treat_time"] = np.nan     # 留一些从未处理的
    res = run_plan(df, plan())
    h = res.diagnostics.get("批次异质性", "")
    # 真实效应各批相同，散布全来自噪声——**不该报「差异大」**
    assert "差异大" not in h, h
    assert "噪声" in h or "不严重" in h or "接近" in h, h


def test_异质性判定要报出各批的单位数(clean) -> None:
    """**一个批次里有几个单位，决定了它的估计值可不可信。**不报出来就没法判断。"""
    res = run_plan(clean, plan())
    h = res.diagnostics.get("批次异质性", "")
    assert "个单位" in h or "国" in h or "n=" in h, h
