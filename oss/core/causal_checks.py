"""因果骨架的机器诊断（设计稿第四、五节）。**门槛初值集中在这一处**，校准后改这里。"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from oss.core import causal_stats as cs
from oss.core.contracts import EventStudyPoint, OlsFit, PretrendCov
from oss.core.findings import Finding

#: 灰区初值（设计稿第五节）。**都是初值**：Task 13 在考卷与留档结果上校准，拿着误拦、漏拦的数请用户定
EFFECTIVE_F_LOW = 10.0             # 有效 F 低于它：主推断改用 AR
PRETREND_POWER_BAND = (0.5, 0.8)   # 预检功效：低于下界不能当证据，中间是灰区
BREAKDOWN_LOW = 1.0                # breakdown 值低于它：写进局限
NEAR_THRESHOLD_P = (0.01, 0.10)    # IV 与双重差分族主结果的 p 落在这里：加一轮检查
IV_OLS_RATIO = 2.0                 # |2SLS / OLS| 超过它：写进局限（设计稿写「校准时定」，先给 2.0）
ALPHA = 0.05


def lead_term(k: int) -> str:
    """`run_event_study` 给第 k 期哑变量起的名字（跟那边同一规则）。"""
    return f"_k{'m' if k < 0 else 'p'}{abs(k)}"


def pretrend_cov(fit: OlsFit | None, ks: list[int]) -> PretrendCov | None:
    """从拟合信息里取处理前各期系数的协方差子矩阵。有一期没估出来（被吸收）就返回 None——**不拼半个矩阵**。"""
    if fit is None or not ks:
        return None
    pos = {t: i for i, t in enumerate(fit.terms)}
    names = [lead_term(k) for k in ks]
    if any(n not in pos for n in names):
        return None
    idx = [pos[n] for n in names]
    V = np.asarray(fit.vcov, dtype=float)[np.ix_(idx, idx)]
    return PretrendCov(ks=list(ks), vcov=V.tolist(), n_clusters=fit.n_clusters)


def joint_pretest(points: list[EventStudyPoint], cov: PretrendCov) -> tuple[float, float, int]:
    """处理前系数的联合 Wald 检验。返回 `(W, p, 自由度)`。

    **按 χ²(m) 判会大量误拒。**协方差不是已知的，是拿 G 个聚类估出来的；m 个矩条件配
    G 个聚类，m/G 一大协方差就近奇异，求逆把统计量推大。2026-09-17 的尺寸模拟：
    120 个单位、5 批的面板上，逐（批 × 期）格子的卡方 Wald 在 5% 水平上错拒 15%–41.5%
    （事前期 4 到 10），而说明书上该是 5%。

    所以知道聚类数时走 Hotelling T² 的有限样本校正 `W·(G−m)/(m·(G−1)) ~ F(m, G−m)`——
    G → ∞ 时它退回 χ²(m)。不知道（老结果文件）才退回卡方，**退回不等于对**。
    """
    by_k = {p.k: p.coef for p in points}
    b = np.array([by_k[k] for k in cov.ks], dtype=float)
    W = cs.wald(b, np.asarray(cov.vcov, dtype=float))
    df = len(cov.ks)
    G = cov.n_clusters
    if df and G > df + 1:
        return W, cs.f_sf(W * (G - df) / (df * (G - 1)), df, G - df), df
    return W, cs.chi2_sf(W, df), df


def joint_pretest_crit(m: int, n_clusters: int, alpha: float = ALPHA) -> float:
    """联合检验的临界值，**折算到 Wald 统计量的尺度上**（跟 `joint_pretest` 同一条分支）。

    预检功效要拿它当门槛：说功效的那把尺子得是检验真正用的那把，
    否则「有 80% 的把握查出来」说的是另一个检验，比实际用的松。
    """
    if m and n_clusters > m + 1:
        return cs.f_ppf(1 - alpha, m, n_clusters - m) * m * (n_clusters - 1) / (n_clusters - m)
    return cs.chi2_ppf(1 - alpha, m)


def joint_pretest_label(df: int, n_clusters: int) -> str:
    """联合检验那半句括号。**逐年展开与分批两条路共用**，免得两处各说各的。"""
    if df and n_clusters > df + 1:
        return f"Wald，自由度 {df}；{n_clusters} 个聚类，小样本按 F 校正"
    return f"Wald，自由度 {df}"


_Z975 = 1.959964


def linear_violation(ks: list[int], theta: float, lags: int) -> np.ndarray:
    """「大到足以抵消估计效应」的线性违背：δ_k = γ·(k+1)，在参照期 −1 为零。

    γ 取到处理后第 0…lags 期的平均偏误正好等于 |θ̂|——Roth 2023：常规检验只有一半把握查出的
    线性违背，造成的偏误常与估计效应一样大甚至更大。
    """
    gamma = abs(theta) / ((lags + 2) / 2.0)
    return np.array([gamma * (k + 1) for k in ks], dtype=float)


def pretrend_power(cov: PretrendCov, delta: np.ndarray, alpha: float = ALPHA) -> float:
    """联合 Wald 预检对违背 δ 的功效：P(χ²_m(λ) > c)，λ = δ'V⁻¹δ。

    `c` 是 `joint_pretest_crit`——**检验真正用的那个临界值**，知道聚类数时是小样本校正过的那个
    （比 χ²_{m,1−α} 高）。拿 χ² 的临界值算会把功效说高，而这个数正是用来判「预检没拒绝算不算证据」的。
    """
    m = len(cov.ks)
    lam = cs.wald(delta, np.asarray(cov.vcov, dtype=float))
    return cs.ncx2_sf(joint_pretest_crit(m, cov.n_clusters, alpha), m, lam)


def breakdown_rm(theta: float, se: float, pre_coefs: list[float], z: float = _Z975) -> float | None:
    """breakdown 值：Rambachan–Roth「相对大小」限制下的最坏偏误近似。

    区间 `θ̂ ± M̄·max|β_pre| ± z·SE` 刚好碰到 0 时的 M̄。StatsPAI 对这种限制的原生算法就是这个近似
    （设计稿第十一节第 1 件；本计划「跟设计稿的出入」第 1 条）。处理前系数全为 0 时返回 None（无穷）。
    """
    D = max((abs(b) for b in pre_coefs), default=0.0)
    if D == 0:
        return None
    return max(0.0, abs(theta) - z * se) / D


def pretrend_texts(points: list[EventStudyPoint], cov: PretrendCov | None,
                   theta: float, se: float, lags: int) -> dict[str, str]:
    """预检功效与 breakdown 值的四条诊断。数值键只放数，说明键放整句。"""
    out: dict[str, str] = {}
    b = breakdown_rm(theta, se, [p.coef for p in points if p.k < 0])
    if abs(theta) <= _Z975 * se:
        # 不显著时这个数按定义是 0，写 0.00 会被读成「很脆」（任务 13 校准；用户 2026-09-14 定）
        out["breakdown 值"] = "不适用"
        out["breakdown 说明"] = ("主结果的 95% 区间本来就含 0（不显著）：breakdown 问的是“违背要多大才推翻显著的结论”，"
                               "这里没有显著的结论可推翻，不适用")
    elif b is None:
        out["breakdown 值"] = "∞"
        out["breakdown 说明"] = "处理前各期系数全为 0：违背多大都推不翻（相对大小限制下的近似）"
    else:
        out["breakdown 值"] = f"{b:.2f}"
        out["breakdown 说明"] = (
            f"处理后的违背要比处理前最大的违背大 {b:.2f} 倍，结论才会被推翻"
            f"（Rambachan–Roth「相对大小」限制下的最坏偏误近似）。"
            f"低于 {BREAKDOWN_LOW:g} 说明处理后的违背不比处理前最大的大，结论就翻")
    if cov is None or not cov.ks:
        out["预检功效"] = "没算"
        out["预检功效说明"] = "没有处理前各期系数的协方差（老结果文件，或有一期被吸收）——**这不等于功效够**"
        return out
    pw = pretrend_power(cov, linear_violation(cov.ks, theta, lags))
    lo, hi = PRETREND_POWER_BAND
    grade = "够" if pw >= hi else ("灰区" if pw >= lo else "不足")
    out["预检功效"] = f"{pw:.2f}"
    out["预检功效说明"] = (
        f"对「大到足以抵消估计效应」的线性违背，联合预检有 {pw:.0%} 的把握查出来（{grade}；"
        f"灰区 {lo:g}–{hi:g}）。**功效不足时，预检没拒绝不能当平行趋势的证据**（Roth 2023）")
    return out


# ══════════════════════════════ 工具变量：有效 F、AR 区间、tF 区间、OLS 对照（设计稿 4.1）


def _scores(W: np.ndarray, u: np.ndarray, clusters: np.ndarray | None) -> np.ndarray:
    """得分 W'u：没有聚类时一行一个观测，有聚类时一行一个簇。"""
    S = W * u[:, None]
    if clusters is None:
        return S
    codes = pd.Categorical(clusters).codes
    out = np.zeros((codes.max() + 1, W.shape[1]))
    np.add.at(out, codes, S)
    return out


