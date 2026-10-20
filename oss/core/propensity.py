"""倾向得分与逆概率加权（IPTW）。

**为什么只做加权、不做配对。**倾向得分配对（PSM）在社科里用得比加权多，
但 King 与 Nielsen（2019）的结果是：配对到一定程度之后，**再往下配反而让协变量更不平衡**
——因为最近邻配对是按一个一维的得分去近似多维的平衡，得分相近不等于协变量相近，
而每删掉一对就多丢一次信息。他们的建议是用别的办法（精确配对、粗化精确配对、加权）。

逆概率加权没有这个毛病：它不丢样本，平衡程度可以逐个协变量量出来（下面的标准化均值差），
而且跟这个项目已有的加权回归、按设计算标准误那一整套**是同一条路**——
抽样权重乘上逆概率权重就是最终权重，不用另起一套。

**加权不解决未观测的混淆。**这一点必须说在前面：IPTW 属于「可观测选择」那一族，
它把**你测到的**协变量调平，对没测到的东西一点办法都没有。平衡表好看
与因果成立是两件事——这跟工具变量那边「第一阶段强不强」与「排他性成不成立」
是两件事，完全同构。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: 加权之后标准化均值差的常用上界。超过它就说这一列没调平。
#: 0.1 是 Austin（2011）以来的通行阈值，不是本项目定的。
SMD_MAX = 0.10
#: 倾向得分被截到的上下界：太靠近 0 或 1 的观测会产生巨大的权重，
#: 一两个人就能主导整个估计。0.01 是常用的截断点。
PS_FLOOR = 0.01


@dataclass
class Balance:
    """一个协变量在加权前后的标准化均值差。"""

    name: str
    smd_before: float
    smd_after: float


@dataclass
class Fit:
    """倾向得分与逆概率权重，外加判它能不能用的那几个数。"""

    ps: np.ndarray                 # 每个人的倾向得分
    weight: np.ndarray             # 逆概率权重（已含抽样权重，已稳定化）
    balance: list[Balance] = field(default_factory=list)
    n_clipped: int = 0             # 得分被截到上下界的人数
    overlap: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    # ↑ (处理组最小得分, 处理组最大得分, 对照组最小得分, 对照组最大得分)
    n_off_support: int = 0         # 落在对方得分范围之外的人数
    ess: float = 0.0               # 加权之后的有效样本量（Kish）
    max_share: float = 0.0         # 单个观测占总权重的最大比例


def _smd(x: np.ndarray, t: np.ndarray, w: np.ndarray | None = None) -> float:
    """标准化均值差：两组均值之差 ÷ 两组方差的合并标准差。

    **分母用的是加权前的合并标准差**，加权前后都用同一个——
    换了分母就没法比「调平了多少」，那正是这张表要回答的事（Austin 2011）。
    """
    w = np.ones(len(x)) if w is None else w
    a, b = t > 0.5, t <= 0.5
    if not a.any() or not b.any():
        return float("nan")
    ma = float(np.average(x[a], weights=w[a]))
    mb = float(np.average(x[b], weights=w[b]))
    # 分母固定用未加权的两组方差
    va, vb = float(np.var(x[t > 0.5])), float(np.var(x[t <= 0.5]))
    pooled = np.sqrt((va + vb) / 2.0)
    return float("nan") if pooled == 0 else abs(ma - mb) / pooled


def fit(X: np.ndarray, t: np.ndarray, names: list[str],
        base_w: np.ndarray | None = None, *, stabilize: bool = True) -> Fit:
    """估倾向得分、算逆概率权重、量加权前后的平衡。

    `X` 不含截距（这里自己加）。`base_w` 是抽样权重，最终权重是它乘上逆概率权重。

    **稳定化**（Robins 等 2000）：分子用处理组的边际比例而不是 1，
    权重的方差小得多，而估的还是同一个量。

    返回的 `weight` 直接拿去做加权回归——跟抽样权重走同一条路，
    所以聚类稳健、按抽样设计算标准误那一整套照样适用。
    """
    from oss.core import discrete

    n = len(t)
    base = np.ones(n) if base_w is None else np.asarray(base_w, dtype=float)
    Xc = np.column_stack([np.ones(n), X])
    fitted = discrete.binary_logit(Xc, t, base)
    ps = np.clip(discrete._p(Xc, fitted.beta), PS_FLOOR, 1 - PS_FLOOR)
    n_clipped = int(((ps <= PS_FLOOR) | (ps >= 1 - PS_FLOOR)).sum())

    treated = t > 0.5
    p_treat = float(np.average(treated.astype(float), weights=base)) if stabilize else 1.0
    num = np.where(treated, p_treat, 1 - p_treat)
    ipw = num / np.where(treated, ps, 1 - ps)
    w = base * ipw

    bal = [Balance(nm, _smd(X[:, j], t), _smd(X[:, j], t, w)) for j, nm in enumerate(names)]

    lo_t, hi_t = (float(ps[treated].min()), float(ps[treated].max())) if treated.any() else (0.0, 0.0)
    lo_c, hi_c = (float(ps[~treated].min()), float(ps[~treated].max())) if (~treated).any() else (0.0, 0.0)
    # 共同支撑域：两组得分范围的交集之外的人
    lo, hi = max(lo_t, lo_c), min(hi_t, hi_c)
    off = int(((ps < lo) | (ps > hi)).sum())

    sw = w.sum()
    ess = float(sw * sw / np.square(w).sum()) if sw > 0 else 0.0
    return Fit(ps=ps, weight=w, balance=bal, n_clipped=n_clipped,
               overlap=(lo_t, hi_t, lo_c, hi_c), n_off_support=off, ess=ess,
               max_share=float(w.max() / sw) if sw > 0 else 0.0)


def worst(bal: list[Balance]) -> list[Balance]:
    """加权之后仍然没调平的那几列，从差到好排。"""
    bad = [b for b in bal if not np.isnan(b.smd_after) and b.smd_after > SMD_MAX]
    return sorted(bad, key=lambda b: -b.smd_after)
