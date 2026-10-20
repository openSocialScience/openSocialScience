"""2026-09-26 三份 demo 真跑逮出来的六处缺陷，一条一个回归测试。

六条的共同形状：**声明了却不生效，或者算错了却不报错**——跟这个项目反复踩的
「判据挂在作者自己声明的字段上」是同一族。所以判据一律挂在算得出来的事实上：
簇数与观测数的大小关系、估计量支不支持交互项、路径里含不含运行目录名、
方向词前面有没有否定。
"""

from __future__ import annotations

import json
import re

import pytest

from oss.api.errors import BadInput
from oss.core.contracts import AnalysisPlan, AnalysisResults, Estimate, RobustnessRun
from oss.core.manuscript import _resolve_raw, _says_not_sig, direction_warnings, mde_misuse
from oss.core.power import compute_power


def _mini_run(tmp_path, *, n_rows: int):
    """最小的运行目录：一份数据 + 画像 + ideas。"""
    import numpy as np
    import pandas as pd

    from oss.api import profile as profile_api
    from oss.core.contracts import Idea, IdeaSet

    run = tmp_path / "r"
    run.mkdir()
    rng = np.random.default_rng(0)
    pd.DataFrame({"y": rng.normal(size=n_rows), "x": rng.normal(size=n_rows)}
                 ).to_csv(run / "data.csv", index=False)
    profile_api.profile(str(run / "data.csv"), run=str(run))
    IdeaSet(ideas=[Idea(idea_id="i1", title="T", research_question="Q",
                        research_type="causal")]).save(run / "ideas.json")
    return run


def _profile_of(tmp_path, df):
    """给 `validate_plan` 用的画像。"""
    from oss.api import profile as profile_api
    from oss.core.contracts import DataProfile

    d = tmp_path / "p"
    d.mkdir(exist_ok=True)
    df.to_csv(d / "data.csv", index=False)
    profile_api.profile(str(d / "data.csv"), run=str(d))
    return DataProfile.load(d / "profile.json")


# ── ① oss power：簇数不能大于观测数 ────────────────────────────────
def test_簇数大于观测数会把功效算高() -> None:
    """**这是拦它的理由，不是口味。**整群设计效应是 1+(m−1)·ρx·ρu，
    m = n/簇数。簇数比观测数大时 m < 1、(m−1) 为负，设计效应掉到 1 以下，
    有效样本量反被放大——功效凭空变高，而且一声不响。

    真跑时把行数 11765 当簇数传进去，功效从 0.73 变成 0.90，判词从 gray 翻成 pass。
    """
    kw = dict(metric="d", n_clusters=None, icc=0.05, treatment_icc=1.0, haircut=1.0)
    _, n_ok, p_ok = compute_power(0.1, 5819, **{**kw, "n_clusters": 131})
    _, n_bad, p_bad = compute_power(0.1, 5819, **{**kw, "n_clusters": 11765})
    assert n_bad > n_ok, "簇数填成行数时有效样本量被放大——这正是要拦的"
    assert p_bad > p_ok


def test_power_接口拒绝显式给的簇数大于观测数(tmp_path) -> None:
    """门在 `oss power` 的入口，不在数学函数里：数学函数按给定参数算是对的，
    错的是让一个不可能的参数进来。

    **只拒显式给的那一路。**画像里的「单位数」记的未必是簇（整份文件的行数也会落进去），
    而 `n` 可能已经收窄到能用的行——两边口径不同，不是用户断言了不可能的事。
    那一路降级成「不按整群折算」并出警告，见下一条。
    """
    from oss.api import power as power_api

    run = _mini_run(tmp_path, n_rows=100)
    with pytest.raises(BadInput) as e:
        power_api.power(str(run), idea="i1", effect=0.2, n=100, clusters=500)
    assert "簇数不能大于观测数" in str(e.value)


def test_画像单位数大于样本量时不折整群且出警告(tmp_path) -> None:
    """照算就是凭空放大有效样本量；静默忽略又是这个项目反复踩的坑。**不折算，并且说出来。**"""
    from oss.api import power as power_api

    run = _mini_run(tmp_path, n_rows=100)
    res = power_api.power(str(run), idea="i1", effect=0.2, n=30)
    assert any("它记的不是簇" in w for w in res["data"]["warnings"]), res["data"]["warnings"]


# ── ② iv 下的交互项：原先被静默吞掉 ────────────────────────────────
def _iv_plan(**kw) -> AnalysisPlan:
    base = dict(plan_id="p", idea_id="i", estimator="iv", outcome="y",
                treatment="x", instruments=["z"], controls=["c"])
    return AnalysisPlan(**{**base, **kw})


