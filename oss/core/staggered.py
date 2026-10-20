"""分批双重差分：Callaway–Sant'Anna (2021)。算法来自 StatsPAI，判断和留痕是我们的。

**为什么要有这个模块。**验收跑的论文用双向固定效应（TWFE）做了 17 个批次的事件研究，
各批效应从 +1.1 到 −3.3，I² = 93%——那正是 TWFE 把「已处理的早批」当成
「晚批的对照」、汇总系数在数学上不可解释的场合。评审第一条致命问题就是它：
「作者把异质性诊断出来了，却没换估计量」。诊断出病不治，等于没诊断。

**为什么用 StatsPAI 而不是自己写。**Callaway–Sant'Anna 的双重稳健估计、影响函数标准误、
各种汇总（按批、按相对期）加起来一千多行，AERS 项目的 StatsPAI（MIT）已经写好并对照过
R 的 `did` 包。自己写省不了多少，出错的机会多得多。

**但「用它算」不等于「信它」**：这个模块把它的输出翻译成我们的 `AnalysisResults`，
每一处都有独立的对照测试（`tests/test_staggered.py`）——真实效应是我们设定的，
算出来必须等于它；无协变量、平衡面板时按批的 ATT 有闭式解，程序算的必须跟手算的一样。

**它只算，不判。**StatsPAI 自带「自动识别设计 → 推荐方法」，我们不用——
用哪个估计量由说明书和 `oss route` 决定，这里只执行。
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from oss.core.contracts import AnalysisPlan, AnalysisResults, Estimate, EventStudyPoint

INSTALL_HINT = "pip install 'opensocialscience[causal]'（或 pip install statspai）"

#: 识别假设的白话版。StatsPAI 给的是英文原文，论文里可以直接引；这里的中文是给判断用的。
_PT_LABELS = {
    "nevertreated": "平行趋势只需对「从未被处理」的单位成立；不限制处理前趋势",
    "notyettreated": "平行趋势对「尚未被处理」的单位成立——**每一期的对照组都在变**，"
                     "全部单位最终都被处理时只能这么比，结论要更谨慎",
}


def available() -> bool:
    try:
        import statspai  # noqa: F401
    except ImportError:
        return False
    return True


def _version() -> str:
    try:
        import statspai
        return getattr(statspai, "__version__", "?")
    except ImportError:
        return "未安装"


#: 在单位之外再聚类时走 StatsPAI 的乘数自助法，种子固定：同一份数据每次跑出同一个数
_BOOT_SEED = 0


def _cluster_col(plan: AnalysisPlan) -> str | None:
    """StatsPAI 在单位之外再按哪一列聚类（`clustervars`）。

    声明了初级抽样单位就按它——StatsPAI 不认分层，按抽样单位聚类是按设计的近似；没声明就按
    `cluster_level`。跟单位相同就不传：影响函数本来就按单位聚类。
    """
    psu = plan.survey.psu if plan.survey is not None else ""
    col = psu or plan.cluster_level
    return col if col and col != plan.unit else None


def _prepare(frame: pd.DataFrame, plan: AnalysisPlan) -> tuple[pd.DataFrame, list[str], int]:
    """筛选样本、造出 StatsPAI 要的 `_g`（0 = 从未处理）和 `_t`，丢掉缺失行。

    权重列与聚类列（`_cluster_col`）一起带上、一起去缺失。**两者都要在单位内不变**：
    StatsPAI 的 `weights` 是单位层面的抽样权重（文档原话「unit-level sampling / population
    weights」，「Must be constant within unit; a time-varying column raises」），聚类也要求
    每个单位只落在一个簇里。这里先拦下，用中文说清楚在 prep.py 里怎么改。
    按抽样单位聚类且写了分层时，抽样单位编号嵌进层里（同一个编号出现在两层算两个单位，
    跟 `analysis._design_vcov` 同一口径）——GSS 那种每层都编 1、2 的，不嵌就只剩两个簇。

    返回 `(工作表, 协变量列表, 丢掉的行数)`。
    """
    from oss.core import analysis as A

    df = frame.query(plan.sample_filter) if plan.sample_filter else frame
    df = A._expand_interactions(df, [c for c in plan.controls if A.is_interaction(c)])
    controls = list(plan.controls)
    cluster = _cluster_col(plan)
    strata = plan.survey.strata if cluster and plan.survey is not None and plan.survey.psu == cluster else ""
    extra = [c for c in (plan.weights, cluster, strata) if c]
    needed = [plan.outcome, plan.unit, plan.time, plan.treat_time, *controls]
    needed += [c for c in dict.fromkeys(extra) if c not in needed]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise A.PlanRejected([f"数据里没有这些列：{'、'.join(missing)}"])
    work = df[needed].copy()
    work["_t"] = pd.to_numeric(work[plan.time], errors="coerce")
    # 缺失 = 从未处理，跟 `did` / `event_study` 同一约定；StatsPAI 用 0 表示
    work["_g"] = pd.to_numeric(work[plan.treat_time], errors="coerce").fillna(0)
    for c in (plan.outcome, *controls):
        work[c] = pd.to_numeric(work[c], errors="coerce")
    if plan.weights:
        work[plan.weights] = A._positive_weights(work, plan.weights)
    before = len(work)
    work = work.dropna(subset=[plan.outcome, "_t", *controls, *extra])
    # 先逐列查单位内变不变，再把抽样单位编号嵌进层里：嵌完再查的话，分层在单位内变会被说成抽样单位在变，点错了列。
    # 权重那一种单独一个类：伴随主规格的分批事件研究认得它，不加权照跑（`analysis._attach_event_study`）
    checks = (
        (plan.weights, "did_cs 的权重要在单位内不变（StatsPAI 按单位层面的抽样权重算）",
         "在 prep.py 里取基期的权重造一列单位层面的权重再跑", A.WeightsVaryWithinUnit),
        (strata, f"按抽样设计聚类时分层 `{strata}` 要在单位内不变（抽样单位的编号嵌在层里，层一变编号就跟着变）",
         f"在 prep.py 里取基期的 `{strata}` 造一列单位层面的再跑", A.PlanRejected),
        (cluster, f"按 `{cluster}` 聚类要每个单位只落在一个簇里（StatsPAI 的限制）",
         f"在 prep.py 里取基期的 `{cluster}` 造一列单位层面的再跑", A.PlanRejected),
    )
    for col, what, fix, refuse in checks:
        if not col:
            continue
        spread = work.groupby(plan.unit)[col].nunique()
        varying = spread[spread > 1]
        if len(varying):
            shown = "、".join(map(str, varying.index[:3]))
            raise refuse([f"{what}：`{col}` 在 {len(varying)} 个单位内会变（如 {shown}）——{fix}"])
    if strata:
        work[cluster] = work.groupby([strata, cluster], sort=False).ngroup()
    return work, controls, before - len(work)


def _k_of(term: str) -> int | None:
    """StatsPAI 的事件研究行叫 `event_-3` / `event_+0`。"""
    if not term.startswith("event_"):
        return None
    try:
        return int(term[len("event_"):])
    except ValueError:
        return None


def _statspai_pre_ks(r: object) -> list[int]:
    """`sp.pretrends_power` 自己会读的处理前期——跟我们窗口化的 `leads` 不是一回事。

    它先看 `r.model_info['event_study']`，没有就退到 `r.detail`；取 `relative_time < 0`
    的行，按时间升序；参照期（系数固定为 0、标准误为 0）排除——这正是它内部
    `_pre_arrays` 的 `estimated = se > 0` 那道过滤。`delta` 的长度得跟它这里数出来的
    一样，不然它会拿我们窗口化的 `leads`（只有 3~4 期）去比它自己算出的更长的历史
    （比如 11 期），维度不对只会报「没算」。
    """
    info = getattr(r, "model_info", None)
    es = info.get("event_study") if isinstance(info, dict) else None
    if not isinstance(es, pd.DataFrame):
        es = r.detail() if callable(getattr(r, "detail", None)) else getattr(r, "detail", None)
    if not isinstance(es, pd.DataFrame):
        return []
    time_col = next((c for c in ("relative_time", "rel_time", "event_time", "t", "time", "period")
                     if c in es.columns), None)
    se_col = next((c for c in ("se", "std_error", "std.error", "stderr", "std_err")
                  if c in es.columns), None)
    if time_col is None:
        return []
    pre = es[pd.to_numeric(es[time_col], errors="coerce") < 0].sort_values(time_col)
    if se_col is not None:
        pre = pre[pd.to_numeric(pre[se_col], errors="coerce") > 0]
    return [int(round(float(t))) for t in pre[time_col]]


def _cohort_members(work: pd.DataFrame, plan: AnalysisPlan, *, cap: int = 12) -> str:
    """每一批里有谁。**评审看批次表就能想到「这些是后共产主义国家」——它得先看得见名单。**"""
    per_unit = work.groupby(plan.unit)["_g"].first()
    per_unit = per_unit[per_unit > 0]
    bits = []
    for g, members in per_unit.groupby(per_unit):
        names = [str(u) for u in members.index]
        shown = "、".join(names[:cap]) + (f"……共 {len(names)} 个" if len(names) > cap else "")
        bits.append(f"{g:.0f} 批（{len(names)} 个单位）：{shown}")
    return "；".join(bits)


def run_cs(frame: pd.DataFrame, plan: AnalysisPlan) -> AnalysisResults:
    """Callaway–Sant'Anna 分批双重差分。`main` 是处理后各观测的平均处理效应。

    - 有从未被处理的单位就只拿它们当对照；一个都没有时退到「尚未被处理」，并写明
    - 逐期效应以处理前一期为参照（跟 `event_study` 同一约定，图能直接画）
    - 每一批各自的效应进 `estimates["cohort_<年>"]`——**分批异质性不再只是一句警告**
    - 处理前平不平行：逐期判 + 窗口内各事件期的联合检验（`_joint_pretrend`），两条都写进
      `diagnostics.平行趋势`。**联合检验先把（批 × 期）格子聚合到事件期再做**——逐格子那一版
      矩条件多、聚类少，尺寸是错的
    """
    from oss.core import analysis as A

    if not available():
        raise A.PlanRejected([f"did_cs 需要 StatsPAI，没装。装：{INSTALL_HINT}"])
    import statspai as sp

    work, controls, dropped = _prepare(frame, plan)
    per_unit = work.groupby(plan.unit)["_g"].first()
    never = int((per_unit == 0).sum())
    ever = int((per_unit > 0).sum())
    if ever == 0:
        raise A.PlanRejected([f"「{plan.treat_time}」全是缺失——没有任何单位被处理，分批估计无从谈起"])
    control_group = "nevertreated" if never > 0 else "notyettreated"
    cluster = _cluster_col(plan)
    # 在单位之外再聚类，StatsPAI 只认乘数自助法（解析标准误不管簇，传了 clustervars 不开自助法它就报错）；
    # 汇总也得走自助法，不然 aggte 的解析标准误又把簇丢了
    boot = dict(bstrap=True, random_state=_BOOT_SEED) if cluster else dict(bstrap=False)

    with warnings.catch_warnings():
        # numpy 1.26 在 macOS 上对含 NaN 的矩阵乘法误报 divide/overflow/invalid，
        # 结果与 numpy 2 下一致。压掉警告，但**结果必须是有限数**（见下）。
        warnings.simplefilter("ignore", RuntimeWarning)
        r = sp.callaway_santanna(
            work, y=plan.outcome, g="_g", t="_t", i=plan.unit,
            x=controls or None, weights=plan.weights or None,
            control_group=control_group, clustervars=cluster, **boot,
            allow_unbalanced_panel=True, base_period="universal",
            pretest="joint", pretest_periods=plan.leads,
        )
        # **汇总不能直接拿 r.estimate。**没有从未处理单位时，晚批处理后没有任何对照，
        # 那些（批 × 期）格子 StatsPAI 记成「效应 0、标准误 ∞」，而它的头条数把这些 0
        # 平均了进去——干净夹具上 3.77 变成 0.84，不报错。`aggte` 会把这些格子丢掉。
        try:
            simple = sp.aggte(r, type="simple", cband=False, **boot)
        except Exception as exc:  # noqa: BLE001
            raise A.PlanRejected([f"分批估计汇总不了（{type(exc).__name__}: {exc}）"]) from exc
        try:
            grp = sp.aggte(r, type="group", cband=False, **boot).tidy()
        except Exception:  # noqa: BLE001 —— 按批汇总算不出来不该让主结果挂掉
            grp = pd.DataFrame(columns=["term", "estimate", "std_error", "p_value", "type"])

    if not (np.isfinite(simple.estimate) and np.isfinite(simple.se) and simple.se > 0):
        raise A.PlanRejected([
            f"分批估计算出了非数（ATT {simple.estimate}，标准误 {simple.se}）——"
            f"通常是某一批只有一个单位、或处理后没有任何可比的对照。不能拿这种结果写论文"])

    res = AnalysisResults(plan_id=plan.plan_id, n=int(r.n_obs))
    # term 用英文：它会进回归表和 {{result:main.term}}，英文稿里不能出现中文
    res.estimates["main"] = Estimate(
        term="ATT",
        coef=float(simple.estimate), se=float(simple.se), p=float(simple.pvalue),
    )

    # 哪些（批 × 期）算不了。**算不了的格子不进平均，但必须写出来**——
    # 否则读者以为 main 覆盖了所有批次，其实晚批整个没算。
    lost: dict[float, list[int]] = {}
    detail = r.detail() if callable(getattr(r, "detail", None)) else getattr(r, "detail", None)
    if isinstance(detail, pd.DataFrame) and {"group", "time", "se"} <= set(detail.columns):
        post = detail[detail["time"] >= detail["group"]]
        for _, row in post[~np.isfinite(post["se"].astype(float))].iterrows():
            lost.setdefault(float(row["group"]), []).append(int(row["time"]))

    # 各批各自的效应
    got: list[tuple[float, float, float, int]] = []
    sizes = per_unit[per_unit > 0].value_counts()
    for _, row in grp[grp["type"] == "group_time"].iterrows():
        term = str(row["term"])                       # att(g=2010.0)
        try:
            g = float(term.split("g=")[1].rstrip(")"))
        except (IndexError, ValueError):
            continue
        se = float(row["std_error"])
        if not (np.isfinite(row["estimate"]) and np.isfinite(se)):
            continue
        n_members = int(sizes.get(g, 0))
        res.estimates[f"cohort_{g:.0f}"] = Estimate(
            term=f"ATT, cohort {g:.0f}", coef=float(row["estimate"]), se=se, p=float(row["p_value"]))
        if se > 0:
            got.append((g, float(row["estimate"]), se, n_members))
    if lost:
        res.diagnostics["算不了的格子"] = "；".join(
            f"{g:.0f} 批在 {min(ts)}–{max(ts)} 年没有可比的对照（{len(ts)} 期）" +
            ("——**这一批整个没算**，main 不包含它" if f"cohort_{g:.0f}" not in res.estimates else "")
            for g, ts in sorted(lost.items())
        ) + "。这些格子不进平均；论文里要说明 main 只覆盖能算的部分"

    # 逐期效应，只报窗口内的（窗外的期多半只剩一两批，噪声大且批次构成在变）
    tidy = r.tidy()
    rel = work["_t"] - work["_g"]
    treated_rel = rel[work["_g"] > 0]
    points = []
    for _, row in tidy[tidy["type"] == "event_study"].iterrows():
        k = _k_of(str(row["term"]))
        if k is None or k < -plan.leads or k > plan.lags:
            continue
        se = float(row["std_error"])
        if not (np.isfinite(row["estimate"]) and np.isfinite(se)):
            continue
        points.append(EventStudyPoint(
            k=k, coef=float(row["estimate"]), se=se, n=int((treated_rel == k).sum())))
    res.event_study = sorted(points, key=lambda p: p.k)
    n_all = int((tidy["type"] == "event_study").sum())

    # ---- 诊断
    res.diagnostics["估计量"] = (
        f"Callaway–Sant'Anna (2021) 分批双重差分（双重稳健；"
        f"对照组：{'从未被处理的单位' if control_group == 'nevertreated' else '尚未被处理的单位'}"
        f"{'；协变量：' + '、'.join(controls) if controls else ''}）。算法：StatsPAI {_version()}"
    )
    pt = (r.model_info or {}).get("parallel_trends", {}) if hasattr(r, "model_info") else {}
    res.diagnostics["识别假设"] = _identification_note(control_group, pt)
    res.diagnostics["处理定义"] = (
        f"「{plan.treat_time}」给出每个单位开始被处理的时间；缺失视为从未处理")
    res.diagnostics["实施批次"] = f"{len(sizes)} 批"
    res.diagnostics["批次成员"] = _cohort_members(work, plan)
    res.diagnostics["对照组"] = (
        f"{never} 个单位从未被处理（作为唯一对照），{ever} 个单位在某个时点被处理"
        if never else
        f"**没有从未被处理的单位**（{ever} 个单位全部最终被处理）——改用「尚未被处理」的单位"
        f"当对照，每一期的对照组都不同；**这仍是分批偏误最重的情形**，结论要谨慎"
    )
    res.diagnostics["汇总方式"] = (
        "main 是处理后各（批 × 期）效应按观测数加权的平均；各批的 cohort_<年> 是该批处理后各期的平均。"
        "两者不必相等——早批处理后的期数多，在 main 里权重就大")
    res.diagnostics["抽样权重"] = A._weights_text(
        work[plan.weights].to_numpy(dtype=float) if plan.weights else None, plan.weights, len(work))
    psu = plan.survey.psu if plan.survey is not None else ""
    if psu or cluster:
        col = psu or cluster
        n_cl = int(work[col].nunique())
        how = "StatsPAI 乘数自助法" if cluster else "StatsPAI 影响函数的解析标准误，本来就按单位聚类"
        if psu:
            strata = plan.survey.strata
            # StatsPAI 不认分层：按抽样单位聚类是按设计的近似，表注照这样写（manuscript._notes）
            res.diagnostics["标准误"] = (
                f"按初级抽样单位 {psu} 聚类{'近似' if strata else ''}（{n_cl} 个抽样单位；{how}）"
                + (f"，分层 {strata} 没进方差（StatsPAI 不认分层）" if strata else ""))
            res.diagnostics["抽样单位数"] = str(n_cl)
        else:
            res.diagnostics["标准误"] = f"聚类到 {cluster}（{n_cl} 个簇；{how}）"
    if dropped:
        extra = "、".join(c for c in (plan.weights and "权重", cluster and "聚类列") if c)
        res.diagnostics["缺失处理"] = (f"结果、控制变量或{extra}缺失，丢 {dropped} 行" if extra
                                   else f"结果或控制变量缺失，丢 {dropped} 行")
    if n_all > len(points):
        res.diagnostics["逐年展开"] = (
            f"共估计 {n_all} 期，只报窗口内（前 {plan.leads} / 后 {plan.lags}）的 {len(points)} 期；"
            f"窗外的期只剩少数批次，噪声大。画图：oss figure event")
    else:
        res.diagnostics["逐年展开"] = f"{len(points)} 期，以处理前一期为参照。画图：oss figure event"

    from oss.core import causal_checks as cc

    jt = _joint_pretrend(r, res.event_study)
    if jt is None:                                    # 拿不到影响函数，退到 StatsPAI 自己那一版
        jt = (r.diagnostics or {}).get("pretrend_test") if isinstance(getattr(r, "diagnostics", None), dict) else None
        if not isinstance(jt, dict):
            try:
                jt = r.pretrend_test() if callable(getattr(r, "pretrend_test", None)) else None
            except Exception:  # noqa: BLE001
                jt = None
    res.diagnostics["平行趋势"] = _trend_verdict(res.event_study, jt, plan.leads)

    m = res.estimates["main"]
    res.diagnostics.update(cc.pretrend_texts(res.event_study, None, m.coef, m.se, plan.lags))
    # 预检功效：分批路径没有我们自己的协方差，用 StatsPAI 在它自己的结果对象上算（Roth 2022）。
    # delta 要按 StatsPAI 自己读的那些处理前期给（`_statspai_pre_ks`），不是我们窗口化的
    # `leads`——两者维度经常不一样，用错了只会「没算」，不是它算不出来
    own_pre = _statspai_pre_ks(r)
    diag_fallback = False
    try:
        delta = cc.linear_violation(own_pre, m.coef, plan.lags)   # 顺序按 Step 0 核过的约定排
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            power_out = sp.pretrends_power(r, delta=delta, test="joint")
        diag_fallback = any("no pre-period covariance matrix found" in str(w.message) for w in caught)
        pw = float(power_out["power"])                            # 键名按 Step 0 核过的写
    except Exception:  # noqa: BLE001 —— 预检功效算不出来不该让主结果挂掉
        pw = None
    if pw is not None and np.isfinite(pw):
        lo, hi = cc.PRETREND_POWER_BAND
        grade = "够" if pw >= hi else ("灰区" if pw >= lo else "不足")
        # 说明协方差从哪来：`callaway_santanna` 不写 `vcv_pre`，StatsPAI 只能退到对角线近似——
        # 说它「按 Callaway–Sant'Anna 的协方差算」是编的，得照它实际用的那份说
        cov_note = ("StatsPAI 拿不到处理前各期的协方差，按对角线近似算" if diag_fallback
                    else "StatsPAI 按 Callaway–Sant'Anna 的协方差算")
        res.diagnostics["预检功效"] = f"{pw:.2f}"
        res.diagnostics["预检功效说明"] = (
            f"对「大到足以抵消估计效应」的线性违背，联合预检有 {pw:.0%} 的把握查出来（{grade}；灰区 {lo:g}–{hi:g}；"
            f"{cov_note}）。**功效不足时，预检没拒绝不能当平行趋势的证据**（Roth 2023）")

    with_treat = A._treat_cols(frame.query(plan.sample_filter) if plan.sample_filter else frame, plan)
    res.diagnostics["处理前平衡"] = A._pre_balance(with_treat, plan)
    res.diagnostics["批次异质性"] = (
        A._heterogeneity_text(got) if len(got) >= 2 else "只有一批，不存在分批偏误")
    res.robustness = A._run_robustness(frame, plan)
    return res


def _identification_note(control_group: str, pt: dict | None) -> str:
    """识别假设那一句。**后端不给自己的表述时，不许拼出空括号。**

    StatsPAI 在 `allow_unbalanced_panel=True` 下不返回 `model_info["parallel_trends"]`，
    原先无条件拼 `（{label}）。原文：{statement}`，拼出来是
    「……不限制处理前趋势（）。原文：」——括号空着、「原文：」后面什么都没有。
    验收跑 r20260826_edufert 与 r20260907_edufert_zh 交付的 results.json 就是这样。

    这一句是九条标准第 5 条（设计的边界写进局限）的原料：**没有原文可引，
    执行者要么略过这一节，要么自己编一句英文**——所以缺了要明说是后端没给。
    中文那半句是我们自己的，任何时候都成立，照常给出。
    """
    label = str((pt or {}).get("label", "")).strip()
    statement = str((pt or {}).get("statement", "")).strip()
    head = _PT_LABELS[control_group]
    if not label and not statement:
        return (f"{head}。（StatsPAI 未随本次估计返回它自己的表述——"
                f"非平衡面板下常缺。论文里引上面这句中文即可，**不要凭空造英文原文**）")
    tail = f"（{label}）" if label else ""
    return f"{head}{tail}。" + (f"原文：{statement}" if statement else "")


def _joint_pretrend(r: object, points: list[EventStudyPoint]) -> dict | None:
    """处理前的联合检验：**先把（批 × 期）格子聚合到事件期，再做 Wald**。

    返回 `{"pvalue", "df", "clusters"}`；拿不到影响函数时返回 None（调用方退回 StatsPAI 自己那一版）。

    **为什么不能直接用逐格子的那一版。**StatsPAI 的 `pretest="joint"` 检验的是每一个
    （批 × 期）格子，格子数随批次数成倍长而聚类数不变：120 国 5 批的面板上，8 个事前期就是
    39 个矩条件配 120 个聚类，协方差近奇异、求逆把统计量推大。2026-09-17 的尺寸模拟里，
    200 份平行趋势按构造成立的面板，5% 水平上错拒 15%（事前期 4）到 41.5%（事前期 10）——
    而聚合到事件期之后是 5.0%–7.5%。后果不是学术上的：干净的因果研究会被 must 级
    `pretrend_fail` 误降为关联，2026-09-16 的真跑里刚发生过。

    聚合用的权重跟 StatsPAI 自己画事件研究图时同一套（批规模在同一事件期内归一），所以聚合出来的
    系数**必须**逐个等于 `points` 里那几个数——对不上说明权重口径漂了，那就不报：
    **协方差得属于正在被检验的那几个系数**，凑一份不配套的比不报更坏。
    """
    from oss.core import causal_checks as cc
    from oss.core.contracts import PretrendCov

    inf = getattr(r, "_influence_funcs", None)
    info = getattr(r, "model_info", None)
    detail = r.detail() if callable(getattr(r, "detail", None)) else getattr(r, "detail", None)
    ks = [p.k for p in points if p.k < 0 and p.se > 0]
    if inf is None or not isinstance(info, dict) or not isinstance(detail, pd.DataFrame) or not ks:
        return None
    if not {"group", "relative_time", "att"} <= set(detail.columns) or inf.shape[1] != len(detail):
        return None
    sizes = info.get("cohort_sizes")
    n = int(info.get("n_units") or 0)
    if sizes is None or n <= 0:
        return None

    rel = pd.to_numeric(detail["relative_time"], errors="coerce").to_numpy()
    share = detail["group"].map(sizes).to_numpy(dtype=float)
    Wm = np.zeros((len(ks), len(detail)))
    for row, k in enumerate(ks):
        w = np.where(rel == k, share, 0.0)
        if not np.isfinite(w).all() or w.sum() <= 0:
            return None
        Wm[row] = w / w.sum()
    theta = Wm @ detail["att"].to_numpy(dtype=float)
    if not np.allclose(theta, [p.coef for p in points if p.k in ks], rtol=1e-6, atol=1e-9):
        return None

    psi = inf @ Wm.T                                  # 一行一个单位、一列一个事件期
    cluster_ids = info.get("_cluster_ids")
    if cluster_ids is not None and len(cluster_ids) == psi.shape[0]:
        # 在单位之外还按别的列聚类：影响函数先在簇内求和，分母自由度也按簇数算
        codes = pd.Categorical(cluster_ids).codes
        stacked = np.zeros((int(codes.max()) + 1, psi.shape[1]))
        np.add.at(stacked, codes, psi)
        psi = stacked
    V = psi.T @ psi / n**2
    if not np.isfinite(V).all():
        return None
    cov = PretrendCov(ks=ks, vcov=V.tolist(), n_clusters=int(psi.shape[0]))
    _, pv, df = cc.joint_pretest(points, cov)
    return {"pvalue": pv, "df": df, "clusters": cov.n_clusters}


def _trend_verdict(points: list[EventStudyPoint], joint: dict | None, leads: int) -> str:
    """处理前平不平行：**以联合检验为准**，逐期只作细节。

    逐期规则（任一期 |t| > 1.96 就判「不成立」）在 3 个处理前期上有约 14% 的误报率——
    干净的分批夹具会被它标红。联合检验问的是「这几期合起来有没有趋势」，才是该问的问题。
    没有联合检验时退回逐期规则（跟 TWFE 的逐年展开同一把尺子）。

    `joint` 带 `clusters` 的是 `_joint_pretrend` 聚合到事件期之后的那一版（自由度 = 事前期数）；
    不带的是 StatsPAI 逐（批 × 期）格子那一版（自由度 = 格子数）。**两者的说法不能混用。**
    """
    from oss.core import analysis as A
    from oss.core import causal_checks as cc

    pre = [p for p in points if p.k < 0]
    pv = joint.get("pvalue") if isinstance(joint, dict) else None
    if pv is None or not np.isfinite(pv):
        return A._parallel_trends(points)
    pv = float(pv)
    df = int(joint.get("df", 0) or 0)
    clusters = int(joint.get("clusters", 0) or 0)
    n_pre, how = ((df, f"各期效应，{cc.joint_pretest_label(df, clusters)}") if clusters
                  else (leads, f"各批 × 期，Wald，自由度 {df}"))
    detail = "；".join(f"第 {p.k} 期 {p.coef:+.3f}（{p.coef / p.se:+.1f} 个标准误）"
                      for p in pre if p.se > 0)
    single = [p for p in pre if p.se > 0 and abs(p.coef / p.se) > 1.96]
    if pv < 0.05:
        return (f"**不成立**：处理前 {n_pre} 期的联合检验（{how}）"
                f"p = {pv:.3f}，拒绝「处理前平行」——两组在政策实施**之前**就已经在分化。"
                f"**这意味着算出来的「效应」里混着本来就存在的趋势，结论不可信。**{detail}")
    head = (f"处理前 {n_pre} 期的联合检验（{how}）p = {pv:.3f}，"
            f"未拒绝，平行趋势未被推翻")
    if single:
        head += ("；" + "、".join(f"第 {p.k} 期" for p in single)
                 + "单看显著但联合检验未拒绝——更可能是噪声，看图确认")
    return f"{head}。{detail}"


def bacon_text(frame: pd.DataFrame, plan: AnalysisPlan) -> str:
    """Goodman-Bacon (2021) 分解：TWFE 的汇总系数到底是哪些「两两比较」的加权平均。

    **这是「为什么要换估计量」的证据。**「早批 vs 晚批」那一项拿已处理的单位当对照，
    它的权重越大、估计值离别的比较越远，TWFE 的系数越不可解释。
    """
    if not available():
        return f"没装 StatsPAI，算不了（装：{INSTALL_HINT}）"
    from oss.core import analysis as A
    import statspai as sp

    try:
        # 分解本身不加权也不聚类（`bacon_decomposition` 不吃权重）：不带权重列、抽样设计与聚类层进 `_prepare`，
        # 免得单位内会变的权重或聚类列把这一条诊断拦下（did 本来就允许逐行的权重）
        bare = AnalysisPlan(**{**plan.model_dump(), "weights": "", "survey": None, "cluster_level": ""})
        work, _, _ = _prepare(frame, bare)
        work["_D"] = ((work["_g"] > 0) & (work["_t"] >= work["_g"])).astype(int)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            b = sp.bacon_decomposition(work, y=plan.outcome, treat="_D", time="_t", id=plan.unit)
    except A.PlanRejected as exc:
        return f"算不出来（{exc}）"
    except Exception as exc:  # noqa: BLE001
        return f"算不出来（{type(exc).__name__}: {exc}）"

    dec = b.get("decomposition")
    if dec is None or len(dec) == 0:
        return "算不出来（没有可分解的比较）"
    by_type = dec.groupby("type").apply(
        lambda d: (float((d["estimate"] * d["weight"]).sum() / d["weight"].sum()), float(d["weight"].sum())),
        include_groups=False,
    )
    names = {"Treated vs Untreated": "处理组 vs 从未处理",
             "Earlier vs Later Treated": "早批 vs 晚批（晚批尚未处理时当对照）",
             "Later vs Earlier Treated": "晚批 vs 早批（**早批已处理，仍被当对照**）"}
    bits = [f"{names.get(t, t)}：{est:+.3f}（权重 {w:.0%}）" for t, (est, w) in by_type.items()]
    bad = float(b.get("already_treated_control_weight_share", 0.0) or 0.0)
    head = (f"**{bad:.0%} 的权重落在「已处理单位当对照」的比较上**——这部分比较在效应随时间变化时是坏的，"
            f"TWFE 汇总系数 {b['beta_twfe']:+.3f} 不可直接当作平均效应；"
            if bad > 0.1 else
            f"「已处理单位当对照」的比较只占 {bad:.0%} 权重，TWFE 汇总系数 {b['beta_twfe']:+.3f} 受分批偏误影响不大；")
    return head + "；".join(bits)
