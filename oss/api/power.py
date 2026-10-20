"""`oss power`：统计功效。效应量先按发表偏倚打折再算。

判词三档：低于下界判死、高于上界通过、**中间交给跨模型判**。
上下界按学科变——用一个值走天下就等于没有学科档案。

读 profile.json（还常读 binding.json）才算得动，所以走 `require_run`：
打错路径要拒绝，不许在一个凭空建出来的空目录上算出一个判词。
"""

from __future__ import annotations

import math
import shutil as _sh
from pathlib import Path

import numpy as np
import pandas as pd

from oss.api.errors import BadInput, Refused
from oss.api.result import result
from oss.api.rundir import emit, first_idea_id, read_json_file, require_run
from oss.api.sim import SIM_STATE
from oss.core import analysis as analysis_mod
from oss.core import datapacks as packs_mod
from oss.core import power as power_mod
from oss.core import simbox
from oss.core import state as state_mod
from oss.core.analysis import _level_text
from oss.core.contracts import (
    ARTIFACTS, AnalysisPlan, BindingReport, DataProfile, IdeaSet, MeasurementSpec, PowerReport)
from oss.core.findings import Finding
from oss.core.loaders import load_any

#: 描述类按精度判时，`--n` 跟分组一起用就拒（终审 M1）：`--n` 只折了整群（每簇 n/k 行），各格照数全文件——
#: 1200 行 40 簇的数据给 --n 300，半宽反倒从 0.144 变窄到 0.132，最小分组的有效样本量 350 比 n 还大
N_WITH_GROUPS = ("按子样本算请在 prep.py 里筛好，或写进计划的 sample_filter；"
                 "--n 跟分组一起用会让整群按 n 折、分组按全文件数，两头对不上")


def power(run: str, *, idea: str = "", effect: float = 0.2, effect_metric: str = "d",
          effect_source: str = "",
          n: int = 0, discipline: str = "", clusters: int = 0, icc: float = 0.05,
          out: str = "", cluster_col: str = "", treatment_col: str = "",
          treatment_icc: float = -1.0, alpha: float = 0.05, weight_col: str | None = None,
          group_col: str = "") -> dict:
    """算功效、落 power.json。`out` 另存一份（不覆盖）——一次研究常要算两个尺度。

    `idea` 不给：先取锚点当前的题（换过题就是新题），再取盘上计划写的题，都没有才取 ideas.json 的第一条（`_default_idea`）。

    `weight_col`：算权重设计效应（Kish）用的列。`None` 取画像检出的权重列；空串 = 不按权重折。

    **描述类题目按精度判、不按功效判**（描述骨架设计稿第四节）：研究类型取本题的 plan.json 的；计划没写，
    或盘上的计划是别的题的（换题后留下的），再取 ideas.json 里这一条的。`effect` 在描述类里读作「有意义的最小差别」
    （`effect_metric="raw"` 时按结局原单位给，按结局的加权标准差折算；按功效判时不认 raw）。`group_col`（逗号分隔，
    至多两列）给了就按这几列交叉数各格，主要比较换成排序第二类对参照组（排序第一类）之差（跟 describe 的主结果
    同一对比；本题的计划是 describe 的，按它的 `description.kind` 定，见 `_precision`）、最小分组单独报；`cluster_col`
    给了就按它数簇，组差的 ρx 也从它算。**本题有计划时，分组、整群、权重这三样没给的按计划取**（`_plan_defaults`），
    分析样本跟 oss run 同一套选行（`_run_rows`）；还没有计划，精度只数分析真会用到的行（`_precision`）。`n` 跟分组
    不能一起给（`N_WITH_GROUPS`，按计划取的分组也算）。不是描述类，`group_col` 不用、计划也不取。样本量不大于 0 直接拒。

    **测量类判「证据做不做得出来」、不算功效**（测量骨架设计稿第四节，见 `_evidence`）。
    主类型不是测量、但计划里写了测量块的，那三条判据照样核（`_also_measurement`）——
    **判据挂在测量块本身，不挂在声明**：只认主类型就能把测量搁进 `also` 躲开，挂在 `also` 上又能不填 `also` 躲开。
    """
    rundir = require_run(run)
    warnings: list[str] = []
    plan = _load_plan(rundir.file(ARTIFACTS["plan"]), warnings)
    idea_id = idea or _default_idea(rundir, plan, warnings)
    prof = DataProfile.load(rundir.file(ARTIFACTS["profile"]))
    clusters_given, n_given = clusters, n
    if n == 0:
        n = _usable_rows(rundir, prof, idea_id, warnings)
    if n <= 0:
        raise BadInput(f"样本量要大于 0，拿到 {n}（--n 给了负数，或画像里一行数据都没有）——算不出功效，也算不出精度")
    if clusters == 0:
        clusters = prof.structure.n_units or 0
    if clusters_given and clusters_given > n:
        # **簇比人还多是不可能的事实，不是口味问题。**而且它不报错，只是悄悄抬高功效：
        # 整群设计效应是 1+(m−1)·ρx·ρu，m = n/簇数 < 1 时 (m−1) 为负，设计效应掉到 1 以下，
        # 有效样本量被放大。真跑时把行数 11765 当簇数传进来，功效从 0.73 变成 0.90，判词从
        # gray 翻成 pass——一道本该拦住的门就这样开了。
        raise BadInput(
            f"簇数不能大于观测数：--clusters 给的是 {clusters_given}，而样本量是 {n}。"
            "簇是观测被分进去的组，组数至多等于观测数。"
            "**这个数填大了不会报错，只会把功效算高**——"
            "用 --cluster-col <列名> 让工具从数据里数，或核对 --clusters 的取值")
    if clusters > n:
        # 这一路的簇数是从画像的「单位数」拿的，而那一格记的未必是簇（整份文件的行数也会落进去），
        # 而 `n` 可能已经收窄到能用的行。**两边口径不同，不是用户断言了不可能的事**，所以不拒；
        # 但也绝不能照算——照算就是凭空放大有效样本量。**不按整群折算，并且说出来。**
        warnings.append(
            f"画像里的单位数是 {clusters}，大于样本量 {n}——它记的不是簇。"
            "本次不按整群折算（只折算权重），要按簇折算请用 --cluster-col <列名> 从数据里数")
        clusters = 0

    # 分组列多了先拒，别等算完才说
    rtype = _research_type(plan, rundir.file(ARTIFACTS["ideas"]), idea_id, warnings)
    descriptive, measurement = rtype == "descriptive", rtype == "measurement"
    simulation = rtype == "simulation"
    if effect_metric == power_mod.RAW_METRIC and not descriptive:
        raise BadInput("--effect-metric raw 只给描述类题目按精度判时用（有意义的差别按结局原单位给）；"
                       "按功效判的效应量用 d / r / beta——从文献搬来的效应量本来就是标准化的")
    if descriptive and (effect == 0 or not math.isfinite(effect)):
        raise BadInput(f"有意义的最小差别不能是 0（--effect 给了 {effect}）：它是这项描述要分辨的最小差别，"
                       "0 什么也分辨不了。给一个带出处或理由的差别")
    group_cols = [c.strip() for c in group_col.split(",") if c.strip()]
    if descriptive and len(group_cols) > 2:
        raise BadInput(f"--group-col 至多两列，给了 {len(group_cols)} 列：{'、'.join(group_cols)}")
    # 本题有计划：没给的分组列、整群列、权重列按计划取（F1 复审 S1）。只管描述类——功效那一路一行不动
    mine = plan if _owns(plan, idea_id) else None
    own = mine if descriptive else None
    plan_note, from_plan = "", False
    if own is not None:
        given = bool(group_cols)
        group_cols, cluster_col, weight_col, plan_note = _plan_defaults(
            own, prof, group_cols, cluster_col, weight_col, clusters_given)
        from_plan = bool(group_cols) and not given
        if plan_note:
            warnings.append(plan_note)
    if descriptive and n_given and group_cols:
        raise BadInput(N_WITH_GROUPS + (f"（分组列 {'、'.join(group_cols)} 是按本题的计划取的）" if from_plan else ""))
    # 主类型不是测量、计划里却有测量块：那三条照样核（`_also_measurement`）。放在算 ρx、算权重之前——
    # 做不出来就早停，别让人等完一轮计算才听见「测量那部分做不出来」
    also_note = "" if measurement else _also_measurement(mine, prof, warnings=warnings)
    # ρx 优先从数据算——它是可算的量，让人猜出来的数会一路传进论文局限。
    rho_x, rho_src = (treatment_icc, "由调用方给定") if treatment_icc >= 0 else (1.0, "")
    if not treatment_col:
        bpath = rundir.file(ARTIFACTS["binding"])
        if bpath.exists():
            b = BindingReport.load(bpath)
            treatment_col = next(
                (x.column for x in b.bindings if x.role == "treatment" and x.column), "")
    # 这一段只管功效。精度的整群另算（_precision：认 --cluster-col，组差用分组列的 ρx）
    if cluster_col and treatment_col and not descriptive:
        try:
            tbl = load_any(prof.file.path)
            if cluster_col in tbl.frame.columns and treatment_col in tbl.frame.columns:
                rho_x = power_mod.cluster_icc(tbl.frame[treatment_col], tbl.frame[cluster_col])
                rho_src = f"从数据算出（{treatment_col} 在 {cluster_col} 内）"
                if clusters == 0:
                    clusters = int(tbl.frame[cluster_col].nunique())
            else:
                warnings.append(f"数据里没有 {cluster_col!r} 或 {treatment_col!r}，ρx 用假定值")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"读数据算 ρx 失败（{exc}），改用假定值")

    # 权重设计效应（Kish）n·Σw²/(Σw)²：权重越不均，同样的行数含的信息越少。只取非缺失、为正的权重；读不了就按 1 算
    wcol = (prof.weight_column or "") if weight_col is None else weight_col.strip()
    wdeff = 1.0
    if wcol:
        try:
            tbl = load_any(prof.file.path)
            if wcol not in tbl.frame.columns:
                warnings.append(f"数据里没有权重列 {wcol!r}，权重设计效应按 1 算")
                wcol = ""
            else:
                w = pd.to_numeric(tbl.frame[wcol], errors="coerce")
                w = w[w > 0]
                if len(w):
                    wdeff = float(len(w) * (w ** 2).sum() / w.sum() ** 2)
                else:
                    warnings.append(f"权重列 {wcol!r} 没有为正的值，权重设计效应按 1 算")
                    wcol = ""
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"读数据算权重设计效应失败（{exc}），按 1 算")
            wcol = ""

    if group_cols and not descriptive:
        warnings.append("--group-col 只在描述类题目按精度判时用；这道题不是描述类，没用上")

    extra_args: dict = {}
    if descriptive:
        rep, group_cols = _precision(
            prof, own, rundir.file(ARTIFACTS["binding"]), idea_id,
            effect=effect, effect_metric=effect_metric, effect_source=effect_source, n=n, n_given=n_given,
            discipline=discipline, alpha=alpha, clusters=clusters, clusters_given=clusters_given, icc=icc,
            cluster_col=cluster_col, group_cols=group_cols, wcol=wcol, wdeff=wdeff, plan_note=plan_note,
            warnings=warnings)
        if not rep.effect_size_source:
            warnings.append("有意义的差别没给出处（--effect-source），判词封顶为灰区；论文里写这个数要带出处或理由")
        diff = (f"{rep.effect_size_raw:g}（结局原单位）= {rep.effect_size_adjusted:g} 个标准差"
                if rep.effect_metric == power_mod.RAW_METRIC else f"{rep.effect_size_adjusted:g} 个标准差")
        note = f"精度 ±{rep.precision_halfwidth:.3f} 个标准差（有意义的差别 {diff}），判词 {rep.verdict}"
        extra_args = {"mode": rep.mode, "group_col": ",".join(group_cols)}
    elif measurement:
        rep, note = _evidence(mine, prof, idea_id, n=n, warnings=warnings)
        extra_args = {"mode": rep.mode}
    elif simulation:
        rep, note = _readiness(mine, rundir, idea_id, n=n, warnings=warnings)
        extra_args = {"mode": rep.mode}
    else:
        rep = power_mod.analyze(
            idea_id, effect, n, discipline=discipline, alpha=alpha,
            n_clusters=clusters or None, icc=icc,
            treatment_icc=rho_x, treatment_icc_source=rho_src,
            effect_metric=effect_metric, effect_size_source=effect_source,
            weight_design_effect=wdeff, weight_column=wcol)
        if not rep.effect_size_source:
            warnings.append(
                "效应量没给出处（--effect-source），判词封顶为灰区；"
                "论文里不许写「按文献报告的效应量」")
        note = f"功效 {rep.power:.2f}，判词 {rep.verdict}（{rep.discipline} 灰区 {rep.gray_band}）"
    note += also_note
    path = emit(rundir, "power", "oss power", "power", rep, note=note, args={
        "idea_id": rep.idea_id, "effect_raw": rep.effect_size_raw,
        "n": rep.n_raw, "clusters": rep.n_clusters, "icc": rep.icc,
        "treatment_icc": rep.treatment_icc, "treatment_icc_source": rep.treatment_icc_source,
        "effect_metric": rep.effect_metric, "effect_source": rep.effect_size_source} | extra_args)

    extra = ""
    if out:
        target = rundir.file(out)
        _sh.copy(rundir.file(ARTIFACTS["power"]), target)
        extra = str(target)
    return result(note, {
        "run": str(rundir.path), "artifact": ARTIFACTS["power"], "path": str(path),
        "extra_path": extra, "idea_id": rep.idea_id, "verdict": rep.verdict,
        "power": rep.power, "gray": rep.verdict == "gray", "gray_band": list(rep.gray_band),
        "discipline": rep.discipline, "reason_lines": rep.reason.split("\n"),
        "weight_design_effect": rep.weight_design_effect, "weight_column": rep.weight_column,
        "mode": rep.mode, "precision_halfwidth": rep.precision_halfwidth,
        "min_cell_n_eff": rep.min_cell_n_eff, "min_cell_label": rep.min_cell_label,
        "warnings": warnings,
    })


