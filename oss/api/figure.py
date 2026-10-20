"""`oss figure`：诊断图。**不是装饰，是最快的错误检测器。**

回归表回答不了「这个模型对不对」，图能。结构判错、块状缺失、簇数太少——
这三样在表格里要靠人逐行核，在图上一眼可见。

只读的命令（画图不产出新的分析结论），所以走 `require_run`：
打错路径要拒绝，不许顺手建一个空目录出来。
"""

from __future__ import annotations

import json
from pathlib import Path

from oss.api.errors import BadInput, Missing, Unavailable
from oss.api.result import result
from oss.api.rundir import official_results, require_run
from oss.core import analysis as analysis_mod
from oss.core import figures as figures_mod
from oss.core.contracts import ARTIFACTS, AnalysisPlan, DataProfile, TraceEvent
from oss.core.loaders import load_any


def figure(kind: str, *, run: str, data: str = "", cluster_col: str = "", by: str = "",
           split: str = "", bins: int = 6, treatment: str = "", icc: float = 0.05,
           out: str = "", lang: str = "zh", style: str = "") -> dict:
    """画一张图，返回路径与图注。

    `data["log"]` 是**边走边打的那几行**（`[stream, text]` 二元组）：
    「分箱斜率取自 results.json」原先打在**画图之前**，图注与 ✓ 打在之后。
    门面先按序打 log 再打其余，顺序跟从前一模一样——散文里一行都不许挪位置。
    """
    rundir = require_run(run)
    if kind not in figures_mod.KINDS:
        raise BadInput(f"不认识的图 {kind!r}。可用：{'、'.join(figures_mod.KINDS)}", code=2)
    prof = DataProfile.load(rundir.file(ARTIFACTS["profile"]))
    frame = load_any(data or prof.file.path).frame
    path = Path(out) if out else rundir.file(f"fig_{kind}.png")
    log: list[list[str]] = []
    binned_from_results = False
    # 画布宽度上限：风格包写了就按它（那一般就是该刊的版心），没写用缺省 4.33 英寸。
    # **图的物理宽度就是画布宽度**，所以这一个数决定了印出来的字号
    cap = None
    if style:
        from oss.api import style as style_api
        from oss.core import style as style_mod

        rules, _profile, _texts = style_mod.load_pack(style_api.pack_dir(style))
        cap = getattr(rules, "figure_max_width", None) if rules is not None else None
        if cap:
            log.append(["out", f"  画布宽度按 {style} 的版心 {cap} 英寸"])

    _ctx = figures_mod.max_width(cap)
    _ctx.__enter__()
    try:
        if kind == "structure":
            path = figures_mod.structure(frame, prof, path)
        elif kind == "missing":
            path = figures_mod.missing(frame, prof, path)
        elif kind in ("marginal", "coef", "collinearity"):
            ppath = rundir.file(ARTIFACTS["plan"])
            if not ppath.exists():
                raise Missing("plan", "缺 plan.json——这几张图要按分析计划画", code=2)
            aplan = AnalysisPlan.load(ppath)
            if kind == "collinearity":
                path = figures_mod.collinearity(frame, aplan, path)
            elif kind == "coef":
                if not rundir.file(ARTIFACTS["results"]).exists():
                    raise Missing("results", "缺 results.json——先跑 oss run", code=2)
                from oss.api.paper import labels_of

                path = figures_mod.coef(official_results(rundir), path, lang=lang,
                                        labels=labels_of(rundir))
            else:
                if not by:
                    raise BadInput("marginal 图要 --by 指定斜率随哪一列变", code=2)
                tcol = treatment or aplan.treatment
                if not treatment and analysis_mod.is_interaction(aplan.treatment):
                    raise BadInput(
                        f"plan.treatment 是交互项「{aplan.treatment}」，"
                        f"它在参照组里恒为 0，分箱斜率画出来是空的。\n"
                        f"  用 --treatment 指定要看哪个变量的斜率"
                        f"（比如 --treatment {analysis_mod.parts_of(aplan.treatment)[0]}）",
                        code=2)
                # 图上的数必须跟 results.json 里的是同一批：结果里有分箱就画它
                # （各箱内带主规格的控制与固定效应），没有才现算二元的原始形状
                series = None
                adjusted = False
                rres = None
                if rundir.file(ARTIFACTS["results"]).exists():
                    rres = official_results(rundir)
                    bspec = aplan.binned
                    same = bspec is not None and bspec.by == by and (
                        not treatment or (bspec.treatment or aplan.treatment) == treatment)
                    if rres.binned and same:
                        series = [(name, [(b.center, b.slope, b.se, b.n) for b in pts])
                                  for name, pts in rres.binned.items()]
                        adjusted = not bspec.bivariate
                        binned_from_results = True
                        log.append(["out", "  分箱斜率取自 results.json"
                                           "（各箱内含主规格的控制变量与固定效应）"])
                from oss.api.paper import labels_of

                path = figures_mod.marginal(
                    frame, aplan.outcome, tcol, by, path, split=split, bins=bins, lang=lang,
                    labels=labels_of(rundir),
                    series=series, adjusted=adjusted, weights=aplan.weights,
                    # 区间临界值随那份结果的抽样设计走；现算的二元形状不出自结果，照旧 1.96
                    results=rres if binned_from_results else None,
                )
        elif kind in ("event", "robustness"):
            if not rundir.file(ARTIFACTS["results"]).exists():
                raise Missing("results", "缺 results.json——先跑 oss run", code=2)
            if kind == "event":
                path = figures_mod.event(official_results(rundir), path, lang=lang)
            else:
                # 横轴要印系数的名字，不是估计量的键——「Coefficient on main」读者看不懂。
                # 跟 coef 图同一份 labels：图和表叫同一个名字
                from oss.api.paper import labels_of

                path = figures_mod.robustness(official_results(rundir), path, lang=lang,
                                              labels=labels_of(rundir))
        else:
            col = cluster_col or prof.structure.unit_id or ""
            if col not in frame.columns:
                raise BadInput("clusters 图要 --cluster-col 指定聚类列", code=2)
            path = figures_mod.clusters(frame, col, path, icc=icc)
    except figures_mod.FiguresUnavailable as exc:
        # 装什么由门面拼：`_install_cmd()` 探的是当前解释器，那是门面的事。
        # log 一并交出去：「分箱斜率取自 results.json」原先是**边走边打**的，
        # 画图依赖缺席时它那时候已经在屏幕上了——失败路径丢掉它就是散文漂移
        raise Unavailable(str(exc), code=1,
                          data={"install_extra": "opensocialscience[figures]",
                                "log": log}) from exc

    finally:
        _ctx.__exit__(None, None, None)

    meta = {}
    meta_path = path.with_suffix(".json")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    rundir.append_trace(TraceEvent(
        seq=rundir.next_seq(), step="figure", command=f"oss figure {kind} --lang {lang}",
        outcome="ok", artifacts=[path.name],
        note=f"{figures_mod.KINDS[kind]}", args={"data_sha256": prof.file.sha256, "lang": lang},
    ))
    return result(f"{path.name}（{figures_mod.KINDS[kind]}）", {
        "run": str(rundir.path), "kind": kind, "kind_label": figures_mod.KINDS[kind],
        "path": str(path), "name": path.name, "has_meta": bool(meta),
        "lang": meta.get("lang", ""), "pdf": meta.get("pdf") or "",
        "caption": meta.get("caption", ""), "labels": meta.get("labels") or {},
        "cjk_labels": list(meta.get("cjk_labels") or []),
        "binned_from_results": binned_from_results, "log": log,
    })
