"""统计功效。

**两条决定成败的规矩：**

**① 效应量先打折再算。**
已发表的社科效应量被发表偏倚系统性夸大。按面值规划，研究注定 underpowered——
而这件事**做完才会发现**。折扣常量来自 ARIS 的社科 skill：`EFFECT_SIZE_HAIRCUT = 0.6`。

**② 阈值按学科变，而且是三档不是两档。**
「0.8 以上算够」是惯例不是法律。0.79 和 0.81 没有实质差别，
一条线会造成假的确定性。所以两头定死、中间交给跨模型判（DECISIONS.md 第六章）。

心理学要求预注册和高功效，社会学宽松得多——**用一个值走天下就等于没有学科档案。**
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import NamedTuple

from oss.core.contracts import PowerReport, Verdict

#: 发表偏倚折扣。不打折的话研究注定样本不足，而且做完才发现。
EFFECT_SIZE_HAIRCUT = 0.6

#: 各学科的功效灰区 `(下界, 上界)`。
#:
#: 三档的含义：低于下界直接判死，高于上界直接通过，中间交给跨模型判。
#: 惯例的 0.8 落在每条带子里面——**带子是围着惯例展开的，不是取代它**。
#:
#: 为什么各学科不同（见 DECISIONS.md 4.6 学科档案）：
#:   psychology  预注册与功效近乎强制，要求最高
#:   economics   识别门硬，但功效标准是常规的 0.8
#:   sociology   识别门软，描述性研究可发，对 NHST 的依赖低
#:
#: **这些数值是初值，需要用真实运行校准。**关键不在准不准，
#: 而在于这个维度必须存在——用一个值走天下就等于没有学科档案。
GRAY_BANDS: dict[str, tuple[float, float]] = {
    "psychology": (0.70, 0.95),
    "economics": (0.50, 0.90),
    "political-science": (0.50, 0.90),
    "communication": (0.45, 0.85),
    "sociology": (0.40, 0.80),
}
DEFAULT_BAND = (0.50, 0.90)


#: MDE 的目标功效。0.8 是社科通行的约定值。
MDE_TARGET_POWER = 0.8


def mde(n_effective: float, alpha: float = 0.05, target_power: float = MDE_TARGET_POWER) -> float:
    """最小可测效应（Cohen's d）：这份数据最小能测出多大的效应。

    跟本模块的功效公式同源——功效 = Φ(d·√n_eff/2 − z_{α/2})，令功效 = `target_power` 反解，
    得 d = 2·(z_{α/2} + z_β) / √n_eff。

    **它只取决于有效样本量，不随效应量变**，所以是候选分诊唯一诚实的那个数：
    判「这题在这份数据上测不测得出来」不必先有一个效应量估计，也就不会退回到
    「拿命令行缺省的 0.2 算出 pass」那条路（见 `PowerReport.effect_size_source`）。

    有效样本量为 0 返回 0——预检要在各种半成品候选上跑，除零不能打断整段预检。
    """
    if n_effective <= 0:
        return 0.0
    return round(2 * (_z(1 - alpha / 2) + _z(target_power)) / math.sqrt(n_effective), 6)


def band_for(discipline: str) -> tuple[float, float]:
    return GRAY_BANDS.get((discipline or "").strip().lower(), DEFAULT_BAND)


# ------------------------------------------------------------------ 计算


def _phi(x: float) -> float:
    """标准正态的累积分布。"""
    return NormalDist().cdf(x)


def _z(p: float) -> float:
    """标准正态的分位数。

    **别自己手抄有理逼近。**这里原本抄了一版 Acklam 逼近，`_z(0.975)` 返回 1.0017
    而不是 1.96，导致所有功效值被系统性高估（0.16 报成 0.51）——
    而且它不会报错，只会安静地给出错的数。

    是跨模型审稿抓到的：审稿模型按文件里自报的 n 和效应量重算了一遍，
    发现对不上。标准库有精确实现，用它。
    """
    return NormalDist().inv_cdf(p)


#: 折价系数的依据。**「无出处」这条指控要靠给出处来回应，不是靠删掉这个参数。**
#:
#: 大规模复制项目实测到的效应量普遍只有原研究的一半到七成左右：
#: 心理学的 Open Science Collaboration（2015, Science）复制出的平均效应量
#: 约为原文的一半；Camerer 等（2018, Nature Human Behaviour）对发表在
#: Nature/Science 上的社科实验的复制，量级也在同一区间；
#: Camerer 等（2016, Science）对经济学实验室实验的复制略高些。
#:
#: 0.6 落在这个区间中部。**但它仍然是一个选择，不是一个事实**——
#: 这些数字来自实验研究，用到观察性研究上是外推；而且区间本身有宽度。
#: 所以每次功效判定都要跟着报敏感性分析：把「换个折价会怎样」直接摆出来。
HAIRCUT_BASIS = (
    "复制性研究实测的效应量普遍为原研究的五到七成"
    "（Open Science Collaboration 2015；Camerer 等 2016、2018）。"
    "0.6 落在该区间中部，**但它仍然是一个选择而非事实**——"
    "那些数字来自实验研究，用到观察性研究上是外推。因此必须配敏感性分析"
)

#: 敏感性分析要扫的取值。只扫**假定出来的**参数——
#: 数据里能算出来的（n、簇数、ρx）不在此列，那些不是假定。
_SENS_HAIRCUTS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0)
_SENS_ICCS = (0.0, 0.01, 0.02, 0.05, 0.10, 0.20)


def design_effect(cluster_size: float, icc: float, treatment_icc: float = 1.0) -> float:
    """聚类的设计效应：有效样本量要按它打折。

        DEFF = 1 + (m − 1) · ρ_x · ρ_u          （Moulton 因子）

    社科数据几乎总是聚类的（同一个县的人不独立），
    **不管它就是系统性高估功效**——而高估功效意味着你以为够了其实不够。

    但**反方向的错同样要防**：`ρ_x` 是**处理变量**的组内相关。
    这里原先隐含地取 `ρ_x = 1`，也就是假定处理变量在簇内完全不变
    （政策在县一级开关、实验在班级一级随机——这类设计才是这样）。

    个体层面的处理变量不是这样。父亲教育年限在同一个区-年里变异很大，
    `ρ_x` 接近 0，真实的设计效应远小于 `1 + (m−1)·ρ_u`。
    GSS 真跑时按 `ρ_x = 1` 算出设计效应 8.11、功效判 fail(0.22)，
    而实际估计的 t 值是 7.1——**被系统性低估了**。（PLAN.md 九之一 ③）

    错的不是这个公式，是**它没说自己假定了什么**。所以 `ρ_x` 现在是显式参数，
    并且要在判词里写明取的是哪个值、从哪来。
    """
    rho = max(0.0, min(1.0, treatment_icc))
    return max(1.0, 1.0 + (max(cluster_size, 1.0) - 1.0) * max(icc, 0.0) * rho)


def cluster_icc(values: "pd.Series", clusters: "pd.Series") -> float:
    """从数据里算一列的组内相关（单因素方差分解）。

    **`ρ_x` 是可以算出来的，不该让人猜。**猜出来的数会一路传进功效判词，
    而判词是要写进论文局限的。
    """
    import numpy as np
    import pandas as pd

    df = pd.DataFrame({"v": pd.to_numeric(values, errors="coerce"), "g": clusters}).dropna()
    if df.g.nunique() < 2 or len(df) < 4:
        return 1.0
    grand = df.v.mean()
    sizes = df.groupby("g").v.size()
    means = df.groupby("g").v.mean()
    k = len(sizes)
    ms_between = float((sizes * (means - grand) ** 2).sum() / max(k - 1, 1))
    ms_within = float(((df.v - df.g.map(means)) ** 2).sum() / max(len(df) - k, 1))
    m0 = float((len(df) - (sizes ** 2).sum() / len(df)) / max(k - 1, 1))
    if ms_within <= 0 or m0 <= 0:
        return 1.0
    icc = (ms_between - ms_within) / (ms_between + (m0 - 1) * ms_within)
    return float(np.clip(icc, 0.0, 1.0))


#: 支持的效应量度量。**换度量就要换公式**，不换而硬算是安静地给出错的数。
#:
#: 这是跨模型审稿抓到的（第二次抓到真问题，第一次是 `_z(0.975)` 返回 1.0017）。
#: 判词原文：「未说明 effect_size 的度量类型（Cohen's d / r / 标准化回归系数），
#: 却直接套用两样本均值差功效公式 d·√n_eff/2；若是 r 或 beta，功效将完全不同。」
#:
#: 一律先折算成 Cohen's d，再走同一条公式：
#:   d       原样
#:   r       d = 2r / √(1−r²)
#:   beta    标准化回归系数，在单个回归元、变量近似标准化时约等于 r，按 r 处理并警告
EFFECT_METRICS: dict[str, str] = {
    "d": "Cohen's d（两组标准化均值差）",
    "r": "相关系数 r（按 d = 2r/√(1−r²) 折算）",
    "beta": "标准化回归系数（近似按 r 处理——单回归元、变量近似标准化时才成立）",
}


def to_cohens_d(effect_size: float, metric: str) -> float:
    """把各种效应量折算成 Cohen's d。**不认识的度量直接拒绝，不猜。**"""
    if metric not in EFFECT_METRICS:
        raise ValueError(
            f"不认识的效应量度量 {metric!r}。支持：{'、'.join(EFFECT_METRICS)}。"
            f"**换度量就要换公式**——硬算等于安静地给出错的数"
        )
    if metric == "d":
        return abs(effect_size)
    r = min(abs(effect_size), 0.999)
    return 2 * r / math.sqrt(1 - r * r)