def _default_idea(rundir, plan: AnalysisPlan | None, warnings: list[str]) -> str:
    """不写 --idea 时算哪道题：锚点当前的题 → 盘上计划写的题 → ideas.json 的第一条。只认 ideas.json 里有的题号。

    原先一律取第一条（终审 M2）：换题（oss anchor switch）后锚点指新题，oss power 算的却是旧题——写出旧题的精度报告，
    还把新题的计划说成「旧计划」；route 开 power_mode_mismatch，处方说重跑 oss power，重跑的还是旧题，永远消不掉。
    环节 4 还没冻锚点、也还没有计划，落到第一条，跟原先一样。取的不是第一条时说一声。
    锚点只读不隔离（`state.peek_anchor_idea`）；它指向 ideas.json 里没有的题，不按它算、说一声（F1 复审 M2-3）。
    """
    first = first_idea_id(rundir)                   # 缺 ideas.json、一条都没有照旧抛 Missing
    known = {i.idea_id for i in IdeaSet.load(rundir.file(ARTIFACTS["ideas"])).ideas}
    anchor = state_mod.peek_anchor_idea(rundir.path)
    if anchor and anchor not in known:
        warnings.append(f"锚点指向的题 {anchor} 不在 ideas.json 里，没按它算——"
                        "查一下 run_state.json 的锚点跟 ideas.json 对不对得上")
    for where, cand in (("锚点当前的题", anchor), ("盘上计划写的题", plan.idea_id if plan else "")):
        if cand and cand in known:
            if cand != first:
                warnings.append(f"没给 --idea：按{where} {cand} 算（ideas.json 的第一条是 {first}）")
            return cand
    return first


def _load_plan(plan_path: Path, warnings: list[str]) -> AnalysisPlan | None:
    """盘上的计划；没有或读不了返回 None。

    读不了的说一声——功效这一步不该因为一份格式坏了的计划失败，但也不许悄悄换了判法。
    """
    if not plan_path.exists():
        return None
    try:
        return AnalysisPlan.load(plan_path)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{plan_path.name} 读不了（{type(exc).__name__}），研究类型改按 ideas.json 取")
        return None


