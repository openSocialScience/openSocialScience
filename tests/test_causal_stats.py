"""卡方与非中心卡方。**不依赖 scipy**：基础安装就要能算联合检验与预检功效。

功效模块出过一次事：正态分位数手抄错了，所有功效被系统性高估（0.16 报成 0.51）。
所以每个函数都要有两道对数：跟教科书常数比，装了 scipy 时再跟 scipy 比。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from oss.core import causal_stats as cs


@pytest.mark.parametrize("df,crit", [(1, 3.841459), (2, 5.991465), (4, 9.487729), (8, 15.507313)])
def test_卡方上五分位跟表一致(df: int, crit: float) -> None:
    assert cs.chi2_ppf(0.95, df) == pytest.approx(crit, abs=1e-5)
    assert cs.chi2_sf(crit, df) == pytest.approx(0.05, abs=1e-6)


def test_非中心参数为零时退化为中心卡方() -> None:
    assert cs.ncx2_sf(5.0, 3, 0.0) == pytest.approx(cs.chi2_sf(5.0, 3))


def test_非中心参数越大上尾越大() -> None:
    c = cs.chi2_ppf(0.95, 3)
    assert cs.ncx2_sf(c, 3, 1.0) < cs.ncx2_sf(c, 3, 5.0) < cs.ncx2_sf(c, 3, 20.0) < 1.0


def test_单变量Wald等于t的平方() -> None:
    assert cs.wald(np.array([2.0]), np.array([[0.25]])) == pytest.approx(16.0)


def test_一个约束的F上尾就是t的双侧尾() -> None:
    """F(1, d) 与 t(d)² 是同一个分布——联合检验只剩一期时，它要跟逐期那把尺子对得上。"""
    for d in (5, 20, 100):
        for x in (0.5, 2.0, 7.0):
            assert cs.f_sf(x, 1, d) == pytest.approx(2 * cs.t_sf(math.sqrt(x), d), rel=1e-9)


def test_F的分位跟上尾互为反解() -> None:
    for dfn, dfd in ((1, 10), (3, 57), (7, 53)):
        c = cs.f_ppf(0.95, dfn, dfd)
        assert cs.f_sf(c, dfn, dfd) == pytest.approx(0.05, abs=1e-9)


def test_分母自由度趋大时F退回卡方() -> None:
    """聚类数远大于矩条件数时，小样本校正应当消失——否则校正本身成了新的偏差。"""
    for k in (1, 3, 7):
        for w in (1.0, 5.0, 15.0):
            assert cs.f_sf(w / k, k, 10_000_000) == pytest.approx(cs.chi2_sf(w, k), rel=1e-4)


def test_跟scipy对数() -> None:
    stats = pytest.importorskip("scipy.stats")
    for df in (1, 3, 7):
        for x in (0.5, 3.0, 12.0):
            assert cs.chi2_sf(x, df) == pytest.approx(stats.chi2.sf(x, df), rel=1e-9)
            for lam in (0.5, 4.0, 30.0):
                assert cs.ncx2_sf(x, df, lam) == pytest.approx(stats.ncx2.sf(x, df, lam), rel=1e-7)
            for dfd in (4, 25, 113):
                assert cs.f_sf(x, df, dfd) == pytest.approx(stats.f.sf(x, df, dfd), rel=1e-9)