def compute_power(
    effect_size: float,
    n: int,
    *,
    metric: str = "d",
    alpha: float = 0.05,
    n_clusters: int | None = None,
    icc: float = 0.05,
    treatment_icc: float = 1.0,
    haircut: float = EFFECT_SIZE_HAIRCUT,
    weight_deff: float = 1.0,
) -> tuple[float, float, float]:
    """算功效。返回 `(打折后的效应量, 有效样本量, 功效)`。

    用标准化效应量 + 正态逼近。对「够不够」这个判断来说精度足够，
    而且没有额外依赖。

    有效样本量 = n ÷（整群设计效应 × 权重设计效应）。权重设计效应（Kish，`weight_deff`）不到 1 按 1 算；
    整群设计效应的簇大小仍按原始 n 算（`n / n_clusters`）——加权不改变每簇有多少行。
    """
    adjusted = to_cohens_d(effect_size, metric) * haircut
    eff_n = float(max(n, 0)) / max(weight_deff, 1.0)
    if n_clusters and n_clusters > 0 and n > 0:
        eff_n = eff_n / design_effect(n / n_clusters, icc, treatment_icc)

    if adjusted <= 0 or eff_n < 4:
        return round(adjusted, 4), round(eff_n, 1), 0.0

    # 双样本 / 回归系数的常见形式：非中心参数 = d * sqrt(n) / 2
    ncp = adjusted * math.sqrt(eff_n) / 2.0
    crit = _z(1 - alpha / 2)
    power = (1 - _phi(crit - ncp)) + _phi(-crit - ncp)
    return round(adjusted, 4), round(eff_n, 1), round(min(max(power, 0.0), 1.0), 4)


