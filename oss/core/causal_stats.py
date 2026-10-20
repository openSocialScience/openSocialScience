"""联合检验与预检功效要用的几个分布。**纯 math / numpy，不依赖 scipy。**

- `chi2_sf` / `chi2_ppf`：中心卡方的上尾概率与下尾分位
- `ncx2_sf`：非中心卡方的上尾概率（泊松混合），给预检功效用
- `wald`：`b' V⁻¹ b`
- `betainc` / `t_sf` / `t_ppf`：正则化不完全贝塔、t 分布上尾与分位（调查层按设计推断用，自由度 = 抽样单位数 − 层数）
- `f_sf`：F 分布上尾（联合检验的小样本校正用，分母自由度来自聚类数）
- `mde_multiplier`：最小可探测效应的倍数，按设计时用 t

正则化不完全伽马函数用级数（x < a+1）与连分式（x ≥ a+1）两段，照《Numerical Recipes》。
"""
from __future__ import annotations

import functools
import math

import numpy as np

_EPS = 1e-15
_TINY = 1e-300


def _gser(a: float, x: float) -> float:
    """下正则化不完全伽马 P(a, x)，级数展开；用于 x < a + 1。"""
    if x <= 0:
        return 0.0
    ap, term, total = a, 1.0 / a, 1.0 / a
    for _ in range(10_000):
        ap += 1.0
        term *= x / ap
        total += term
        if abs(term) < abs(total) * _EPS:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    """上正则化不完全伽马 Q(a, x)，Lentz 连分式；用于 x ≥ a + 1。"""
    b = x + 1.0 - a
    c = 1.0 / _TINY
    d = 1.0 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = _TINY if abs(d) < _TINY else d
        c = b + an / c
        c = _TINY if abs(c) < _TINY else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def _gammaincc(a: float, x: float) -> float:
    """上正则化不完全伽马 Q(a, x) = 1 − P(a, x)。"""
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        return 1.0 - _gser(a, x)
    return _gcf(a, x)


def chi2_sf(x: float, df: int) -> float:
    """中心卡方的上尾概率 P(χ²_df > x)。"""
    if x <= 0:
        return 1.0
    return _gammaincc(df / 2.0, x / 2.0)


def chi2_ppf(q: float, df: int) -> float:
    """中心卡方的下尾分位：P(χ²_df ≤ x) = q。二分法反解 `chi2_sf`。"""
    target = 1.0 - q
    lo, hi = 0.0, max(1.0, float(df))
    while chi2_sf(hi, df) > target:
        hi *= 2.0
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if chi2_sf(mid, df) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return (lo + hi) / 2.0


def ncx2_sf(x: float, df: int, lam: float) -> float:
    """非中心卡方的上尾概率 P(χ²_df(λ) > x)：泊松（均值 λ/2）加权的中心卡方混合。

    权重在对数空间里算，λ 很大时 `exp(−λ/2)` 不会下溢；只加众数两侧足够宽的一段。
    """
    if lam <= 0:
        return chi2_sf(x, df)
    half = lam / 2.0
    mode = int(half)
    span = int(10.0 * math.sqrt(half) + 50)
    total = 0.0
    for j in range(max(0, mode - span), mode + span + 1):
        logw = -half + (j * math.log(half) if j else 0.0) - math.lgamma(j + 1)
        total += math.exp(logw) * chi2_sf(x, df + 2 * j)
    return min(1.0, total)


def wald(b: np.ndarray, V: np.ndarray) -> float:
    """Wald 统计量 `b' V⁻¹ b`。V 奇异时用伪逆——**不许因此报 0**。"""
    b = np.asarray(b, dtype=float).ravel()
    V = np.asarray(V, dtype=float)
    try:
        sol = np.linalg.solve(V, b)
    except np.linalg.LinAlgError:
        sol = np.linalg.pinv(V) @ b
    return float(b @ sol)


#: 连分式最多迭代几步。自然的输入远到不了（a = b = 1e12 也只要两千来步）；到了还没收敛就报错
_BETACF_MAX_ITER = 10_000