def _correction(n: int, k: int, k_absorbed: int, clusters: np.ndarray | None) -> float:
    """小样本校正，跟 `analysis.py` 同一口径：HC1 是 n/(n−k)，聚类是 G/(G−1)·(n−1)/(n−k)。"""
    if clusters is None:
        return n / max(n - k - k_absorbed, 1)
    G = len(np.unique(clusters))
    return (G / max(G - 1, 1)) * ((n - 1) / max(n - k - k_absorbed, 1))


def _aux(Z: np.ndarray, exog: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """辅助回归的设计矩阵 [Z, 外生] 与它的 (W'W)⁻¹。"""
    W = np.column_stack([Z, exog]) if exog.size else Z
    return W, np.linalg.inv(W.T @ W)


def effective_f(x: np.ndarray, Z: np.ndarray, exog: np.ndarray,
                clusters: np.ndarray | None, k_absorbed: int) -> float:
    """Olea–Pflueger 有效 F（一个内生变量）：F_eff = π̂'Z̃'Z̃π̂ / tr(V̂_π Z̃'Z̃)。

    Z̃ 是拿掉外生变量之后的工具变量，V̂_π 是第一阶段工具系数的稳健方差（跟主回归同一口径）。
    同方差时退化为普通第一阶段 F；只有一个工具时等于稳健的 Wald 统计量。
    """
    W, A = _aux(Z, exog)
    coef = A @ W.T @ x
    u = x - W @ coef
    S = _scores(W, u, clusters)
    V = _correction(len(x), W.shape[1], k_absorbed, clusters) * (A @ (S.T @ S) @ A)
    L = Z.shape[1]
    pi, Vpi = coef[:L], V[:L, :L]
    Zt = Z - exog @ np.linalg.lstsq(exog, Z, rcond=None)[0] if exog.size else Z
    Q = Zt.T @ Zt
    return float(pi @ Q @ pi / np.trace(Vpi @ Q))


def anderson_rubin(y: np.ndarray, x: np.ndarray, Z: np.ndarray, exog: np.ndarray,
                   clusters: np.ndarray | None, k_absorbed: int, beta_hat: float, se_hat: float,
                   *, alpha: float = ALPHA, width: float = 20.0, points: int = 4001):
    """AR 检验取反得到的置信集：{β₀ : AR(β₀) ≤ χ²_{L,1−α}}，方差跟主回归同一口径。

    ỹ(β₀) = y − β₀x 对 [Z, 外生] 回归、检验 Z 的系数全为 0。系数与得分都是 β₀ 的线性函数，
    所以「肉」是 β₀ 的二次式：先算好三块，网格上逐点只做小矩阵运算。
    返回 `(下界, 上界, 状态, β₀ = 0 时的 p)`；状态是「有界」「无界」（碰到网格边）「不连通」「空」。
    """
    W, A = _aux(Z, exog)
    gy, gx = A @ W.T @ y, A @ W.T @ x
    Sy, Sx = _scores(W, y - W @ gy, clusters), _scores(W, x - W @ gx, clusters)
    c = _correction(len(y), W.shape[1], k_absorbed, clusters)
    L = Z.shape[1]
    AL = A[:L, :]
    Myy, Mxx, Mxy = Sy.T @ Sy, Sx.T @ Sx, Sx.T @ Sy
    crit = cs.chi2_ppf(1 - alpha, L)

    def ar(b0: float) -> float:
        g = gy[:L] - b0 * gx[:L]
        meat = Myy - b0 * (Mxy + Mxy.T) + b0 * b0 * Mxx
        return cs.wald(g, c * (AL @ meat @ AL.T))

    grid = np.linspace(beta_hat - width * se_hat, beta_hat + width * se_hat, points)
    ok = np.array([ar(b) <= crit for b in grid])
    p0 = cs.chi2_sf(ar(0.0), L)
    if not ok.any():
        return None, None, "空", p0
    first, last = int(np.argmax(ok)), len(ok) - 1 - int(np.argmax(ok[::-1]))
    if ok[0] or ok[-1]:
        status = "无界"
    elif not ok[first:last + 1].all():
        status = "不连通"
    else:
        status = "有界"
    return float(grid[first]), float(grid[last]), status, p0


def tf_interval(beta: float, se: float, f_eff: float):
    """tF 调整后的区间。临界值表来自原文，本仓库不收（原文不在阶段 1 的地图里），走 StatsPAI；没装返回 None。"""
    try:
        import statspai as sp
    except ImportError:
        return None
    crit = float(sp.tF_adjustment(f_eff))          # Step 0 核过：返回的是 t 检验的临界值
    return beta - crit * se, beta + crit * se, crit


def iv_diagnostics(y, x, Z, exog, clusters, k_absorbed, beta, se, ols_coef, ols_se) -> dict[str, str]:
    """工具变量的六条诊断（设计稿 4.1）。数值键只放数。"""
    out: dict[str, str] = {}
    F = effective_f(x, Z, exog, clusters, k_absorbed)
    out["有效 F"] = f"{F:.2f}"
    lo, hi, status, p0 = anderson_rubin(y, x, Z, exog, clusters, k_absorbed, beta, se)
    out["AR 区间"] = f"[{lo:.3f}, {hi:.3f}]" if status == "有界" else status
    out["AR 说明"] = (f"Anderson–Rubin 置信集（95%，对弱工具稳健）：{status}"
                     + (f" [{lo:.3f}, {hi:.3f}]" if lo is not None else "")
                     + f"；检验「效应为 0」的 p = {p0:.3f}")
    tf = tf_interval(beta, se, F)
    if tf is None:
        out["tF 区间"] = "没算（tF 的临界值表来自 StatsPAI，没装）"
    else:
        out["tF 区间"] = f"[{tf[0]:.3f}, {tf[1]:.3f}]"
    ratio = beta / ols_coef if ols_coef else float("inf")
    out["2SLS/OLS"] = f"{ratio:.2f}"
    # **有没有固定效应要照实说。**原先这句无条件写「同样控制与固定效应」，
    # 而主规格没有固定效应时（`k_absorbed == 0`）这是不实的——读者会以为
    # OLS 对照带了固定效应而 2SLS 没带。真跑时论文审稿人逐字点了这一条。
    same = "同样控制与固定效应" if k_absorbed else "同样控制变量（主规格无固定效应）"
    out["OLS 对照"] = (f"同一样本、{same}的 OLS：{ols_coef:+.3f}（标准误 {ols_se:.3f}）；"
                      f"2SLS {beta:+.3f}；比值 {ratio:.2f}。观察研究里 2SLS 远大于 OLS 是违背无混杂或排除限制的信号（Lal 等 2024）")
    conv_sig = abs(beta) > 1.959964 * se
    if F < EFFECTIVE_F_LOW:
        out["第一阶段强度"] = (
            f"**太弱，不可用常规推断**：有效 F = {F:.2f}，低于 {EFFECTIVE_F_LOW:g}。借力变量与处理几乎无关时，"
            f"估计会剧烈失真，**偏误可能比直接回归还大**——而它不会报错。主推断改用 AR 区间；"
            f"或者换借力变量、放弃这个设计")
    elif tf is not None and conv_sig != (tf[0] > 0 or tf[1] < 0):
        out["第一阶段强度"] = (f"有效 F = {F:.2f}，过了下界 {EFFECTIVE_F_LOW:g}，但**落在灰区**：常规区间与 tF 区间"
                             f"对「效应是否为 0」的结论不一致——交识别审稿人判，局限里写明")
    else:
        out["第一阶段强度"] = f"有效 F = {F:.2f}，**够强**（按异方差与聚类结构算的 Olea–Pflueger 有效 F，下界 {EFFECTIVE_F_LOW:g}）"
    return out


# ══════════════════════════════ 选择可观测：稳健值、Oster δ、E 值（设计稿 4.3）


def partial_r2(t: float, dof: int) -> float:
    """偏 R²：t²/(t² + 自由度)。这条恒等式只在**同方差经典** t 下成立（Cinelli–Hazlett 2020）。"""
    return t * t / (t * t + dof)


def robustness_value(t: float, dof: int, *, q: float = 1.0, alpha: float | None = None) -> float:
    """Cinelli–Hazlett 稳健值：未观测混淆要同时解释处理与结果残差方差的多大比例，才能把效应削减 q。

    `t` 必须是同方差经典 t（`OlsFit.vcov_iid` 算出来的那个），不是稳健／聚类 t——下面的闭式解靠
    `partial_r2` 那条恒等式，只在经典 t 下成立。

    q = 1 是削到 0；给了 alpha 是削到不显著。跟 StatsPAI `sensemakr` 内部的 `_robustness_value`
    对过（Step 0）：`alpha=None` 时（这里对应它 `alpha=1`）临界值恒为 0，两边化简到同一个闭式，
    数值上完全相等；`alpha` 给了值时（对应它的 `rv_qa`），StatsPAI 用 t 分布 `t.ppf` 的临界值，
    这里用正态分位数做大样本近似（自由度不大时会跟它有小偏差）——这条只当参照，不当门。
    """
    f = q * abs(t) / math.sqrt(dof)
    if alpha is not None:
        f -= NormalDist().inv_cdf(1 - alpha / 2) / math.sqrt(max(dof - 1, 1))
    if f <= 0:
        return 0.0
    return 0.5 * (math.sqrt(f ** 4 + 4 * f ** 2) - f ** 2)


def oster(beta_short: float, r2_short: float, beta_long: float, r2_long: float,
          r_max: float | None = None) -> tuple[float | None, float, float]:
    """Oster 2019 的系数稳定性：返回 `(使效应为 0 的 δ, δ = 1 时的偏误调整系数, 用的 R_max)`。

    照原文 §3.2 的近似式与命题 1：β* ≈ β̃ − δ·(β̊ − β̃)·(R_max − R̃)/(R̃ − R̊)（β̊ = beta_short 不带控制，
    β̃ = beta_long 带控制），于是让 β* = 0 的 δ* = β̃·(R̃ − R̊) / ((β̊ − β̃)·(R_max − R̃))。
    直觉：控制变量每解释一份 R² 把系数挪了多少，剩给未观测变量的那份 R²（R_max − R̃）就按同一比例再挪。
    **StatsPAI `oster_bounds` 把这个比值写倒了**（它用 (R̃ − R̊)/(R_max − R̃)，剩余空间趋于 0 时调整量反而趋于无穷）——
    这里照原文，这一项不跟它对（2026-09-14 回 Oster 2019 正式发表版原文核）。

    R_max 缺省取 min(1, 1.3·R²_long)：Oster (2019) 拿一批随机实验论文校准，找一个比例 Π 让至少
    90% 的随机实验结果在置信区间与拒绝零假设两个检验上都算「稳健」，校出来 Π = 1.3
    （docs/research/related-work.json 条目 `oster2019-coefficient-stability`，引句核过）。
    剩给未观测变量的空间为 0（R_max ≤ R̃）时不调整，β* 就是 β̃；没有剩余空间、或系数不随控制变量移动时
    δ* 无穷，返回 None（`sensitivity_texts` 拿它写「∞」）。
    """
    r_max = min(1.0, 1.3 * r2_long) if r_max is None else r_max
    move_b, move_r, gap = beta_short - beta_long, r2_long - r2_short, max(r_max - r2_long, 0.0)
    if abs(move_r) < 1e-12:
        return None, beta_long, r_max
    beta_star = beta_long - move_b * gap / move_r
    if abs(move_b) < 1e-12 or gap < 1e-12:
        return None, beta_star, r_max
    return beta_long * move_r / (move_b * gap), beta_star, r_max


def evalue_ols(beta: float, se: float, sd: float) -> tuple[float, float]:
    """E 值：点估计与区间靠零一端各一个。连续结局按 RR ≈ exp(0.91·d) 换算（**换算出处待核**，Step 7）。

    E 值用报出来的估计量与标准误——不改用经典标准误：这是论文里报的那个数，
    区间那一端的 E 值问的正是「按论文报的这个区间，还留多大空间」。
    跟 StatsPAI `evalue(measure="OLS", sd=...)` 对过（Step 0）：点估计公式完全一致；
    区间那一端 StatsPAI 用四舍五入过的常数 `1.78 ≈ 0.91×1.96` 走一步算，这里分两步算（先除以 sd
    得到 d 再乘 0.91），数值上差在 1e-3 量级，不影响读数。
    """
    def e_of(d: float) -> float:
        rr = math.exp(0.91 * abs(d))
        return rr + math.sqrt(rr * (rr - 1))

    if sd <= 0:
        return float("nan"), float("nan")
    d = beta / sd
    lim = (abs(beta) - _Z975 * se) / sd
    return e_of(d), (e_of(lim) if lim > 0 else 1.0)


def sensitivity_texts(fit: OlsFit, main_coef: float, main_se: float, treatment: str,
                      short_fit: OlsFit, aux_fit: OlsFit | None, controls: list[str], sd_y: float) -> dict[str, str]:
    """选择可观测设计的敏感性诊断（设计稿 4.3）。**不自动降级**：稳健值小不等于证明了有混淆。

    稳健值与偏 R² 靠 `R²_{Y~D|X} = t²/(t²+自由度)` 这条恒等式，**只在同方差经典 OLS t 下成立**
    （sensemakr 自己内部就是拿经典 σ̂²·(X'X)⁻¹ 算的这个 t，Step 0 核过）。主回归报的是 HC1／聚类
    稳健标准误，那不是同一个 t——所以这里凡是喂进 `robustness_value` / `partial_r2` 的 t，
    全部从 `fit.vcov_iid`（以及 `aux_fit.vcov_iid`）算，不用 `main_se`。E 值与 Oster 仍然用报出来
    的估计量；E 值区间那一端仍然用报出来的（稳健）标准误——那才是论文里报的标准误，问的是
    「按论文报的这个区间，还留多大空间」，跟稳健值问的不是同一件事。
    """
    out: dict[str, str] = {}
    i_t = fit.terms.index(treatment)
    se_iid_t = math.sqrt(fit.vcov_iid[i_t][i_t])
    t = main_coef / se_iid_t
    rv, rva = robustness_value(t, fit.dof), robustness_value(t, fit.dof, alpha=ALPHA)
    # 「削到不显著要多强的混淆」只对显著的主结果有意义：不显著时按定义是 0，写不适用（任务 13 校准；
    # 用户 2026-09-14 定）。显不显著看论文报的（稳健）标准误，跟 breakdown 同一个口径
    sig = abs(main_coef) > _Z975 * main_se
    rva_txt = f"{rva:.3f}" if sig else "不适用——主结果本来就不显著"
    out["稳健值"], out["稳健值（显著性）"] = f"{rv:.3f}", (f"{rva:.3f}" if sig else "不适用")
    bench, who = 0.0, ""
    if aux_fit is not None:
        for c in controls:
            if c in fit.terms and c in aux_fit.terms:
                i, j = fit.terms.index(c), aux_fit.terms.index(c)
                ty = fit.beta[i] / math.sqrt(fit.vcov_iid[i][i])
                td = aux_fit.beta[j] / math.sqrt(aux_fit.vcov_iid[j][j])
                b = min(partial_r2(ty, fit.dof), partial_r2(td, aux_fit.dof))
                if b > bench:
                    bench, who = b, c
    out["敏感性基准值"] = f"{bench:.3f}"
    beta_short = short_fit.beta[short_fit.terms.index(treatment)]
    delta0, beta_star, r_max = oster(beta_short, short_fit.r2, main_coef, fit.r2)
    out["Oster δ"] = "∞" if delta0 is None else f"{delta0:.2f}"
    e_pt, e_ci = evalue_ols(main_coef, main_se, sd_y)
    out["E 值"] = f"{e_pt:.2f}"
    if not sig:
        verdict = "主结果本来就不显著，谈不上“削到不显著”，这个基准只作参照"
    elif rva < bench:
        verdict = "**偏弱**：一个跟它一样强的混淆就够把结论削到不显著"
    else:
        verdict = "稳健值高于这个基准"
    # δ 为负只印一个负数，读者会误读成「很弱」——说清方向（任务 13 校准，cgss_like 查出）
    sign = (f"；δ 为负：加进控制变量后系数离 0 更远，要把效应拉到 0，未观测混淆得跟已观测的反方向起作用、强 {-delta0:.2f} 倍"
            if delta0 is not None and delta0 < 0 else "")
    out["敏感性分析"] = (
        f"稳健值 {rv:.3f}（削到不显著：{rva_txt}；用同方差经典标准误算的 t——"
        f"这两个数跟主回归报的稳健／聚类标准误不是一回事，公式本身只在经典 t 下成立）："
        f"未观测混淆要同时解释处理与结果残差方差的这么大比例才能推翻结论"
        f"（Cinelli–Hazlett 2020）。最强的已观测控制变量「{who or '无'}」的基准是 {bench:.3f}"
        f"——{verdict}。"
        f"Oster δ = {out['Oster δ']}（R_max = {r_max:.3f}，δ = 1 时偏误调整后的系数 {beta_star:+.3f}{sign}；"
        f"带固定效应时这里的 R² 是组内 R²，不是总 R²；R_max 取值：Oster (2019) 用随机实验数据校准的 1.3 倍）。"
        f"E 值 {e_pt:.2f}（区间一端 {e_ci:.2f}，用论文报的（稳健）标准误；连续结局近似换算，出处待核）。"
        f"**这些数只说明「要多强的混淆才推翻」，不说明没有混淆**")
    return out


# ══════════════════════════════ 论文检查的硬门与合规规则（设计稿 4.1、4.3）


_SENS_KEYS = ("稳健值", "Oster δ", "E 值")


def _weak_iv_f(diagnostics: dict[str, str]) -> float | None:
    """有效 F 是有限数、且低于下界时返回它；否则 None——`nan`／`inf` 那类不是「测出来弱」，
    `float()` 转得过去但不该开门。"""
    try:
        F = float(diagnostics.get("有效 F", ""))
    except ValueError:
        return None
    return F if math.isfinite(F) and F < EFFECTIVE_F_LOW else None


def paper_problems(diagnostics: dict[str, str], paper: str) -> list[str]:
    """论文检查与导出的硬门（`export` 主路径查的是这一份，源稿 `paper.md` 里占位符还没换成
    实际值）：有效 F 低于下界时，正文主结果的区间必须引 AR 区间（设计稿 4.1）。"""
    F = _weak_iv_f(diagnostics)
    if F is not None and "{{diag:AR 区间}}" not in paper:
        return [Finding(f"有效 F = {F:.2f}，低于 {EFFECTIVE_F_LOW:g}：弱工具下常规的 2SLS 区间不可信，"
                f"正文主结果的区间要引 {{{{diag:AR 区间}}}}（Anderson–Rubin，对弱工具稳健）", "iv.ar_missing")]
    return []


def paper_problems_rendered(diagnostics: dict[str, str], rendered: str) -> list[str]:
    """`export` 兜底路径（没有语料时转的是 `paper_rendered.md`）查同一道硬门：这份稿子
    占位符早换成了实际值，`{{diag:AR 区间}}` 这个占位符字面串已经不在文本里了——
    查的是 AR 区间的**值**字符串在不在渲染稿里；这个值没算出来（空串）也算没引。"""
    F = _weak_iv_f(diagnostics)
    if F is None:
        return []
    val = diagnostics.get("AR 区间", "")
    if not val or val not in rendered:
        return [Finding(f"有效 F = {F:.2f}，低于 {EFFECTIVE_F_LOW:g}：弱工具下常规的 2SLS 区间不可信，"
                f"渲染稿里没有出现 AR 区间的值（{val or '没算'}）——正文主结果的区间要引 AR 区间"
                f"（Anderson–Rubin，对弱工具稳健）", "iv.ar_missing")]
    return []


def paper_warnings(diagnostics: dict[str, str], paper: str, *, causal_ols: bool) -> list[str]:
    """因果类 ols_fe 没引敏感性数字：**只警告**（用户定的第十一节第 3 件，硬门只有四样）。"""
    if causal_ols and "稳健值" in diagnostics and not any(f"{{{{diag:{k}}}}}" in paper for k in _SENS_KEYS):
        return [Finding("因果类 ols_fe 的论文没引敏感性数字（{{diag:稳健值}}、{{diag:Oster δ}}、{{diag:E 值}} 至少一个）——"
                "读者要知道「多强的未观测混淆才推翻结论」；合规检查判 should 级不过", "causal.sensitivity_uncited")]
    return []
