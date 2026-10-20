"""研究类型门：类型随锚点冻结，冻结后只许在识别站不住时从因果降为描述。

设计稿 docs/design/2026-09-13-research-types.md 第 4.2 节：

- **允许**：causal → descriptive。理由只认两条机械可查的——识别审计判 FAIL 且替代设计已穷尽
  （留痕 `design_alternatives_exhausted`，`oss loop status` 报 exhausted），或机器诊断「平行趋势：不成立」
- **不允许**：往大改（descriptive → causal）、平移（measurement ↔ descriptive 之类），
  以及**因为结果不显著而改**——p 值不在理由里，零结果照样是因果研究的零结果
- 要往别处改：回环节 5 重写计划，由用户授权 `oss anchor switch` 重置锚点

**计划没跟着改也要按降级查措辞**（用户 2026-09-14 决定 4A，Task 11c）：停止条件里「审计判 FAIL、
替代设计也穷尽」那一条是强制的——`stop-conditions.md` 写的是「保留原设计，全文降级为关联」，
不是「可以选择降级」。`plan.json` 的 `research_type` 还没跟着从 `causal` 改成 `descriptive`，
不该让论文继续用因果的口气写：`wording_scope` 见到 `forced_downgrade(rundir)` 为真，
直接把它当 `("descriptive", True)` 处理——跟计划本来就写着 descriptive、且合规降级过
的论文，措辞检查走的是同一档。**机器诊断平行趋势不成立不算这一档的理由**：那条理由
只是 `downgrade_grounds` 里**允许**改计划降级的另一条机械理由，本身另有 must 级的
处置路径（`oss route --disclose pretrend_fail`），不是「审计 FAIL、替代设计穷尽」这条
必须执行的停止条件——单靠它，计划没改就还是因果，措辞检查不查。

锚点没冻结、计划没声明类型——都不比：老运行目录照旧能跑。**但冻结之后不许再撤回**——
把 `research_type` 删掉或改回空串不算「没声明」，算「想撤」，一样拒绝（下面 `check()`）。

**首次声明即锁**（用户 2026-09-14 决定 2A）：锚点冻结那一刻计划本身没声明类型（新式锚点，
这一格留的是空串，老运行目录压根没写这一栏的除外），**第一次有计划声明了类型的那次跑**
就把这一格锁死（`oss/core/state.py::refill_plan_sha`）——锁定之后就走上面这条「冻结后
不许撤回、只许因果降描述」的老规矩。这一次「补」是在看过结果之后才补的：`oss run`
会另留一行 `research_type_declared_late`（`declared_late()` 认的形状：`command` 恰好是
`oss run`、`args` 里的 `research_type` 是合法值），`oss eval` 的 `research-type-declared`
规则见到这一行就判 FAIL——类型该在第一次 `oss run` 之前写明，不是看过结果才定。
换题之后新题自己的第一次跑（`plan_sha` 那一刻也跟着留空）不算「看过结果才补」，不留这一行。
**legacy 锚点例外**：这一格压根不存在的运行目录（锚点冻结时研究类型这条轴线还没上线）——
不补这一格，也不留痕：「从没声明过」是这一批老运行目录的固有状态，不该被强行判成「看过结果才补」。

**光写 `plan.json`、不重跑，锁不上，但骗不过合规检查**（复审 2026-09-14 抓出）：
上面这套「首次声明即锁」靠的是 `oss run` 跑到最后那一步才真的往锚点里写——如果补写了
`research_type` 却从没再跑一次 `oss run`，锚点这一格永远留空，`check()` 见到「不比」
直接放行，`oss eval`、`identification-audit`、`oss paper check` / `export` 单看
`plan.json` 会误以为类型早就定了、也不用审。`declared_unlocked()` 就是补的这一道：
锚点冻结、这一格存在但是空、`plan_sha` 已经非空（不是换题后新题的第一次跑）、
这次计划声明的类型合法——四条都对上，就是「写完类型没重跑」，`skillcheck` 与
`oss paper check` / `export` 见到就拒，`RunView.is_causal` 在这个状态下也不能让
一个刚写上、还没锁定的非因果标签压过估计量本身的因果形状（`did` / `did_cs` 之类）——
不然「把研究类型改成描述、不重跑」就成了一条比走 `oss run` 更省事的免审计的路。
**这道检查只在 `oss run` 之外的只读路径上拦**（`skillcheck`、`paper check` / `export`）：
`oss run` 自己走到冻结/补格那一步之前，锚点这一格本来就该是「存在但空」——
`research_type.check()` 不认这条，否则 `oss run` 自己都跑不到能把类型锁上的那一步。

**同题换题不许洗掉「看过结果才补」的记录**：说明书教的重定类型的正规做法是
`oss anchor switch --to <当前 idea_id>`——题目没换，只是借这个口子把新类型正式写
进锚点。`_since_last_switch` 拿任意成功的 `topic_switch` 当边界，会把这一行本身
也当成「翻篇」，边界一移，之前记的 `research_type_declared_late` 就被挡在外头，
`declared_late()` 判成 False——「看过结果才补」的证据被一次自己授权给自己的换题
洗白了。`declared_late()` 因此改用 `_since_last_topic_change`：`args.from == args.to`
的 `topic_switch` 不算边界（`already_recorded` / `downgrade_grounds` 仍用
`_since_last_switch`，两件事不是同一个问题，不能共用同一个口径）。

**降级留痕只记一次、之后靠它免检**：降级那一刻的理由已经核过、也留痕了；
若之后重新指定设计（比如把 did 换成 ols_fe），下一次 `results.json` 里可能已经没有
「平行趋势」这条诊断——那不是「机械理由消失了」，是「已经过关的降级」换了个不再需要
同一份证据的设计。`check()` 因此认已记过的降级本身就是理由，不会在换设计后把它当成
「无理由降级」再拦一次。**但这份留痕认得很挑**（`already_recorded`）：只认门自己在
`oss run` / `oss paper check` 里写的那一种行——`command` 以那两个开头、`args` 恰好是
`{"from": "causal", "to": "descriptive"}`；`oss trace save --step research_type_downgrade`
写不出这个形状，伪造不出「合规降级」。

**换题会清空这份免检**：`oss anchor switch` 授权换到另一道题之后，旧题的审计 FAIL、
穷尽留痕、已经记过的降级，都只在**旧题名下**算数——新题要降级得自己攒出机械理由，
不能借用上一道题的。判据是留痕里最近一条**成功**（非 refused）的 `topic_switch`：
之后的那一段才算这道题的历史，之前的一律不看（`_since_last_switch`）；**被拒的换题
尝试不算翻篇**——`outcome="refused"` 那一行不移动边界，之前记的理由照样算数。

**「换题之后真跑出过一份新结果」也认得很挑**：`downgrade_grounds` 判平行趋势时，
只认 `oss run` 落盘 `results.json` 时写的那一种行（`step="results"`、`outcome="ok"`、
`command=="oss run"`、`artifacts` 里有 `results.json`，见 `oss/api/rundir.py:emit`）。
`oss trace save --step results` 写得出同名的 `step`，但 `command` 是空串、`artifacts`
是空列表——伪造不出这个形状，骗不到「新题真跑过」，也就复活不了盘上残留的、
旧题（或手改过）的平行趋势诊断。

**留痕里的 `seq` 读不出来就当它不存在**（`_seq`）：手改坏的行不该让 `int()` 直接把
整道门崩掉；也不当 0 处理——0 会垫高换题边界或混进换题之后那一段，读不准就宁可
让这一行两头都不算数。
"""