def _betacf(a: float, b: float, x: float) -> float:
    """不完全贝塔函数的连分式（Lentz），照《Numerical Recipes》6.4。

    到迭代上限还没收敛就报 `RuntimeError`——原先悄悄返回半截的值，p 值与临界值会跟着错而没人知道。
    """
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    h = d
    for m in range(1, _BETACF_MAX_ITER):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + aa / c
        c = c if abs(c) > _TINY else _TINY
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > _TINY else _TINY)
        c = 1.0 + aa / c
        c = c if abs(c) > _TINY else _TINY
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    else:
        raise RuntimeError(f"不完全贝塔的连分式迭代到上限 {_BETACF_MAX_ITER} 步还没收敛（a={a}, b={b}, x={x}）")
    return h


def betainc(a: float, b: float, x: float) -> float:
    """正则化不完全贝塔 I_x(a, b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """t 分布上尾 P(T > t)。"""
    if df <= 0:
        return float("nan")
    tail = 0.5 * betainc(df / 2.0, 0.5, df / (df + t * t))
    return tail if t >= 0 else 1.0 - tail


def f_sf(x: float, dfn: int, dfd: float) -> float:
    """F 分布上尾 P(F_{dfn,dfd} > x) = I_{dfd/(dfd+dfn·x)}(dfd/2, dfn/2)。

    **联合检验的分母自由度要从聚类数来。**Wald 统计量按 χ²(m) 判，前提是协方差已知；
    实际上它是估出来的，用 G 个聚类估 m×m 个元素，m/G 一大就近奇异、求逆把统计量推大。
    Hotelling T² 的有限样本校正 `W·(G−m)/(m·(G−1)) ~ F(m, G−m)` 正是为这件事准备的，
    G → ∞ 时退回 χ²(m)/m（`f_sf` 的分母自由度趋大时与 `chi2_sf` 重合，有测试盯着）。
    """
    if dfn <= 0 or dfd <= 0:
        return float("nan")
    if x <= 0:
        return 1.0
    return betainc(dfd / 2.0, dfn / 2.0, dfd / (dfd + dfn * x))


def f_ppf(q: float, dfn: int, dfd: float) -> float:
    """F 分布的下尾分位（二分反解 `f_sf`，跟 `chi2_ppf` 同一写法）。

    **预检功效拿它当门槛**：功效说的必须是检验真正用的那把尺子，不然报出来的比实际能查出来的多。
    """
    if not 0.0 < q < 1.0:
        raise ValueError("q 要在 (0, 1) 里")
    if dfn <= 0 or not (math.isfinite(dfd) and dfd > 0):
        raise ValueError(f"F 分布的自由度要是正的有限数，拿到的是 ({dfn}, {dfd})")
    target = 1.0 - q
    lo, hi = 0.0, 1.0
    while f_sf(hi, dfn, dfd) > target:
        hi *= 2.0
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if f_sf(mid, dfn, dfd) > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-12 * max(1.0, hi):
            break
    return 0.5 * (lo + hi)


def t_ppf(q: float, df: float) -> float:
    """t 分布的下尾分位（二分）：给按设计的区间取临界值。

    自由度要是正的有限数，否则报 `ValueError`——原先 0、负数、NaN、无穷都悄悄返回约 3e-61，
    拿去当临界值就是一个零宽的区间。
    """
    if not 0.0 < q < 1.0:
        raise ValueError("q 要在 (0, 1) 里")
    if not (math.isfinite(df) and df > 0):
        raise ValueError(f"t 分布的自由度要是正的有限数，拿到的是 {df}")
    if q < 0.5:
        return -t_ppf(1.0 - q, df)
    lo, hi = 0.0, 1.0
    while t_sf(hi, df) > 1.0 - q:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_sf(mid, df) > 1.0 - q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@functools.lru_cache(maxsize=None)
def mde_multiplier(dof: int = 0) -> float:
    """最小可探测效应的倍数（80% 功效、5% 双侧）：MDE = 倍数 × 标准误。

    `dof` ≤ 0 按正态取 2.80（老结果逐位不变）；按抽样设计时用 t 分布：`t(0.975, dof) + t(0.80, dof)`——
    自由度 30 约 2.90，自由度 9 约 3.15。抽样单位只有几十个时照正态算，会把能分辨的最小差异说小。
    `Estimate` 每次校验都要算一遍（读结果文件也算），所以缓存。
    """
    if dof <= 0:
        return 2.80
    return t_ppf(0.975, dof) + t_ppf(0.80, dof)
