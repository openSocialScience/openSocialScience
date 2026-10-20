"""借力变量法与断点法。

**两者的核心假设，一个能检验、一个永远不能——这个不对称必须写进工具。**

| 方法 | 核心假设 | 能不能用数据检验 |
|---|---|---|
| 借力变量法 | 借力变量只通过处理影响结果 | **根本不能**（数学上无法检验） |
| 借力变量法 | 借力变量与处理确实相关 | **能**，第一阶段强度 |
| 断点法 | 线两侧的人除了过没过线几乎一样 | **能**，看线附近的人数分布与协变量连续性 |

所以借力变量法必须**每次都把「排他性无法检验」说出来**——
一个核心假设永远无法被检查的方法，不能让它安静地产出一个数。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from oss.core.analysis import run_plan
from oss.core.contracts import AnalysisPlan

FIXTURES = Path(__file__).parent / "fixtures"
TRUE_BETA, TRUE_TAU, CUTOFF = 2.0, 4.0, 60.0


def iv_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="iv", outcome="y",
                treatment="x", instruments=["z"], controls=["w"])
    return AnalysisPlan(**{**base, **kw})


def rdd_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="rdd", outcome="y",
                running="score", cutoff=CUTOFF, bandwidth=10.0)
    return AnalysisPlan(**{**base, **kw})


# ══════════════════════════════ 借力变量法：数值正确性


def test_借力变量法能还原真实效应() -> None:
    """**最硬的一条**：真实效应是我设定的 2.0，而直接回归会算成 2.68。

    算不出 2.0 就说明公式写错了。
    """
    df = pd.read_csv(FIXTURES / "iv_like.csv")
    got = run_plan(df, iv_plan()).estimates["main"].coef
    assert abs(got - TRUE_BETA) < 0.15, f"真实 {TRUE_BETA}，算出 {got}"


def test_直接回归确实会偏() -> None:
    """确认夹具真的有内生性——否则上一条测试是空的。"""
    df = pd.read_csv(FIXTURES / "iv_like.csv")
    ols = run_plan(df, AnalysisPlan(
        plan_id="p", idea_id="i", estimator="ols_fe",
        outcome="y", treatment="x", controls=["w"])).estimates["main"].coef
    assert ols > TRUE_BETA + 0.3, f"直接回归 {ols}，没造出内生性"


def test_恰好识别时等于比值估计() -> None:
    """**独立复核。**一个借力变量、一个处理、无控制变量时有闭式解：

        β = Cov(z, y) / Cov(z, x)

    程序算的必须跟它一致。对不上就是公式写错了。
    """
    df = pd.read_csv(FIXTURES / "iv_like.csv")
    got = run_plan(df, iv_plan(controls=[])).estimates["main"].coef
    manual = np.cov(df.z, df.y)[0, 1] / np.cov(df.z, df.x)[0, 1]
    # 容差取 1e-5 而不是 1e-8：系数存进产出物时保留六位小数。
    # 更严的容差测的是「存储精度」，不是「公式对不对」。
    assert got == pytest.approx(manual, abs=1e-5), f"程序 {got}，手算 {manual}"


# ══════════════════════════════ 借力变量法：两条必须报的诊断


def test_第一阶段强度必须报出来() -> None:
    """**这是借力变量法唯一能检验的那个假设。**"""
    res = run_plan(pd.read_csv(FIXTURES / "iv_like.csv"), iv_plan())
    f = res.diagnostics.get("第一阶段强度", "")
    assert "F" in f, f
    assert "够强" in f or "充分" in f, f


def test_弱借力变量必须被拦住() -> None:
    """**弱借力变量的估计会剧烈失真，而且不报错。**

    经验阈值是第一阶段 F 大于 10；低于它，估计量的偏误可能比直接回归还大。
    """
    res = run_plan(pd.read_csv(FIXTURES / "iv_weak.csv"), iv_plan())
    f = res.diagnostics.get("第一阶段强度", "")
    assert "太弱" in f or "不可用" in f, f
    assert "偏误" in f, "要说清后果"


def test_工具变量报有效F_AR区间与OLS对照() -> None:
    d = run_plan(pd.read_csv(FIXTURES / "iv_like.csv"), iv_plan()).diagnostics
    assert float(d["有效 F"]) > 10
    lo, hi = (float(v) for v in d["AR 区间"].strip("[]").split(","))
    assert lo < TRUE_BETA < hi, d["AR 区间"]
    assert "OLS" in d["OLS 对照"] and float(d["2SLS/OLS"]) > 0


def test_弱工具的第一阶段强度说要改用AR() -> None:
    d = run_plan(pd.read_csv(FIXTURES / "iv_weak.csv"), iv_plan()).diagnostics
    assert float(d["有效 F"]) < 10
    assert "太弱" in d["第一阶段强度"] and "偏误" in d["第一阶段强度"] and "AR" in d["第一阶段强度"]


def test_有效F与AR跟StatsPAI对数() -> None:
    sp = pytest.importorskip("statspai")
    df = pd.read_csv(FIXTURES / "iv_like.csv")
    d = run_plan(df, iv_plan()).diagnostics
    ef = sp.effective_f_test(df, endog="x", instruments=["z"], exog=["w"], vcov="HC1")
    assert float(d["有效 F"]) == pytest.approx(float(ef["F_eff"]), rel=0.02)          # 键名照 Step 0
    ar = sp.anderson_rubin_test(df, y="y", endog="x", instruments=["z"], exog=["w"], h0=0.0, vcov="HC1")
    assert "p" in d["AR 说明"] and float(ar["ar_pvalue"]) < 0.05                        # 键名照 Step 0：ar_pvalue


def test_没装StatsPAI时tF写没算(monkeypatch) -> None:
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "statspai":
            raise ImportError("no statspai")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    d = run_plan(pd.read_csv(FIXTURES / "iv_like.csv"), iv_plan()).diagnostics
    assert d["tF 区间"].startswith("没算")


def test_排他性无法检验这件事必须每次都说() -> None:
    """**一个核心假设永远无法被检查的方法，不能让它安静地产出一个数。**

    排他性（借力变量只通过处理影响结果）在数学上无法用数据检验。
    工具能做的只有一件事：**每次都把这句话说出来**。
    """
    res = run_plan(pd.read_csv(FIXTURES / "iv_like.csv"), iv_plan())
    e = res.diagnostics.get("排他性", "")
    assert "无法" in e and "检验" in e, e
    assert "论证" in e or "讲道理" in e, "要说清唯一的出路是论证"


# ══════════════════════════════ 断点法：数值正确性


def test_断点法能还原真实跳跃() -> None:
    """**判据要用多次抽样的均值，不能用一次。**

    第一版只跑一次，用默认种子算出 4.615，超出容差就报错——
    但换 12 个种子重跑，三个带宽下的均值都是 4.08、标准差 0.17–0.33。
    **那一次只是抽到了偏高的一抽，不是公式错。**

    一次抽样的波动被当成 bug，会逼人去「修」一个没坏的东西；
    反过来，用一次抽样去证明「算对了」也同样不可靠。
    """
    import sys

    sys.path.insert(0, str(FIXTURES))
    from make_causal_like import build_rdd

    got = [run_plan(build_rdd(seed=s), rdd_plan()).estimates["main"].coef
           for s in range(8)]
    mean = float(np.mean(got))
    assert abs(mean - TRUE_TAU) < 0.3, f"真实 {TRUE_TAU}，8 次抽样均值 {mean:.3f}：{got}"


def test_带宽变化时结论要稳() -> None:
    """**带宽是人选的，所以必须报出「换个带宽会怎样」。**"""
    df = pd.read_csv(FIXTURES / "rdd_like.csv")
    res = run_plan(df, rdd_plan())
    b = res.diagnostics.get("带宽敏感性", "")
    assert "带宽" in b, b
    for w in (5, 15):
        got = run_plan(df, rdd_plan(bandwidth=float(w))).estimates["main"].coef
        assert abs(got - TRUE_TAU) < 1.0, f"带宽 {w} 时算出 {got}"


# ══════════════════════════════ 断点法：卡线必须被抓住


def test_卡线必须被抓住() -> None:
    """**线下的人挤到线上，断点法就失效了，而它不报错。**

    判据是机械的：看分数线紧邻两侧的人数是不是明显不对称。
    """
    res = run_plan(pd.read_csv(FIXTURES / "rdd_manip.csv"), rdd_plan())
    m = res.diagnostics.get("卡线检验", "")
    assert "不通过" in m or "疑似" in m, m
    assert "结论不可信" in m or "失效" in m, "要说清后果"


def test_没卡线的不该被误报() -> None:
    """**别把对的也标红。**"""
    res = run_plan(pd.read_csv(FIXTURES / "rdd_like.csv"), rdd_plan())
    m = res.diagnostics.get("卡线检验", "")
    assert "不通过" not in m and "疑似" not in m, m


def test_协变量在线上应当连续() -> None:
    """该在线上连续的东西如果也跳了，说明跳的不只是处理。"""
    res = run_plan(pd.read_csv(FIXTURES / "rdd_like.csv"), rdd_plan(controls=["w"]))
    c = res.diagnostics.get("协变量连续性", "")
    assert "w" in c, c