def _owns(plan: AnalysisPlan | None, idea_id: str) -> bool:
    """这份计划是不是这道题的。**换题（oss anchor switch）后旧题的计划还留在盘上**：它的研究类型、抽样设计、
    结局都不算新题的——换题时锚点同样不认它（oss/api/anchor.py）。没写 idea_id 的老计划照旧认。"""
    return plan is not None and plan.idea_id in ("", idea_id)


def _research_type(plan: AnalysisPlan | None, ideas_path: Path, idea_id: str, warnings: list[str]) -> str:
    """选中题目的研究类型：本题的计划写了就按计划（因果降为描述只改计划），否则按 ideas.json 里这一条。

    盘上的计划是别的题的（换题后留下的）：它写的类型不算，跟 ideas.json 里这一条不一样时说一声。
    读不了的文件按没声明算并说一声。
    """
    if plan is not None and _owns(plan, idea_id) and plan.research_type:
        return plan.research_type
    declared = ""
    if ideas_path.exists():
        try:
            declared = next((i.research_type for i in IdeaSet.load(ideas_path).ideas if i.idea_id == idea_id), "")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{ideas_path.name} 读不了（{type(exc).__name__}），研究类型按没声明算")
    if plan is not None and not _owns(plan, idea_id) and plan.research_type and plan.research_type != declared:
        warnings.append(
            f"{ARTIFACTS['plan']} 是 {plan.idea_id} 的计划（换题后留在盘上的旧计划），它写的研究类型 "
            f"{plan.research_type} 不算 {idea_id} 的——按 ideas.json 里这一条（{declared or '没声明'}）判")
    return declared


#: 「有没有可对账的外部指标」不合格时的处方。**警告要直接指向药，不能只描述病。**
_NO_CRITERION_FIX = ("两条路二选一：另找一份带外部指标的数据合并进来（oss data）再声明 criterion；"
                     "或把 criterion 从 validity_evidence 里去掉，只报这份数据做得出来的那几类"
                     "（content 内容 / structural 结构 / discriminant 区分），论文里就只说那几类的话")


def _evidence(own_plan: AnalysisPlan | None, prof: DataProfile, idea_id: str, *,
              n: int, warnings: list[str]) -> tuple[PowerReport, str]:
    """测量研究的环节 4：判「证据做不做得出来」，不判统计量（测量骨架设计稿第四节）。返回 `(报告, 判词那一句)`。

    因果算**功效**（能不能测出效应）、描述判**精度**（能分辨多大的差别）；**测量研究的可行性不是样本量问题，
    是手头的数据支不支持你要给的那种效度证据**。三条机械可判的：

    - **题目数够不够**（停）：造尺子（`kind=instrument`）而 `indicators` 少于 3 道。
      **两道题的量表给不出结构效度**——两个指标的因子模型识别不了（待估参数多于协方差矩阵里的信息，自由度为负），
      做不出因子结构，剩下的只是一个相关系数。这不是「样本量再大一点就行」，是这份数据本身做不出那种证据
    - **跨组比较有没有分组变量**（停）：声明了 `groups_compared`，列却不在数据里、整列缺失、或只有一个取值
    - **有没有可对账的外部指标**（警告 + 处方）：造尺子且声明要报效标关联效度（`criterion`），
      数据里却没有别的可用变量。**不停**——外部指标常要另找一份数据，停在这里等于不许做

    前两条当场停（`Refused`，理由带编号）；能走到落盘的，判词恒为 pass。
    **功效那一路对测量类关掉**：不算效应量、不打发表偏倚的折、不算最小可测效应、不出功效判词，
    所以 `--effect` 与 `--effect-source` 在这一路上都不读。

    还没有本题的测量块（环节 4 常常还没写计划）：三条都核不了，说出来、不停。
    """
    spec = own_plan.measurement if own_plan is not None else None
    bits = ["测量研究判「证据做不做得出来」，不判统计量：可行性不是样本量问题，"
            "是手头的数据支不支持你要给的那种效度证据（测量骨架设计稿第四节）。"
            "**这一路不算功效**：效应量、最小可测效应、功效判词都不出",
            f"数据：{n} 行"]
    if spec is None:
        warnings.append("还没有本题的测量块（plan.measurement）：题目数够不够、跨组比较有没有分组变量、"
                        "有没有可对账的外部指标——三条都核不了。写好测量块之后重跑 oss power")
        bits.append("**三条判据一条都没核**：本题还没有测量块（plan.measurement）。这不是「过了」，是「还没判」")
        return _evidence_report(idea_id, n, bits), "测量类判「证据做不做得出来」：还没有测量块，三条都没核"

    problems, more, said = _evidence_checks(spec, prof, warnings=warnings)
    bits += more
    if problems:
        # 理由也拼进 summary：`oss power` 的 CLI 只打 summary 那一行（跟 `_refused` 同一个办法），
        # 光给一句「做不出来」，照着改的人看不见该改什么
        raise Refused("测量类判「证据做不做得出来」，这份数据做不出来："
                      + "；".join(problems), code=1, problems=problems, data={"warnings": warnings})
    bits.append(f"判词：{said}——按声明的那几类效度证据，这份数据做得出来")
    return _evidence_report(idea_id, n, bits), f"测量类判「证据做不做得出来」：{said}"


def _evidence_checks(spec: MeasurementSpec, prof: DataProfile, *,
                     warnings: list[str]) -> tuple[list[str], list[str], str]:
    """那三条判据本身：`(停的理由, 逐条说明, 判词那半句)`；**只警告的那一条直接进 `warnings`**。

    单拆出来是因为它有两个调用方：主类型就是测量（`_evidence`），以及**主类型是别的、但计划里写了测量块**
    （`_also_measurement`）。判据只按主类型分路的话，主类型写 causal、测量搁进 `also`，
    三条一条都不核——跟计划门上堵掉的第八个洞完全同形。
    """
    known = {v.name for v in prof.variables}
    empty = set(prof.all_missing_columns)
    inds = [c for c in spec.indicators if c.strip()]
    problems: list[str] = []
    bits: list[str] = []

    if spec.kind == "instrument" and len(inds) < 3:
        problems.append(Finding(
            f"造尺子（kind=instrument）只写了 {len(inds)} 道题（{'、'.join(inds) or '一道都没有'}）——"
            f"**两道题的量表给不出结构效度**：两个指标的因子模型识别不了（自由度为负），"
            f"做不出因子结构，剩下的只是一个相关系数。样本量再大也补不上这一条。"
            f"至少三道：回去找题，或改成用现成量表做实质研究（kind=application）",
            "plan.measurement.too_few_indicators"))
    bits.append(f"题目数：{len(inds)} 道（{'、'.join(inds)}）"
                + ("——造尺子至少三道，做得了因子结构" if spec.kind == "instrument"
                   else "；这一条只卡造尺子（kind=instrument），这份计划不是"))

    groups = [c for c in spec.groups_compared if c.strip()]
    for g in groups:
        if g not in known or g in empty:
            near = analysis_mod._nearest(g, known)
            problems.append(Finding(
                f"跨组比较的分组变量「{g}」"
                + ("整列都是缺失" if g in known else "不在数据里" + (f"，最接近的是 {near}" if near else ""))
                + "——跨组比较比不了：换一列，或把 groups_compared 留空、这篇只做组内的测量",
                "plan.measurement.group_missing"))
        elif (v := prof.var(g)) is not None and v.n_unique <= 1:
            problems.append(Finding(
                f"跨组比较的分组变量「{g}」整列只有 {v.n_unique} 个取值——只有一组，比不了。"
                f"换一列，或把 groups_compared 留空、这篇只做组内的测量",
                "plan.measurement.group_missing"))
    bits.append(f"跨组比较的分组变量：{'、'.join(groups)}——都在数据里，且不止一组" if groups
                else "跨组比较：没声明（groups_compared 空着），这一条不核")

    if problems:
        return problems, bits, ""

    lacking = False
    if spec.kind == "instrument" and "criterion" in spec.validity_evidence:
        others = sorted(known - set(inds) - empty)
        lacking = not others
        if lacking:
            warnings.append(Finding(
                "声明要报效标关联效度（criterion），数据里却没有别的可用变量可以对账——"
                "除了这几道题本身，一列都没有。" + _NO_CRITERION_FIX,
                "plan.measurement.no_criterion_var"))
            bits.append("外部对账指标：**一列都没有**（声明了 criterion，数据里除指标之外没有可用的列）——警告，不停")
        else:
            bits.append(f"外部对账指标：数据里还有 {len(others)} 列可对账"
                        f"（{'、'.join(others[:5])}{'…' if len(others) > 5 else ''}）")

    return problems, bits, "两条停的都过，外部对账指标只警告" if lacking else "三条都过"


