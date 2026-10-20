"""`oss bind`：把构念绑到数据的真实列上。绑不上就说清楚缺什么。

两种用法：不带 mappings **只列候选**（算），让 skill 去选（判）；
带 mappings **回验** skill 选的列，产出 binding.json。

两条都先读 profile.json / ideas.json，所以走 `require_run`：
打错路径要拒绝。顺手把目录建出来的话，报的会是「缺 ideas.json」——
看着像「这一步还没做」，而真相是「这个目录根本不是你要的那个」。
"""

from __future__ import annotations

from oss.api.errors import BadInput, Missing
from oss.api.result import result
from oss.api.rundir import emit, first_idea_id, require_run
from oss.core import binding as binding_mod
from oss.core.contracts import ARTIFACTS, DataProfile, IdeaSet
from oss.host.base import RunDir

#: 候选列完之后给的下一步。**三行原样**（含开头那个换行）——散文直接打它。
NEXT = ("\n选好之后回来："
        "\n  oss bind --idea <id> --map '构念=列名:角色' --map '构念=A/B:角色' ..."
        "\n角色：treatment / outcome / control / instrument / mediator / moderator")


def _idea(rundir: RunDir, idea: str):
    idea_id = idea or first_idea_id(rundir)
    prof = DataProfile.load(rundir.file(ARTIFACTS["profile"]))
    ideaset = IdeaSet.load(rundir.file(ARTIFACTS["ideas"]))
    the_idea = next((i for i in ideaset.ideas if i.idea_id == idea_id), None)
    if the_idea is None:
        raise Missing("ideas", f"ideas.json 里没有 {idea_id}", code=1)
    return idea_id, prof, the_idea


def candidates(run: str, *, idea: str = "") -> dict:
    """只算候选，不下结论——**选哪个是 skill 的判断。**

    规则给不出候选时（`rule_failed`）`matches` 是空的，同时把全表的列交出去：
    **一份跟构念无关的名单会诱导模型在里面选一个**，噪声比空白更糟。
    """
    rundir = require_run(run)
    idea_id, prof, the_idea = _idea(rundir, idea)
    constructs, failed = [], False
    for c in the_idea.constructs_needed:
        sl = binding_mod.shortlist(c, prof)
        failed = failed or sl.rule_failed
        constructs.append({
            "construct": c, "rule_failed": sl.rule_failed, "note": sl.note,
            "matches": [{"column": m.column, "score": m.score, "why": m.why}
                        for m in ([] if sl.rule_failed else sl.matches)]})
    variables = [] if not failed else [
        {"name": v.name, "semantic_type": v.semantic_type, "missing_rate": v.missing_rate,
         "n_unique": v.n_unique, "label": v.label} for v in prof.variables]
    return result(f"{idea_id} 需要绑定 {len(constructs)} 个构念", {
        "run": str(rundir.path), "idea_id": idea_id, "constructs": constructs,
        "rule_failed": failed, "all_variables": variables, "next_command": NEXT,
    })


def bind(run: str, *, idea: str = "", mappings: list[str]) -> dict:
    """回验 skill 选的列，产出 binding.json。"""
    rundir = require_run(run)
    idea_id, prof, the_idea = _idea(rundir, idea)
    bindings = []
    for item in mappings:
        construct, sep, rest = item.partition("=")
        expr, _, role = rest.rpartition(":")
        role = role.strip() or "control"
        expr = expr.strip()
        if not sep or not construct.strip() or not expr:
            raise BadInput(f"看不懂 --map {item!r}，格式是 构念=列名:角色", code=1)
        # 含运算符的当派生变量：urban_inc / rural_inc
        is_derived = any(op in expr for op in "+-*/()")
        bindings.append(binding_mod.verify(
            construct.strip(), role, None if is_derived else expr, prof,
            derivation=expr if is_derived else ""))

    report = binding_mod.build_report(idea_id, bindings, prof, strategy=the_idea.identification_strategy)
    note = "核心构念绑定成功" if report.core_ok else f"核心构念绑不上：{report.blocking}"
    path = emit(rundir, "binding", "oss bind", "binding", report,
                note=note, args={"idea_id": report.idea_id})
    return result(note, {
        "run": str(rundir.path), "artifact": ARTIFACTS["binding"], "path": str(path),
        "idea_id": report.idea_id, "core_ok": report.core_ok,
        "blocking": list(report.blocking),
        "needs_gap_columns": any(b.status in ("proxy", "derived") for b in report.bindings),
        "bindings": [{"construct_name": b.construct_name, "role": b.role, "column": b.column,
                      "derivation": b.derivation, "status": b.status, "note": b.note,
                      "is_usable": b.is_usable} for b in report.bindings],
    })
