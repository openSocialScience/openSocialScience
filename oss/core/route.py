"""诊断 → 处置。**查出病就得开方，方子得吃，吃了要复查。**

评审对第十次验收跑的判词：「这篇论文的自我诊断能力远强于它的研究设计能力。
它把自己所有的病症都准确地描述了一遍，然后没有治任何一个。」

工具箱一直在产诊断（`results.json` 的 `diagnostics`、`power.json` 的判词、因果审计的意见），
但诊断只是文字——执行者读了、写进局限、交付。这个模块把每条诊断接上一个**动作**：

| 诊断 | 处置 |
|---|---|
| 功效判 FAIL | **停**：换结局 / 换样本 / 换题目，或有文献依据地重算。不许带着 FAIL 写论文 |
| 描述研究精度不足（精度模式判 fail） | **不停**：收窄题目，或局限写明分不出多小的差别（must 级，写进局限即处置） |
| 功效报告跟计划对不上（因果计划配精度报告、描述计划配功效报告，或报告算的是另一道题） | **停**：重跑 `oss power --idea <计划的题>`（有了计划它按计划的类型分路）；驳回不了 |
| 分批实施却用 TWFE | 换估计量：`did_cs`（补丁，一键应用） |
| 平行趋势不成立 | 设计级：换窗口 / 剔除抢跑期 / 只作关联——**必须处置**，写理由 |
| 处理反转 / 面板流失 | 加一项稳健性（补丁，一键应用；列由 oss run 自动加） |
| 处理前不平衡 / 无从未处理 / 共线严重 / 缺失重 | 写进局限（处置 = 标记「已写」） |
| 稳健性里方向反转或显著性翻转 | 正文必须说——写进局限或结果 |
| 因果审计的每条设计级意见 | 写进局限（不提审计） |

规矩：**一次一处置**。应用一条 → `oss run` 重跑 → `oss route` 复查（触发消失的自动标「已解决」）。
`must` 级的处方没处置，`paper check` 拒绝、`paper export` 拒绝。
处方是机械的；**该不该吃这副药是判断**——驳回要写理由，理由留在 `route.json` 里。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from oss.core import plan_checks
from oss.core import state as state_mod
from oss.core.contracts import (
    ARTIFACTS, AnalysisPlan, AnalysisResults, BindingReport, DID_FAMILY, DataProfile, PlanCheck,
    PowerReport, RobustnessSpec, Route, RouteItem, TraceEvent,
)
from oss.core.findings import Finding

#: 驳回不了的处方：功效 FAIL 只能靠换结局 / 样本 / 题目或重算功效来消，不能「说一句理由」就过；
#: 功效报告跟计划的研究类型对不上（power_mode_mismatch），只能靠重跑 oss power 来消。
NOT_DISMISSABLE = {"power_fail", "power_mode_mismatch"}

#: power_mode_mismatch 的一种：换题后功效报告已按新题（锚点当前的题）算了，盘上的计划还是换题前那道题的
#: （F1 复审 M2-5）。触发以它开头；拦截说明据此改说先写计划，不说重跑 oss power
STALE_PLAN = "计划是换题前"


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _artifact(cls, path: Path):
    """读一份产出物，读不了、或形状对不上就给 None——**开处方这一步不该被一份坏文件带崩**。"""
    try:
        return cls.load(path)
    except Exception:                    # noqa: BLE001
        return None


def _plan_sha(path: Path) -> str:
    from oss.core.loop import plan_sha
    return plan_sha(path)


def _cohorts(results: AnalysisResults | None) -> int:
    if results is None:
        return 0
    m = re.search(r"(\d+)\s*批", results.diagnostics.get("实施批次", ""))
    return int(m.group(1)) if m else 0


def _has_robustness(plan: AnalysisPlan, needle: str) -> bool:
    return any(needle in (r.sample_filter or "") for r in plan.robustness)


def _num(d: dict, key: str) -> float | None:
    """诊断里的数值键；「没算」「∞」这类不是有限数的，一律 None。"""
    try:
        v = float(d.get(key, ""))
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _causal_items(plan: AnalysisPlan, results: AnalysisResults, power: PowerReport | None) -> list[RouteItem]:
    """因果骨架的七条处方（设计稿第四、五节）。门槛初值都在 `causal_checks`。"""
    from oss.core import causal_checks as cc

    d, out = results.diagnostics, []
    strict = (power.discipline if power else "") in ("economics", "political-science")
    F = _num(d, "有效 F")
    if F is not None and F < cc.EFFECTIVE_F_LOW:
        out.append(RouteItem(
            id="weak_iv", priority="must", kind="disclose", trigger=f"有效 F 低于 {cc.EFFECTIVE_F_LOW:g}",
            evidence=d.get("第一阶段强度", "")[:240],
            advice="主推断改用 AR 区间：正文主结果的区间引 {{diag:AR 区间}}（oss paper check 会核），"
                   "局限里写明工具弱、为什么还用这个设计。换得了借力变量就换；换不了用 --disclose 写明"))
    elif F is not None and "灰区" in d.get("第一阶段强度", ""):
        out.append(RouteItem(
            id="iv_gray", priority="should", kind="disclose", trigger="有效 F 过了下界，但常规区间与 tF 区间结论不一致",
            evidence=d.get("第一阶段强度", "")[:240],
            advice="两种区间都报，局限里写明结论不一致；这一格交识别审稿人判（DECISIONS §6 的灰区）"))
    ratio = _num(d, "2SLS/OLS")
    if ratio is not None and abs(ratio) > cc.IV_OLS_RATIO:
        out.append(RouteItem(
            id="iv_ols_gap", priority="should", kind="disclose", trigger=f"|2SLS/OLS| 大于 {cc.IV_OLS_RATIO:g}",
            evidence=d.get("OLS 对照", "")[:240],
            advice="观察研究里 2SLS 远大于 OLS 是违背无混杂或排除限制的信号（Lal 等 2024）：局限里写明，"
                   "讨论排他性为什么还站得住"))
    pw, (lo, hi) = _num(d, "预检功效"), cc.PRETREND_POWER_BAND
    if pw is not None and pw < hi and "不成立" not in d.get("平行趋势", ""):
        grade = "不足" if pw < lo else "灰区"
        out.append(RouteItem(
            id="pretrend_underpowered", priority="should", kind="disclose", trigger=f"预检功效{grade}（{pw:.2f}）",
            evidence=d.get("预检功效说明", "")[:240],
            advice="预检没拒绝不能当平行趋势的证据：正文不写「平行趋势检验通过、因此成立」，"
                   "局限写明预检对多大的违背有多少把握" + ("；这一格交识别审稿人判" if grade == "灰区" else "")))
    # breakdown 与敏感性两条只对显著的主结果开：不显著时这两个数按定义就是 0，没有结论可翻
    # （任务 13 校准：样本 4/14、留档 4/5 是这种误拦；用户 2026-09-14 定）
    main = results.estimates.get("main")
    sig = main is not None and main.p < cc.ALPHA
    bd = _num(d, "breakdown 值")
    if sig and bd is not None and bd < cc.BREAKDOWN_LOW:
        out.append(RouteItem(
            id="low_breakdown", priority="should", kind="disclose", trigger=f"breakdown 值 {bd:.2f} 小于 {cc.BREAKDOWN_LOW:g}",
            evidence=d.get("breakdown 说明", "")[:240],
            advice="处理后的违背不比处理前最大的违背大，结论就会翻：局限里写明，正文结论按这个口径收着写"))
    rva, bench = _num(d, "稳健值（显著性）"), _num(d, "敏感性基准值")
    if sig and rva is not None and bench is not None and rva < bench:
        out.append(RouteItem(
            id="weak_sensitivity", priority="must" if strict else "should", kind="disclose",
            trigger="敏感性偏弱：一个跟最强已观测控制变量一样强的混淆就能把结论削到不显著",
            evidence=d.get("敏感性分析", "")[:240],
            advice="局限里写明并引 {{diag:稳健值}}；**不自动降级**——稳健值小不等于证明了有混淆"
                   + ("。经济学、政治学里这一条是 must 级：学科档案要求观察数据的因果主张附敏感性分析" if strict else "")))
    p_lo, p_hi = cc.NEAR_THRESHOLD_P
    if main is not None and plan.estimator in ("iv", *DID_FAMILY) and p_lo <= main.p <= p_hi:
        rb = [r for r in results.robustness.values() if not r.error and "main" in r.estimates]
        same = sum(1 for r in rb if r.estimates["main"].coef * main.coef > 0 and r.estimates["main"].p < 0.05)
        out.append(RouteItem(
            id="near_threshold", priority="should", kind="disclose",
            trigger=f"主结果 p = {main.p:.3f}，落在 {p_lo:g}–{p_hi:g}",
            evidence=f"已锁定的稳健性 {len(rb)} 项里，{same} 项同号且显著",
            advice="IV 与双重差分的主结果在阈值附近最容易是挑出来的（Brodeur 等 2020）：正文照报已锁定稳健性清单上的分布，"
                   "**不许为此新加稳健性项**；清单里没有换窗口、换控制集的，写明「这一轮检查做不全」，交审稿人重点看"))
    return out


def _survey_items(plan: AnalysisPlan, results: AnalysisResults, survey: dict) -> list[RouteItem]:
    """调查层的四条处方（设计稿第七节）。门槛初值都在 `survey_checks`。"""
    from oss.core import survey_checks as sc

    d, out = results.diagnostics, []
    design = plan.survey
    gap = sc.weight_gap(plan, results)
    if design is not None and design.weight_use == "model" and gap is not None and gap[2] > sc.WEIGHT_GAP_SE:
        out.append(RouteItem(
            id="survey_weight_gap", priority="should", kind="disclose",
            trigger=f"加权与不加权两版差 {gap[2]:.1f} 个标准误（门槛 {sc.WEIGHT_GAP_SE:g}）",
            evidence=f"加权 {gap[0]:+.3f}，不加权 {gap[1]:+.3f}",
            advice="局限里解释两版为什么差这么多（Deaton 1997：行为模型加权与不加权都报，差得多要解释）——"
                   "多半是效应随某个跟入样概率相关的特征而异；**不自动换主规格**"))
    if design is not None and design.strata and plan.estimator == "did_cs":
        out.append(RouteItem(
            id="survey_design_approx", priority="should", kind="disclose",
            trigger="声明了分层，但 did_cs 只能按初级抽样单位聚类近似",
            # did_cs 把「按初级抽样单位聚类（分层没进方差）」写在「标准误」里，没有「抽样设计」这个键
            evidence=(d.get("标准误") or d.get("抽样设计", ""))[:240],
            advice="局限写明：分层没进方差，分层通常让方差变小，所以这个近似一般偏保守"))
    flags = (survey or {}).get("imputation_flags") or {}
    for flag, target in flags.items():
        if target and target in (plan.outcome, plan.treatment) \
                and not any(flag in (r.sample_filter or "") for r in plan.robustness):
            out.append(RouteItem(
                id="survey_imputed", priority="should", kind="disclose",
                trigger=f"「{target}」有插补值（标记列 {flag}）",
                evidence=f"画像认出插补标记 {flag} → {target}",
                advice=f"加一项剔除插补值的稳健性（例如 sample_filter: \"{flag} == 0\"——**先核码本**，"
                       f"确认 1 表示插补而不是原值），或局限里写明插补占多少、可能往哪边偏（Meyer 等 2015）"))
            break
    n_psu, singles = _num(d, "抽样单位数"), _num(d, "单抽样单位的层")
    if (n_psu is not None and n_psu < sc.FEW_PSU) or (singles is not None and singles > 0):
        out.append(RouteItem(
            id="survey_few_psu", priority="should", kind="disclose",
            trigger=f"初级抽样单位 {n_psu:.0f} 个" if n_psu is not None else "有只含一个抽样单位的层",
            evidence=d.get("标准误", "")[:240],
            advice="按设计的推断靠抽样单位的个数：太少时区间不稳。局限写明抽样单位数、自由度，以及单抽样单位的层怎么处理的"))
    return out


def _simulation_items(d: dict) -> list[RouteItem]:
    """仿真骨架的一条处方：合成样本跟真实调查对不上账（设计稿 4.3 乙）。

    **判据不在这里算**：方差比与关系方向要同时拿到合成数据与真实数据，而处方这一步
    手上只有计划、结果、画像、绑定。`oss run` 算完写进 `results.diagnostics` 的
    「合成数据保真度」那一行，这里读它——跟「分批实施」「Bacon 分解」同一个办法。

    **为什么是 must 级却不是 stop**（2026-09-19 用户拍板）：补一份真实数据、或者改提示词
    重跑一遍模拟，都不是一两分钟的事，硬停等于只留下造假和换题两条路。但作者是**自己声明**
    这批小人代表真人的——对不上账就是这个主张不成立，所以不许驳回：要么改、要么把主张撤掉。

    对得上账的那一行也写在诊断里（`_fidelity_line`），这里不开处方——
    「对过账、对得上」跟「没对过账」长得不一样，这一点要留在留痕里。
    """
    line = (d or {}).get("合成数据保真度", "")
    if not line or "对得上" in line:
        return []
    return [RouteItem(
        id="simulation_fidelity", priority="must", kind="disclose",
        trigger="合成样本跟真实调查对不上账",
        evidence=line[:240],
        advice="**是你自己声明这批小人代表真人的**（simulation.stands_in_for_humans），"
               "对不上账就是这个主张不成立：方差假小，标准误就假小、显著性就假强"
               "（Bisbee 2024）。**三条出路选一条**："
               "① 改提示词重跑模拟（别把答案的范围暗示进去），重跑后 `oss route` 复查，"
               "触发消失即解决；"
               "② 把 stands_in_for_humans 改成 false，正文只说「在本模型内」的话——"
               "这篇就不再主张合成样本可以代替真人调查；"
               "③ 确有理由认为这一差别不影响本文的结论（例如比的是不同人群），"
               "`--disclose` 写明理由并在局限里交代方差比是多少。"
               "这几对列本来就不该对账，`--dismiss` 写明理由")]


def _measurement_items(plan: AnalysisPlan, results: AnalysisResults | None,
                       profile=None, binding=None) -> list[RouteItem]:
    """测量骨架的一条处方：跨组比较之前先验测量等价（设计稿 5.2 的 M4）。

    **为什么是 must 级却不是 stop**：等价检验做不出来时，正确的做法是把跨组比较
    **降级为「组内描述」**——每组各报各的、不比大小，并在局限里写明为什么不比。
    直接停等于只留下造假和换题两条路。所以开 `disclose`：两条出路写在处方里，选一条即可。

    **触发也认「不声明」那一路**（实施计划「零之二」第 ③ 条）：光在计划门里警告堵不住洞——
    声明了要过一道 must 级处方、不声明只得一条警告，绕行的代价还是零。所以这里不只看
    `groups_compared`，还从别处对账（`measurement_group_signals`）。

    `profile` 与 `binding` 是给第四处对账用的（处理变量本身就是分组变量）：那一处要从画像
    判处理变量是不是分类或二值，还要知道结局落不落在这个测量块上。**处方这一步手上没有数据**，
    所以「结局是不是这几道题算出来的量表分」那一路在这里走不通，只走得通绑定那一路——
    绑定缺席时这一处判不出来，按「判不出来就不报」处理，计划的门那一侧照样拦得住。
    """
    from oss.core.analysis import measurement_group_signals

    spec = plan.measurement
    if spec is None or (spec.evidence_outputs.get("equivalence") or "").strip():
        return []
    declared = [g for g in spec.groups_compared if g.strip()]
    signals = measurement_group_signals(plan, results, profile=profile, binding=binding)
    if not declared and not signals:
        return []
    return [RouteItem(
        id="measurement_equivalence", priority="must", kind="disclose",
        trigger=("跨组比较，产出登记里没有测量等价检验" if declared
                 else "measurement.groups_compared 空着，别处却有分组的迹象"),
        evidence=("measurement.groups_compared：" + "、".join(declared) if declared
                  else "；".join(signals))[:240],
        advice="没验过等价，跨组的数不可比：同一批题在两组里量的可能不是同一个东西，"
               "组间差里混着「题目在两组含义不同」。**两条出路选一条**："
               "① 做测量等价检验，把那份产出登记进 plan.measurement.evidence_outputs 的 equivalence 一行，"
               "重跑后 `oss route` 复查，触发消失即解决；"
               "② **把跨组比较降级为组内描述**——每组各报各的、不比大小，`--disclose` 标记并在局限里"
               "写明等价没验过、所以不比组间差。"
               "这几处根本不是跨组比较（例如交互项的调节变量是连续的），`--dismiss` 写明理由")]


def _descriptive_items(plan: AnalysisPlan, results: AnalysisResults, structure_kind: str) -> list[RouteItem]:
    """描述骨架的三条结果期处方（设计稿第六节）；精度不足那条在 power_fail 旁边。门槛初值都在 `descriptive_checks`。"""
    from oss.core import descriptive_checks as dc

    if plan.research_type != "descriptive":
        return []
    out: list[RouteItem] = []
    small = [c for c in results.groups if 0 < (c.n_eff or c.n) < dc.SMALL_CELL_NEFF]
    if small:
        out.append(RouteItem(
            id="small_cells", priority="should", kind="disclose",
            trigger=f"{len(small)} 格的有效样本量少于 {dc.SMALL_CELL_NEFF}",
            evidence="；".join(f"{c.label}：有效样本量 {(c.n_eff or c.n):.0f}" for c in small)[:300],
            advice="这几格的均值区间很宽、组间差不稳：合并分组，或表里不报这几格并写明为什么"))
    d = plan.description
    if d is not None and d.change == "social" and structure_kind.startswith("panel"):
        out.append(RouteItem(
            id="panel_for_social_change", priority="should", kind="disclose",
            trigger="用面板描述总体随时间的变化",
            evidence=f"画像判的数据结构：{structure_kind}",
            advice="面板的人会变老、会流失，越往后越不代表当期的总体：用横截面权重或补充样本，局限写明样本老化与流失"
                   "（Firebaugh：社会变化用重复截面；Deaton：重复截面也能按出生队列造伪面板）"))
    if d is None or not d.benchmarks:
        out.append(RouteItem(
            id="reality_check_missing", priority="should", kind="disclose",
            trigger="没写外部基准，现实核查没做",
            evidence="plan.description.benchmarks 为空",
            advice="找一两个外部已知数（普查、官方统计年鉴）写进 description.benchmarks（带出处），论文里报本文的数跟它差多少、"
                   "差得多要解释（Firebaugh 第 3 条「现实核查」）"))
    return out


# ------------------------------------------------------------------ 处方表


def _power_mode_mismatch(plan: AnalysisPlan | None, power: PowerReport | None, anchor_idea: str = "") -> str:
    """功效报告跟计划对不上：返回触发说明；对得上或无从判断返回空串。两种对不上：

    - **题号**（终审 M2）：power.json 算的是别的题——换题（oss anchor switch）后没按新题重算，或算的时候写了别的
      --idea。原先只比类型：换题后不写 --idea 的 oss power 算的是 ideas.json 的第一题，处方说「重跑 oss power」，
      重跑的还是那一题，这条永远消不掉。触发里就写出带 --idea 的命令。计划没写 idea_id（老计划）不比。
      **报告的题就是锚点当前的题**（`anchor_idea`，`build` 从 run_state.json 只读不隔离地取）：报告没算错，是计划还停在
      换题前（F1 复审 M2-5）——原先照样说「重跑 oss power --idea <计划的题>」，照做就算回旧题。这时改说先给锚点的题写计划
    - **研究类型**：环节 4 的 `oss power` 在计划之前跑，按 idea 的类型分路，描述类 idea 得到精度报告；环节 5 计划写成因果
      （陪审团第 5 条正是在请人这样改），这份报告答的就不是这篇论文的问题。反过来也一样。
      **只看计划上写的类型、不看锚点**：从因果降为描述的计划，锚点里还留着 causal，重跑 oss power 之后
      报告按精度算，那就对得上。计划没写类型（老计划）不判；老 power.json 没有 mode 的按功效算（契约缺省）
    """
    if plan is None or power is None:
        return ""
    if power.idea_id and plan.idea_id and power.idea_id != plan.idea_id:
        if anchor_idea and power.idea_id == anchor_idea:
            return f"{STALE_PLAN} {plan.idea_id} 的；锚点现在是 {anchor_idea}：先给 {anchor_idea} 写计划"
        return f"power.json 是 {power.idea_id} 的，计划是 {plan.idea_id} 的：重跑 oss power --idea {plan.idea_id}"
    if not plan.research_type:
        return ""
    mode = getattr(power, "mode", "power") or "power"
    if plan.research_type != "descriptive" and mode == "precision":
        return (f"power.json 是按描述类的精度算的，计划的研究类型是 {plan.research_type}："
                "重跑 oss power（它按计划的类型分路）")
    if plan.research_type == "descriptive" and mode != "precision":
        return "计划是描述类，power.json 却按功效算：重跑 oss power，描述类按精度判"
    return ""


_NESTED_SAY = {"finer": "`{c}` 嵌套在 `{o}` 里", "coarser": "`{o}` 嵌套在 `{c}` 里",
               "same_rows": "`{c}` 与 `{o}` 有类恰好是同一批行"}


def _plan_check_items(plan: AnalysisPlan, check: PlanCheck) -> list[RouteItem]:
    """计划体检的两种处方。都是 must：数据里现成的东西，漏用要么补上、要么说出理由。"""
    items: list[RouteItem] = []
    for p in check.pairs:
        if plan.outcome not in (p.a, p.b):
            continue
        other = p.b if plan.outcome == p.a else p.a
        if plan_checks.uses(plan, other):
            break
        same = sum(q.marker == p.marker for q in check.pairs)
        items.append(RouteItem(
            id="plan_paired", priority="must", kind="add_robustness",
            trigger="结局有配对列，计划只用了一边",
            evidence=(f"结局 `{plan.outcome}` 与 `{other}` 是一对（标记 {p.marker}，这份数据里共 {same} 对），"
                      f"`{other}` 在计划里哪儿都没用"),
            robustness=RobustnessSpec(label=f"配对列 {other}", outcome=other,
                                      rationale=f"结局 {plan.outcome} 的配对列，同口径算一遍放在一起看"),
            advice=("数据里结局有配对的另一边（原始 / 复制、前测 / 后测这类）。题目要比两边时，只算一边答不全。"
                    "`oss route --apply plan_paired` 在稳健性里加一项：结局换成另一边、其余照主规格，再 `oss run`；"
                    "题目确实只问这一边，`--dismiss plan_paired --why` 写明。")))
        break

    live = [n for n in check.nested if n.column in plan_checks.group_columns(plan, [n.column])]
    if live:
        # **先说清计划现在按哪一列分**：只说「journal 嵌套在 project.x 里」，读的人（和模型）
        # 会把方向读反，以为计划按 journal 分。真跑里就这么栽过一次
        lines = [f"计划按 `{n.column}` 分组；"
                 + _NESTED_SAY[n.relation].format(c=n.column, o=n.other) + "：" + "；".join(n.mapping)
                 for n in live[:3]]
        items.append(RouteItem(
            id="plan_nested", priority="must", kind="disclose",
            trigger="计划的分组列跟另一列嵌套，或有一类恰好是同一批行",
            evidence="。".join(lines)[:600],
            advice=("同一批行有两种分法。题目问的是哪种分法，计划就按哪一列分——要换就改分组列重跑。"
                    "选定之后 `oss route --disclose plan_nested --why` 写一句两种分法在这份数据里怎么对应，"
                    "读者才知道换一种分法结论会不会变；两列真不相干就 `--dismiss` 写理由。")))
    return items


def _rules(plan: AnalysisPlan | None, results: AnalysisResults | None,
           power: PowerReport | None, audit_findings: list[str],
           weight_column: str = "", survey: dict | None = None, structure_kind: str = "",
           anchor_idea: str = "", profile=None, binding=None,
           plan_check: PlanCheck | None = None) -> list[RouteItem]:
    items: list[RouteItem] = []
    d = results.diagnostics if results else {}

    # ⓪ 画像检出了抽样权重列，计划却没用它。
    #
    # **这不是「忘了加个选项」。**CGSS / CFPS / GSS 这类数据按复杂抽样设计采集，
    # 各人入样概率不同；不加权算出来的是「样本里的中国」而不是「中国」，
    # 而两者的差别在系数上是看不出来的——没有任何一个统计量会告诉你算错了对象。
    #
    # 但**不加权是一个正当的选择**（有人就是要估样本内的关系，也有人拿它当稳健性），
    # 所以这里开的是 should 级、可驳回：要么加权，要么说出理由。
    if plan is not None and weight_column and not plan.weights:
        # 七种估计量都真加权（2026-09-15 调查层六种，描述骨架的 describe 也是），处方只剩一条路：填上权重、声明抽样设计
        ways = (f"两条路二选一：把 `plan.weights` 填成 `{weight_column}` 并在 `plan.survey` 里声明抽样设计重跑；"
                f"或 `--dismiss` 写明为什么这篇不加权，"
                f"并在数据与方法一节写「本文报告未加权估计」及其含义。")
        items.append(RouteItem(
            id="weights_unused", priority="should", kind="disclose",
            trigger="画像检出抽样权重列，计划未使用",
            evidence=f"`{weight_column}`（profile.weight_column）；plan.weights 为空",
            advice=ways + "**不许默认沉默**——读者分不出「这份数据没有权重」和「有权重但没用」"))

    # ⓪″ 计划体检（docs/design/2026-09-23-plan-check.md）：数据里现成、计划漏用的。事实是 oss run
    #    在分析帧上当场算的（route 手上没有数据），这里拿当下的计划去对——应用补丁后复查，触发当场消失
    if plan is not None and plan_check is not None:
        items.extend(_plan_check_items(plan, plan_check))

    # ⓪′ 功效报告跟计划的研究类型对不上：停，重跑 oss power（复审 2026-09-15）。开了它，下面 ① 与 ①′
    #    这一趟都不开——报告答的不是这篇论文的问题，它判 fail 还是 pass 都没有意义
    mismatch = _power_mode_mismatch(plan, power, anchor_idea)
    if mismatch and plan is not None and power is not None:
        how = "精度" if getattr(power, "mode", "power") == "precision" else "功效"
        # 命令带上计划的题号：不写 --idea 的话，重跑算的未必是这道题（终审 M2）
        rerun = f"oss power --idea {plan.idea_id}" if plan.idea_id else "oss power"
        # 描述类计划重跑时按计划的分组、整群与权重算（F1 复审 S1）；别的类型重跑走功效，不用计划的这几样
        per_plan = "（会按计划的分组、整群与权重算）" if plan.research_type == "descriptive" else ""
        if mismatch.startswith(STALE_PLAN):
            evidence = (f"power.json 的 idea_id 是 {power.idea_id}（按{how}判，判词 {power.verdict}），就是锚点当前的题；"
                        f"plan.json 的 idea_id 是 {plan.idea_id}")
            advice = (f"换过题（oss anchor switch），功效报告已经按新题 {anchor_idea} 算了，盘上的计划还是换题前 "
                      f"{plan.idea_id} 的——它说的不是现在这道题。**唯一的出路是先给 {anchor_idea} 写计划**"
                      f"（plan.json 的 idea_id 写 {anchor_idea}），再 `oss run`、`oss route` 复查，触发消失即解决；"
                      "新计划的研究类型跟报告对不上的，这一条会改说重跑 oss power。"
                      "**这条处方驳回不了，也不能靠写进局限了事。**")
        else:
            if power.idea_id and plan.idea_id and power.idea_id != plan.idea_id:
                evidence = (f"power.json 的 idea_id 是 {power.idea_id}（按{how}判，判词 {power.verdict}）；"
                            f"plan.json 的 idea_id 是 {plan.idea_id}")
                why = ("功效报告算的是另一道题：换题（oss anchor switch）之后没按新题重算，或算的时候写了别的 --idea——"
                       "它的判词说的不是这篇论文。")
            else:
                evidence = (f"power.json 按{how}判（判词 {power.verdict}）；"
                            f"plan.json 的 research_type 是 {plan.research_type}")
                why = ("环节 4 的 `oss power` 在计划写出来之前跑，按 idea 的类型分路；计划在环节 5 换了类型"
                       "（例如陪审团指出标成描述是在躲识别审计），这份报告答的就不是这篇论文的问题。")
            advice = (why + f"**唯一的出路是重跑 `{rerun}`**{per_plan}：有了计划，它按计划的类型分路；"
                      "重跑后 `oss route` 复查，触发消失即解决。**这条处方驳回不了，也不能靠写进局限了事。**")
        items.append(RouteItem(
            id="power_mode_mismatch", priority="must", kind="stop",
            trigger=mismatch, evidence=evidence, advice=advice))

    # ① 功效 FAIL：停。用户定的——「数据做不了就停」，不是当结果报。
    #    只管功效模式：描述研究按精度判，它的 fail 是「精度不足」，不停（见下面的 ①′）
    if not mismatch and power is not None and power.verdict == "fail" \
            and getattr(power, "mode", "power") != "precision":
        items.append(RouteItem(
            id="power_fail", priority="must", kind="stop",
            trigger="power.json 判 fail",
            evidence=f"功效 {power.power:.2f}，目标效应 {power.effect_size_raw}（折后 {power.effect_size_adjusted}），"
                     f"有效样本 {power.n_effective}" + ("；判词对假定参数敏感" if power.verdict_fragile else ""),
            advice=("**这份数据回答不了这个问题，停。**评审：「检验力不足是设计层面的失败，不是可报告的结果」。"
                    "三条出路，选一条再往下走：① 换结局——对处理反应更快、效应更大的（存量指标换流量指标）；"
                    "② 换样本或扩窗口——多拿几年、多拿几个单位（`oss data search`）；③ 换题目——回 ideas.json "
                    "挑陪审团留下的别的候选（**换题要用户点头**；已跑过 oss run 的只能 "
                    "`oss anchor switch --to <idea> --authorized-by user`）。"
                    "只有文献里有明确更大的效应量时才第四条：`oss power --effect <值>` "
                    "重算，效应量的出处要写进论文。**这条处方驳回不了。**"),
        ))

    # ①′ 描述研究精度不足：**不停**——「这份数据分不出多小的差别」本身是一个结果（描述骨架设计稿第四节）。
    #    不依赖结果，环节 4 一算完就开；计划写成了别的研究类型的，由 ⓪′ 接手（开 power_mode_mismatch），这里不开。
    #    有效样本量为 0 时 power.json 把半宽存成 0.0，所以措辞按有效样本量分，不拿半宽单独判
    if not mismatch and power is not None and getattr(power, "mode", "power") == "precision" \
            and power.verdict == "fail":
        if power.n_effective > 0 and power.precision_halfwidth > 0:
            trigger = (f"精度不足：主要比较的 95% 区间半宽 {power.precision_halfwidth:.3f} 个标准差，"
                       "比有意义的最小差别还宽")
        else:
            trigger = "精度不足：有效样本量是 0，算不出区间——先查数据与样本筛选"
        # 最小分组那半句按标签判：没给分组列时标签是空串，不打「最小分组 —（有效样本量 0）」
        cell = (f"；最小分组 {power.min_cell_label}（有效样本量 {power.min_cell_n_eff:.0f}）"
                if power.min_cell_label else "")
        items.append(RouteItem(
            id="precision_low", priority="must", kind="disclose",
            trigger=trigger,
            evidence=f"有效样本量 {power.n_effective}" + cell,
            advice="描述研究精度不足**不停**：收窄题目（合并分组、只报总体层面），或局限里写明「这份数据分不出小于 X 的差别」；"
                   "处置方式：--disclose 写明选了哪条"))

    # ①″ 测量：跨组比较之前先验测量等价（测量骨架设计稿 5.2）。不依赖结果——结果在不在，
    #    「有没有验过等价」这件事都判得出来；有结果时再多一处对账（结果里真排出来的分组表）
    if plan is not None:
        items.extend(_measurement_items(plan, results, profile, binding))

    # ①‴ 仿真：合成样本跟真实调查对不上账（仿真骨架设计稿 4.3 乙）。
    #    判据在 `oss run` 那一步算（处方这一步手上没有数据），这里读它写下的诊断
    items.extend(_simulation_items(d))

    if plan is not None and results is not None:
        n_cohorts = _cohorts(results)
        bacon = d.get("Bacon 分解", "")
        bad_share = re.search(r"\*\*(\d+)% 的权重落在", bacon)

        # ② 分批实施却用 TWFE：换估计量。
        # `ols_fe` 也在内——写成 `ols_fe` + 一个 post 哑变量的双向固定效应，算的跟 `did`
        # 一模一样，坑也一模一样；`实施批次` 那条诊断由 `analysis._twfe_staggered_cohorts`
        # 按数据判出来，**不是按计划里写的估计量名字**
        if plan.estimator in ("did", "event_study", "ols_fe") and n_cohorts > 1:
            ev = f"{n_cohorts} 批分批实施；{d.get('批次异质性', '')[:120]}"
            if bad_share:
                ev += f"；Bacon 分解：{bad_share.group(1)}% 的权重落在「已处理单位当对照」的坏比较上"
            items.append(RouteItem(
                id="staggered_twfe", priority="must", kind="patch_plan",
                trigger=f"estimator = {plan.estimator}，而处理分批实施",
                evidence=ev, patch={"estimator": "did_cs"},
                advice=("双向固定效应把已处理的早批当成晚批的对照，各批效应不同时汇总系数不可解释。"
                        "换 Callaway–Sant'Anna（`did_cs`）：只拿从未处理的单位比，各批效应分别报。"
                        f"应用：oss route --apply staggered_twfe，然后 oss run 重跑。"),
            ))
        elif "差异大" in d.get("批次异质性", "") and plan.estimator != "did_cs":
            items.append(RouteItem(
                id="cohort_hetero", priority="should", kind="disclose",
                trigger="批次异质性：各批效应差异大",
                evidence=d.get("批次异质性", "")[:200],
                advice=("伴随的分批事件研究已按 Callaway–Sant'Anna 算（各批效应在 estimates.cohort_<年>）。"
                        "论文用 {{table:cohorts}} 报各批，对着 diagnostics.批次成员 的名单想**替代解释**："
                        "效应最负的那几批是不是同一类国家 / 同一段历史（后共产主义转型、艾滋病冲击）？"
                        "是就加一项剔除那一批的稳健性（`sample_filter`），并在讨论里写。"),
            ))
        elif "差异大" in d.get("批次异质性", ""):
            items.append(RouteItem(
                id="cohort_hetero", priority="should", kind="disclose",
                trigger="批次异质性：各批效应差异大",
                evidence=d.get("批次异质性", "")[:200],
                advice=("用 {{table:cohorts}} 报各批效应；对着 diagnostics.批次成员 想替代解释，"
                        "同一类单位集中在效应最极端的批次里就剔除它们跑一项稳健性，并在讨论里写。"),
            ))

        # ③ 平行趋势不成立：设计级，必须处置
        if "不成立" in d.get("平行趋势", ""):
            items.append(RouteItem(
                id="pretrend_fail", priority="must", kind="disclose",
                trigger="平行趋势：不成立",
                evidence=d.get("平行趋势", "")[:240],
                advice=("处理前两组已在分化，算出来的「效应」里混着本来就有的趋势。按顺序试："
                        "缩短窗口（`leads` 减到分化开始之后）→ 剔除抢跑期（处理前 1–2 期不进估计，"
                        "在 prep.py 里把那几期的观测标掉）→ 只留从未处理的对照（`did_cs` 已是）→ "
                        "都不行就**降级为关联**：摘要和结论不许说因果，局限写明为什么。"
                        "处置方式：改了设计重跑（触发消失即解决）；或 --disclose 写进局限并附理由。"),
            ))

        # ④ 处理反转：加一项稳健性
        if d.get("处理反转") and not _has_robustness(plan, "_reversal"):
            items.append(RouteItem(
                id="reversal", priority="should", kind="add_robustness",
                trigger="处理反转：有单位从 1 变回 0",
                evidence=d.get("处理反转", "")[:200],
                robustness=RobustnessSpec(label="Exclude units with treatment reversals",
                                          rationale="Reversed units contaminate the treated group",
                                          sample_filter="_reversal == 0"),
                advice="剔除反转单位再估一次；结果变了就说明主结果依赖那些反转的单位。应用后 oss run 重跑。",
            ))

        # ④′ 左删失：处理单位没有处理前观测，平行趋势对它无从检验——剔掉再估一次
        if "左删失" in d.get("数据契约", "") and not _has_robustness(plan, "_has_pre"):
            items.append(RouteItem(
                id="left_censored", priority="should", kind="add_robustness",
                trigger="数据契约：有处理单位没有处理前观测",
                evidence=d.get("数据契约", "")[:200],
                robustness=RobustnessSpec(label="Exclude units without pre-treatment observations",
                                          rationale="Left-censored units cannot be checked for pre-trends",
                                          sample_filter="_has_pre == 1"),
                advice="剔掉没有处理前观测的单位再估一次；主结果靠它们撑着的话，平行趋势那一节站不住。应用后 oss run 重跑。",
            ))

        # ⑤ 面板流失：只留平衡面板的稳健性
        if ("流失明显" in d.get("面板流失", "") or "流失偏多" in d.get("面板流失", "")) \
                and not _has_robustness(plan, "_balanced"):
            items.append(RouteItem(
                id="attrition", priority="should", kind="add_robustness",
                trigger="面板流失",
                evidence=d.get("面板流失", "")[:200],
                robustness=RobustnessSpec(label="Balanced panel only",
                                          rationale="Attrition is rarely random",
                                          sample_filter="_balanced == 1"),
                advice="只留全部期都有观测的单位再估一次；跟主结果差得远就说明流失不是随机的。应用后 oss run 重跑。",
            ))

        # ⑥ 写进局限的几条
        for key, cond, advice in (
            ("处理前平衡", lambda v: "不平衡" in v,
             "两组处理前本来就不像；局限里写清哪些变量不平衡、偏差可能朝哪。能修剪到共同支撑域就加一项稳健性。"),
            ("对照组", lambda v: "没有从未被处理" in v,
             "对照组每一期都在变（尚未处理的单位）——分批偏误最重的情形，局限里写。"),
            ("共线诊断（VIF）", lambda v: "严重" in v,
             "有系数不能单独解读；正文只解读能解读的那些，局限里写明哪些不能。"),
            ("算不了的格子", lambda v: bool(v),
             "有的批次处理后没有任何对照，main 不包含它们——正文说明 main 覆盖哪些批次。"),
        ):
            v = d.get(key, "")
            if v and cond(v):
                items.append(RouteItem(
                    id={"处理前平衡": "imbalance", "对照组": "no_never_treated",
                        "共线诊断（VIF）": "collinearity", "算不了的格子": "lost_cells"}[key],
                    priority="should", kind="disclose", trigger=key, evidence=v[:200], advice=advice))

        m = re.search(r"\((\d+(?:\.\d+)?)%\)", d.get("因缺失丢弃", ""))
        if m and float(m.group(1)) >= 30:
            items.append(RouteItem(
                id="missing_heavy", priority="should", kind="disclose", trigger="因缺失丢弃 ≥ 30%",
                evidence=d.get("因缺失丢弃", ""),
                advice="三成以上观测因缺失丢掉，留下的可能不是随机的一半：数据一节写清丢的是谁（哪些国家 / 年份），局限里写方向。"))

        # ⑦ 稳健性里翻了
        main = results.estimates.get("main")
        if main is not None:
            flips = []
            for label, rb in results.robustness.items():
                e = rb.estimates.get("main") if not rb.error else None
                if e is None:
                    continue
                if e.coef * main.coef < 0:
                    flips.append(f"「{label}」方向反转（{e.coef:+.3f} vs {main.coef:+.3f}）")
                elif (e.p < 0.05) != (main.p < 0.05):
                    flips.append(f"「{label}」显著性翻转（p {e.p:.3f} vs {main.p:.3f}）")
            if flips:
                items.append(RouteItem(
                    id="robustness_flip", priority="should", kind="disclose", trigger="稳健性结果与主规格不一致",
                    evidence="；".join(flips)[:300],
                    advice="正文不能写「稳健」。逐项说哪一项变了、为什么可能变，结论按最保守的那个写。"))

        items.extend(_causal_items(plan, results, power))
        items.extend(_survey_items(plan, results, survey or {}))
        items.extend(_descriptive_items(plan, results, structure_kind))

    # ⑧ 因果审计的设计级意见
    for i, f in enumerate(audit_findings, start=1):
        items.append(RouteItem(
            id=f"audit_{i}", priority="should", kind="disclose", trigger="因果审计 [设计]",
            evidence=f[:240],
            advice="局限里一句交代：承认、后果、为什么没法解决——**不提审计**，写那个局限本身。能改设计就改（改了触发不会消失，用 --disclose 标记并写改了什么）。"))
    return items


# ------------------------------------------------------------------ 生成 / 处置


def build(rundir: Path, *, save: bool = True) -> Route:
    """读运行目录，生成（或刷新）处方。老处方的状态按 id 带过来；触发消失的标「已解决」。

    `save=False` 只算不写：`oss run` 的计划体检要知道 `plan_*` 处置了没有，但**不许替模型把
    route.json 写出来**——「盘上有没有 route.json」是别的规则判「开没开过方」的依据。
    """
    plan_path = rundir / ARTIFACTS["plan"]
    plan = AnalysisPlan.load(plan_path) if plan_path.exists() else None
    rp = rundir / ARTIFACTS["results"]
    results = AnalysisResults.load(rp) if rp.exists() else None
    pp = rundir / ARTIFACTS["power"]
    power = PowerReport.load(pp) if pp.exists() else None
    findings: list[str] = []
    for p in sorted([*rundir.glob("review_plan*.json"), *rundir.glob("review_identification*.json")]):
        d = _read(p) or {}
        findings.extend(str(f) for f in d.get("findings") or [] if str(f).startswith("[设计]"))

    prof = _read(rundir / ARTIFACTS["profile"]) or {}
    fresh = _rules(plan, results, power, findings, str(prof.get("weight_column") or ""),
                   survey=prof.get("survey") or {},
                   structure_kind=str((prof.get("structure") or {}).get("kind") or ""),
                   anchor_idea=state_mod.peek_anchor_idea(rundir),
                   profile=_artifact(DataProfile, rundir / ARTIFACTS["profile"]),
                   binding=_artifact(BindingReport, rundir / ARTIFACTS["binding"]),
                   plan_check=_artifact(PlanCheck, rundir / plan_checks.ARTIFACT))
    old_path = rundir / ARTIFACTS["route"]
    old = Route.load(old_path) if old_path.exists() else Route()
    by_id = {i.id: i for i in old.items}
    out: list[RouteItem] = []
    for it in fresh:
        prev = by_id.get(it.id)
        if prev is not None and prev.status != "resolved":
            it.status, it.why = prev.status, prev.why
            # 应用过补丁但触发还在（比如应用后还没重跑）：保持 applied，不回退成 pending
        out.append(it)
    fresh_ids = {i.id for i in fresh}
    for it in old.items:
        if it.id not in fresh_ids and it.status != "resolved":
            it.status = "resolved"
            out.append(it)
        elif it.id not in fresh_ids:
            out.append(it)
    route = Route(plan_sha=_plan_sha(plan_path) if plan_path.exists() else "", items=out)
    if save:
        route.save(old_path)
    return route


def _find(route: Route, item_id: str) -> RouteItem:
    for it in route.items:
        if it.id == item_id:
            return it
    raise KeyError(f"没有叫「{item_id}」的处方。现有：{'、'.join(i.id for i in route.items) or '（无）'}")


def apply(rundir: Path, route: Route, item_id: str) -> str:
    """把一条补丁盖到 plan.json 上。只有 patch_plan / add_robustness 能应用。返回一句说明。"""
    it = _find(route, item_id)
    if it.kind == "stop":
        raise ValueError(f"「{item_id}」没有补丁可应用——{it.advice}")
    if it.kind == "disclose":
        raise ValueError(f"「{item_id}」是写进局限的那种，用 --disclose 标记，不用 --apply")
    plan_path = rundir / ARTIFACTS["plan"]
    plan = AnalysisPlan.load(plan_path)
    data = plan.model_dump()
    if it.kind == "patch_plan":
        data.update(it.patch)
        what = "、".join(f"{k} → {v}" for k, v in it.patch.items())
    else:
        assert it.robustness is not None
        if any(r.label == it.robustness.label for r in plan.robustness):
            what = f"稳健性「{it.robustness.label}」已经在计划里"
        else:
            data["robustness"] = [*data["robustness"], it.robustness.model_dump()]
            # 说清这一项覆盖了什么。**不许只打 sample_filter**：换结局那种补丁
            # （计划体检的配对列）没有筛选式，打出来是「（None）」
            over = "、".join(f"{k}={v}" for k, v in it.robustness.model_dump(exclude_none=True).items()
                             if k not in ("label", "rationale", "executable"))
            what = f"加稳健性「{it.robustness.label}」（{over}）"
    AnalysisPlan(**data).save(plan_path)
    it.status = "applied"
    route.plan_sha = _plan_sha(plan_path)
    route.save(rundir / ARTIFACTS["route"])
    _trace(rundir, f"应用处方 {item_id}：{what}")
    return what + "。下一步：oss run 重跑，再 oss route 复查"


def dismiss(rundir: Path, route: Route, item_id: str, why: str) -> None:
    it = _find(route, item_id)
    if it.id in NOT_DISMISSABLE:
        raise ValueError(f"「{item_id}」驳回不了：{it.advice}")
    if not why.strip():
        raise ValueError("驳回要写理由（--why）。理由留在 route.json 里，审稿人会看")
    it.status, it.why = "dismissed", why.strip()
    route.save(rundir / ARTIFACTS["route"])
    _trace(rundir, f"驳回处方 {item_id}：{why.strip()[:120]}")


def disclose(rundir: Path, route: Route, item_id: str, why: str) -> None:
    it = _find(route, item_id)
    if it.kind == "stop":
        raise ValueError(f"「{item_id}」不能靠写进局限了事：{it.advice}")
    it.status, it.why = "disclosed", why.strip()
    route.save(rundir / ARTIFACTS["route"])
    _trace(rundir, f"处方 {item_id} 写进局限：{why.strip()[:120]}")


def _trace(rundir: Path, note: str) -> None:
    """留痕。core/ 不许 import host/，所以这里自己数流水号（跟 RunDir.next_seq 同一规则：数盘上的行）。"""
    path = rundir / ARTIFACTS["trace"]
    try:
        seq = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()) + 1
    except OSError:
        seq = 1
    ev = TraceEvent(seq=seq, step="route", command="oss route", outcome="ok",
                    artifacts=[ARTIFACTS["route"]], note=note)
    with path.open("a", encoding="utf-8") as f:
        f.write(ev.to_line() + "\n")


def blockers(route: Route) -> list[str]:
    """给 paper check / export 用：must 级没处置的，一条一句。"""
    out = []
    for it in route.pending_must():
        how = {"stop": "换结局 / 样本 / 题目，或重算功效",
               "patch_plan": f"oss route --apply {it.id} 然后 oss run",
               "add_robustness": f"oss route --apply {it.id} 然后 oss run",
               "disclose": f"改设计重跑，或 oss route --disclose {it.id} --why <理由>"}[it.kind]
        if it.id == "power_mode_mismatch":      # 报告答错了问题：不是换结局 / 样本 / 题目
            how = ("先给新题写计划，再 oss run、oss route 复查" if it.trigger.startswith(STALE_PLAN)
                   else "重跑 oss power，再 oss route 复查")
        out.append(Finding(f"处方「{it.id}」还没处置（{it.trigger}）——{how}", "gate.route_pending"))
    return out
