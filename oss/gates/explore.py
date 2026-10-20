"""探索门：想试一试可以，但要挂在一条意见上、不许碰结局和处理变量、结果不进论文。

事故（2026-08-28）：模型自己写了 213 段回归脚本，用它们「否证」自己的题目、
换题四次；探索出来的数字漏进正文，被 paper check 拦了 25 次。
说明书禁了拼接/重塑/功效脚本，没禁探索性回归——现在给它一条合法的路，然后堵死私路。
"""

from __future__ import annotations

import re
from pathlib import Path

from oss.core.contracts import AnalysisPlan
from oss.core.loop import _read_json, _trace, paper_review_round

#: 改了这些就不是「探索」，是换题——走 oss anchor switch
LOCKED = ("estimator", "outcome", "treatment", "treat_time", "instruments", "running", "cutoff", "unit", "time")
#: 探索可以动的：规格层面的东西，动了还是在回答同一个问题。
#: 权重与抽样设计跟 cluster_level 同属设计层（标准误怎么算、样本怎么代表总体），跟它放在一起：
#: 原先两样都不在任何一张表里，探针改了抽样设计，`diff` 也不留痕
FREE = ("controls", "fixed_effects", "cluster_level", "weights", "survey", "sample_filter", "interactions",
        "robustness", "binned", "bandwidth", "leads", "lags")
#: 同一条意见最多试几次；超过要 --why
MAX_PER_TARGET = 5
_REVIEW_REF = re.compile(r"^review-r(\d+)$")


def target_exists(rundir: Path, for_id: str) -> bool:
    """`--for` 指向的东西存在吗：route.json 里的处方 id，或已有的审稿轮次 review-rN。"""
    m = _REVIEW_REF.match(for_id)
    if m:
        want = int(m.group(1))
        return any(paper_review_round(p) == want for p in Path(rundir).glob("review_*.json"))
    route = _read_json(Path(rundir) / "route.json") or {}
    return any(str(it.get("id")) == for_id for it in route.get("items") or [])


def used_count(rundir: Path, for_id: str) -> int:
    """这条意见上已经**成功**探索了几次。

    额度限的是「探索出多少证据」，不是「手滑了几次」：被门拒掉的那次一个数字都没产出，
    算进额度只会让模型为了省额度而不敢按规矩试——把它推回私自写脚本那条路上去。
    """
    return sum(1 for r in _trace(Path(rundir))
               if r.get("step") == "explore"
               and (r.get("outcome") or "ok") == "ok"
               and (r.get("args") or {}).get("for") == for_id)


def diff(base: AnalysisPlan, probe: AnalysisPlan) -> list[str]:
    """探针相对基准动了哪些规格字段。留痕记下来，事后才看得清「到底试过什么」。"""
    return [k for k in FREE if getattr(base, k, None) != getattr(probe, k, None)]


def validate(rundir: Path, base: AnalysisPlan, probe: AnalysisPlan, for_id: str, why: str) -> list[str]:
    problems: list[str] = []
    if not for_id:
        problems.append("探索必须带 --for <处方id 或 review-rN>：说清是为了回应哪条意见")
    elif not _REVIEW_REF.match(for_id) and not (Path(rundir) / "route.json").exists():
        # 跟「找不到」分开报：没有处方单和挂错处方是两件事，合成一句话人会去翻不存在的文件
        problems.append("还没有 route.json——先跑 oss route 生成处方，再挂上去探索")
    elif not target_exists(rundir, for_id):
        problems.append(f"--for「{for_id}」在 route.json 里找不到，也不是已有的审稿轮次")
    changed = [k for k in LOCKED if getattr(base, k) != getattr(probe, k)]
    if changed:
        problems.append("探索不许改这些字段（改了就是换题，走 oss anchor switch）：" + "、".join(changed))
    if for_id and used_count(rundir, for_id) >= MAX_PER_TARGET and not why.strip():
        problems.append(f"同一条「{for_id}」已探索 {MAX_PER_TARGET} 次，再试要 --why \"<理由>\"")
    return problems
