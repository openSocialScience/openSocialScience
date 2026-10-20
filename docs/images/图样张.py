"""八种图型的样张。**数据全是造的**，只为看版面效果。

题目设成一个像真的社科问题：高校扩招是否削弱了家庭出身对子女职业地位的影响。
面板：2000–2019，30 个省，每省每年若干人。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from oss.core import figures
from oss.core.contracts import (
    AnalysisPlan, AnalysisResults, BinnedSlope, DataProfile, DataStructure,
    Estimate, EventStudyPoint, FileInfo, RobustnessRun, VariableProfile,
)

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "gallery")
OUT.mkdir(parents=True, exist_ok=True)
LANG = sys.argv[2] if len(sys.argv) > 2 else "zh"
rng = np.random.default_rng(20260924)

#: 样张的变量名两套。**英文样张不能用中文名**——中文字配英文衬线字体会排成一排方框，
#: 而「方框比没有图更糟，它看起来像图能用」。真跑时这一条由 `cjk_labels` 记下来、导出时拦。
NAMES = {
    "zh": {"origin": "家庭出身", "exp": "扩招强度", "edu": "本人受教育年限", "isei": "职业地位",
           "female": "性别", "age": "年龄", "hukou": "农业户口"},
    "en": {"origin": "Social origin", "exp": "Expansion intensity", "edu": "Years of schooling",
           "isei": "Occupational status", "female": "Female", "age": "Age", "hukou": "Rural hukou"},
}[LANG if LANG in ("zh", "en") else "zh"]
N = NAMES

ROB = {
    "zh": [("不加权", -0.0389, 0.0141, 11840), ("只用 2006 年后各轮", -0.0447, 0.0189, 8120),
           ("控制省份线性趋势", -0.0355, 0.0152, 11840), ("剔除直辖市", -0.0401, 0.0147, 10630),
           ("职业地位换用 EGP 类别", -0.0438, 0.0163, 11840), ("排除疫情三年", -0.0426, 0.0151, 10240)],
    "en": [("Unweighted", -0.0389, 0.0141, 11840), ("Rounds from 2006 onwards", -0.0447, 0.0189, 8120),
           ("Province-specific linear trends", -0.0355, 0.0152, 11840),
           ("Excluding municipalities", -0.0401, 0.0147, 10630),
           ("EGP class scheme for the outcome", -0.0438, 0.0163, 11840),
           ("Excluding the pandemic years", -0.0426, 0.0151, 10240)],
}

# ─────────────────────────────────────────────── 造一份面板

YEARS = list(range(2000, 2020))
PROV = [f"省{i:02d}" for i in range(1, 31)]
rows = []
for p_i, prov in enumerate(PROV):
    n_per = int(rng.integers(40, 160))          # 省之间样本量差很多，簇图才好看
    for yr in YEARS:
        for _ in range(n_per // len(YEARS) + 1):
            origin = rng.normal(0, 1)
            exp = (yr - 2000) / 19
            isei = 40 + 8 * origin * (1 - .45 * exp) + 6 * exp + rng.normal(0, 9) + p_i * .1
            rows.append({
                "省份": prov, "年份": yr,
                "家庭出身": origin, "本人受教育年限": 9 + 6 * exp + rng.normal(0, 2.5),
                "职业地位": isei, "性别": int(rng.random() < .49),
                "年龄": int(rng.integers(25, 60)), "农业户口": int(rng.random() < .42),
            })
frame = pd.DataFrame(rows)
# 缺失：越往后越少（追访变好），正是 missing 图要显示的形状
for col, base in (("职业地位", .18), ("本人受教育年限", .10)):
    p_miss = base * (1 - (frame["年份"] - 2000) / 26)
    frame.loc[rng.random(len(frame)) < p_miss, col] = np.nan

profile = DataProfile(
    profile_id="demo", file=FileInfo(path="扩招面板.csv"),
    variables=[VariableProfile(name=c, label=c, dtype=str(frame[c].dtype),
                               n_missing=int(frame[c].isna().sum()),
                               missing_rate=float(frame[c].isna().mean()),
                               n_unique=int(frame[c].nunique()))
               for c in frame.columns],
    structure=DataStructure(kind="repeated_cross_section", unit_id="省份", time_id="年份",
                            n_units=len(PROV), n_periods=len(YEARS),
                            nesting_levels=["省份"], detection_note="每期都是新抽的人"),
)

# ─────────────────────────────────────────────── 造结果

def est(term, coef, se):
    return Estimate(term=term, coef=coef, se=se,
                    p=float(2 * (1 - min(.9999, abs(coef / se) / 4))), dof=9800)

results = AnalysisResults(
    plan_id="demo", n=11840,
    estimates={
        "main":   est(f"{N['origin']}*{N['exp']}", -0.0412, 0.0138),
        "origin": est(N["origin"], 0.1873, 0.0265),
        "exp":    est(N["exp"], 0.0614, 0.0201),
        "edu":    est(N["edu"], 0.0455, 0.0072),
        "female": est(N["female"], -0.0533, 0.0091),
        "age":    est(N["age"], 0.0031, 0.0011),
        "hukou":  est(N["hukou"], -0.0928, 0.0154),
        "f_x":    est(f"{N['female']}*{N['exp']}", 0.0067, 0.0129),
        "h_x":    est(f"{N['hukou']}*{N['exp']}", -0.0184, 0.0166),
    },
    diagnostics={"组内 R²": "0.184", "吸收的自由度": "49", "标准误": "按省份聚类（30 簇）"},
    event_study=[EventStudyPoint(k=k, coef=c, se=s, n=11840) for k, c, s in [
        (-4, 0.004, 0.019), (-3, -0.007, 0.017), (-2, 0.002, 0.015), (-1, 0.0, 0.0),
        (0, -0.018, 0.014), (1, -0.031, 0.015), (2, -0.045, 0.016),
        (3, -0.052, 0.018), (4, -0.061, 0.021)]],
    binned={"全样本": [BinnedSlope(group="全样本", bin_index=i, center=c, slope=sl, se=se, n=n)
                     for i, (c, sl, se, n) in enumerate([
                         (0.08, 0.201, 0.031, 2380), (0.28, 0.176, 0.028, 2410),
                         (0.50, 0.142, 0.027, 2365), (0.72, 0.118, 0.029, 2340),
                         (0.92, 0.089, 0.033, 2345)])]},
    robustness={
        lab: RobustnessRun(label=lab, n=n, estimates={"main": est(f"{N['origin']}*{N['exp']}", c, s)})
        for lab, c, s, n in [
            *(ROB[LANG if LANG in ("zh", "en") else "zh"]),
        ]},
)

plan = AnalysisPlan(plan_id="demo", idea_id="demo", estimator="ols_fe",
                    outcome="职业地位", treatment="家庭出身",
                    controls=["本人受教育年限", "性别", "年龄", "农业户口"],
                    fixed_effects=["省份", "年份"], cluster_level="省份")

LABELS = {N["origin"]: N["origin"], N["exp"]: N["exp"]}
#: 英文样张要英文名——中文列名配英文衬线字体会排成一排方框
LABELS_MG = {"职业地位": N["isei"], "家庭出身": N["origin"], "本人受教育年限": N["edu"]}

# ─────────────────────────────────────────────── 画

made = []
for name, fn in [
    ("01_结构判定",   lambda p: figures.structure(frame, profile, p)),
    ("02_缺失率",     lambda p: figures.missing(frame, profile, p)),
    ("03_簇与设计效应", lambda p: figures.clusters(frame, "省份", p, icc=0.06)),
    ("04_主回归系数",  lambda p: figures.coef(results, p, lang=LANG, labels=LABELS)),
    ("05_稳健性森林图", lambda p: figures.robustness(results, p, key="main", lang=LANG, labels=LABELS)),
    ("06_逐年展开",   lambda p: figures.event(results, p, lang=LANG)),
    ("07_分箱斜率",   lambda p: figures.marginal(frame, "职业地位", "家庭出身", "本人受教育年限", p, lang=LANG, labels=LABELS_MG)),
    ("08_共线诊断",   lambda p: figures.collinearity(frame, plan, p)),
]:
    try:
        fn(OUT / f"{name}.png")
        made.append(f"  ✓ {name}")
    except Exception as exc:                       # noqa: BLE001 —— 样张脚本，哪张画不出就说哪张
        made.append(f"  ✗ {name}：{type(exc).__name__}: {exc}")

print("\n".join(made))
print(f"\n→ {OUT}")