def verdict_for(power: float, band: tuple[float, float]) -> Verdict:
    """两头定死、中间跨模型（DECISIONS.md 6.2）。

    **这个函数是唯一的判词来源**——假实现和真实现共用它，
    否则灰区分支会因为某一边写死而永远走不到。
    """
    low, high = band
    if power < low:
        return "fail"
    if power > high:
        return "pass"
    return "gray"


# ------------------------------------------------------------------ 报告


def sensitivity(
    effect_size: float,
    n: int,
    *,
    metric: str = "d",
    alpha: float = 0.05,
    n_clusters: int | None = None,
    icc: float = 0.05,
    treatment_icc: float = 1.0,
    haircut: float = EFFECT_SIZE_HAIRCUT,
    discipline: str = "",
    weight_design_effect: float = 1.0,
) -> tuple[dict[str, list[tuple[float | str, float, str]]], list[str], bool]:
    """判词对那几个**拍脑袋定的参数**有多敏感。

    跨模型审稿的原话：「haircut 与 gray_band 均无出处或敏感性分析；
    **改动这些任意参数即可改变 verdict**」。
    **这条指控只能用一张可核对的表回应，辩解没有用。**

    扫三个假定参数：发表偏倚折价、残差组内相关、学科灰区上下界。
    数据里能算出来的（n、簇数、ρx）不扫——那些不是假定。

    返回 `(表, 结论, 判词是否脆弱)`。

    **只把会翻盘的参数写进结论。**不敏感的参数也报，但要明说「不敏感」——
    否则读者分不出哪几条要紧。跟 VIF 对交互项误报是同一个道理：
    **警报天天响就等于没有警报。**
    """
    band = band_for(discipline)

    def power_at(**over) -> float:
        kw = dict(metric=metric, alpha=alpha, n_clusters=n_clusters,
                  icc=icc, treatment_icc=treatment_icc, haircut=haircut,
                  weight_deff=weight_design_effect)
        kw.update(over)
        return compute_power(effect_size, n, **kw)[2]

    base_power = power_at()
    table: dict[str, list[tuple[float | str, float, str]]] = {
        "haircut": [
            (float(h), (pw := power_at(haircut=h)), verdict_for(pw, band))
            for h in _SENS_HAIRCUTS
        ],
        # 灰区上下界不影响功效值，只影响判词——所以扫的是各学科档案。
        # **第一列存学科名，不存序号。**存序号的话表上会打出「取 3 时翻为 fail」，
        # 读者会把那个 3 当成一个参数取值去理解，而它其实是 psychology。
        # 实测记录：审稿模型正是被这一列误导过。
        "gray_band": [
            (name, base_power, verdict_for(base_power, b))
            for name, b in sorted(GRAY_BANDS.items())
        ],
    }
    if n_clusters:
        table["icc"] = [
            (float(i), (pw := power_at(icc=i)), verdict_for(pw, band))
            for i in _SENS_ICCS
        ]

    labels = {"haircut": "发表偏倚折价", "icc": "残差组内相关 ρu",
              "gray_band": "学科灰区上下界"}
    cur = {"haircut": f"{haircut:g}", "icc": f"{icc:g}", "gray_band": discipline or "默认"}
    base_verdict = verdict_for(base_power, band)
    notes: list[str] = []
    fragile = False
    for key, rows in table.items():
        verdicts = {v for _, _, v in rows}
        if len(verdicts) <= 1:
            notes.append(f"{labels[key]}：**不敏感**（扫过的取值全部判 {base_verdict}）")
            continue
        fragile = True
        # 逐个列举不如给出**边界**：读者要知道的是「离翻盘还有多远」，
        # 不是每一格分别是什么。边界在扫过的网格上是精确的。
        segs: list[str] = []
        for verd in ("fail", "gray", "pass"):
            hit = [v for v, _, x in rows if x == verd]
            if not hit or verd == base_verdict:
                continue
            if all(isinstance(v, (int, float)) for v in hit):
                lo, hi = min(hit), max(hit)
                rng = f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"
            else:
                # 学科是**类别**，没有大小顺序——取区间会造出
                # 「communication–sociology」这种读不通的东西。列举。
                rng = "、".join(str(v) for v in hit)
            segs.append(f"取 {rng} 时翻为 {verd}")
        notes.append(
            f"{labels[key]}：**会翻盘**（当前 {cur.get(key, '')} 判 {base_verdict}，"
            + "，".join(segs) + "）"
        )
    return table, notes, fragile


