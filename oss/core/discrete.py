"""二值与有序 Logit。**手写牛顿法，不加依赖。**

这个仓库的 OLS、组内去均值、夹心估计量全是 numpy 手写的（`analysis.py`），
logit 的牛顿法四十行就够，路子跟已有的一致。scipy 虽然装着，但 `pyproject.toml`
里一个字都没声明——它是 `causal` 那个 extra（statspai）顺带带进来的，
建在它上面等于「只有装了分批双重差分才能跑 logit」。

**为什么补这两个**（设计稿 `docs/design/2026-09-25-logit-multilevel.md`）：
中文社科的因变量大量是 1–5 分的态度量表（公平感、幸福感、阶层认同），
标准做法是有序 Logit。找复现论文时筛了九篇，**三篇都用了我们跑不了的估计量**。

**这里只管算，不管门。**门在 `analysis.py`（固定效应太多就拒、平行线假定要验），
判断「该不该用 logit」在说明书。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: 牛顿法的收敛判据与轮数上限。对数似然的相对变化小于它就停。
#: 二值 logit 的对数似然是凹的，**一定收敛**；轮数给 60 是为了病态设计矩阵那种慢的情形
_TOL, _MAX_ITER = 1e-10, 60
#: 概率截断。完全分离（perfect separation）时 p 会跑到 0 或 1，
#: 权重 p(1−p) 变成 0，海森矩阵奇异。截断让它退化成一个大系数而不是崩掉，
#: **而且完全分离本身要报出来**——那是数据的事实，不是数值问题
_EPS = 1e-12


class Separated(RuntimeError):
    """完全分离。**单列出来，不混进「不收敛」**——
    两者的处置完全不同：分离要去查那一列是不是混进了结局本身，
    不收敛要去查共线。报成同一种，人会照着错的方向查。"""

    def __init__(self, cols: list[int], msg: str) -> None:
        super().__init__(msg)
        self.cols = cols


class NotConverged(RuntimeError):
    """牛顿法没收敛。**如实抛，不返回最后一轮的值**——
    不收敛的估计值看起来跟收敛的一模一样，交出去没人分得出。"""


@dataclass
class LogitFit:
    beta: np.ndarray                       #: 斜率（不含截距的那部分见 `has_const`）
    vcov: np.ndarray
    llf: float                             #: 对数似然
    n: int
    n_iter: int
    cuts: np.ndarray = field(default_factory=lambda: np.zeros(0))   #: 有序 logit 的切点
    separated: list[int] = field(default_factory=list)              #: 完全分离的列号

    @property
    def se(self) -> np.ndarray:
        return np.sqrt(np.clip(np.diag(self.vcov), 0.0, None))


def _logistic(z: np.ndarray) -> np.ndarray:
    """1/(1+exp(−z))，两支写法。

    直接写会在 z 很负时让 `exp(-z)` 溢出——**有序 logit 上一定会撞到**：
    切点减去线性预测值，尾部那些观测的 |z| 轻易到几十。
    溢出的结果碰巧是对的（inf → 概率 0），但它是个警告，
    **而「跑出来有警告但结果碰巧对」正是最容易被放过去的那种毛病**。
    """
    out = np.empty_like(z, dtype=float)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    ez = np.exp(z[~pos])
    out[~pos] = ez / (1.0 + ez)
    return out


def _p(X: np.ndarray, beta: np.ndarray) -> np.ndarray:
    """logistic(Xβ)，按数值稳定的写法算。

    直接 `1/(1+exp(-z))` 在 z 很负时 `exp(-z)` 溢出。分两支写，两边都不溢出。
    """
    return _logistic(X @ beta)


def _separated(X: np.ndarray, y: np.ndarray) -> list[int]:
    """完全分离的列：某一列上，y=1 与 y=0 的取值范围**完全不重叠**。

    这种列的系数在理论上是无穷大，估出来是一个由截断决定的大数——
    **看起来像个正常的强效应**，而它其实是「这一列完美预测了结局」。
    不报出来，读者会把它当成一个了不起的发现。
    """
    out = []
    for j in range(X.shape[1]):
        a, b = X[y == 1, j], X[y == 0, j]
        if a.size == 0 or b.size == 0:
            continue
        if a.min() > b.max() or b.min() > a.max():
            out.append(j)
    return out


def binary_logit(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> LogitFit:
    """加权二值 logit，牛顿-拉弗森。

    似然是 Σ wᵢ[yᵢ log pᵢ + (1−yᵢ) log(1−pᵢ)]，梯度 X'W(y−p)，海森 −X'diag(Wp(1−p))X。
    权重直接进似然——**抽样权重在最大似然里就是这么进的**，跟 WLS 把数据乘 √w 不是一回事。

    `vcov` 这里给的是**模型方差**（海森的逆）。聚类稳健与按设计的方差在
    `analysis.py` 里用 `score_matrix()` 另算——夹心的面包是这里的逆海森，肉在那边。
    """
    n, k = X.shape
    w = np.ones(n) if w is None else np.asarray(w, dtype=float)
    # **完全分离要在开跑之前查**，不能等牛顿法发散。
    # 那种列的系数理论上是无穷大，迭代会一路跑到不收敛，抛出来的话是
    #「设计矩阵病态」——**跟真正的原因差着十万八千里**，人照着它去查共线性会白查。
    sep = _separated(X, y)
    beta = np.zeros(k)
    llf_old = -np.inf
    for it in range(1, _MAX_ITER + 1):
        p = np.clip(_p(X, beta), _EPS, 1 - _EPS)
        grad = X.T @ (w * (y - p))
        hess = -(X.T * (w * p * (1 - p))) @ X
        try:
            step = np.linalg.solve(hess, grad)
        except np.linalg.LinAlgError as exc:
            raise NotConverged("海森矩阵奇异——多半是完全共线或完全分离") from exc
        beta = beta - step
        llf = float(np.sum(w * (y * np.log(p) + (1 - y) * np.log1p(-p))))
        if abs(llf - llf_old) < _TOL * (abs(llf_old) + _TOL):
            p = np.clip(_p(X, beta), _EPS, 1 - _EPS)
            hess = -(X.T * (w * p * (1 - p))) @ X
            return LogitFit(beta=beta, vcov=np.linalg.pinv(-hess), llf=llf, n=n,
                            n_iter=it, separated=sep)
        llf_old = llf
    if sep:
        raise Separated(sep, f"第 {sep} 列完全分离：这几列的取值把 y 完全分开了，"
                             f"系数在理论上无穷大。**这不是数值问题，是数据的事实**——"
                             f"要么这几列里混进了结局本身，要么样本里这个组合只有一种结局")
    raise NotConverged(f"{_MAX_ITER} 轮还没收敛——设计矩阵多半病态（共线）")


def score_matrix(X: np.ndarray, y: np.ndarray, beta: np.ndarray,
                 w: np.ndarray | None = None) -> np.ndarray:
    """每个观测对每个参数的得分 wᵢ(yᵢ−pᵢ)xᵢ。

    夹心方差的「肉」按它算：把同一簇的行加起来再取外积。
    跟 OLS 那边 `X_g' u_g` 是同一个东西——**残差换成了得分**。
    """
    n = X.shape[0]
    w = np.ones(n) if w is None else np.asarray(w, dtype=float)
    return (w * (y - _p(X, beta)))[:, None] * X


def ame(X: np.ndarray, beta: np.ndarray, vcov: np.ndarray,
        w: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """平均边际效应与它的标准误（delta 方法）。

    **这是 logit 的主结果，不是系数。** 系数是对数几率，读者判断不了量级；
    odds ratio 也不行——「几率高 1.8 倍」在基线概率 2% 和 40% 的组里
    是完全不同的两回事。AME 读成「概率高 X 个百分点」，跟线性概率模型可比。

    AME_j = Σ wᵢ p(1−p) β_j / Σ wᵢ
    雅可比 ∂AME_j/∂β_m = Σ wᵢ p(1−p)[1{j=m} + (1−2p) β_j xᵢₘ] / Σ wᵢ
    """
    n = X.shape[0]
    w = np.ones(n) if w is None else np.asarray(w, dtype=float)
    sw = float(np.sum(w))
    p = _p(X, beta)
    d = p * (1 - p)                               # ∂p/∂(Xβ)
    m = float(np.sum(w * d)) / sw
    eff = m * beta
    # 雅可比：第一项是 m·I，第二项来自 d 本身随 β 变
    jac = m * np.eye(len(beta)) + np.outer(beta, (w * d * (1 - 2 * p)) @ X) / sw
    var = jac @ vcov @ jac.T
    return eff, np.sqrt(np.clip(np.diag(var), 0.0, None))


# ─────────────────────────────────────────────────────── 有序 logit


def _ordered_nll(theta: np.ndarray, X: np.ndarray, yi: np.ndarray, w: np.ndarray,
                 J: int) -> tuple[float, np.ndarray]:
    """有序 logit 的负对数似然与梯度。

    切点用**递增参数化**：cut₀ = θ₀，cutⱼ = cutⱼ₋₁ + exp(θⱼ)。
    这样无约束优化也保证切点递增——直接估切点的话，牛顿法一步就能把它们跨过去，
    而跨过去之后概率是负的，似然变 nan，**看起来像「数据有问题」，其实是参数化的问题**。
    """
    k = X.shape[1]
    beta = theta[:k]
    raw = theta[k:]
    cuts = np.cumsum(np.r_[raw[0], np.exp(raw[1:])]) if J > 2 else np.array([raw[0]])
    z = X @ beta
    # P(y ≤ j) = logistic(cut_j − z)
    F = np.column_stack([_logistic(c - z) for c in cuts])
    F = np.column_stack([np.zeros(len(z)), F, np.ones(len(z))])
    prob = np.clip(F[np.arange(len(z)), yi + 1] - F[np.arange(len(z)), yi], _EPS, 1.0)
    nll = -float(np.sum(w * np.log(prob)))
    # 数值梯度：参数个数少（k + J−1），中心差分够用且不会写错
    g = np.zeros_like(theta)
    h = 1e-6
    for i in range(len(theta)):
        tp, tm = theta.copy(), theta.copy()
        tp[i] += h
        tm[i] -= h
        g[i] = (_ordered_nll_value(tp, X, yi, w, J) - _ordered_nll_value(tm, X, yi, w, J)) / (2 * h)
    return nll, g


def _ordered_nll_value(theta: np.ndarray, X: np.ndarray, yi: np.ndarray,
                       w: np.ndarray, J: int) -> float:
    k = X.shape[1]
    beta, raw = theta[:k], theta[k:]
    cuts = np.cumsum(np.r_[raw[0], np.exp(raw[1:])]) if J > 2 else np.array([raw[0]])
    z = X @ beta
    F = np.column_stack([_logistic(c - z) for c in cuts])
    F = np.column_stack([np.zeros(len(z)), F, np.ones(len(z))])
    prob = np.clip(F[np.arange(len(z)), yi + 1] - F[np.arange(len(z)), yi], _EPS, 1.0)
    return -float(np.sum(w * np.log(prob)))


def ordered_logit(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None,
                  max_iter: int = 200) -> LogitFit:
    """加权有序 logit（比例优势模型）。

    **X 不含截距**——截距跟切点不可识别（两者同时加减一个常数，似然不变）。
    调用方要把常数列去掉，`analysis.py` 那边会查。

    用 BFGS 风格的拟牛顿（数值梯度 + 线搜索）。参数个数是 k + J − 1，社科里
    很少超过二三十个，数值梯度的代价可以接受，**而手写解析梯度最容易在切点那几项上写错**。
    """
    n, k = X.shape
    w = np.ones(n) if w is None else np.asarray(w, dtype=float)
    levels = np.unique(y)
    J = len(levels)
    if J < 3:
        raise ValueError(f"有序 logit 要至少三个有序取值，这一列只有 {J} 个——两个取值用 logit")
    yi = np.searchsorted(levels, y)
    # 起点：切点按累积频率的 logit，斜率全 0
    cum = np.cumsum([np.sum(w[yi == j]) for j in range(J - 1)]) / np.sum(w)
    c0 = np.log(np.clip(cum, 1e-6, 1 - 1e-6) / (1 - np.clip(cum, 1e-6, 1 - 1e-6)))
    raw0 = np.r_[c0[0], np.log(np.clip(np.diff(c0), 1e-6, None))] if J > 2 else c0[:1]
    theta = np.r_[np.zeros(k), raw0]

    H = np.eye(len(theta))                       # 逆海森的近似
    f, g = _ordered_nll(theta, X, yi, w, J)
    for it in range(1, max_iter + 1):
        d = -H @ g
        step = 1.0
        for _ in range(40):                      # 回溯线搜索
            cand = theta + step * d
            fc = _ordered_nll_value(cand, X, yi, w, J)
            if np.isfinite(fc) and fc <= f - 1e-4 * step * float(g @ -d):
                break
            step *= 0.5
        else:
            break                                # 步长缩到底还不下降：当作收敛
        new_theta = theta + step * d
        f_new, g_new = _ordered_nll(new_theta, X, yi, w, J)
        s, yv = new_theta - theta, g_new - g
        sy = float(s @ yv)
        if sy > 1e-12:                           # BFGS 更新，曲率为正才做
            rho = 1.0 / sy
            I = np.eye(len(theta))
            H = (I - rho * np.outer(s, yv)) @ H @ (I - rho * np.outer(yv, s)) + rho * np.outer(s, s)
        done = abs(f - f_new) < _TOL * (abs(f) + _TOL)
        theta, f, g = new_theta, f_new, g_new
        if done:
            break
    else:
        raise NotConverged(f"{max_iter} 轮还没收敛")

    beta = theta[:k]
    raw = theta[k:]
    cuts = np.cumsum(np.r_[raw[0], np.exp(raw[1:])]) if J > 2 else np.array([raw[0]])
    # 方差用数值海森（参数少，代价可接受）
    m = len(theta)
    Hn = np.zeros((m, m))
    h = 1e-5
    for i in range(m):
        tp, tm = theta.copy(), theta.copy()
        tp[i] += h
        tm[i] -= h
        _, gp = _ordered_nll(tp, X, yi, w, J)
        _, gm = _ordered_nll(tm, X, yi, w, J)
        Hn[i] = (gp - gm) / (2 * h)
    Hn = (Hn + Hn.T) / 2
    vcov = np.linalg.pinv(Hn)[:k, :k]
    return LogitFit(beta=beta, vcov=vcov, llf=-f, n=n, n_iter=it, cuts=cuts)


def parallel_lines(X: np.ndarray, y: np.ndarray, w: np.ndarray | None = None,
                   ) -> tuple[float, int, list[np.ndarray]]:
    """平行线（比例优势）假定的 **Brant 检验**。返回 (卡方统计量, 自由度, 各切点的系数)。

    **有序 logit 的核心假定是：同一个自变量对「≥2 分」和「≥4 分」的影响相同。**
    几乎没人验它，而它不成立时报出来的那一个系数是几个不同效应的某种平均，
    对哪一段都不成立。

    做法：对每个切点 j 跑二值 logit（y > j），得到 J−1 组系数，检验它们相等。

    **关键在协方差，这一处第一版写错了。** 各组用的是**同一批观测**，估计之间
    正相关；把它们当独立处理，统计量会大得离谱——实测在平行线**明明成立**的
    模拟数据上（n=12000）也给出 p≈0。**那样的门会误报，而会误报的门比没有门更糟**，
    人会学会绕过去。

    所以按 Brant（1990）原式算协方差：

        Var(β_j)     = (X'W_j X)^-1
        Cov(β_j,β_k) = (X'W_j X)^-1 · X'W_jk X · (X'W_k X)^-1
        W_j  = diag(π_j(1−π_j))            π_j = P(y > j)
        W_jk = diag(π_max − π_j·π_k)       max 取 j、k 中较大的那个切点

    统计量是对比 D = (β_1−β_2, β_2−β_3, …) 的 Wald 检验，自由度 (J−2)·k。
    **阈值不调**——统计量算对了就不用调。
    """
    n, k = X.shape
    w = np.ones(n) if w is None else np.asarray(w, dtype=float)
    # **每个二值 logit 都要有自己的截距**，它正对应那个切点。
    # 有序 logit 的 X 不含截距（跟切点不可识别），但这里的分项拟合含——
    # 漏掉它，各组的拟合全是错的，协方差矩阵会算出负的特征值（实测踩过）。
    # 检验只比斜率，截距那一列最后要剔出去
    Xc = np.column_stack([np.ones(n), X])
    levels = np.unique(y)
    betas: list[np.ndarray] = []
    pis: list[np.ndarray] = []
    bread: list[np.ndarray] = []
    for c in levels[:-1]:
        yb = (y > c).astype(float)
        if yb.sum() in (0, len(yb)):            # 这个切点一边没人，跳过
            continue
        fit = binary_logit(Xc, yb, w)
        pi = _p(Xc, fit.beta)
        betas.append(fit.beta[1:])              # 去掉截距，只留斜率
        pis.append(pi)
        # **面包保持含截距的整块**，最后再切斜率——先切再乘，对角块和非对角块
        # 就不在同一个空间里了，协方差矩阵会出负特征值（两个自变量时实测踩过：
        # 单变量时碰巧还是正定的，所以一元的测试全过、二元的一跑就崩）
        bread.append(np.linalg.pinv((Xc.T * (w * pi * (1 - pi))) @ Xc))
    m = len(betas)
    if m < 2:
        return 0.0, 0, betas

    # 全协方差矩阵：块 (j,k)
    V = np.zeros((m * k, m * k))
    for j in range(m):
        for kk in range(m):
            if j == kk:
                blk = bread[j]
            else:
                hi = max(j, kk)
                Wjk = w * (pis[hi] - pis[j] * pis[kk])
                blk = bread[j] @ ((Xc.T * Wjk) @ Xc) @ bread[kk]
            V[j * k:(j + 1) * k, kk * k:(kk + 1) * k] = blk[1:, 1:]   # 切斜率放在最后

    # 对比矩阵：相邻两组之差
    C = np.zeros(((m - 1) * k, m * k))
    for j in range(m - 1):
        C[j * k:(j + 1) * k, j * k:(j + 1) * k] = np.eye(k)
        C[j * k:(j + 1) * k, (j + 1) * k:(j + 2) * k] = -np.eye(k)
    d = C @ np.concatenate(betas)
    Vd = C @ V @ C.T
    # **协方差矩阵必须半正定**。写错公式时它不是——第一版漏了各分项 logit 的截距，
    # 算出来特征值有负的，统计量是 −8304。**卡方不可能为负**，而那个负数
    # 照样能被 p 值函数算出一个数来（开立方开成复数才崩的），
    # 所以这里挡一道：不正定就说不出话，不给一个像模像样的假统计量
    if float(np.linalg.eigvalsh(Vd).min()) < -1e-8 * max(1.0, float(np.trace(Vd))):
        raise NotConverged("平行线检验的协方差矩阵不是半正定——统计量不可信，不报")
    stat = float(d @ np.linalg.pinv(Vd) @ d)
    if stat < 0:
        raise NotConverged(f"平行线检验算出负的卡方（{stat:.3g}）——不报")
    return stat, (m - 1) * k, betas