def _also_measurement(own_plan: AnalysisPlan | None, prof: DataProfile, *, warnings: list[str]) -> str:
    """主类型不是测量、计划里却有测量块：环节 4 那三条照样核。返回拼进判词那一行的一句（没得核就空串）。

    **判据挂在测量块本身，不挂在声明**——跟计划门那三条（`_measurement_plan_problems` 的 ②③④）同一个口径：
    这一块是谁写的谁就得对得上。只认 `research_type == "measurement"` 的话，主类型写 causal、
    测量搁进 `also`，题目数够不够、分组变量在不在，一条都不核；而挂在 `also` 上，连 `also` 都不填就又躲开了。
    写了块就核，**少填哪一栏都躲不掉**。

    主类型那一路一行不动：功效还是功效、精度还是精度——契约里 `also` 的说法就是「只加它那类的检查」。
    声明了附带测量却没写块：三条核不了，说一声、不停（拒是计划门 `spec_missing` 的事）。
    """
    if own_plan is None:
        return ""
    spec = own_plan.measurement
    if spec is None:
        if "measurement" not in own_plan.also:
            return ""
        warnings.append("附带类型里列了测量（plan.also），却没写测量块（plan.measurement）：题目数够不够、"
                        "跨组比较有没有分组变量、有没有可对账的外部指标——三条都核不了。写好之后重跑 oss power")
        return "；附带的测量部分：没有测量块，三条都没核"
    problems, _bits, said = _evidence_checks(spec, prof, warnings=warnings)
    if problems:
        raise Refused("计划里的测量块做不出它声明的证据（主类型另算，这一条只管测量那部分）："
                      + "；".join(problems), code=1, problems=problems, data={"warnings": warnings})
    return f"；附带的测量部分：{said}"


def _evidence_report(idea_id: str, n: int, bits: list[str]) -> PowerReport:
    """evidence 模式的报告：**除了行数与理由，一个统计量都不写**——效应量、有效样本量、最小可测效应、
    功效全留 0，免得下游把一个没算过的数当成算过的。"""
    return PowerReport(idea_id=idea_id, n_raw=int(n), mode="evidence", power=0.0,
                       verdict="pass", reason="\n".join(bits))


#: 三条判据不合格时的处方。**警告要直接指向药，不能只描述病**（跟 `_NO_CRITERION_FIX` 同一条规矩）。
_SINGLE_CONDITION_FIX = ("两条路二选一：货箱里补一个对照条件重跑（配料单 conditions 加一条），"
                         "或者把这篇改成单条件的模式展示——正文只说「这套机制在这一组参数下产生了什么」，"
                         "不出现任何条件间比较的说法")
_LOW_REPS_FIX = ("两条路二选一：每个条件补到至少 3 次重复重跑；"
                 "或者照降级写——**全文**（不只是局限那一节）改用「初步 / 探索性」的措辞，"
                 "局限节逐字写上「单次模拟无法区分随机波动与真实规律」")
_NO_BENCHMARK_FIX = ("两条路二选一：把那份真实数据备好（`oss data get <数据包名>`，或放进运行目录）；"
                     "或者把 stands_in_for_humans 改成 false，正文只说「在本模型内」的话——"
                     "**没有对照就核不出方差有没有被压扁**，而那正是拿合成数据当调查数据用最容易出事的地方")


def _readiness(own_plan: AnalysisPlan | None, rundir, idea_id: str, *,
               n: int, warnings: list[str]) -> tuple[PowerReport, str]:
    """仿真研究的环节 4：判「这个模型跑得出可判的东西吗」，不算功效（仿真骨架设计稿第三节）。

    因果算**功效**（能不能测出效应）、描述判**精度**、测量判**「证据做不做得出来」**；
    **仿真的可行性根本不是样本量问题**——想跑多少个小人就跑多少个，跑十万个也不会让结论更可信。
    真正的可行性是**跑出来的东西够不够分辨条件之间的差别**。三条：

    - **条件够不够**：配料单只有一个条件 —— 单条件只能做模式展示，做不了条件间比较
    - **重复够不够**：某条件 `n_reps` < 3（`simbox.REP_FLOOR`）—— 全文要降级为「初步 / 探索性」
    - **对照物在不在**：主张合成样本代表真人（`stands_in_for_humans`），而声明的那份真实数据不在

    **前两条不重算**：`simbox.check_box` 早就算出了 `single_condition` 与 `downgrade`，
    这里读 `sim_state.json` 就行——判据只有一处，环节 4 做的是把它从「入库时说一声」
    提到「判可行时写进判词」。

    **三条都只警告、不停**，跟测量那一路不同：两道题的量表在数学上就给不出结构效度，
    所以那边停；而单条件与重复不足**都是正当研究**，正确答案是把话说软，不是不许做
    （2026-08-31 用户拍板「收但降级」，这一条照旧）。

    **还没入库**（环节 4 常常在花钱跑模拟之前）：三条都核不了，说出来、不停。

    「主张代表真人」的另一条判据——**运行目录里货箱与真实数据包同时在**——放在环节 6
    （`analysis._simulation_plan_warnings`）：那时候数据确实在盘上，算得出方差比。
    """
    bits = ["仿真研究判「这个模型跑得出可判的东西吗」，不算功效：可行性不是样本量问题"
            "（想跑多少个小人就跑多少个），是跑出来的东西够不够分辨条件之间的差别"
            "（仿真骨架设计稿第三节）。**这一路不算功效**：效应量、最小可测效应、功效判词都不出",
            f"数据：{n} 行"]
    spec = own_plan.simulation if own_plan is not None else None
    state = read_json_file(rundir.file(SIM_STATE)) if rundir.file(SIM_STATE).exists() else None

    if not isinstance(state, dict):
        warnings.append("还没有入库的货箱（sim_state.json）：条件够不够、重复够不够、对照物在不在——"
                        "三条都核不了。跑完模拟 `oss sim import` 之后重跑 oss power")
        bits.append("**三条判据一条都没核**：还没有入库的货箱。这不是「过了」，是「还没判」")
        return _readiness_report(idea_id, n, bits), "仿真类判「跑得出可判的东西吗」：还没入库，三条都没核"

    meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    conds = meta.get("conditions") if isinstance(meta.get("conditions"), dict) else {}

    if (stale := _state_stale(state)):
        warnings.append(Finding(
            f"入库档案（sim_state.json）跟货箱对不上：{stale}——**下面三条判的是入库那一刻的货箱，不是现在的**。"
            f"改完配料单或重跑过模拟就要重新入库：`oss sim import --box <货箱> --run <运行目录>`",
            "plan.simulation.state_stale"))
        bits.append(f"入库档案：**跟货箱对不上**（{stale}）——下面三条判的是入库那一刻的货箱")

    if state.get("single_condition"):
        warnings.append(Finding(
            f"货箱只有一个条件（{'、'.join(conds) or '配料单 conditions 里只有一条'}）——"
            f"**单条件跑不出条件间比较**：没有对照条件，「这个机制起了作用」跟「本来就会这样」"
            f"分不开。" + _SINGLE_CONDITION_FIX, "plan.simulation.single_condition"))
        bits.append("条件数：**只有一个**——只能做模式展示，不能做条件间比较（警告，不停）")
    else:
        bits.append(f"条件数：{len(conds)} 个（{'、'.join(conds)}）——比得了")

    if state.get("downgrade"):
        warnings.append(Finding(
            f"有条件的重复次数不足 {simbox.REP_FLOOR} 次——**单次模拟分不开随机波动与真实规律**："
            f"跑一次看到的差别，可能只是这一次的随机数。" + _LOW_REPS_FIX,
            "plan.simulation.low_reps"))
        bits.append(f"重复次数：**不足 {simbox.REP_FLOOR} 次**——全文要降级为「初步 / 探索性」（警告，不停）")
    else:
        bits.append(f"重复次数：每条件至少 {simbox.REP_FLOOR} 次——够分开随机波动")

    if spec is None:
        bits.append("对照物：没有仿真块（plan.simulation），这一条不核")
    elif not spec.stands_in_for_humans:
        bits.append("对照物：没主张这批小人代表真人（stands_in_for_humans=false），这一条不核——"
                    "面向模式的仿真本来就不需要真人对照")
    elif (miss := _benchmark_missing(spec.real_benchmark, rundir)):
        warnings.append(Finding(
            f"主张这批合成样本代表真人（stands_in_for_humans=true），声明的对照数据却{miss}——"
            f"**合成样本的方差会被压扁**（Bisbee 2024），拿它当调查数据用，标准误会假小、"
            f"显著性会假强。" + _NO_BENCHMARK_FIX, "plan.simulation.no_benchmark"))
        bits.append(f"对照物：**{miss}**（警告，不停）")
    else:
        bits.append(f"对照物：{spec.real_benchmark.data} 备着，"
                    f"{len(spec.real_benchmark.pairs)} 对列可对账")

    said = "三条都过" if not warnings else "过不了的都只警告不停（单条件与重复不足都是正当研究，把话说软即可）"
    bits.append(f"判词：{said}")
    return _readiness_report(idea_id, n, bits), f"仿真类判「跑得出可判的东西吗」：{said}"