def analyze(
    idea_id: str,
    effect_size: float,
    n: int,
    *,
    discipline: str = "",
    alpha: float = 0.05,
    n_clusters: int | None = None,
    icc: float = 0.05,
    treatment_icc: float = 1.0,
    treatment_icc_source: str = "",
    effect_metric: str = "d",
    effect_size_source: str = "",
    weight_design_effect: float = 1.0,
    weight_column: str = "",
) -> PowerReport:
    band = band_for(discipline)
    # 两个同名的量分开叫：整群设计效应（Moulton）与权重设计效应（Kish）。没有权重时判词一个字不变
    wd = max(float(weight_design_effect), 1.0)
    weighted = bool(weight_column) or wd > 1.0
    adjusted, eff_n, power = compute_power(
        effect_size, n, metric=effect_metric, alpha=alpha,
        n_clusters=n_clusters, icc=icc, treatment_icc=treatment_icc, weight_deff=wd,
    )
    v = verdict_for(power, band)
    de = design_effect(n / max(n_clusters, 1), icc, treatment_icc) if n_clusters else 1.0

    # 效应量没出处就不许判 pass。整个判词建立在这一个数上，而它是谁定的、
    # 从哪篇文献来的没有记录——那就是「拿不准」，拿不准归灰区（灰区不挡路，但强制写进局限）。
    # fail 不动：无出处的数算出 fail，停下来仍是保守的那一边。
    sourced = bool(effect_size_source.strip())
    downgraded = not sourced and v == "pass"
    if downgraded:
        v = "gray"

    head = (f"文献效应量 {effect_size}（出处：{effect_size_source.strip()}）" if sourced
            else f"效应量 {effect_size}——**没有给出处**")
    bits = [
        f"{head}，度量为 **{EFFECT_METRICS[effect_metric]}**，"
        f"折算成 Cohen's d 后再打 {EFFECT_SIZE_HAIRCUT} 折 → {adjusted}"
        f"（发表偏倚会系统性夸大已发表的效应量。折价系数本身无出处，改它即可改判词）"
    ]
    if not sourced:
        bits.append(
            "⚠ **效应量没有出处。**判词整个建立在这个数上，而它是谁定的、从哪篇文献来的，"
            "没有记录。**论文里不许写「按文献报告的效应量」**——那是一句无法核对的话。"
            "补出处：`oss power --effect <值> --effect-source \"<哪条语料记录、哪一页、原话>\"`"
            + ("；在此之前判词封顶为灰区（本可判 pass）" if downgraded else "")
        )
    if n_clusters:
        src = treatment_icc_source or (
            "**假定处理变量在簇内完全不变**（政策在县一级开关这类设计）。"
            "个体层面的处理变量不是这样，这会让功效被系统性低估——"
            "用 --treatment-col 从数据里算真实值"
            if treatment_icc >= 1.0 else "由调用方给定"
        )
        bits.append(
            f"{n} 个观测聚类到 {n_clusters} 个单位（每簇均 {n / n_clusters:.1f}），"
            f"残差 ICC={icc}，处理变量组内相关 ρx={treatment_icc:.3f}（{src}），"
            + (f"整群设计效应 = 1+(m−1)·ρx·ρu = {de:.2f}" if weighted else
               f"设计效应 = 1+(m−1)·ρx·ρu = {de:.2f}，**功效口径的有效样本量降到 {eff_n:.0f}**")
        )
        if "算出" in src:
            # 跨模型审稿指出的：ρx 从待分析数据算，是用了两次数据。
            # 它是设计量不是效应量，所以偏差有限，但方向是乐观的——必须说出来。
            bits.append(
                "⚠ ρx 是**从待分析的同一份数据**算出来的，不是预注册的。"
                "它是设计量而非效应量，偏差有限，但方向是**乐观**的"
                "（实测 ρx 偏低会抬高功效）。要更严格就用外部数据或先验值"
            )
    if weighted:
        split = (f"n ÷（整群设计效应 × 权重设计效应）= {n} ÷（{de:.2f} × {wd:.2f}）" if n_clusters
                 else f"n ÷ 权重设计效应 = {n} ÷ {wd:.2f}")
        bits.append(
            f"权重设计效应（Kish）{wd:.2f}" + (f"（按 `{weight_column}`）" if weight_column else "")
            + f"：权重越不均，同样的行数含的信息越少；功效口径的有效样本量 = {split}，**降到 {eff_n:.0f}**"
              + "（这个数同时折算了整群与权重两个设计效应；`oss run` 诊断里的「加权有效样本量」只折算权重，比它大）"
        )
    bits.append(
        f"功效 {power:.2f}（双侧检验，非中心参数 = d·√n_eff/2，**正态近似**——"
        f"未用 t 分布，簇数少时会略微高估。"
        + ("有效样本量按 n ÷（整群设计效应 × 权重设计效应）折算，" if weighted else "有效样本量按 n/设计效应 折算，")
        + f"**未考虑簇不平衡**，不平衡会让真实{'整群' if weighted else ''}设计效应更大），"
        f"{discipline or '默认'} 学科的灰区是 [{band[0]}, {band[1]}]"
        f"（上下界尚未校准，见 DECISIONS.md 9.2）"
    )
    sens_table, sens_notes, fragile = sensitivity(
        effect_size, n, metric=effect_metric, alpha=alpha, n_clusters=n_clusters,
        icc=icc, treatment_icc=treatment_icc, discipline=discipline, weight_design_effect=wd,
    )
    if fragile:
        bits.append(
            "⚠ **判词对假定参数敏感**——" + "。".join(sens_notes)
            + f"。折价系数的依据：{HAIRCUT_BASIS}"
        )
    else:
        bits.append("判词对扫过的假定参数都不敏感：" + "。".join(sens_notes))

    if downgraded:
        bits.append(
            f"判词按功效值本该是 pass（{power:.2f} 已过上界 {band[1]}），**因效应量无出处降为灰区**。"
            "补上出处重算即可恢复；不补就照灰区办——写进局限，说明这个数是假定值"
        )
    elif v == "gray":
        bits.append(
            "落在灰区。**这一档没有标准答案**——0.79 和 0.81 没有实质差别，"
            "所以不由工具箱下结论，交给跨模型判"
        )

    return PowerReport(
        idea_id=idea_id,
        effect_size_raw=effect_size,
        effect_size_adjusted=adjusted,
        haircut=EFFECT_SIZE_HAIRCUT,
        n_raw=int(n),
        n_effective=int(round(eff_n)),
        mde=mde(eff_n, alpha),
        n_clusters=n_clusters,
        icc=icc if n_clusters else 0.0,
        effect_metric=effect_metric,
        effect_size_source=effect_size_source.strip(),
        treatment_icc=round(treatment_icc, 4) if n_clusters else 1.0,
        treatment_icc_source=treatment_icc_source or ("默认假定簇内不变" if n_clusters else ""),
        design_effect=round(de, 4),
        weight_design_effect=round(wd, 4),
        weight_column=weight_column,
        sensitivity="。".join(sens_notes) + f"。折价系数的依据：{HAIRCUT_BASIS}",
        sensitivity_table=sens_table,
        verdict_fragile=fragile,
        alpha=alpha,
        power=power,
        verdict=v,
        gray_band=band,
        discipline=discipline,
        # 用换行分条，**不用「；」**——判词正文里本来就有分号（文献引用、并列说明），
        # 拿分号当分隔符会把一条判词劈成两半显示。分隔符必须是正文里不会出现的东西。
        reason="\n".join(bits),
    )


