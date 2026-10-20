"""造借力变量法与断点法的夹具。**真实效应是我设定的，所以能验证算得对不对。**

这条纪律来自更早的教训：手抄的公式抄错了不报错，只安静地给出错的数
（正态分位数返回 1.0017 而不是 1.96，功效被高估三倍）。

## 借力变量法：`iv_like.csv`

真实因果效应 **β = 2.0**。

关键在于**故意造一个内生性**：有一个观测不到的东西 `u`
同时影响处理 `x` 和结果 `y`。所以：

- **直接回归（不用借力变量）会算出偏大的数** —— 这是「关联不等于因果」的具体形态
- **借力变量法应当还原出 2.0**

借力变量 `z` 只影响 `x`，不直接影响 `y`——这就是那个**无法用数据检验**的假设，
这里由构造保证它成立。

另外造一份 `iv_weak.csv`：借力变量与处理几乎无关（第一阶段很弱）。
这种情况下借力变量法的估计会剧烈失真，**而且它不会报错**——
必须靠第一阶段的强度检验拦住。

## 断点法：`rdd_like.csv`

真实跳跃 **τ = 4.0**，分数线在 60 分。

再造一份 `rdd_manip.csv`：**分数线下方的人挤到了上方**（改分、卡线）。
这会让断点法失效，而它同样不报错——要靠「分数线附近的人数分布」检验拦住。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TRUE_IV_BETA = 2.0
TRUE_RDD_TAU = 4.0
CUTOFF = 60.0


def build_iv(n: int = 4000, pi: float = 1.0, seed: int = 20260824) -> pd.DataFrame:
    """`u` 是观测不到的混杂：它同时抬高 x 和 y，所以直接回归会高估 β。"""
    rng = np.random.default_rng(seed)
    z = rng.normal(0, 1, n)                       # 借力变量
    u = rng.normal(0, 1, n)                       # 观测不到的混杂
    w = rng.normal(0, 1, n)                       # 一个普通控制变量
    x = pi * z + 1.2 * u + 0.5 * w + rng.normal(0, 0.5, n)
    y = TRUE_IV_BETA * x + 1.5 * u + 0.8 * w + rng.normal(0, 1.0, n)
    return pd.DataFrame({"y": y, "x": x, "z": z, "w": w})


def build_rdd(n: int = 4000, manipulate: bool = False,
              seed: int = 20260824) -> pd.DataFrame:
    """分数线两侧除了「过没过线」几乎一样，所以线上的跳跃就是效应。"""
    rng = np.random.default_rng(seed)
    score = rng.uniform(30, 90, n)
    if manipulate:
        # **卡线**：线下 3 分内的人有七成挤到线上
        near = (score >= CUTOFF - 3) & (score < CUTOFF)
        move = near & (rng.random(n) < 0.7)
        score = np.where(move, CUTOFF + rng.uniform(0, 2, n), score)
    passed = (score >= CUTOFF).astype(float)
    # 平滑的本底关系 + 线上的跳跃
    y = 20 + 0.35 * (score - CUTOFF) + TRUE_RDD_TAU * passed + rng.normal(0, 2.5, n)
    w = 0.02 * (score - CUTOFF) + rng.normal(0, 1, n)   # 该在线上连续的控制变量
    return pd.DataFrame({"y": y, "score": score, "passed": passed, "w": w})


if __name__ == "__main__":
    here = Path(__file__).parent
    for name, df, note in (
        ("iv_like.csv", build_iv(), f"真实 β = {TRUE_IV_BETA}，借力变量强"),
        ("iv_weak.csv", build_iv(pi=0.04), "借力变量**很弱**——估计会剧烈失真且不报错"),
        ("rdd_like.csv", build_rdd(), f"真实跳跃 τ = {TRUE_RDD_TAU}，分数线 {CUTOFF:.0f}"),
        ("rdd_manip.csv", build_rdd(manipulate=True), "**线下的人挤到了线上**——断点法失效"),
    ):
        df.to_csv(here / name, index=False)
        print(f"写出 {name}：{len(df)} 行 × {df.shape[1]} 列  —— {note}")
    d = build_iv()
    import numpy.linalg as la
    X = np.column_stack([np.ones(len(d)), d.x, d.w])
    ols = la.lstsq(X, d.y, rcond=None)[0][1]
    print(f"\n  对照：直接回归算出 β = {ols:.3f}，而真实值是 {TRUE_IV_BETA}"
          f"（高估 {ols - TRUE_IV_BETA:+.3f}）——**这就是内生性**")