def _state_stale(state: dict) -> str:
    """入库档案跟货箱现在的配料单对不上就返回一句人话，对得上（或核不了）返回空串。

    **三条判据全读 `sim_state.json`，而它只是运行目录里一个普通 JSON**：货箱改完配料单、
    或者重跑过模拟，不重新入库，它就停在入库那一刻——`downgrade` 还是旧的 false，
    三条一声不响。这不是谁要作弊，是**入库档案会过期**这件事本身没人管。

    所以判据不能只信档案：货箱还在盘上的，拿它现在的 `sim.meta.json` 对一遍。
    货箱搬走了或读不了就**不报**——那时候核不了，报一句「对不上」是冤枉。
    """
    box = state.get("box")
    if not isinstance(box, str) or not box.strip():
        return ""
    meta_path = Path(box) / "sim.meta.json"
    if not meta_path.exists():
        return ""
    now = read_json_file(meta_path)
    if not isinstance(now, dict):
        return ""
    was = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    if now == was:
        return ""
    changed = sorted(k for k in set(now) | set(was) if now.get(k) != was.get(k))
    return "货箱的配料单已经改过：" + "、".join(changed)


def _benchmark_missing(bench, rundir) -> str:
    """声明的真实对照数据在不在：不在就返回一句人话，在就返回空串。

    两种写法都认——**数据包名**（`oss data` 那套，要已经转好落到本地）与
    **运行目录里的文件名**。两条都不是就说「既不是数据包名也不是运行目录里的文件」：
    作者最常见的错是把数据包的**标题**写进来（「中国综合社会调查」而不是 `cgss2017`）。
    """
    if bench is None or not bench.data.strip():
        return "一栏都没写"
    name = bench.data.strip()
    if name in packs_mod.PACKS:
        return "" if packs_mod.status(name) == "local" else f"数据包 {name} 还没备好（`oss data get {name}`）"
    return "" if rundir.file(name).exists() else f"「{name}」既不是数据包名、也不是运行目录里的文件"


def _readiness_report(idea_id: str, n: int, bits: list[str]) -> PowerReport:
    """readiness 模式的报告：跟 evidence 一样，**除了行数与理由一个统计量都不写**。"""
    return PowerReport(idea_id=idea_id, n_raw=int(n), mode="readiness", power=0.0,
                       verdict="pass", reason="\n".join(bits))