# ------------------------------------------------------------------ 描述研究：精度代替功效

#: 描述类精度专用的度量：有意义的差别按**结局原单位**给，由调用方按结局的（加权）标准差折成标准差单位。
#: **不进 EFFECT_METRICS**：功效模式的效应量是从文献搬来的标准化数，`to_cohens_d` 见了 raw 照旧拒——
#: 放进去就等于让功效模式悄悄收一个没有标准差可折的数。
RAW_METRIC = "raw"


def first_two(sizes: list[int]) -> list[int]:
    """组差那一对在 `sizes` 里的下标：头两个有行的格——第一个是参照组，第二个跟它比。空格子不算。

    **跟 `describe` 的主结果同一对比**：它的参照组是排序后的第一格、主结果是第二格减参照格（oss/core/analysis.py
    `run_describe`），所以 `sizes` 要按它的顺序给（分组列的取值排序，数值列按数值、交叉时按元组）。原先取行数最大的
    两组，分组多于两格时，环节 4 判的精度跟论文报的主结果就可能不是同一对。精度与 `oss power` 算 ρx 共用这一处。
    """
    return [i for i, g in enumerate(sizes) if g > 0][:2]


class _Precision(NamedTuple):
    half: float                 # 主要比较的 95% 区间半宽（标准差为单位）
    eff: float                  # 全样本的有效样本量（ρx 取 1）
    cell: float                 # 有效样本量最小的一格（ρx 取 1）；没给分组为 0
    z: float
    de: float                   # 整群设计效应，ρx 取 1：总体水平与最小分组用
    de_gap: float               # 组差的整群设计效应，ρx = gap_rho_x
    cells: list[int]            # 有行的格在 group_sizes 里的下标
    pair: list[int]             # 组差那一对的下标：参照组、排序第二类
    smallest: int               # 有效样本量最小的一格的下标；没有分组为 -1
    kish: list[float]           # 各格的权重设计效应，跟 group_sizes 对齐
    pair_eff: tuple[float, ...]  # 组差那一对的有效样本量（整群按 de_gap 折）


