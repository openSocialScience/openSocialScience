"""造一份**分批实施、各批效应不同**的夹具：老方法算错、新方法算对的那种数据。

`did_clean.csv` 里两批的真实效应一样（都是 3.0），所以双向固定效应的双重差分
（TWFE）也能算对——那份夹具证明不了「为什么要换估计量」。

这一份专门造成 TWFE 最容易出偏的形态：

- **早处理批（2006 年）的效应随时间增长**：第 k 期效应 = 1 + 0.5k
- **晚处理批（2012 年）的效应恒定**：2.0
- 三分之一的单位从未处理

TWFE 会把已处理的早批当成晚批的对照；早批的效应还在涨，
「对照」自己在动，算出来的汇总系数被拖到真实平均值以下一大截
（实测：真实 3.56，TWFE 2.13，Callaway–Sant'Anna 3.51）。

**`true_effect` 列是每个观测的真实效应**，测试用它算「真实平均处理效应」，
不把答案写死在测试里——夹具变了答案跟着变。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

YEARS = list(range(2000, 2021))
N_UNITS = 150
COHORTS = {0: None, 1: 2006, 2: 2012}


def effect(t0: int | None, year: int) -> float:
    if t0 is None or year < t0:
        return 0.0
    k = year - t0
    return 1.0 + 0.5 * k if t0 == 2006 else 2.0


def build(*, seed: int = 20260826) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    unit_fe = rng.normal(0, 4, N_UNITS)
    year_fe = {y: 0.3 * (y - 2000) + rng.normal(0, 0.5) for y in YEARS}
    rows = []
    for u in range(N_UNITS):
        t0 = COHORTS[u % 3]
        for y in YEARS:
            eff = effect(t0, y)
            val = 20 + unit_fe[u] + year_fe[y] + eff + rng.normal(0, 1.2)
            rows.append(dict(
                unit=u, year=y,
                treat_time=(t0 if t0 is not None else np.nan),
                post=1.0 if (t0 is not None and y >= t0) else 0.0,
                true_effect=eff,
                x=float(rng.normal(0, 1)),
                y=round(float(val), 4),
            ))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    df = build()
    out = Path(__file__).parent / "did_staggered.csv"
    df.to_csv(out, index=False)
    treated = df[df.post == 1]
    print(f"写出 {out.name}：{len(df)} 行，{df.unit.nunique()} 个单位 × {df.year.nunique()} 年")
    print(f"  真实平均处理效应（处理后各观测的平均）{treated.true_effect.mean():.3f}；"
          f"各批：{treated.groupby('treat_time').true_effect.mean().round(3).to_dict()}")