def _precision(prof: DataProfile, own_plan: AnalysisPlan | None, binding_path: Path, idea_id: str, *,
               effect: float, effect_metric: str, effect_source: str, n: int, n_given: int, discipline: str,
               alpha: float, clusters: int, clusters_given: int, icc: float, cluster_col: str,
               group_cols: list[str], wcol: str, wdeff: float, plan_note: str,
               warnings: list[str]) -> tuple[PowerReport, list[str]]:
    """描述类的精度（描述骨架设计稿第四节）。返回 `(报告, 实际用上的分组列)`。读数据做几件功效那一路不做的事：

    - **分析样本**（终审 M1、M3；F1 复审）：n、簇数、各格、各格的权重设计效应与组差的 ρx、结局的标准差，都只数分析真会
      用到的行。本题有计划：就是 oss run 用的那批行（`_run_rows`，借 `analysis._ols_rows`），oss run 会拒的精度也拒。
      还没有计划：去掉结局缺失、权重缺失或不为正、簇列缺失、分组列缺失的行（`_drop_missing`）。原先按全文件数：结局缺
      三成时精度乐观约 √0.7 倍。去掉多少行、为什么，判词里写。给了 `--n`（这时不许给分组）的，样本量按它取，别的照样按分析样本算
    - **整群**：给了 `--cluster-col`（本题有计划时没给就按计划取，见 `_plan_defaults`）就按它数簇（非缺失的不同取值），
      不管有没有处理列——描述研究没有处理列，而截面数据画像的「单位数」就是行数，不数的话整群设计效应恒为 1。
      没有整群信息、画像认出疑似初级抽样单位的，提示加 `--cluster-col`（疑似的列不自动用）。簇数不少于行数（截面没给簇列时
      画像的单位数就是行数、簇列给成了个人编号）按没有整群算：power.json 不记簇数、判词不写聚类（终审 M8），提醒只一条
    - **分组**：各格的行数、各格自己的权重设计效应、组差的 ρx（见 `_group_cells`）
    - **主要比较**（终审 M5、M4）：本题的计划是 describe 的，按它的 `description.kind` 定——gap 是排序第二类对参照组之差；
      level、distribution 的主结果是全样本均值，按总体水平判、分组只报最小分组；trend 仍按首两期之差算，判词写明是近似。
      计划是描述类、估计量不是 describe（因果降为描述的 ols_fe 这类）又没给 `--group-col` 的见 `_association_groups`。
      没有计划（环节 4 多半还没有）照旧：不给分组是总体水平，给了是组差
    - **结局的标准差**：`--effect-metric raw` 靠它把原单位折成标准差单位；d 模式有它时判词另写原单位的半宽
    - `plan_note`：按本题的计划取了哪几样（`_plan_defaults` 写好），判词里说一次，放在分析样本那一句前面
    """
    ycol = _outcome_col(binding_path, own_plan, idea_id)
    raw = effect_metric == power_mod.RAW_METRIC
    assoc = own_plan is not None and own_plan.estimator not in ("", "describe")
    desc = own_plan.description if own_plan is not None and own_plan.estimator == "describe" else None
    kind = desc.kind if desc is not None else ""
    frame = None
    if cluster_col or group_cols or ycol or wcol or own_plan is not None:
        try:
            frame = load_any(prof.file.path).frame
        except Exception as exc:  # noqa: BLE001
            if raw:
                raise BadInput(f"读数据失败（{exc}），按结局原单位给的差别折不成标准差——"
                               "改用标准差单位（--effect-metric d，--effect 给几个标准差）") from exc
            warnings.append(f"读数据失败（{exc}），整群、分组与结局的标准差都按没有算，精度按总体水平算")

    main_note = sample_note = ""
    if frame is not None:
        total = len(frame)
        if own_plan is not None:
            frame, steps = _run_rows(frame, own_plan)
            if assoc and not group_cols:
                group_cols, main_note = _association_groups(frame, own_plan, ycol, n_given, warnings)
            why = "跟 oss run 同一套选行"
        else:
            frame, steps = _drop_missing(frame, ycol, wcol, cluster_col, group_cols)
            why = "还没有本题的计划，先按这几条去掉（写了计划重跑 oss power，就按 oss run 的选行算）"
        if frame.empty:
            raise BadInput(f"分析样本一行都不剩（全文件 {total} 行，{'，'.join(steps)}）——先查数据与样本筛选")
        rows = len(frame)
        if not n_given:
            n = rows
        if steps or n_given:
            sample_note = (f"分析样本 {rows} 行"
                           + (f"：全文件 {total} 行，{'，'.join(steps)}——{why}，精度只按分析样本算" if steps else "")
                           + (f"；样本量按 --n 取 {n}，簇数、权重设计效应与结局的标准差按这 {rows} 行算" if n_given else ""))
        if wcol and wcol in frame.columns:
            w = pd.to_numeric(frame[wcol], errors="coerce")
            w = w[w > 0]
            if len(w):
                wdeff = float(len(w) * (w ** 2).sum() / w.sum() ** 2)
    sample_note = "。".join(x for x in (plan_note, sample_note) if x)

    cvals = None
    uid = prof.structure.unit_id or ""
    if cluster_col and frame is not None:
        if cluster_col in frame.columns:
            cvals = frame[cluster_col]
            k = int(cvals.dropna().nunique())
            if clusters_given and clusters_given != k:
                warnings.append(f"--clusters {clusters_given} 跟按 --cluster-col {cluster_col} 数出的 {k} 个簇对不上，"
                                "按数出来的算")
            clusters = k
        else:
            warnings.append(f"数据里没有聚类列 {cluster_col!r}，没按它数簇")
    elif not clusters_given and frame is not None and uid and uid in frame.columns:
        clusters = int(frame[uid].dropna().nunique())      # 画像的单位数按全文件数；分析样本少了行，单位也可能少
    hint: list[str] = []
    src = ""
    if not cluster_col and not clusters_given:
        # 本题的计划写了 survey.psu 的，power() 已经按它取了整群列（_plan_defaults）；走到这里只剩画像的疑似列
        hint = list(prof.survey.psu_candidates)
        if hint:
            src = f"画像认出疑似初级抽样单位 {'、'.join(hint)}"
    if clusters and clusters >= n:
        if cvals is not None:
            warnings.append(f"每簇一行，整群没算：{clusters} 个簇对 {n} 行，整群设计效应按 1 算"
                            f"（--cluster-col {cluster_col} 的每个取值只有一行：给成了个人编号？）")
        elif clusters_given:
            warnings.append(f"每簇一行，整群没算：{clusters} 个簇对 {n} 行，整群设计效应按 1 算")
        elif not cluster_col:
            where = (f"{src}，加 --cluster-col {hint[0]}（疑似的列不自动用，要你确认）" if hint
                     else "整群抽样的数据（多数调查都是）给 --cluster-col <簇列>")
            warnings.append(f"没给整群信息，整群没算：当每人独立抽样、整群设计效应按 1 算，精度偏乐观——{where}")
        clusters = 0                                        # 不写聚类：「1200 个观测聚类到 1200 个单位」没有意义
    elif hint:
        warnings.append(f"没给整群信息，{src}——描述类的精度要按整群折，加 --cluster-col {hint[0]}"
                        "（疑似的列不自动用，要你确认；不加就当每人独立抽样，精度偏乐观）")

    sizes: list[int] = []
    labels: list[str] = []
    gdeffs: list[float] | None = None
    rho, rho_src = 1.0, ""
    if group_cols and frame is not None:
        sizes, labels, gdeffs, rho, rho_src = _group_cells(frame, group_cols, wcol, cvals, cluster_col, warnings)

    level_main = kind in ("level", "distribution")
    two = sum(1 for s in sizes if s > 0) >= 2
    if level_main:
        main_note = (f"计划的 description.kind 是 {kind}：describe 的主结果是全样本均值，主要比较按总体水平判"
                     + ("，分组只用来报最小分组" if two else ""))
    elif kind == "trend" and desc is not None:
        main_note = (f"趋势的精度按首两期之差近似：describe 的主结果是 {ycol or '结局'} 对 {desc.over} 的线性斜率，"
                     "这里拿排序头两期之差代替" if two else
                     f"趋势的精度按总体水平近似：describe 的主结果是对 {desc.over} 的线性斜率——"
                     f"给 --group-col {desc.over} 就按首两期之差近似")

    sd, sd_w = 0.0, False
    if frame is not None and ycol and ycol in frame.columns:
        sd, sd_w = _outcome_sd(frame[ycol], frame[wcol] if wcol else None)
    if raw:
        if not ycol:
            raise BadInput("--effect-metric raw 要按结局的标准差把差别折成标准差单位，可不知道结局是哪一列："
                           "binding.json 没绑 outcome、本题的计划也没写——先 oss bind，"
                           "或改用标准差单位（--effect-metric d，--effect 给几个标准差）")
        if frame is None or ycol not in frame.columns:
            raise BadInput(f"结局列 {ycol!r} 不在数据里，按结局原单位给的差别折不成标准差——"
                           "改用标准差单位（--effect-metric d，--effect 给几个标准差）")
        if not sd > 0:
            raise BadInput(f"结局列 {ycol!r} 算不出标准差（能用的数值不到两个，或全是同一个值），"
                           "按结局原单位给的差别折不成标准差——改用标准差单位（--effect-metric d）")
    # 中位数计划：半宽用自助法当场算（analysis.median_halfwidth，跟 oss run 同一段代码），
    # 不拿均值的解析公式糊弄过去——那是把判词挂在一个算错的数上
    half_override = half_note = None
    if (frame is not None and own_plan is not None
            and (got := analysis_mod.median_halfwidth(frame, own_plan)) is not None):
        raw_half, half_note = got
        if sd > 0:
            half_override = raw_half / sd          # 判词按标准差单位比，换算过去
        else:
            half_note += "；结局算不出标准差，半宽折不成标准差单位，仍按解析公式判"
            half_note, half_override = half_note, None

    try:
        rep = power_mod.analyze_precision(
            idea_id, effect, n, discipline=discipline, alpha=alpha, n_clusters=clusters or None, icc=icc,
            effect_metric=effect_metric, effect_size_source=effect_source,
            weight_design_effect=wdeff, weight_column=wcol, group_sizes=sizes, group_labels=labels,
            group_weight_deffs=gdeffs, gap_rho_x=rho, gap_rho_x_source=rho_src,
            outcome_sd=sd, outcome_sd_weighted=sd_w,
            level_main=level_main, main_note=main_note, sample_note=sample_note,
            half_override=half_override, half_note=half_note or "")
    except ValueError as exc:
        raise BadInput(str(exc)) from exc
    return rep, group_cols