def _precision_detail(
    n: int, *, n_clusters: int | None = None, icc: float = 0.05, weight_deff: float = 1.0,
    group_sizes: list[int] | None = None, alpha: float = 0.05,
    group_weight_deffs: list[float] | None = None, gap_rho_x: float = 1.0, level_main: bool = False,
) -> _Precision:
    """`level_main`：给了分组、主要比较仍是总体水平（describe 的 level / distribution，主结果是全样本均值）；
    分组只用来找最小分组。"""
    if not n > 0:
        raise ValueError(f"样本量要大于 0（拿到 {n}）：算不出精度")
    z = _z(1 - alpha / 2)
    wd = max(float(weight_deff), 1.0)
    m = n / n_clusters if n_clusters and n_clusters > 0 else 1.0
    rho = gap_rho_x if math.isfinite(gap_rho_x) else 1.0
    de, de_gap = design_effect(m, icc, 1.0), design_effect(m, icc, rho)
    eff = n / wd / de
    sizes = list(group_sizes or [])
    given = list(group_weight_deffs or [])
    # 某一格算不出自己的 Kish（没有为正的权重）就用全样本的；不到 1 按 1 算（Kish 设计效应不会小于 1）
    kish = ([max(float(k), 1.0) if k is not None and math.isfinite(k) else wd for k in given]
            if len(given) == len(sizes) else [wd] * len(sizes))
    cells = [i for i, g in enumerate(sizes) if g > 0]
    if len(cells) < 2 or level_main:
        smallest = min(cells, key=lambda i: sizes[i] / kish[i]) if cells else -1
        cell = sizes[smallest] / kish[smallest] / de if cells else 0.0
        return _Precision(z / math.sqrt(eff), eff, cell, z, de, de, cells, [], smallest, kish, ())
    pair = first_two(sizes)
    pair_eff = tuple(sizes[i] / kish[i] / de_gap for i in pair)
    smallest = min(cells, key=lambda i: sizes[i] / kish[i])
    return _Precision(z * math.sqrt(sum(1 / e for e in pair_eff)), eff, sizes[smallest] / kish[smallest] / de,
                      z, de, de_gap, cells, pair, smallest, kish, pair_eff)


def precision(
    n: int, *, n_clusters: int | None = None, icc: float = 0.05, weight_deff: float = 1.0,
    group_sizes: list[int] | None = None, alpha: float = 0.05,
    group_weight_deffs: list[float] | None = None, gap_rho_x: float = 1.0,
) -> tuple[float, float, float]:
    """描述研究的预期精度（描述骨架设计稿第四节）。返回 `(主要比较的 95% 区间半宽（标准差为单位）, 有效样本量, 最小分组的有效样本量)`。

    有效样本量跟功效同一口径：n ÷ 权重设计效应 ÷ 整群设计效应。没给分组：主要比较是总体水平，半宽 = z / √n_eff；
    给了分组：主要比较是排序第二类对参照组（排序第一类）之差——跟 `describe` 的主结果同一对比，要换参照组就在
    prep.py 重编码；半宽 = z · √(1/n₁ + 1/n₂)，最小分组（有效样本量最小的一格）单独报。

    - **各组按自己的行数算**：n_g ÷ 这一组的权重设计效应 ÷ 整群设计效应。分组列缺值的行不进任何一组，
      也不给别的组虚增（原先按非缺失行里的占比摊全样本的有效样本量，缺值越多各组越虚胖）
    - **权重**：给了 `group_weight_deffs`（各组按自己的权重算的 Kish）就用它，没给用全样本的 `weight_deff`
    - **整群**：总体水平与最小分组描述的是结局本身，ρx 取 1；组差里 ρx 用 `gap_rho_x`——分组列在簇内的组内相关，
      从数据算（`oss power --cluster-col`）。缺省 1 = 假定分组在簇内不变，保守：城乡这类个人层面的分组在区县内并不是不变的，
      CGSS 2017 真数据上按 ρx = 1 把城乡差的半宽算成 6.7 个百分点，按数据的 ρx（0.49）是 5.2，按抽样设计实算是 4.8

    样本量不大于 0 直接拒（`ValueError`）：半宽会是无穷，写不进 power.json。
    """
    p = _precision_detail(n, n_clusters=n_clusters, icc=icc, weight_deff=weight_deff, group_sizes=group_sizes,
                          alpha=alpha, group_weight_deffs=group_weight_deffs, gap_rho_x=gap_rho_x)
    return p.half, p.eff, p.cell


