"""审稿—修改—复审的循环记账。**判断归审稿人，计数与放行归工具。**

七次真跑里没有一次闭合过这个循环：审稿人说「该换估计量」，
执行者把它写进「下一步」就结束了；改稿后的复审全部超时，
留痕写着「改稿后的版本未经复审」，然后照样交付。

**借自参考库（ARIS `auto-review-loop` / `research-refine`）的四条：**

1. 停止条件用**「与」**不用「或」——两个条件同时成立才算收敛
2. 轮次有上限；耗尽了可以交付，但必须**大声说出遗留了什么**
3. 循环状态落盘（`loop_state.json`），会话被打断也能接着走
4. 冻结**原始问题锚点**（方向 + 选定的 idea），迭代设计时不许跑偏

**本项目自己加的一条**：因果审计判 FAIL 之后，机械地记下**试没试过替代设计**。
没试过**照样允许导出**——但审计的每一条意见都要写进局限，`paper export` 只提醒不拦
（目标是合格，不是把稿子堵死；「写没写」由「局限写全没有」那位审稿人对着审计报告核）。
显式留痕说明替代设计已穷尽是记录在案的逃生口，不是默认。

**第 1 条那两个条件是什么，代码说了算，这里只记它变过一次。**
原先是「设计级意见清零 **且** 没有 FAIL/ERROR/BLOCKED」；后来清零那一半去掉了
（用户定的：目标是合格，不是收敛到完美——改不了的设计级意见写进「尚未解决的问题」即可），
只剩「没有 FAIL/ERROR/BLOCKED **且** 复审覆盖不比首轮窄」。
这段话曾经比代码晚了一步，而**注释和代码不一致时，挡住错误的是运气不是设计**。

桌审与编辑综合**不算审稿轮次里的一位**（见 `_NOT_PAPER_REVIEW`）——它们不是独立的
第三方意见。但「不算一位」不等于「可以不提」：编辑是全流程唯一一处给意见排轻重、
裁审稿人分歧的角色，它的判词由 `_editor_note` 接进决定的理由里。

意见的类别由审稿人在每条前面打标（`[设计]` / `[数据]` / `[文字]`）；
**没打标的一律按「设计」算**——判不了就按最严重的算，不能因为漏标就放行。
第 2 轮起还有第四类 `[新发现]`：复审时新看到的问题**只计数**，不构成本轮的义务，
也不算遗留的设计级意见——否则每轮换一批新意见，收敛就成了移动的球门。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: 审稿轮次上限。原来是 3 轮 × 3 位审稿人，一次跑 77 分钟花在这。
#: 目标是「合格」不是「收敛到完美」：一轮改一次、最多两轮。
MAX_ROUNDS = 2

#: 决定的四种值。**模型只执行，不解释。**
DECISION_REVIEW_FIRST = "review_first"
DECISION_CONTINUE = "continue"
DECISION_STOP = "stop"
DECISION_EXPORT_OPEN = "export_with_open_items"

#: 意见类别。审稿人打在每条意见开头的方括号里。
KIND_DESIGN, KIND_DATA, KIND_TEXT = "设计", "数据", "文字"
#: 第四类：复审时新看到的问题。**不作为 FAIL 依据，也不算遗留的设计级意见。**
#: 事故（2026-08-28）：五轮审稿全 FAIL，每轮 FAIL 的理由都跟上一轮不同——
#: 作者改一条，审稿人换一条新的提。**那不是收敛，是移动的球门。**
KIND_NEW = "新发现"
_TAG = re.compile(
    r"^\s*[\[【(（]\s*(设计|数据|文字|新发现|design|data|wording|text|new)\s*[\]】)）]", re.I)
_TAG_MAP = {"design": KIND_DESIGN, "data": KIND_DATA, "wording": KIND_TEXT,
            "text": KIND_TEXT, "new": KIND_NEW}

#: 第 2 轮起，意见行按钉死的格式写成 `[设计] 3 处置了 —— …`。
#: 处置词**必须紧跟在标签和编号后面**才算数：`[设计] 5 没交代——作者说处置了但正文找不到`
#: 里也有「处置了」三个字，松一点就会把没交代的当成解决了放行。
#: 匹配不上的一律留在 design_open——**判不了就按最严重的算**，跟没打标签同一条规矩。
_DISPOSED = re.compile(r"^\s*[\[【(（][^\]】)）]{1,8}[\]】)）]\s*\d+\s*[.、:：]?\s*(处置了|写进局限了)")


def is_disposed(text: str) -> bool:
    """这条意见在复审里已判为处置了 / 写进局限了。**只在第 2 轮起有意义。**"""
    return bool(_DISPOSED.match(text or ""))

#: 这些审稿文件不是「论文轮次」的一部分
# 桌审和编辑综合不算审稿人：桌审是送审前的门，综合是对报告的整理——都不构成一份独立意见
_NOT_PAPER_REVIEW = ("review_ideas", "review_power", "review_citation", "review_desk", "review_editor")
#: 加上计划审稿——「第几轮论文审稿」的完整排除名单
NOT_PAPER_REVIEW = _NOT_PAPER_REVIEW + ("review_plan",)


def paper_review_round(p: Path) -> int | None:
    """这份报告算第几轮审稿；不是论文审稿的返回 None。**轮次以文件里的 round 为准，不看文件名。**

    「第 N 轮」这个判断原先只长在 `loop_status` 里，探索门要问同一个问题时
    只能照抄一份文件名正则——两处一旦漂移，`--for review-r2` 认的轮次
    就跟循环状态认的不是同一轮。判据只留一处。
    """
    if p.name.startswith(NOT_PAPER_REVIEW):
        return None
    d = _read_json(p)
    return int(d.get("round") or 1) if d else None


def classify_finding(text: str) -> str:
    """一条意见属于哪一类。**没打标 → 设计**（fail closed）。"""
    m = _TAG.match(text or "")
    if not m:
        return KIND_DESIGN
    k = m.group(1).lower()
    return _TAG_MAP.get(k, m.group(1))


def prelocked_robustness(rundir: Path) -> set[str] | None:
    """事前锁定的稳健性项：**第一次成功跑分析时清单里就有的那些。**

    返回 None 表示还没跑过分析，或者留痕里没记（老运行目录）——**这时不判**，
    不要把「查不到」当成「一项都没锁定」。

    为什么要有这个：事前锁定的稳健性项是一句承诺（分析开始前就写下要查什么），
    看过结果才加的是一次回应。两者可信度完全不同，而论文必须把它们分开报。
    原先分不出来，于是「稳健性清单不许超过 12 项」这条判据会把审稿人指名要求
    补做的诊断也算进去——**罚的是照要求补做，这个方向是反的**。

    判据挂在留痕上，不挂在计划里的声明字段上：谁都可以在 `rules` 里写一句
    「这项是事前锁定的」，但没人能改写第一次跑分析那一刻盘上是什么。
    """
    first: list[str] | None = None
    for row in _trace(rundir):
        if row.get("command") != "oss run" or (row.get("outcome") or "") != "ok":
            continue
        labels = (row.get("args") or {}).get("robustness")
        if labels is None:
            continue
        first = list(labels)
        break
    return set(first) if first is not None else None


def plan_sha(plan_path: Path) -> str:
    """分析计划的指纹。**用来判断「是不是真的换了设计」**——
    同一个计划重跑一百次也不算尝试了替代设计。"""
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    # 只对**会改变估计的字段**取指纹，改个 rationale 措辞不算换设计
    keep = {k: raw.get(k) for k in (
        "estimator", "outcome", "treatment", "controls", "fixed_effects", "cluster_level",
        "sample_filter", "interactions", "instruments", "running", "cutoff", "bandwidth",
        "unit", "time", "treat_time", "leads", "lags",
    )}
    # 描述块里改变估计的是 kind / groups / over：换了分组就是换了设计。meaningful_difference 与 benchmarks
    # 是写给论文与审稿看的字，改它们不算换设计（跟 rationale 同理）。**只在写了描述块时才进指纹**——
    # 没写的计划（所有老计划）指纹逐位不变，锚点里存的旧指纹照旧对得上
    desc = raw.get("description")
    if isinstance(desc, dict):
        keep["description"] = {k: desc.get(k) for k in ("kind", "groups", "over")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


@dataclass
class RoundSummary:
    round: int
    n_reviews: int = 0
    verdicts: list[str] = field(default_factory=list)
    design_open: int = 0
    data_open: int = 0
    text_open: int = 0
    #: 复审新看到的问题。**只计数，不进 design_open，不参与 FAIL / blocker 判断。**
    new_open: int = 0
    #: 复审判为「处置了」/「写进局限了」的上一轮意见。**不是遗留**，所以也不进 design_open。
    resolved: int = 0
    design_items: list[str] = field(default_factory=list)


@dataclass
class LoopState:
    round: int = 0
    max_rounds: int = MAX_ROUNDS
    rounds: list[RoundSummary] = field(default_factory=list)
    converged: bool = False
    exhausted: bool = False
    #: none / ok / fail_untried / exhausted
    identification_gate: str = "none"
    identification_note: str = ""
    anchor: dict = field(default_factory=dict)
    status: str = "no_reviews"
    blockers: list[str] = field(default_factory=list)
    #: 程序给出的下一步：review_first / continue / stop / export_with_open_items
    decision: dict = field(default_factory=dict)
    #: 编辑综合的判词与「必须改」条数。**不参与收敛判定**（编辑不是第四位审稿人），
    #: 但必须出现在决定里——见 `_editor_note`。
    editor: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _trace(rundir: Path) -> list[dict]:
    out: list[dict] = []
    p = rundir / "trace.jsonl"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


#: 公开名。research_type 门要在**换题之后的一段留痕**上单独判断，不能整份 trace 都读——
#: 跨模块用别人的下划线名迟早断（同 `manuscript.strip_code_and_quotes` 的先例）
trace = _trace


def _identification_gate(rundir: Path, trace: list[dict]) -> tuple[str, str]:
    """因果审计判 FAIL 之后有没有**真的**试过别的设计。

    判据全是机械的：
    - FAIL 的那一刻，最近一次 `oss run` 的计划指纹是 sha0
    - 之后任何一次 `oss run` 的指纹 ≠ sha0 → 试过了（ok）
    - 之后有一条 `design_alternatives_exhausted` 留痕 → 记录在案的逃生口（exhausted）
    - 都没有 → fail_untried：**允许导出**，但审计的每条意见都要写进局限（`paper export` 只提醒）
    """
    fail_seq = None
    for r in trace:
        arts = r.get("artifacts") or []
        note = str(r.get("note") or "")
        if r.get("step") == "review" and any(str(a).startswith("review_plan") for a in arts) \
                and note.startswith("FAIL"):
            fail_seq = r.get("seq", 0)
    if fail_seq is None:
        # 审计文件在但留痕里没记（老运行）：直接看文件
        # 执行者曾把审计报告改名成 review_identification.json 复制过来——两种名字都认
        for p in [*rundir.glob("review_plan*.json"), *rundir.glob("review_identification*.json")]:
            d = _read_json(p) or {}
            if d.get("verdict") == "FAIL":
                return "fail_untried", f"{p.name} 判 FAIL，留痕里找不到之后的重跑"
        return "none", ""

    sha0 = ""
    for r in trace:
        if r.get("seq", 0) < fail_seq and r.get("step") == "results":
            sha0 = str((r.get("args") or {}).get("plan_sha") or "")
    for r in trace:
        if r.get("seq", 0) <= fail_seq:
            continue
        if r.get("step") == "design_alternatives_exhausted":
            return "exhausted", str(r.get("note") or "")
        if r.get("step") == "results":
            sha = str((r.get("args") or {}).get("plan_sha") or "")
            if sha and sha != sha0:
                return "ok", f"FAIL 后换了设计重跑（指纹 {sha0 or '?'} → {sha}）"
    return "fail_untried", (
        "因果审计判 FAIL，之后没有换过设计重跑。允许导出，但每条审计意见都要写进局限；"
        "如果试过替代设计，至少一种要 `oss run` 重跑留痕，"
        "确实穷尽了就 `oss trace save --step design_alternatives_exhausted --note <理由>`"
    )


#: 公开名，理由同上：research_type 门要在换题后的子集留痕上复用这段判断，不整份 trace 重判
identification_gate = _identification_gate


#: 编辑综合里「必须改」的标记。编辑把三份报告合成一张单子时会分轻重，
#: 而这是全流程唯一一处**排过序**的意见——丢掉它等于丢掉整份综合的价值。
_MUST_FIX = re.compile(r"必须改|must[- ]fix", re.I)


def read_editor(rundir: Path, rnd: int | None = None) -> dict:
    """最近一轮编辑综合的判词与「必须改」条数。没有就返回空字典。"""
    best: dict = {}
    for p in sorted(rundir.glob("review_editor*.json")):
        d = _read_json(p) or {}
        if not d.get("verdict"):
            continue
        r = int(d.get("round") or 1)
        if rnd is not None and r != rnd:
            continue
        if r >= int(best.get("round") or 0):
            fs = [str(f) for f in (d.get("findings") or [])]
            best = {"round": r, "verdict": str(d["verdict"]), "file": p.name,
                    "n_findings": len(fs),
                    "must_fix": sum(1 for f in fs if _MUST_FIX.search(f))}
    return best


def _editor_note(ed: dict) -> str:
    """把编辑的判词接进决定的理由里。

    **原先它一个字都进不来。**编辑与桌审被排除在「论文轮次」之外是对的——
    它们不是独立的第三方意见。但排除出轮次计数，被写成了排除出全部下游：
    十一轮 CGSS 那次真跑，编辑判 FAIL、列了三条「必须改」，
    而 `oss loop status` 打出来的是「决定 stop：可导出」，**两句话之间没有任何东西**。

    按本模块的原则（轮次耗尽也可以交付，但**必须大声说出遗留了什么**），
    编辑的判词不该拦门，但必须跟着决定一起说出来。
    """
    if not ed or ed.get("verdict") in (None, "PASS"):
        return ""
    must = ed.get("must_fix") or 0
    tail = (f"，其中 {must} 条标了「必须改」" if must else "")
    return (f"；**编辑综合判 {ed['verdict']}**（第 {ed['round']} 轮，"
            f"{ed.get('n_findings', 0)} 条意见{tail}）——编辑不是第四位审稿人，"
            f"不拦导出，但这些条目要逐条写进论文的「尚未解决的问题」")


def decide(st: LoopState) -> dict:
    """下一步做什么，由这里说，不由 loop status 的散文让模型猜。

    事故（2026-08-28，DeepSeek 真跑）：状态是 exhausted，阻塞信息却写着
    "用 --round 3 复审"，模型照做，审到第 5 轮、四个半小时。

    只对 loop_status 填好的状态有意义（converged / exhausted 互斥由那里保证）。
    """
    if not st.rounds:
        return {"decision": DECISION_REVIEW_FIRST, "next_round": 1, "open_design_items": 0,
                "reason": "还没有任何审稿；先审一轮"}
    audit_note = ("；因果审计判 FAIL 且未换设计——允许，但每条审计意见都要写进局限"
                  if st.identification_gate == "fail_untried" else "") + _editor_note(st.editor)
    last = st.rounds[-1]
    if st.converged:
        return {"decision": DECISION_STOP, "next_round": None, "open_design_items": last.design_open,
                "reason": "最近一轮无 FAIL/ERROR/BLOCKED 且复审覆盖不缩：可导出" + audit_note}
    if st.exhausted:
        expected = max(r.n_reviews for r in st.rounds)
        missing = expected - last.n_reviews
        # 复审缩水时「最近一轮」的遗留条数会凭空归零：三位提了设计级意见，
        # 第二轮只回来一位 → 报 0。**没人复审 ≠ 没有遗留。**
        open_items = last.design_open if missing <= 0 else max(r.design_open for r in st.rounds)
        return {"decision": DECISION_EXPORT_OPEN, "next_round": None,
                "open_design_items": open_items,
                "reason": f"轮次耗尽（{st.max_rounds}）：导出；遗留 {open_items} 条设计级意见"
                          + (f"、{missing} 位审稿人未复审" if missing > 0 else "")
                          + "，写进论文 Open questions；不再复审" + audit_note}
    return {"decision": DECISION_CONTINUE, "next_round": last.round + 1,
            "open_design_items": last.design_open,
            "reason": f"第 {last.round} 轮有 FAIL/ERROR/BLOCKED 或覆盖不全：改稿、重渲染、"
                      f"用 --round {last.round + 1} 复审"}


def loop_status(rundir: Path) -> LoopState:
    """算出循环走到哪了，并落盘 `loop_state.json`。"""
    st = LoopState()
    trace = _trace(rundir)

    # 锚点：方向 + 选定的 idea。迭代设计时对着它看有没有跑偏。
    ideas = _read_json(rundir / "ideas.json") or {}
    plan = _read_json(rundir / "plan.json") or {}
    st.anchor = {"direction": ideas.get("direction", ""), "idea_id": plan.get("idea_id", ""),
                 "plan_sha": plan_sha(rundir / "plan.json")}

    by_round: dict[int, RoundSummary] = {}
    for p in sorted(rundir.glob("review_*.json")):
        rnd = paper_review_round(p)
        if rnd is None:
            continue
        d = _read_json(p)
        if not d:
            continue
        rs = by_round.setdefault(rnd, RoundSummary(round=rnd))
        rs.n_reviews += 1
        rs.verdicts.append(str(d.get("verdict", "ERROR")))
        for f in d.get("findings") or []:
            k = classify_finding(f)
            if k == KIND_NEW:
                rs.new_open += 1          # 不进 design_open：新发现不构成本轮的义务
            elif rnd >= 2 and is_disposed(str(f)):
                # **已判处置的不是遗留。**照原样计数的话，全部解决的一轮也会报
                # 「遗留 3 条」，模型照着这个数把已经改好的条目写进 Open questions。
                # 只在第 2 轮起认：首轮的意见还没有人判过处置状态。
                rs.resolved += 1
            elif k == KIND_DESIGN:
                rs.design_open += 1
                rs.design_items.append(str(f)[:160])
            elif k == KIND_DATA:
                rs.data_open += 1
            else:
                rs.text_open += 1
    st.rounds = [by_round[k] for k in sorted(by_round)]
    st.round = max(by_round) if by_round else 0

    st.identification_gate, st.identification_note = _identification_gate(rundir, trace)
    st.editor = read_editor(rundir)

    if not st.rounds:
        st.status = "no_reviews"
        st.decision = decide(st)
    else:
        last = st.rounds[-1]
        # BLOCKED 也算 bad。**判不了就按最严重的算**——BLOCKED 是「没法核」，
        # 不是「核过了没问题」，把它当收敛等于让后端不可用替审稿人签字放行。
        bad = any(v in ("FAIL", "ERROR", "BLOCKED") for v in last.verdicts)
        # **复审的覆盖面不能比首轮窄。**第一次真跑这个循环：首轮三位审稿人，
        # 第二轮只有一位回来了（其余两位网关超时），设计级意见 0 → 判了收敛。
        # 一场超时风暴就能让收敛变得毫不费力——那不是收敛，是没人在场。
        expected = max(r.n_reviews for r in st.rounds)
        covered = last.n_reviews >= expected
        # 「合格」的收敛：最近一轮没有 FAIL/ERROR/BLOCKED，且复审覆盖不比首轮窄。
        # 设计级意见**不再要求清零**——能改设计就改，改不了就写进局限的
        # 「未解决的审稿意见」。原来要求清零，一次跑 77 分钟花在审稿上，
        # 而目标是合格，不是收敛到完美（用户定的）。
        st.converged = (not bad) and covered
        st.exhausted = (not st.converged) and st.round >= st.max_rounds
        st.status = "converged" if st.converged else ("exhausted" if st.exhausted else "in_progress")
        # 决定先算出来，阻塞话术再引用它的遗留条数。**遗留几条只有一个出处。**
        # 阻塞信息原来自己数「最近一轮」：复审缩水时三位提的设计级意见会凭空归零，
        # 而 decide() 会退回历史最大值——两个数不一样，模型照着阻塞信息写论文。
        # decide() 不读 blockers，提前调用是安全的。
        st.decision = decide(st)
        if bad and not st.exhausted:
            st.blockers.append(f"第 {last.round} 轮有 FAIL/ERROR/BLOCKED 判词——改稿、重渲染、"
                               f"用 --round {last.round + 1} 复审")
        if bad and st.exhausted:
            st.blockers.append(
                f"轮次耗尽（{st.max_rounds}）且最近一轮有 FAIL/ERROR/BLOCKED——**不再复审**；"
                f"导出，并把遗留的 {st.decision['open_design_items']} 条设计级意见"
                f"写进论文 Open questions")
        if not covered and not bad:
            if st.exhausted:
                st.blockers.append(
                    f"轮次耗尽（{st.max_rounds}）且第 {last.round} 轮只有 {last.n_reviews}/{expected} "
                    f"位审稿人回来了——**不再复审**；导出，但必须写明哪几位没复审")
            else:
                st.blockers.append(
                    f"第 {last.round} 轮只有 {last.n_reviews}/{expected} 位审稿人回来了——"
                    f"复审覆盖不全不算收敛。后端超时就重试，重试不成就等；"
                    f"轮次耗尽（{st.max_rounds}）后可以导出，但要写明哪几位没复审")
    if st.identification_gate == "fail_untried":
        st.blockers.append("因果审计判 FAIL 后没有尝试替代设计")

    (rundir / "loop_state.json").write_text(
        json.dumps(st.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return st
