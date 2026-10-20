"""run_state.json：一次运行的状态指针。

状态在盘上，不在对话里。上下文压缩、会话中断、换一个模型接手，
都从这个文件续，而不是靠模型记得「现在第几轮」。
事故（2026-08-28）：压缩后模型把第 3 轮记成第 2 轮，多审了两轮。

只记四样：阶段指针、锚点（冻结的原始问题）、审稿轮次与最近决定、模型身份。
不记任何模型可以编的内容。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Literal

from oss.core.contracts import RESEARCH_TYPES, now as _utcnow

FILE = "run_state.json"
#: 读不出来的状态文件挪到这里，不删也不覆盖
CORRUPT = "run_state.corrupt.json"

#: 这三个字段是字典：update() 合并它们，load() 遇到别的类型就换成空字典
_DICT_FIELDS = ("anchor", "review", "models")
#: 这四个字段是字符串：load() 遇到别的类型就换回字段自己的缺省值
_STR_FIELDS = ("run_id", "stage", "stage_status", "updated_at")
#: 锚点 `source` 只认这两个值（跟 `contracts.Idea.source` 同一份取值表）
_SOURCES = ("model", "user")


def now_stamp() -> str:
    """跟 trace.jsonl 里的 ts 同一种写法（2026-08-28T17:12:19.517395Z）。

    两个文件的时间戳要能直接比大小、直接对齐——各写各的格式，
    「审稿报告是在哪次导出之前写的」这种问题就得靠人换算。
    """
    return _utcnow().isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass
class RunState:
    run_id: str = ""
    #: 例：1-profile / 2-literature / 3-ideas / 4-feasibility / 5-plan / 6-run / 7-paper / 8-review / done
    stage: str = ""
    stage_status: str = "in_progress"
    #: direction / idea_id / plan_sha / source / research_type / frozen_at。**冻结后只能通过 oss anchor switch 改。**
    #: `research_type` 是冻结那一刻计划写的研究类型（空串 = 没声明）；老状态文件里**没有这一格**——
    #: 研究类型门按「不比」处理（oss/gates/research_type.py）。
    #: 写 anchor 的入口只有三个：freeze_anchor / reset_anchor / refill_plan_sha，别处不许写。
    #: `source` 是 `model` / `user`：这一题是链条自己找的，还是用户用 `--- idea:` 自带的。
    #: 老状态文件里没有这一格，读出来就是缺席——`behavior.py` 按「不是 user 就 n/a」判。
    anchor: dict = field(default_factory=dict)
    #: round / max_rounds / last_decision
    review: dict = field(default_factory=dict)
    #: executor / reviewer / family_relation
    models: dict = field(default_factory=dict)
    updated_at: str = ""


def _quarantine(p: Path) -> None:
    """损坏的文件留证据，不静默吞掉——静默返回空状态会让锚点被重新冻结成当前题目。

    挪到 run_state.corrupt.json：下一次 save() 写出干净的一版，
    而「上一版长什么样、是被谁写坏的」还在盘上，事后能查。

    **平名是警报**（`anchor.check` 认的就是这个名字），所以留给第一次损坏；
    修好之前又坏一次就另存带时间戳的一份——原来是覆盖，等于**最早那份坏文件
    永远查不到**，而查的正是「锚点最先是被谁写坏的」。
    """
    dest = p.with_name(CORRUPT)
    if dest.exists():
        stamp = now_stamp().replace(":", "-")          # 文件名里不放冒号
        dest = p.with_name(f"run_state.corrupt.{stamp}.json")
        n = 2
        while dest.exists():                           # 同一微秒内连坏两次也不互相盖
            dest = p.with_name(f"run_state.corrupt.{stamp}.{n}.json")
            n += 1
    try:
        p.replace(dest)
    except OSError:
        pass                                  # 挪不动（只读目录之类）也不能让命令挂掉


def load(rundir: Path) -> RunState:
    """读状态。读不出来就当没有——**但绝不抛异常**：状态文件坏掉不该让整条命令挂掉。"""
    p = Path(rundir) / FILE
    blank = RunState(run_id=Path(rundir).name)
    if not p.exists():
        return blank
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _quarantine(p)
        return blank
    if not isinstance(d, dict):
        # 盘上是个列表或字符串——不是这个文件该有的形状，当没有
        _quarantine(p)
        return blank
    keys = {f.name for f in fields(RunState)}
    st = RunState(**{k: v for k, v in d.items() if k in keys})
    # 人手改过的文件里 anchor 可能是 null、stage 可能是数字。类型对不上，
    # 后面每一个 .get / .startswith 都会炸在离这里很远的地方——在入口就掰回来。
    defaults = {f.name: f.default for f in fields(RunState)}
    for k in _STR_FIELDS:
        if not isinstance(getattr(st, k), str):
            setattr(st, k, defaults[k])
    for k in _DICT_FIELDS:
        if not isinstance(getattr(st, k), dict):
            setattr(st, k, {})
    st.run_id = st.run_id or Path(rundir).name
    return st


def save(rundir: Path, st: RunState) -> RunState:
    st.updated_at = now_stamp()
    p = Path(rundir) / FILE
    # 覆盖写不是原子的——写到一半被杀会留下半个文件，load() 当它损坏返回空状态，
    # 锚点就「静默解冻」了。先写临时文件再 replace，读到的永远是完整的某一版。
    tmp = p.with_name(f".{FILE}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return st


def update(rundir: Path, **changes) -> RunState:
    """字典字段合并，标量字段覆盖。

    合并意味着**字典字段清不掉**：`update(anchor={})` 什么也不会发生。
    要清空或替换请走 `reset_anchor()`——换题是件该留痕的事，
    不该是某次顺手 update 的副作用。
    """
    st = load(rundir)
    for k, v in changes.items():
        cur = getattr(st, k)
        if k in _DICT_FIELDS and not isinstance(v, dict):
            raise TypeError(f"{k} 必须是 dict")
        if isinstance(cur, dict) and isinstance(v, dict):
            cur.update(v)
        else:
            setattr(st, k, v)
    return save(rundir, st)


def set_stage(rundir: Path, stage: str, status: str = "in_progress") -> RunState:
    """阶段指针与状态是一对，分开写会漂：导出后再改稿会留下 8-review / done。"""
    return update(rundir, stage=stage, stage_status=status)


def norm_direction(s: str) -> str:
    """比方向只比实质：空白怎么排不算换题，标点和措辞算。

    跟 `current_anchor` 放在一起：**「还是不是同一个问题」的判据只有这一处。**
    原先长在 `gates/anchor.py` 里，于是事后核账的 `core/behavior.py` 要么从 gates 反向
    import（把 core → gates 这条边固化下来），要么自己再写一遍——两条路都会让
    「方向变没变」在跑的时候和核账的时候答得不一样。
    """
    return " ".join(str(s or "").split())


def current_anchor(rundir: Path) -> dict:
    """锚点由什么构成，只在这里定义一次：ideas.json 的 direction + plan.json 的 idea_id
    + 计划指纹 + 这一题是谁出的。gates/anchor.py 与 oss anchor switch 都用它，不各自拼。

    `source` 读**计划指向的那一条** idea：按 `plan.json` 的 `idea_id` 去 `ideas.json.ideas`
    里找同号的那条，取它的 `source`。自带想法模式下清单里只有一条，两种读法答案一样；
    但换过题、或者清单里不止一条时就不一样了——读第一条等于「谁排在最前面谁说了算」，
    而锚点上那一格问的是**这次要跑的这道题**是谁出的。`--- idea:` 的那条排在第二、
    计划指的正是它时，读第一条会把 `user` 读成 `model`，两条自带想法指标从此一律 n/a。

    找不到（没有 `plan.json`、没有 `idea_id`、清单里没有同号的那条）**退回第一条**：
    环节 3 刚整理完、计划还没写的时候只有这一条，那时它就是答案。
    **两头都读不出来一律算 `model`**：文件坏了、`ideas` 不是列表、第一项不是对象、
    字段缺席——这些都是「看不出来是谁出的题」，而把它当成 `user` 会让两条只在 user 下判的
    指标在老运行目录上凭空判起来，判出两条它们量不了的通过。
    """
    from oss.core.loop import _read_json, plan_sha   # 局部 import，避免 loop ↔ state 循环引用
    ideas = _read_json(Path(rundir) / "ideas.json") or {}
    plan = _read_json(Path(rundir) / "plan.json") or {}
    raw = ideas.get("ideas") if isinstance(ideas, dict) else None
    lst = raw if isinstance(raw, list) else []
    idea_id = str(plan.get("idea_id", "")) if isinstance(plan, dict) else ""
    picked = next((x for x in lst if isinstance(x, dict) and idea_id
                   and str(x.get("idea_id") or "") == idea_id), None)
    if picked is None:
        picked = lst[0] if lst and isinstance(lst[0], dict) else {}
    rtype = str(plan.get("research_type") or "") if isinstance(plan, dict) else ""
    return {"direction": str(ideas.get("direction", "")) if isinstance(ideas, dict) else "",
            "idea_id": idea_id,
            "plan_sha": plan_sha(Path(rundir) / "plan.json"),
            "source": "user" if picked.get("source") == "user" else "model",
            "research_type": rtype if rtype in RESEARCH_TYPES else ""}


def peek_anchor_idea(rundir: Path) -> str:
    """锚点上的题号，只读不动。直接读 run_state.json、不走 `load`：那条路读到坏文件会把它挪去隔离，
    算一次功效、刷一次处方都不该有这种副作用。读不出来当没有。

    `oss power` 不写 --idea 时按它选题，`oss route` 拿它分辨「换题后计划没跟上」与「报告算错了题」（F1 复审 M2-5）。
    """
    try:
        st = json.loads((Path(rundir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    anchor = st.get("anchor") if isinstance(st, dict) else None
    return str(anchor.get("idea_id") or "") if isinstance(anchor, dict) else ""


def _anchor(direction: str, idea_id: str, plan_sha: str, source: str,
            research_type: str = "") -> dict:
    """锚点那六格的形状只在这里拼一次。

    原先 `freeze_anchor` 与 `reset_anchor` 各写一遍同一个字面量：加一格要改两处，
    漏一处就是「换过题的运行少一格」——只在换题这条路径上才看得见的洞。

    `source` 在写盘之前归一化：类型标注挡得住看代码的人，挡不住运行时传进来的
    `"users"`。**不在 `_SOURCES` 里的一律按 `model`**——`user` 是那两条自带想法
    指标开判的开关，读不准就宁可不判（跟 `current_anchor` 同一条口径：宁可少判）。
    `research_type` 同理：不在四类里的一律写空串（= 没声明，研究类型门不比）。
    """
    return {"direction": direction, "idea_id": idea_id, "plan_sha": plan_sha,
            "source": source if source in _SOURCES else "model",
            "research_type": research_type if research_type in RESEARCH_TYPES else "",
            "frozen_at": now_stamp()}


def freeze_anchor(rundir: Path, *, direction: str, idea_id: str, plan_sha: str,
                  source: Literal["model", "user"] = "model", research_type: str = "") -> RunState:
    """第一次跑分析时冻结原始问题。已冻结则原样返回——换题不走这里。

    `source` 有缺省值，所以老调用方（测试里那几处 `freeze_anchor(direction=..., idea_id=...,
    plan_sha=...)`）一个字都不用改；而 `freeze_anchor(**current_anchor(...))` 那几处
    自动就带上了。**缺省是 `model` 不是空串**：空串会让「没传」和「模型出的题」
    在盘上长得不一样，而它们本来就是同一件事。取值跟 `contracts.Idea.source`
    是同一个 `Literal`——两处对不上，锚点上就会出现 ideas.json 里不存在的来源。
    `research_type` 同样有缺省值、同样跟着 `**current_anchor(...)` 自动带上：
    冻结那一刻计划没写研究类型（老计划、或压根没这一栏），就写空串。
    """
    st = load(rundir)
    if st.anchor.get("frozen_at"):
        return st
    if not direction and not idea_id:
        # 空锚点冻上就再也改不了；等下一次真有题目时再冻
        return st
    st.anchor = _anchor(direction, idea_id, plan_sha, source, research_type)
    return save(rundir, st)


def refill_plan_sha(rundir: Path, *, idea_id: str, plan_sha: str,
                    research_type: str = "") -> tuple[RunState, bool]:
    """补回锚点里空着的两格：指纹，以及**首次声明即锁**的研究类型。返回 `(状态, late)`。

    这是 freeze_anchor / reset_anchor 之外**唯一**获准写 anchor 的入口，而且只补空的那两格：
    已经有值的，或者盘上的题跟锚点对不上，都原样返回，不改任何东西（`late` 也是 `False`）。

    **指纹**：`oss anchor switch` 换题时新计划还没写，plan_sha 只能留空——不补的话，
    换过题的运行锚点指纹一辈子是空的。新计划第一次跑出结果时在这里补上。

    **研究类型·首次声明即锁**（用户 2026-09-14 决定 2A）：锚点冻结那一刻计划没声明类型
    （新式锚点，这一格留的是空串），**第一次有计划声明了类型的那次跑**就把这一格锁死——
    不管那次跑时 plan_sha 是不是已经有值。这跟以前只在「plan_sha 也空着」（先换题、
    后写计划）时才补是两回事：单靠「plan_sha 空不空」分不出「换题后第一次跑」和
    「同一道题、之前没声明、这次才声明」——**这一格本身在不在**才是能分开两者的判据，
    所以补研究类型只看这一格是不是「存在但空」，不再借用 plan_sha 是否为空这个条件。

    **`late`**：这次是不是「看过结果才补」——只在**这次真的把 research_type 从空填成
    了某个值、而且填之前 plan_sha 已经非空**（不是换题后新题的第一次跑：那种情况下
    这一格空着时 plan_sha 也跟着空）时才是 `True`。这份判据只写这一处：调用方
    （`oss/api/analysis.py::run`，此函数目前唯一的调用方）拿它决定要不要留一行
    `research_type_declared_late`，不必自己在调用前后各读一次锚点、再比一遍差异——
    早先那样两处各算一遍，算法一旦漂开就是「idea_id 对不上时明明什么都没补，
    却照样写了一行『从这一次起锁定』」这种复审抓出的真问题（2026-09-14 二次复审）。

    **legacy 锚点例外**：这一格压根不存在的运行目录（锚点冻结时研究类型这条轴线还没上线）——
    这里不补，也不该补：「从没声明过」是这一批老运行目录的固有状态，硬填一个值等于
    替它们编了一段从没发生过的历史；只有这一格**存在但是空串**的新式锚点，才会被
    第一次声明它的那次跑锁定。
    """
    st = load(rundir)
    if not st.anchor.get("frozen_at"):
        return st, False
    if idea_id != st.anchor.get("idea_id"):
        return st, False
    plan_sha_was_set = bool(st.anchor.get("plan_sha"))
    fill: dict = {}
    if not plan_sha_was_set:
        fill["plan_sha"] = plan_sha
    filled_type = (research_type in RESEARCH_TYPES and not st.anchor.get("research_type")
                   and ("research_type" in st.anchor or not plan_sha_was_set))
    if filled_type:
        fill["research_type"] = research_type
    if not fill:
        return st, False
    return update(rundir, anchor=fill), (filled_type and plan_sha_was_set)


def reset_anchor(rundir: Path, *, direction: str, idea_id: str, plan_sha: str,
                 source: Literal["model", "user"] = "model", research_type: str = "") -> RunState:
    """只给 `oss anchor switch` 用：带用户授权留痕之后才允许调用。

    `source` 也要重算一遍（调用方从 `current_anchor()` 取）。**不带上就等于换一次题
    把「这是用户出的题」抹掉**——两条自带想法的指标从下一刻起永远 n/a，
    而换过题之后正是最该继续量它们的时候。`research_type` 同理：调用方从
    **换题这一刻盘上的新计划**重算（`oss anchor switch` 走的是 `current_anchor()`），
    不带上就等于换一次题把研究类型抹回空串。
    """
    st = load(rundir)
    st.anchor = _anchor(direction, idea_id, plan_sha, source, research_type)
    return save(rundir, st)