def analyze_precision(
    idea_id: str, effect_size: float, n: int, *, discipline: str = "", alpha: float = 0.05,
    n_clusters: int | None = None, icc: float = 0.05, effect_metric: str = "d", effect_size_source: str = "",
    weight_design_effect: float = 1.0, weight_column: str = "",
    group_sizes: list[int] | None = None, group_labels: list[str] | None = None,
    group_weight_deffs: list[float] | None = None, gap_rho_x: float = 1.0, gap_rho_x_source: str = "",
    outcome_sd: float = 0.0, outcome_sd_weighted: bool = False,
    level_main: bool = False, main_note: str = "", sample_note: str = "",
    half_override: float | None = None, half_note: str = "",
) -> PowerReport:
    """描述研究的环节 4：按精度判，不按功效判（描述骨架设计稿第四节）。

    `effect_size` 在这里读作「有意义的最小差别」，照旧折成标准差单位，**不打发表偏倚的折**（它不是文献效应量）；
    出处照旧必填，没有就封顶灰区。判词：半宽 ≤ PRECISION_PASS × 差别为 pass，≤ PRECISION_GRAY × 差别为 gray，
    再宽为 fail——**fail 在这里是「精度不足」，不停**：`oss route` 开 must 级、可处置的 precision_low。

    `effect_metric="raw"`：差别按结局原单位给，除以 `outcome_sd`（结局的加权标准差）折成标准差单位；
    给了 `outcome_sd` 时判词另写一句原单位的半宽，单位写错一眼看得出。差别是 0、样本量不大于 0 都拒（`ValueError`）。
    组差的 ρx（`gap_rho_x` 与来源）写进 `treatment_icc` / `treatment_icc_source`：在组差里，分组列就是那个「X」。

    - `sample_note`：分析样本从全文件去掉了哪些行、为什么（调用方写好），放在整群那一句前面——n 是分析样本的行数
    - `level_main`：给了分组、主要比较仍按总体水平判（describe 的 level / distribution 主结果是全样本均值），只报最小分组
    - `main_note`：主要比较那一句后面补的一句（按计划的 kind 判的依据、趋势是近似、关联计划怎么读）
    - `half_override`：半宽由别处算出（**中位数计划：自助法**，标准差单位），解析公式让位；`half_note` 写明它怎么来的。
      中位数的标准误没有解析式，照均值公式算就是把判词挂在一个算错的数上
    """
    from oss.core import descriptive_checks as dc
    sd_ok = outcome_sd > 0 and math.isfinite(outcome_sd)
    raw = effect_metric == RAW_METRIC
    if raw and not sd_ok:
        raise ValueError("有意义的差别按结局原单位给（raw）要结局的标准差才折得成标准差单位；"
                         "算不出标准差就改用标准差单位（d）")
    d = abs(effect_size) / outcome_sd if raw else to_cohens_d(effect_size, effect_metric)
    if not (d > 0 and math.isfinite(d)):
        raise ValueError(f"有意义的最小差别不能是 0（拿到 {effect_size}）：它是这项描述要分辨的最小差别，0 什么也分辨不了")
    wd = max(float(weight_design_effect), 1.0)
    weighted = bool(weight_column) or wd > 1.0
    p = _precision_detail(n, n_clusters=n_clusters, icc=icc, weight_deff=wd, group_sizes=group_sizes, alpha=alpha,
                          group_weight_deffs=group_weight_deffs, gap_rho_x=gap_rho_x, level_main=level_main)
    half, eff, cell = p.half, p.eff, p.cell
    if half_override is not None and math.isfinite(half_override) and half_override > 0:
        half = float(half_override)      # 中位数：自助法算的半宽，解析公式让位
    ratio = half / d
    # 上面两道拒绝之后到不了这里：留着兜底——power.json 里不许有无穷与非数（存成 null 就读不回来）
    if not all(math.isfinite(v) for v in (half, eff, cell, ratio)):
        raise ValueError(f"精度算出了无穷或非数（半宽 {half}、有效样本量 {eff}）：先查数据与样本筛选")
    verdict: Verdict = "pass" if ratio <= dc.PRECISION_PASS else ("gray" if ratio <= dc.PRECISION_GRAY else "fail")
    sourced = bool(effect_size_source.strip())
    capped = not sourced and verdict == "pass"
    if capped:
        verdict = "gray"
    sizes = list(group_sizes or [])
    labels = list(group_labels or [])
    named = len(labels) == len(sizes)
    label = labels[p.smallest] if named and p.smallest >= 0 else ""
    grouped = len(p.cells)
    own_w = bool(group_weight_deffs) and len(group_weight_deffs or []) == len(sizes)
    gap_on = len(p.pair) == 2 and bool(n_clusters)      # 主要比较是组差才另算组差的整群
    rho = max(0.0, min(1.0, gap_rho_x)) if math.isfinite(gap_rho_x) else 1.0
    rho_src = gap_rho_x_source or (
        "由调用方给定" if rho < 1.0 else
        "只知道簇数、不知道哪一列是簇：假定分组在簇内不变、ρx 取 1——**保守**，组差的区间可能偏宽；"
        "给 --cluster-col 就从数据算")
    z = p.z

    if raw:
        head = (f"有意义的最小差别 {effect_size}（结局原单位）= {d:.3f} 个标准差"
                f"（结局{'加权' if outcome_sd_weighted else ''}标准差 {outcome_sd:.3f}）"
                + (f"，出处：{effect_size_source.strip()}" if sourced else "——**没有给出处**"))
        bits = [
            f"{head}。**不打发表偏倚的折**：它不是文献效应量，是这项描述要分辨的最小差别。"
            "描述研究按精度判、不按功效判：主要比较的 95% 区间够不够窄，拿它比"
        ]
    else:
        head = (f"有意义的最小差别 {effect_size}（出处：{effect_size_source.strip()}）" if sourced
                else f"有意义的最小差别 {effect_size}——**没有给出处**")
        bits = [
            f"{head}，度量为 **{EFFECT_METRICS[effect_metric]}**，折算成 Cohen's d → {round(d, 4)}"
            "（**不打发表偏倚的折**：它不是文献效应量，是这项描述要分辨的最小差别）。"
            "描述研究按精度判、不按功效判：主要比较的 95% 区间够不够窄，拿它比"
        ]
    if not sourced:
        bits.append(
            "⚠ **有意义的差别没有出处。**判词整个建立在这个数上，它凭什么算「有意义」没有记录——审稿人一定会问。"
            "补出处或理由：`oss power --effect <值> --effect-source \"<哪份文献、哪条政策线或哪条理由>\"`"
            + ("；在此之前判词封顶为灰区（本可判 pass）" if capped else "")
        )
    if sample_note:
        bits.append(sample_note)
    if n_clusters:
        bits.append(
            f"{n} 个观测聚类到 {n_clusters} 个单位（每簇均 {n / n_clusters:.1f}），残差 ICC={icc}；"
            f"描述的是结局本身，整群设计效应里 ρx 取 1：1+(m−1)·ρu = {p.de:.2f}"
            + ("（总体水平与最小分组用它）" if gap_on else "")
        )
        if gap_on:
            bits.append(
                f"组差另算整群：分组在簇内的组内相关 ρx = {rho:.3f}（{rho_src}），"
                f"组差的整群设计效应 = 1+(m−1)·ρx·ρu = {p.de_gap:.2f}"
                + ("。ρx 是从待分析的同一份数据算的：它是设计量不是结果，偏差有限" if "算出" in rho_src else "")
            )
    if weighted:
        shown = list(dict.fromkeys([*p.pair, p.smallest])) if own_w and grouped else []
        per = "、".join(f"{labels[i] if named else i} {p.kish[i]:.2f}" for i in shown if i >= 0)
        bits.append(
            f"权重设计效应（Kish）{wd:.2f}" + (f"（按 `{weight_column}`）" if weight_column else "")
            + "：权重越不均，同样的行数含的信息越少"
            + (f"；分组按各组自己的权重另算 Kish（{per}），{'组差与最小分组' if p.pair else '最小分组'}用它，不用全样本的"
               if per else "")
        )
    bits.append(
        f"有效样本量 = n ÷（整群设计效应 × 权重设计效应）= {n} ÷（{p.de:.2f} × {wd:.2f}）= {eff:.0f}（跟功效同一口径）"
    )
    if len(p.pair) == 2:
        a, b = p.pair
        pair = f"（{labels[b]} 对 {labels[a]}）" if named else ""
        n1, n2 = p.pair_eff
        bits.append(
            f"主要比较：排序第二类对参照组（排序第一类）之差{pair}，跟 describe 的主结果同一对比；"
            f"95% 区间半宽 = z·√(1/n₁+1/n₂) = {half:.3f} 个标准差"
            f"（z = {z:.2f}；n₁ = {n1:.0f}、n₂ = {n2:.0f} 是这两组各自的有效样本量：按各组自己的行数"
            + ("与权重" if own_w else "") + "算，分组列缺值的行不进任何一组）"
        )
    else:
        bits.append(f"主要比较：总体水平，95% 区间半宽 = z/√n_eff = {half:.3f} 个标准差（z = {z:.2f}）")
    if main_note:
        bits.append(main_note)
    if sd_ok:
        bits.append(
            f"半宽 {half:.3f} 个标准差 ≈ {half * outcome_sd:.3f}（结局原单位），按结局"
            f"{'加权' if outcome_sd_weighted else ''}标准差 {outcome_sd:.3f} 换算"
            + ("" if raw else "（--effect 按标准差单位读；想按结局原单位给差别就加 --effect-metric raw）")
        )
    if half_note:
        bits.append(half_note)
    bits.append(
        f"半宽是有意义差别的 {ratio:.2f} 倍：不超过 {dc.PRECISION_PASS:g} 倍算够、不超过 {dc.PRECISION_GRAY:g} 倍算灰区、"
        f"再宽算精度不足（{dc.PRECISION_PASS:g} = 1.96 ÷ 2.8，跟「80% 功效」一样松紧；初值，尚未校准）。"
        "**正态近似**，未用 t 分布；未考虑簇不平衡"
    )
    if grouped:
        bits.append(
            f"最小分组 {label or '（没给标签）'}：有效样本量约 {cell:.0f}"
            + (f"，少于 {dc.SMALL_CELL_NEFF}——这一格的数不稳，合并分组或不报这几格（门槛是初值，无文献出处）"
               if cell < dc.SMALL_CELL_NEFF else "")
        )
    if verdict == "fail":
        bits.append(
            f"精度不足：这份数据分不出小于 {half / dc.PRECISION_PASS:.2f} 个标准差的差别——收窄题目或写进局限，不停"
            "（描述研究「分不出差别」本身是一个结果，不是设计失败）"
        )
    elif capped:
        bits.append(
            f"判词按精度本该是 pass（半宽是差别的 {ratio:.2f} 倍，不超过 {dc.PRECISION_PASS:g}），"
            "**因有意义的差别无出处降为灰区**。补上出处或理由重算即可恢复；不补就照灰区办——写进局限，说明这个数是假定值"
        )
    elif verdict == "gray":
        bits.append(
            f"落在灰区：半宽介于有意义差别的 {dc.PRECISION_PASS:g} 与 {dc.PRECISION_GRAY:g} 倍之间。"
            "写进局限——一句话说清这份数据分辨得出多小的差别"
        )

    return PowerReport(
        idea_id=idea_id,
        effect_size_raw=effect_size,
        effect_size_adjusted=round(d, 4),
        haircut=1.0,
        n_raw=int(n),
        n_effective=int(round(eff)),
        n_clusters=n_clusters,
        icc=icc if n_clusters else 0.0,
        effect_metric=effect_metric,
        effect_size_source=effect_size_source.strip(),
        treatment_icc=round(rho, 4) if gap_on else 1.0,
        treatment_icc_source=rho_src if gap_on else ("描述的是结局本身，ρx 取 1" if n_clusters else ""),
        design_effect=round(p.de, 4),
        weight_design_effect=round(wd, 4),
        weight_column=weight_column,
        mode="precision",
        precision_halfwidth=round(half, 4),
        min_cell_n_eff=round(cell, 1),
        min_cell_label=label,
        alpha=alpha,
        power=0.0,
        verdict=verdict,
        gray_band=(dc.PRECISION_PASS, dc.PRECISION_GRAY),
        discipline=discipline,
        reason="\n".join(bits),
    )
