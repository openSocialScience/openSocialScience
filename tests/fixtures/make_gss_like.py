"""造一份 **GSS 形态** 的小夹具。

现有的两份夹具测不出真数据的坑，因为它们是我编的，而我编不出我没见过的东西。
这一份是 2026-08-24 拿 GSS 真跑一遍之后，**照着真实踩到的坑反向造的**。

四个坑，各对应 PLAN.md 九之一的一条发现：

**① 编号每期回收**（对应发现 ①）
    GSS 的 `id` 是受访者在**当年问卷内**的序号，每一轮从 1 重新编。
    于是 id=1 在 2000 年和 2004 年是两个毫无关系的人。
    这会让 `repeated`（个体出现在多期的比例）**虚高**，
    从 `repeated < 0.2` 那条防线的**另一侧**穿过去，被判成面板。
    判成面板就会授权个体固定效应——把陌生人当同一个人做组内比较。

    **证据列**：`cohort`（出生年）在同一个 id 内会变。真人不会换出生年。

**② 年龄-时期-队列恒等式**（对应发现 ②）
    `cohort = year - age`，但少数行因生日时点差 1。
    差这 1 会让共线**不精确**，于是 `matrix_rank` 满秩，秩检验放行，
    回归照跑、照出星号，而 `age` 的系数已经不可解读。

**③ 块状缺失**（对应诊断图 ⑬）
    `paeduc` 在最早两轮**整段缺失**——真实调查里题目是会中途加进来的。
    按整体缺失率看是「缺了三成」，按年份看是「前两轮一条都没有」，
    两者对分析的含义完全不同。

**④ 簇数极少**（对应发现 ③ 与诊断图 ⑭）
    只有 3 个区域。聚类稳健标准误在簇数这么少时本身就是有偏的。

跑 `python tests/fixtures/make_gss_like.py` 重新生成（固定随机种子，结果可复现）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

YEARS = [2000, 2004, 2008, 2012, 2016, 2020]
N_PER_YEAR = [52, 48, 55, 50, 47, 53]
BLOCK_MISSING_YEARS = {2000, 2004}   # paeduc 这两轮整段没问


def build() -> pd.DataFrame:
    rng = np.random.default_rng(20260824)
    rows = []
    for year, n in zip(YEARS, N_PER_YEAR):
        for i in range(1, n + 1):
            age = int(rng.integers(25, 71))
            # 坑 ②：绝大多数行满足 cohort = year - age，少数差 1（生日还没过）
            cohort = year - age - (1 if rng.random() < 0.04 else 0)
            region = int(rng.integers(1, 4))
            female = int(rng.random() < 0.52)
            educ = int(np.clip(rng.normal(13.5, 2.6), 8, 20))
            # 坑 ③：最早两轮整段缺失；其余轮次另有约两成随机缺失
            if year in BLOCK_MISSING_YEARS or rng.random() < 0.2:
                paeduc = np.nan
            else:
                paeduc = float(np.clip(rng.normal(11.0, 3.2), 4, 20))
            base = 20 + 1.9 * educ + 0.7 * (paeduc if paeduc == paeduc else 11.0)
            sei = float(base - 2.4 * female + 1.5 * region + rng.normal(0, 7))
            rows.append(
                dict(
                    year=year,
                    id=i,                       # 坑 ①：每一轮都从 1 重编
                    age=age,
                    cohort=cohort,
                    region=region,
                    female=female,
                    educ=educ,
                    paeduc=paeduc,
                    sei=round(sei, 2),
                    wtssps=round(float(rng.gamma(9, 1 / 9)), 4),   # 坑：权重列，名字不含 "weight"
                )
            )
    df = pd.DataFrame(rows)
    df["region_year"] = df.region.astype(str) + "_" + df.year.astype(str)
    return df


if __name__ == "__main__":
    df = build()
    out = Path(__file__).parent / "gss_like.csv"
    df.to_csv(out, index=False)

    per_unit = df.groupby("id").year.nunique()
    apc = (df.year - df.age - df.cohort).abs()
    print(f"写出 {out}  {len(df)} 行 × {df.shape[1]} 列")
    print(f"  ① 出现在多期的编号占比 {(per_unit > 1).mean():.0%}"
          f"（>20% 就会被现有逻辑判成面板）")
    print(f"     同一编号内出生年不唯一的占比 "
          f"{(df.groupby('id').cohort.nunique() > 1).mean():.0%}  ← 真人不会换出生年")
    print(f"  ② APC 恒等式：{(apc == 0).sum()} 行精确成立，{(apc == 1).sum()} 行差 1")
    print(f"  ③ paeduc 整体缺失 {df.paeduc.isna().mean():.0%}；"
          f"按年份看 {sorted(BLOCK_MISSING_YEARS)} 两轮为 100%")
    print(f"  ④ 区域 {df.region.nunique()} 个，region_year {df.region_year.nunique()} 个")
