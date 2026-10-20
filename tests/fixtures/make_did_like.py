"""造两份**政策冲击**形态的夹具，用来验证因果方法算得对不对。

**关键在于「真实效应是我设定的」**——所以能检查算出来的数对不对。
之前踩过的教训：手抄的公式抄错了不报错，只安静地给出错的数
（正态分位数返回 1.0017 而不是 1.96，功效被高估三倍）。
新方法只会更容易出这种事，所以每一个都要有**能独立算出同一答案的对照**。

两份夹具，一份是「干净的」，一份是「有致命问题的」：

**① `did_clean.csv` —— 处理前两组走势平行**
    真实效应 = 3.0，且是**分批实施**的（有的单位 2010 年开始、有的 2014 年）。
    分批很重要：现实里的政策几乎都是分批铺开的，
    而分批恰恰是双向固定效应做法最容易出偏的场合。

**② `did_pretrend.csv` —— 处理前两组就已经在分化**
    真实效应同样是 3.0，但**处理组在政策实施之前就已经比对照组涨得快**。
    这种数据算出来的「效应」是假的，而**表格上看不出来，图上一眼可见**。
    这份夹具是用来验证「平行趋势检验」能不能拦住它的。

    **前趋势必须是「非线性」的，线性趋势造不出来。**踩了两次才想清楚：

    第一版按日历年造（处理组每年多涨），逐年展开图上看不出来。
    第二版改成按相对期数造，**结果一模一样**——因为 `(y − t0)` 与 `y`
    只差一个「单位常数」，而那正好被单位固定效应吃掉。
    **两种写法是同一个模型。**

    真正能被逐年展开图抓到的，是**非线性**的前趋势。
    最典型也最现实的一种是「抢跑」：政策落地前几期，处理组已经开始动了
    （预期到要改，提前反应）。这不是一条直线，固定效应吃不掉它。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

YEARS = list(range(2004, 2021))
N_UNITS = 60
TRUE_EFFECT = 3.0
#: 分批实施：三分之一 2010 年、三分之一 2014 年、三分之一从未处理
COHORTS = {0: None, 1: 2010, 2: 2014}


def build(*, pretrend: float = 0.0, seed: int = 20260824) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    unit_fe = rng.normal(0, 4, N_UNITS)
    year_fe = {y: 0.4 * (y - 2004) + rng.normal(0, 0.5) for y in YEARS}
    treat_time = {u: COHORTS[u % 3] for u in range(N_UNITS)}

    rows = []
    for u in range(N_UNITS):
        t0 = treat_time[u]
        for y in YEARS:
            post = 1.0 if (t0 is not None and y >= t0) else 0.0
            # 「抢跑」：政策落地前四期开始，处理组逐期抬高；落地后停在那个水平。
            # **非线性，所以固定效应吃不掉**——这才是逐年展开图能抓到的形态。
            if t0 is None:
                drift = 0.0
            else:
                k = y - t0
                drift = pretrend * (max(0, k + 4) if k < 0 else 4)
            val = (20 + unit_fe[u] + year_fe[y] + drift
                   + TRUE_EFFECT * post + rng.normal(0, 1.2))
            rows.append(dict(
                unit=u, year=y,
                treat_time=(t0 if t0 is not None else np.nan),
                post=post,
                x=float(rng.normal(0, 1)),      # 一个无关控制变量
                y=round(float(val), 4),
            ))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    here = Path(__file__).parent
    for name, pt in (("did_clean.csv", 0.0), ("did_pretrend.csv", 0.5)):
        df = build(pretrend=pt)
        df.to_csv(here / name, index=False)
        treated = df[df.treat_time.notna()]
        print(f"写出 {name}：{len(df)} 行，{df.unit.nunique()} 个单位 × {df.year.nunique()} 年")
        print(f"  真实效应 {TRUE_EFFECT}；分批实施：{sorted(treated.treat_time.unique())}")
        print(f"  从未处理的单位 {df[df.treat_time.isna()].unit.nunique()} 个")
        if pt:
            print(f"  **政策落地前四期开始「抢跑」，每期多涨 {pt}**——这会造出一个假效应")
