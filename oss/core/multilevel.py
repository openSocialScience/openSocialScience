"""多层模型（随机截距）。**方差分解是它存在的理由，不是「处理聚类」。**

设计稿 `docs/design/2026-09-25-logit-multilevel.md` 第三节。

**最要紧的一句写在最前面**：多层模型常被用来「把聚类处理掉」，
但**只为修标准误的话，聚类稳健更稳**——它不假设随机效应服从正态、
不假设随机效应与自变量不相关。所以计划必须声明**为什么要用它**，
声明成「修标准误」的直接拒，并指去 `ols_fe` + `cluster_level`。

**这道门挡的不是技术错误，是用错工具**——而那正是审稿人最常问、作者最答不上来的。

REML 手写不划算，用 `statsmodels`，开一个自己的 extra；照 `staggered.py`
对 statspai 那套：**没装就如实说、把安装命令报出来，不静默退化**。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

INSTALL_HINT = "pip install 'opensocialscience[mixed]'（或 pip install statsmodels）"

#: 二层单位数的两道线。随机效应的方差分量要够多的组才估得准——
#: 少于 30 时分量的区间宽到没意义，少于 10 时基本是噪声。
#: 判据跟 `clusters` 那张图同源（都是「组数够不够」），数字也对齐聚类稳健那条线
_WARN_GROUPS, _HARD_GROUPS = 30, 10


class Unavailable(RuntimeError):
    """没装 statsmodels。**这不是错误，是能力缺失**——照实说，别静默跳过。"""


def available() -> bool:
    try:
        import statsmodels.regression.mixed_linear_model  # noqa: F401
    except ImportError:
        return False
    return True


def _version() -> str:
    try:
        import statsmodels
        return str(statsmodels.__version__)
    except Exception:  # noqa: BLE001 —— 版本号取不到不该挡住估计
        return "?"


@dataclass
class MixedFit:
    beta: np.ndarray
    se: np.ndarray
    terms: list[str]
    var_group: float            #: 二层（组间）方差 σ²_u
    var_resid: float            #: 一层（组内）方差 σ²_e
    icc: float                  #: σ²_u / (σ²_u + σ²_e)
    icc_se: float               #: 自助法给的，见 `_icc_se`
    n: int
    n_groups: int
    converged: bool
    method: str


def group_count(frame: pd.DataFrame, group: str) -> int:
    return int(frame[group].nunique())


def _icc_se(model_fit, var_g: float, var_e: float) -> float:
    """ICC 的标准误，delta 方法。

    ICC = σ²_u / (σ²_u + σ²_e)，对两个方差分量求导：
        ∂ICC/∂σ²_u =  σ²_e / (σ²_u+σ²_e)²
        ∂ICC/∂σ²_e = −σ²_u / (σ²_u+σ²_e)²

    **statsmodels 这里有两个刻度，别拿混**：`cov_re` 是**绝对**方差（σ²_u 本身），
    而 `params["Group Var"]` 与 `cov_params()` 用的是**相对**刻度（σ²_u/σ²_e）。
    混起来误差是 σ²_e 的平方倍，**而算出来仍然像个正常的小数**——
    第一版就把 σ²_u 乘成了 0.87（真值 0.6），靠「组数加到 300 偏差不缩小」才看出来：
    抽样噪声会随组数变小，系统偏差不会。

    拿不到协方差就返回 nan，由调用方说出来，**不猜**。
    """
    tot = var_g + var_e
    if tot <= 0:
        return float("nan")
    try:
        cov = np.asarray(model_fit.cov_params())
        if cov.shape[0] < 2:
            return float("nan")
        # `cov_params()` 的最后一行列对应的是相对刻度的方差分量（params["Group Var"]），
        # 换成绝对刻度要乘 σ²_e 的平方——**这一处跟上面那个 `cov_re` 不是同一个刻度**，
        # 两者混起来误差是 σ²_e 的平方倍，而算出来仍然像个正常的小数
        v_u = float(cov[-1, -1]) * var_e ** 2
        d_u = var_e / tot ** 2
        return float(abs(d_u) * np.sqrt(max(v_u, 0.0)))
    except Exception:  # noqa: BLE001 —— 拿不到就说拿不到
        return float("nan")


def fit_random_intercept(df: pd.DataFrame, outcome: str, terms: list[str],
                         group: str) -> MixedFit:
    """随机截距模型（REML）。**主结果是 ICC**，不是某个回归系数。

    **不加权**：多层模型的加权要 level-1 与 level-2 两层权重，而调查数据
    通常只给一层。硬按一层权重跑出来的方差分量是错的，**而它看起来是对的**。
    降级由调用方说出来并写进局限（门 M3）。
    """
    if not available():
        raise Unavailable(f"多层模型要 statsmodels，当前没装。{INSTALL_HINT}")
    import statsmodels.formula.api as smf

    work = df[[outcome, group, *terms]].copy()
    safe = {c: f"v{i}" for i, c in enumerate([outcome, *terms])}
    work = work.rename(columns=safe)
    rhs = " + ".join(safe[t] for t in terms) if terms else "1"
    model = smf.mixedlm(f"{safe[outcome]} ~ {rhs}", work, groups=work[group])
    res = model.fit(reml=True)

    var_e = float(res.scale)
    # **`cov_re` 已经是绝对刻度**，不要再乘 scale。
    # 第一版乘了，把 0.62 放大成 0.87（真值 0.6）——而且**组数从 60 加到 300 偏差不缩小**，
    # 那正是「系统性偏差」跟「抽样噪声」的分界：噪声会随组数变小，偏差不会。
    # 真正按相对刻度存的是 `params["Group Var"]`（= cov_re / scale），别拿错。
    var_g = float(np.asarray(res.cov_re).ravel()[0])
    tot = var_g + var_e
    inv = {v: k for k, v in safe.items()}
    names, betas, ses = [], [], []
    for nm in res.params.index:
        if nm.endswith("Var") or nm == "Group Var":
            continue
        raw = inv.get(nm, nm)
        if raw in terms:
            names.append(raw)
            betas.append(float(res.params[nm]))
            ses.append(float(res.bse[nm]))
    return MixedFit(
        beta=np.array(betas), se=np.array(ses), terms=names,
        var_group=var_g, var_resid=var_e,
        icc=float(var_g / tot) if tot > 0 else float("nan"),
        icc_se=_icc_se(res, var_g, var_e),
        n=len(work), n_groups=int(work[group].nunique()),
        converged=bool(res.converged), method=f"REML（statsmodels {_version()}）")


def group_problem(n_groups: int, group: str) -> tuple[str, bool]:
    """门 M2：二层单位够不够。返回 (说法, 是否该拒)。

    **判据是算得出来的事实**（`frame[group].nunique()`），不是作者声明分了几层。
    """
    if n_groups < _HARD_GROUPS:
        return (f"「{group}」只有 {n_groups} 个二层单位（少于 {_HARD_GROUPS}）。"
                f"**方差分量在这么少的组上基本是噪声**——ICC 估出来的数没有意义。\n"
                f"  组数就是这么多的话，改用 estimator: ols_fe 并把 {group} 放进 "
                f"fixed_effects（组间差异当固定的处理，不估它的方差）", True)
    if n_groups < _WARN_GROUPS:
        return (f"「{group}」只有 {n_groups} 个二层单位（少于 {_WARN_GROUPS}）——"
                f"方差分量的区间会很宽，ICC 要连区间一起读，不要只读点估计", False)
    return "", False