def _plan_defaults(plan: AnalysisPlan, prof: DataProfile, group_cols: list[str], cluster_col: str,
                   weight_col: str | None, clusters_given: int) -> tuple[list[str], str, str | None, str]:
    """本题有计划时，命令行没给的按计划取（F1 复审 S1）。返回 `(分组列, 整群列, 权重列, 说明)`；说明是空串 = 一样也没取。

    原先照 route 的处方不带参数重跑 `oss power --idea <题>`，组差计划落回总体水平、也不算整群：区县整群的城乡差
    该判 fail（半宽 0.144），判成了 pass（0.064），precision_low 永远不开。给了的照给的：

    - 分组列：describe 的分组（`analysis._describe_by`：description.groups，趋势再加 over）。别的估计量不取——
      描述类的关联按 treatment 分两组，见 `_association_groups`
    - 整群列：`survey.psu`，没写取 `cluster_level`——跟 oss run 方差的单位同一个次序（`analysis._variance_units`）。
      给了 `--clusters` 不取（那也是显式给的整群信息）
    - 权重列：`weights`；空串就是不加权，画像检出了权重列也不用（oss run 也不用）
    """
    said: list[str] = []
    if not group_cols:
        group_cols = analysis_mod._describe_by(plan)
        if group_cols:
            said.append(f"分组列 {'、'.join(group_cols)}")
    if not cluster_col and not clusters_given:
        psu = plan.survey.psu.strip() if plan.survey is not None else ""
        cluster_col = psu or plan.cluster_level.strip()
        if cluster_col:
            said.append(f"整群列 {cluster_col}")
    bare = ""
    if weight_col is None:
        weight_col = plan.weights.strip()
        if weight_col:
            said.append(f"权重 {weight_col}")
        elif prof.weight_column:
            bare = f"不加权：计划的 weights 是空的，画像检出的权重列 {prof.weight_column} 不用"
    if not said and not bare:
        return group_cols, cluster_col, weight_col, ""
    parts = [f"按本题计划的{'、'.join(said)} 算"] if said else []
    if bare:
        parts.append(bare if said else "按本题计划" + bare)
    return group_cols, cluster_col, weight_col, (
        "，".join(parts) + "（命令行没给的都按计划取；要换就显式给 --group-col / --cluster-col / --weight-col）")


def _run_rows(frame: pd.DataFrame, plan: AnalysisPlan) -> tuple[pd.DataFrame, list[str]]:
    """本题有计划：分析样本就是 oss run 用的那批行（F1 复审 f1_frame）。返回 `(那批行, 逐步的说明)`，那批行不砍列。

    原先自己数一套（结局、权重、簇列、分组列缺失），跟 oss run 两头对不上：分层或抽样单位缺失、没给分组列时分组或分期
    缺失、关联计划的控制变量缺失的行，精度数了、oss run 去掉了；不加权的计划碰上画像的权重列、聚类层缺失的行，精度
    去掉了、oss run 留着。现在借 oss run 的选行：

    - 最外层先加标志列、查数据契约（`run_plan` 的头两步：筛选可以直接引用 `_reversal` 这类列）
    - `analysis._ols_rows`：样本筛选、权重转数值并拒非正、按该跑的列与权重和抽样设计列去缺失（不按聚类层去）。
      该跑的列：describe 是结局与分组（`_describe_by`）；别的估计量照 `run_ols_fe`——结局、处理、控制与交互项（交互项先现场乘出来）
    - oss run 会拒的（权重有 0 或全缺、列不在数据里、筛完或去完缺失一行不剩、数据契约不满足、筛选写错了）精度也拒，
      理由照抄（`_refused`）——不再去掉那几行照算
    - 说明逐步数：筛选筛掉几行，再按列逐个数缺失（`_missing_steps`，加起来就是去掉的总数）
    """
    try:
        df, _ = analysis_mod._inject_flags(frame, plan)
        df, _ = analysis_mod._data_contract(df, plan)
    except analysis_mod.PlanRejected as exc:
        raise _refused(list(exc.problems)) from exc
    except Exception:  # noqa: BLE001 —— 补标志列出了别的错：不补，筛选没引用标志列的照样算
        df = frame
    if plan.estimator == "describe":
        specs, cols = [], [plan.outcome, *analysis_mod._describe_by(plan)]
    else:
        specs = [*plan.interactions, *([plan.treatment] if analysis_mod.is_interaction(plan.treatment) else [])]
        cols = [plan.outcome, plan.treatment, *plan.controls, *(i for i in plan.interactions if i != plan.treatment)]
    try:
        src = analysis_mod._expand_interactions(df, specs)
        rows, _, _ = analysis_mod._ols_rows(src, plan, cols)
    except analysis_mod.PlanRejected as exc:
        raise _refused(list(exc.problems)) from exc
    except Exception as exc:  # noqa: BLE001 —— 筛选写错了之类：oss run 在这里也跑不下去
        raise _refused([f"{type(exc).__name__}: {exc}"]) from exc
    kept = src.query(plan.sample_filter) if plan.sample_filter else src       # 上面筛过一次，不会再出错
    gone = len(src) - len(kept)
    steps = [f"计划的样本筛选 `{plan.sample_filter.strip()}` 筛掉 {gone} 行"] if gone else []
    return df.loc[rows.index], steps + _missing_steps(kept, plan, cols)


def _missing_steps(kept: pd.DataFrame, plan: AnalysisPlan, cols: list[str]) -> list[str]:
    """`_ols_rows` 按哪几列去缺失、各去掉几行：逐列去、逐列数（加起来就是去掉的总数）；权重跟它一样先转成数值。"""
    need = list(dict.fromkeys(c for c in (*cols, *analysis_mod._weight_design_cols(plan)) if c))
    s, d = plan.survey, plan.description
    role = {plan.outcome: "结局", plan.treatment: "treatment", plan.weights: "权重",
            **{c: "控制变量" for c in plan.controls}, **{c: "分组列" for c in analysis_mod._describe_by(plan)},
            **({d.over: "分期"} if d is not None and d.kind == "trend" and d.over else {}),
            **({s.psu: "初级抽样单位", s.strata: "分层"} if s is not None else {})}
    cur = kept[need].copy()
    if plan.weights in cur.columns:
        cur[plan.weights] = pd.to_numeric(cur[plan.weights], errors="coerce")
    steps: list[str] = []
    for c in need:
        mask = cur[c].notna()
        if gone := int((~mask).sum()):
            steps.append(f"{role.get(c, '列')} {c} 缺失 {gone} 行")
            cur = cur[mask]
    return steps


def _refused(problems: list[str]) -> BadInput:
    """oss run 按本题的计划选行会拒：精度也拒，理由照抄（F1 复审 B：原先去掉那几行照算，还说「oss run 也用不上」）。"""
    return BadInput("按本题的计划选分析样本，oss run 会拒，精度也不算：" + "；".join(problems), problems=problems)


def _association_groups(frame: pd.DataFrame, plan: AnalysisPlan, ycol: str, n_given: int,
                        warnings: list[str]) -> tuple[list[str], str]:
    """描述类、估计量不是 describe 的计划（因果降为描述的 ols_fe 这类，终审 M4）：没有 description，主结果是 treatment 的
    系数。返回 `(分组列, 写进判词的一句)`。

    - treatment 是二值（两个不缺的取值）：按它的两组之差算——按总体水平算的半宽顶多是组差的一半，判词会松一倍
    - 取值多于两个：照旧按总体水平算，判词说读作标准化系数（单个回归元、变量都标准化时系数的标准误约是 1/√n）
    - treatment 不在数据里（交互项之类）：什么都不改
    """
    t = plan.treatment.strip()
    if not t or t not in frame.columns:
        return [], ""
    k = int(frame[t].dropna().nunique())
    if k == 2:
        if n_given:
            raise BadInput(f"{N_WITH_GROUPS}（计划是描述类的关联，精度按 treatment 列 {t} 的两组之差算，也是分组）")
        warnings.append(f"计划是描述类的关联（{plan.estimator}），按 treatment 列 {t} 的两组之差算精度")
        return [t], ""
    if k > 2:
        return [], (f"计划是描述类的关联（{plan.estimator}），treatment 列 {t} 不是二值：精度按总体水平算，"
                    f"读作标准化系数的精度——X（{t}）每变一个标准差、Y（{ycol or '结局'}）变多少个标准差")
    return [], ""