def test_iv_带交互项要在计划期就拒(tmp_path) -> None:
    """`run_iv` 取列时根本没把 interactions 算进去，于是写了交互项
    **既不报错、也不生效**，跑出来还是没有交互项的那个规格。

    真做要两个内生变量，而有效 F、AR 区间、tF 区间三样诊断都是按单内生变量建的。
    没重做之前明说做不了，比悄悄少算一项强。
    """
    import numpy as np
    import pandas as pd

    from oss.core.analysis import validate_plan

    rng = np.random.default_rng(0)
    df = pd.DataFrame({"y": rng.normal(size=60), "x": rng.normal(size=60),
                       "z": rng.normal(size=60), "c": rng.normal(size=60),
                       "m": rng.integers(0, 2, 60).astype(float)})
    prof = _profile_of(tmp_path, df)
    codes = [p.code for p in validate_plan(_iv_plan(interactions=["x*m"]), prof, df)[0]]
    assert "plan.interaction.iv_unsupported" in codes
    # 不带交互项的同一个计划不该被这条拦住
    assert "plan.interaction.iv_unsupported" not in [
        p.code for p in validate_plan(_iv_plan(), prof, df)[0]]


# ── ③ 稳健性项的诊断引得出占位符了 ────────────────────────────────
def _results_with_diag() -> AnalysisResults:
    return AnalysisResults(
        plan_id="p", n=100,
        estimates={"main": Estimate(term="x", coef=0.044, se=0.018, p=0.016)},
        robustness={"只看汉族": RobustnessRun(
            label="只看汉族", n=4628,
            estimates={"main": Estimate(term="x", coef=0.009, se=0.043, p=0.83)},
            diagnostics={"有效 F": "26.32", "AR 区间": "[-0.084, 0.100]",
                         "2SLS/OLS": "0.53"})})


@pytest.mark.parametrize("key,want", [
    ("有效 F", "26.32"),                 # 键里带空格
    ("AR 区间", "[-0.084, 0.100]"),      # 值里带方括号与负号
    ("2SLS/OLS", "0.53"),                # **键里带斜杠**——label 与后半的分隔符也是斜杠
])
def test_稳健性项的诊断引得出来(key: str, want: str) -> None:
    """精确值一直在 `results.json` 里，占位符却只认 estimates 的几个字段，
    于是正文只能写「近四倍」这种约数。**能算出精确数却在正文写约数**，
    是这个项目最不该有的形状。
    """
    val, err = _resolve_raw("robust", f"只看汉族/diag.{key}", _results_with_diag(), None)
    assert err == "" and val == want


def test_稳健性项没有的诊断要报出可用的() -> None:
    val, err = _resolve_raw("robust", "只看汉族/diag.不存在的", _results_with_diag(), None)
    assert val is None and "有效 F" in err


# ── ④ 两个「有效样本量」不再同名 ──────────────────────────────────
def test_两个有效样本量各自写明口径() -> None:
    """一个只折算权重（置信区间用），一个还折算整群设计效应（功效用），
    原先都叫「有效样本量」。真跑时论文里一处 3606、一处 2541，读者对不上。
    """
    import pandas as pd

    import numpy as np

    from oss.core.analysis import _weights_text

    txt = _weights_text(np.array([1.0, 2.0, 3.0, 4.0]), "w", 4)
    assert "加权有效样本量" in txt
    assert "只折算了权重" in txt and "oss power" in txt


# ── ⑤ paper export 的 --out 不再静默套两层 ────────────────────────
def test_out_含运行目录名要拒(tmp_path) -> None:
    from oss.api.paper import _export_target
    from oss.api.rundir import open_run

    rd = open_run(tmp_path / "demo_causal")
    assert _export_target(rd, "paper.docx").name == "paper.docx"
    assert _export_target(rd, "/tmp/a.docx").as_posix() == "/tmp/a.docx"
    with pytest.raises(BadInput) as e:
        _export_target(rd, "runs/demo_causal/paper.docx")
    assert "套了两层" in str(e.value)


# ── ⑥ 方向词与显著性判据认否定了 ──────────────────────────────────
@pytest.mark.parametrize("sent,coef,should_warn", [
    ("信任一侧没有出现同向的正向关联。", "-0.044", False),
    ("whereas no positive association is detected for income.", "-0.042", False),
    ("捐赠的概率提高了。", "0.044", False),
    ("捐赠的概率下降了。", "0.044", True),          # 真的说反了
    ("这一关联不仅正向，而且稳健。", "-0.030", True),  # 「不仅」是强调不是否定
])
def test_方向词前的否定不算那个方向(sent: str, coef: str, should_warn: bool) -> None:
    assert bool(direction_warnings(sent, "x.coef", coef)) is should_warn


@pytest.mark.parametrize("sent,not_sig", [
    ("加入省份固定效应后该系数不再显著。", True),
    ("该系数不复显著。", True),
    ("该系数不显著。", True),
    ("该系数显著。", False),
    ("该系数不仅显著，而且稳健。", False),
])
def test_不再显著要认成不显著(sent: str, not_sig: bool) -> None:
    """否定词与「显著」中间隔着一个字，原先两条分支都够不着，
    于是「不再显著」被判成在说显著。"""
    assert _says_not_sig(sent) is not_sig


def test_不是排除了不算犯那个谬误() -> None:
    """「也**不是**排除了全部正向关联」是正确的自我限定，
    原先挡板里没有「不是」一族，这句被判成把 MDE 当排除下界。"""
    assert mde_misuse("本数据能分辨的最小效应是 0.065，也不是排除了全部正向关联。") == ""
    assert mde_misuse("最小可探测效应是 0.065，因此可以排除更大的效应。") != ""