from __future__ import annotations

import json
from pathlib import Path

from oss.core import loop as loop_mod
from oss.core import state as state_mod
from oss.core.contracts import ARTIFACTS, RESEARCH_TYPE_LABELS, RESEARCH_TYPES
from oss.core.findings import Finding

#: 降级留痕的 step 名。**只记一次**：`oss run` 与 `oss paper check` 谁先看见谁记
DOWNGRADE_STEP = "research_type_downgrade"

#: 「首次声明即锁、但那次声明是看过结果之后才补的」留痕的 step 名。补一次锁一次，只记一次
DECLARED_LATE_STEP = "research_type_declared_late"


def _label(t: str) -> str:
    return RESEARCH_TYPE_LABELS.get(t, t or "—")


def plan_type(rundir: Path) -> str:
    """plan.json 写的研究类型。没有计划、读不进、没声明、不在四类里——一律空串（= 不比、不查）。

    `oss paper check` / `oss paper export` 用它：那两处不该因为计划文件坏了就崩在研究类型这一步。
    """
    try:
        d = json.loads((Path(rundir) / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    t = str(d.get("research_type") or "") if isinstance(d, dict) else ""
    return t if t in RESEARCH_TYPES else ""


def _seq(row: dict) -> int | None:
    """一行的序号，读不出来就是 `None`——**不当 0 处理**。手改坏的行（`seq` 写成字符串、
    列表……）不该让 `int()` 直接炸穿整道门；也不能悄悄当 0：0 会参与下面两处比较
    （垫高换题边界、或混进换题之后那一段），读不准就宁可让这一行两头都不算数——
    伪造一行读不懂的 `seq` 骗不到「边界往后挪」，也混不进「换题之后」的证据。
    """
    try:
        return int(row.get("seq"))
    except (TypeError, ValueError):
        return None


def _since_last_switch(rundir: Path) -> list[dict]:
    """当前这道题名下的留痕：最近一条**成功**（非 `refused`）的 `topic_switch` 之后的那些行；
    没换过题就是整份 trace。**换题是这道门唯一认的「翻篇」**——授权换题会指向另一道题，
    旧题的审计 FAIL、穷尽留痕、已经记过的降级都不该被新题借用，否则「授权换题」
    就成了一条绕过降级门的路（被拒的换题尝试没有真的翻篇，不算数）。

    返回的每一行都是**浅拷贝，`seq` 换成了解析出来的整数**：手改坏的留痕能把 `seq` 写成
    数字字符串（比如 `"11"`）——`_seq()` 解析得懂、这一行照样留下，但原样传给
    `loop.identification_gate` 的话，它拿字符串 `seq` 跟别的整数 `seq` 一比就是
    `TypeError`（`int` 和 `str` 不能比大小）。这里统一换回整数，下游就不必再操心类型。
    """
    rows = loop_mod.trace(rundir)
    last = 0
    for r in rows:
        if r.get("step") == "topic_switch" and str(r.get("outcome") or "ok") != "refused":
            s = _seq(r)
            if s is not None:
                last = max(last, s)
    out = []
    for r in rows:
        s = _seq(r)
        if s is not None and s > last:
            out.append({**r, "seq": s})
    return out


def _since_last_topic_change(rundir: Path) -> list[dict]:
    """跟 `_since_last_switch` 一样找「翻篇」的边界，但**同题换题**（`args.from == args.to`）
    不算翻篇——只给 `declared_late` 用。

    `oss anchor switch --to <当前 idea_id>` 是说明书教的「重定研究类型」的正规做法
    （先改 `plan.json` 的 `research_type`，再授权 `switch --to <当前 idea_id>`）：
    题目根本没换，只是借这个口子把新类型正式写进锚点。如果照 `_since_last_switch` 的
    口径，这一行本身也会被当成「翻篇」，把它之前记的 `research_type_declared_late`
    一并挡在边界外——「看过结果才补」的证据就这样被一次跟自己谈判似的换题洗掉了。
    这不是 `already_recorded` / `downgrade_grounds` 要防的那种「借旧题理由」，
    是反过来：不该让同题换题**清空**一条本该留着的、对自己不利的记录，
    所以只给 `declared_late` 开这个口子，`_since_last_switch` 原样不动。
    """
    rows = loop_mod.trace(rundir)
    last = 0
    for r in rows:
        if r.get("step") == "topic_switch" and str(r.get("outcome") or "ok") != "refused":
            args = r.get("args") or {}
            if str(args.get("from") or "") == str(args.get("to") or ""):
                continue
            s = _seq(r)
            if s is not None:
                last = max(last, s)
    out = []
    for r in rows:
        s = _seq(r)
        if s is not None and s > last:
            out.append({**r, "seq": s})
    return out


def downgrade_grounds(rundir: Path) -> str:
    """因果 → 描述的机械理由；都不满足返回空串。**结果显不显著不在其中。**

    只看这道题名下（换题之后）的那一段留痕（`_since_last_switch`）：
    `oss loop status` 直接调用会把 `loop_state.json` 落盘，那是有副作用的读——
    这里改叫 `loop.identification_gate`，只对着那一段算，不落盘。
    """
    rundir = Path(rundir)
    rows = _since_last_switch(rundir)
    gate, _ = loop_mod.identification_gate(rundir, rows)
    if gate == "exhausted":
        return "识别审计判 FAIL、替代设计已穷尽（留痕 design_alternatives_exhausted）"
    # 平行趋势只在**这道题换题之后真跑出过一份结果**时才算数——没有这一条，
    # 盘上的 results.json 可能是旧题留下的（新题还一次都没跑过），拿它当新题的诊断是假的。
    # 只认 `emit()` 落盘 results.json 时写的那一种行的形状（oss/api/rundir.py）：
    # step="results"、outcome="ok"、command 恰好是 "oss run"、artifacts 里有 results.json。
    # `oss trace save --step results` 写得出同名的 step，但 command 是空串、artifacts 是空列表——
    # 松一分（比如只认 step 名、或 outcome 不是 refused 就算）就是 R2：换题之后伪造一行
    # `results`，能把旧题手改的 / 上一次真跑剩下的 results.json 冒充成「新题刚跑出来的」
    fresh_results = any(r.get("step") == "results" and str(r.get("outcome") or "ok") == "ok"
                        and r.get("command") == "oss run"
                        and ARTIFACTS["results"] in (r.get("artifacts") or [])
                        for r in rows)
    if fresh_results:
        try:
            res = json.loads((rundir / "results.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            res = {}
        if "不成立" in str(((res or {}).get("diagnostics") or {}).get("平行趋势", "")):
            return "机器诊断：平行趋势不成立"
    return ""


def check(rundir: Path, plan_type: str) -> tuple[list[str], str]:
    """比对计划与冻结锚点的研究类型。返回 `(拒绝理由, 降级理由)`，二者至多一个非空。

    `not frozen`（没冻结，或冻结那一刻就没声明类型）才不比；**冻结之后 `plan_type` 变空
    不算「没声明」，算「想撤」**——把 `research_type` 删掉或写回空串，字面上跟「一直没声明」
    长得一样，但语义完全不同：前者是从没做过判断，后者是判断已经做完、盘上也认了，
    现在想让后面的审计与措辞检查（Task 6/8）把它当成没声明过而跳过。两者只能靠
    「锚点当时冻的是不是空」分：`frozen` 非空就是曾经声明过，`plan_type` 再变空就要拒。
    """
    rundir = Path(rundir)
    anchor = state_mod.load(rundir).anchor
    frozen = str(anchor.get("research_type") or "") if anchor.get("frozen_at") else ""
    if not frozen or plan_type == frozen:
        return [], ""
    if not plan_type:
        return [(Finding(f"研究类型检测：锚点冻结的研究类型是「{_label(frozen)}」，plan.json 把 research_type "
                 f"删了（或写成了空）——冻结后不许撤回类型：写回 {frozen}", "gate.research_type"))], ""
    if frozen == "causal" and plan_type == "descriptive":
        why = downgrade_grounds(rundir)
        # 机械理由这一刻查不到，但降级此前已经记过——那次记录本身就是理由：
        # 换设计之后新结果里可能已经没有当初那条诊断了，不能因此把已核过的降级重新当成无理由
        why = why or ("已记录过合规降级（留痕 research_type_downgrade）" if already_recorded(rundir) else "")
        if why:
            return [], why
        return [(Finding("研究类型检测：plan.json 把研究类型从「因果」降成了「描述」，但没有降级的理由——"
                 "只有识别站不住才能降：审计判 FAIL 且替代设计穷尽后留痕 design_alternatives_exhausted，"
                 "或机器诊断平行趋势不成立。**结果不显著不是降级的理由**：零结果照样按因果研究写"
                 "（先要有识别审计判 FAIL；替代设计确实穷尽了，再用 oss trace save "
                 "--step design_alternatives_exhausted --note <理由> 记下）", "gate.research_type"))], ""
    return [(Finding(f"研究类型检测：锚点冻结的研究类型是「{_label(frozen)}」，plan.json 改成了「{_label(plan_type)}」——"
             f"冻结后只许在识别站不住时从因果降为描述。要改成别的类型得回环节 5 重写计划，"
             f"并由用户授权重置锚点：oss anchor switch --to <idea_id> --why ... --authorized-by user", "gate.research_type"))], ""


def forced_downgrade(rundir: Path) -> bool:
    """计划仍标 `causal`，但这道题名下的识别审计已经判 FAIL、替代设计也穷尽了——
    用户 2026-09-14 决定 4A（Task 11c）：措辞检查按已经降级处理，不等 `plan.json` 跟着改。

    只认 `identification_gate` 判成 `"exhausted"` 那一种——`downgrade_grounds` 里另一条
    机械理由「机器诊断：平行趋势不成立」**不算**：平行趋势不成立只是**允许**把计划改成
    descriptive（改了、留痕记一次，走 `check()` 那条路就通过），不是必须执行的停止条件；
    它自己另有 must 级的处置路径（`pretrend_fail` 处方），不是这里要拦的「审计 FAIL、
    替代设计穷尽 → 保留原设计、全文降级为关联」那一条。

    只看这道题名下（换题之后）的那一段留痕，跟 `downgrade_grounds` 用同一个口径
    （`_since_last_switch`）——旧题的审计 FAIL、穷尽留痕不该被新题借用。
    """
    rundir = Path(rundir)
    if plan_type(rundir) != "causal":
        return False
    gate, _ = loop_mod.identification_gate(rundir, _since_last_switch(rundir))
    return gate == "exhausted"


def wording_scope(rundir: Path) -> tuple[str, bool]:
    """措辞检查用哪个类型、是不是从因果降级来的。

    没声明类型 → `("", False)`：不查措辞。计划仍标 `causal` 但没被强制降级
    （`forced_downgrade`）→ 同样不查。计划仍标 `causal`、但识别审计判 FAIL、替代设计也
    穷尽了（`forced_downgrade` 为真，用户 2026-09-14 决定 4A）——按停止条件「保留原设计，
    全文降级为关联」，当它已经是 descriptive 且是降级来的：`("descriptive", True)`。
    **光是机器诊断平行趋势不成立不够**：那条理由只*允许*改 `plan.json`，不强制；
    计划真的还没改，`plan_type` 读到的还是 `causal`，措辞检查就不查。

    `oss paper check`、`oss paper export`、`oss review --paper` 三处共用——判据只写一处。
    """
    rundir = Path(rundir)
    rtype = plan_type(rundir)
    if rtype == "causal":
        return ("descriptive", True) if forced_downgrade(rundir) else ("", False)
    if not rtype:
        return "", False
    _, down = check(rundir, rtype)
    return rtype, bool(down)


#: `already_recorded` 只认的降级留痕形状：门自己写的 args 恰好是这样
_DOWNGRADE_ARGS = {"from": "causal", "to": "descriptive"}


def already_recorded(rundir: Path) -> bool:
    """降级留痕记过没有。**只认门自己写的那一种行**——`step` 是 `DOWNGRADE_STEP`、
    `outcome` 是 `"ok"`（或缺席，老留痕没有这一格）、`command` 以 `oss run` 或
    `oss paper check` 开头、`args` 恰好是 `{"from": "causal", "to": "descriptive"}`。

    `oss trace save --step research_type_downgrade --note x` 能写出同名的 `step`，
    但 `command` 是空串、`args` 是空字典——伪造不出这三样一起对上，冒充不了「合规降级」。
    只看这道题名下（换题之后）的那一段：旧题记过的降级，换了题就不再算数（`_since_last_switch`）。
    """
    for r in _since_last_switch(Path(rundir)):
        if (r.get("step") == DOWNGRADE_STEP
                and str(r.get("outcome") or "ok") == "ok"
                and str(r.get("command") or "").startswith(("oss run", "oss paper check"))
                and r.get("args") == _DOWNGRADE_ARGS):
            return True
    return False


def declared_late(rundir: Path) -> bool:
    """这道题名下有没有「类型是看过结果之后才补的」这行留痕（`oss/core/skillcheck.py`
    的 `research-type-declared` 规则、`RunView.is_causal` 靠它判）。**只认 `oss run`
    自己写的那一种行**——`step` 是 `DECLARED_LATE_STEP`、`outcome` 是 `"ok"`（或缺席）、
    `command` 恰好是 `"oss run"`、`args` 里的 `research_type` 是四类里的合法值。

    `oss trace save --step research_type_declared_late --note x` 能写出同名的 `step`，
    但 `command` 是空串、`args` 是空字典——伪造不出这三样一起对上，冒充不了「真是补的」。

    只看这道题名下的那一段——但**用的是 `_since_last_topic_change`，不是
    `_since_last_switch`**：换题一般算翻篇，但「同题换题」（`switch --to` 当前那道题，
    说明书教的重定类型的正规做法）不该把这一行洗掉，见 `_since_last_topic_change` 的说明。
    """
    for r in _since_last_topic_change(Path(rundir)):
        if (r.get("step") == DECLARED_LATE_STEP
                and str(r.get("outcome") or "ok") == "ok"
                and str(r.get("command") or "") == "oss run"
                and str((r.get("args") or {}).get("research_type") or "") in RESEARCH_TYPES):
            return True
    return False


def declared_unlocked(anchor: dict, plan_type: str) -> bool:
    """`plan.json` 写着一个合法类型，但这一格从没被**填进锚点**、锁死过——是刚在
    `plan.json` 上敲的字，还没有再跑一次 `oss run` 让 `refill_plan_sha` 把它写进锚点。
    `oss/core/skillcheck.py::r_research_type_declared`、`RunView.is_causal`、
    `oss/api/paper.py` 的 `check` / `export` 靠它挡「写完类型不重跑也能过关」的漏洞：
    不重跑的话，`research_type.check()` 见到锚点这一格空着直接判「不比」放行，
    `oss eval`、论文校验与导出单看 `plan.json` 会以为类型早就定了。

    **纯函数**：吃锚点字典，不读盘、不碰 `state.load()`——`skillcheck` 判一条合规规则
    不该顺手触发 `state.load()` 对损坏 `run_state.json` 的隔离动作（那是锚点门自己的
    报警，不是一条只读规则该有的副作用），调用方（已经手上有 `RunView.json` 或
    `state_mod.load` 读出来的那份 `anchor`）直接把字典传进来就是。

    判据：锚点已冻结 AND 这一格**存在但是空串**（新式锚点、还没被首次声明锁过——
    这一格压根不存在的 legacy 锚点不算，那是「这条轴线还没上线」，不是「没重跑」）
    AND `plan_sha` 非空（换题后新题的第一次跑那一刻 `plan_sha` 本来就留空，
    这一格空着是正常状态，不该判「没重跑」）AND 这次计划声明的类型合法。
    """
    if not anchor.get("frozen_at"):
        return False
    if "research_type" not in anchor or anchor.get("research_type"):
        return False
    if not anchor.get("plan_sha"):
        return False
    return plan_type in RESEARCH_TYPES


def causal_question(rundir: Path, idea_id: str) -> list[str]:
    """选中那道题的题目与研究问题里有没有因果问法。有就返回拒绝理由。

    防的是「为了躲识别审计，把因果问题标成描述」。用 `causal_wording.asks_causal`：硬拦档的说法，
    加上问句里的因果问法（「是否导致」/"whether … causes"——研究问题天然是问句，扫描器把问句降成标出档的设问）。
    只是模糊的问法（「……的影响」）不在这里拦——那一半是陪审团判「类型标得对不对」的活。
    """
    from oss.core import causal_wording

    try:
        ideas = json.loads((Path(rundir) / "ideas.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    lst = ideas.get("ideas") if isinstance(ideas, dict) else None
    idea = next((x for x in (lst or []) if isinstance(x, dict) and x.get("idea_id") == idea_id), None)
    if not idea:
        return []
    text = f"{idea.get('title', '')}\n{idea.get('research_question', '')}"
    hits = causal_wording.asks_causal(causal_wording.scan(text))
    if not hits:
        return []
    return [Finding(f"研究类型标的是非因果，但选中那道题（{idea_id}）自己就在问因果：「{hits[0].snippet}」——"
            f"想下因果结论就把 research_type 写成 causal（要过识别审计）；只想描述，就把题目与研究问题改成描述的问法", "gate.causal_question")]