def _usable_rows(rundir, prof, idea_id: str, warnings: list[str]) -> int:
    """功效按**分析真会用到的行**算，不按全文件行数算。

    真跑 r20260916_netdem 撞出来的：处理变量 `wdi_internet` 缺失 54%，全文件 11136 行，
    按全量算 MDE = 0.074，按可用的 5148 行算是 0.122——**偏乐观六成半**。
    ⑤ 经验预检给多个候选分诊靠的就是这个数，偏乐观会把做不了的题排到前面去。

    精度那一路本来就「只数分析真会用到的行」（`_drop_missing`），功效这一路一直没跟上。
    这里按本题 `binding.json` 里绑上的**结局与处理**两列去缺失；没有绑定、读不到数据、
    或者绑定不属于本题，就退回全文件行数并说出来——**看不出来就不改**，不许悄悄换掉一个数。
    """
    total = prof.file.n_rows
    path = rundir.file(ARTIFACTS["binding"])
    if not path.exists():
        return total
    try:
        rep = BindingReport.load(path)
    except (OSError, ValueError):
        return total
    if rep.idea_id and idea_id and rep.idea_id != idea_id:
        warnings.append(f"binding.json 记的是 {rep.idea_id}，不是本题 {idea_id}——行数按全文件 {total} 算，"
                        "先对本题跑 oss bind")
        return total
    cols = [b.column for b in rep.bindings
            if b.role in ("outcome", "treatment") and b.column and b.is_usable]
    if not cols:
        return total
    try:
        frame = load_any(prof.file.path).frame
    except Exception as exc:  # noqa: BLE001 —— 读不到就退回全文件，不把功效整段打断
        warnings.append(f"读数据失败（{exc}），行数按全文件 {total} 算")
        return total
    have = [c for c in cols if c in frame.columns]
    if not have:
        return total
    rows = int(frame[have].notna().all(axis=1).sum())
    if rows <= 0:
        warnings.append(f"按 {'、'.join(have)} 去缺失之后一行不剩（全文件 {total} 行）——行数仍按全文件算，先查数据")
        return total
    if rows < total:
        warnings.append(f"分析样本 {rows} 行：全文件 {total} 行，去掉 {total - rows} 行"
                        f"（{'、'.join(have)} 有缺失）——功效只按分析样本算。"
                        "要按别的样本量算就显式给 --n")
    return rows


def _drop_missing(frame: pd.DataFrame, ycol: str, wcol: str, cluster_col: str,
                  group_cols: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """去掉分析用不上的行：结局缺失、权重缺失或不为正、簇列缺失、分组列缺失。返回 `(剩下的行, 逐步的说明)`。

    逐步去、逐步数，说明里的行数加起来就是去掉的总数。列不在数据里的那一步跳过（缺列另有警告）。
    """
    checks: list[tuple[str, object]] = []
    if ycol and ycol in frame.columns:
        checks.append((f"结局 {ycol} 缺失", lambda f: f[ycol].notna()))
    if wcol and wcol in frame.columns:
        checks.append((f"权重 {wcol} 缺失或不为正", lambda f: pd.to_numeric(f[wcol], errors="coerce") > 0))
    if cluster_col and cluster_col in frame.columns:
        checks.append((f"簇列 {cluster_col} 缺失", lambda f: f[cluster_col].notna()))
    if group_cols and all(c in frame.columns for c in group_cols):
        checks.append((f"分组列 {'、'.join(group_cols)} 缺失", lambda f: f[group_cols].notna().all(axis=1)))
    steps: list[str] = []
    for why, keep in checks:
        mask = keep(frame)                                  # type: ignore[operator]
        gone = int((~mask).sum())
        if gone:
            steps.append(f"{why} {gone} 行")
            frame = frame[mask]
    return frame, steps


def _group_cells(frame: pd.DataFrame, cols: list[str], wcol: str, cvals: pd.Series | None, cluster_col: str,
                 warnings: list[str]) -> tuple[list[int], list[str], list[float] | None, float, str]:
    """按分组列交叉数各格（精度用）。返回 `(各格行数, 标签, 各格的权重设计效应, 组差的 ρx, ρx 的来源)`。

    - 顺序与标签跟 describe 一样：分组列的取值排序（数值列按数值、交叉时按元组），标签写成 `列=值` 或
      `列=值 × 列=值`，取值的写法就用 describe 的 `_level_text`（两边的标签要逐字对得上，不各写一份）。
      分组列缺值的行不进任何一格：各格按自己的行数算，不给别的格虚增
    - 分类型的分组列（parquet 读进来的）只数出现过的类（`observed=True`）：pandas 2.2 缺省把没出现的类也算成一格
    - 给了权重列：各格按自己的权重算 Kish 的有效样本量 (Σw)²/Σw²，权重设计效应 = 行数 ÷ 它；
      没有为正权重的格给 NaN，精度那边改按全样本的算
    - 给了簇列：ρx = 组差那一对（参照组与排序第二类，跟 describe 的主结果同一对比）的行里「是不是排序第二类」
      在簇内的组内相关，组差的整群设计效应用它。
      功效从数据算处理变量的 ρx，这里是同一口径（描述骨架设计稿第四节：精度跟功效同一口径）
    """
    lost = [c for c in cols if c not in frame.columns]
    if lost:
        warnings.append(f"数据里没有分组列 {'、'.join(lost)}，精度按总体水平算")
        return [], [], None, 1.0, ""
    grouped = frame.groupby(cols, observed=True)
    counts = grouped.size()
    labels = [" × ".join(f"{c}={_level_text(v)}" for c, v in zip(cols, key if isinstance(key, tuple) else (key,)))
              for key in counts.index]
    sizes = [int(x) for x in counts]

    gdeffs: list[float] | None = None
    if wcol:
        w = pd.to_numeric(frame[wcol], errors="coerce")
        w = w.where(w > 0)
        tmp = frame[cols].copy()
        tmp["__oss_w"], tmp["__oss_w2"] = w, w ** 2
        sums = tmp.groupby(cols, observed=True)[["__oss_w", "__oss_w2"]].sum().reindex(counts.index)
        gdeffs = []
        for size, (s1, s2), lab in zip(sizes, sums.itertuples(index=False, name=None), labels):
            kish = s1 * s1 / s2 if s2 > 0 else 0.0
            if kish > 0:
                gdeffs.append(size / kish)
            else:
                gdeffs.append(float("nan"))
                warnings.append(f"分组 {lab} 没有为正的权重，这一格按全样本的权重设计效应算")

    rho, src = 1.0, ""
    pair = power_mod.first_two(sizes)
    if cvals is not None and len(pair) == 2:
        gid = grouped.ngroup()                      # 跟 counts 同一个顺序；分组列缺值的行是 NaN
        pick = gid.isin(pair)
        r = power_mod.cluster_icc((gid[pick] == pair[1]).astype(float), cvals[pick])
        if math.isfinite(r):
            rho = r
            src = (f"从数据算出（{' × '.join(cols)} 在 {cluster_col} 内，"
                   f"只看参照组 {labels[pair[0]]} 与排序第二类 {labels[pair[1]]}）")
    return sizes, labels, gdeffs, rho, src


def _outcome_col(binding_path: Path, own_plan: AnalysisPlan | None, idea_id: str) -> str:
    """结局是哪一列：先看本题的 binding.json 绑的 outcome（跟处理列同一个来处），再看本题的计划。都没有返回空串。"""
    if binding_path.exists():
        try:
            b = BindingReport.load(binding_path)
        except Exception:  # noqa: BLE001
            b = None
        if b is not None and b.idea_id in ("", idea_id):
            col = next((x.column for x in b.bindings if x.role == "outcome" and x.column), "")
            if col:
                return col
    return own_plan.outcome.strip() if own_plan is not None else ""


def _outcome_sd(y: pd.Series, w: pd.Series | None) -> tuple[float, bool]:
    """结局的（加权）标准差，有意义差别换单位用；返回 `(标准差, 是否加权)`。

    权重跟功效同一个来处（画像的权重列或 --weight-col），只取为正的；能用的数不到两个返回 0（调用方当算不出）。
    """
    yv = pd.to_numeric(y, errors="coerce")
    if w is not None:
        wv = pd.to_numeric(w, errors="coerce")
        ok = yv.notna() & (wv > 0)
        yy, ww = yv[ok].to_numpy(float), wv[ok].to_numpy(float)
    else:
        yy = yv.dropna().to_numpy(float)
        ww = np.ones(len(yy))
    if len(yy) < 2:
        return 0.0, w is not None
    mu = float(np.average(yy, weights=ww))
    sd = float(np.sqrt(np.average((yy - mu) ** 2, weights=ww)))
    return (sd if math.isfinite(sd) else 0.0), w is not None
