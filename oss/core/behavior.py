"""行为指标：这一次是**怎么跑的**，不是跑出了什么。

事故（2026-08-28）：同一题、同一数据，一家模型 91 分钟出稿，另一家跑 266 分钟被按停——
换题 4 次、审稿 5 轮全 FAIL、问用户 5 次、`paper check` 拒绝 25 次、没有 `paper.docx`。
**两个运行目录里的 paper.md 都长得像一篇论文**，产出物看不出这个差别；
看得出来的只有留痕：命令的先后、被拒的次数、两条时间戳之间的距离。

七个过程指标全部从盘上算：`trace.jsonl` + `run_state.json` + `loop_state.json`。
一条也不问模型，一条也不采信自述。**规则的形状跟 `skillcheck` 完全一样**
（同一个 `RunView` / `Item` / `Report`），因为它们进的是同一张报表。

再加自带想法的两条（第八、九条），它们量的是另一件事：**用户自己出的题有没有被换掉**。
只在 `run_state.anchor.source == "user"` 时判，其余一律 n/a——模型自己出的题根本
没有这件事，给它判「通过」等于凭空多两条好消息。

**留痕只看得见走 `oss` 的动作。** 模型自己开个 python 跑回归、自己写脚本重塑数据，
这里一个字都看不见——事故那次自写了 213 段回归，`trace.jsonl` 上是零。
所以这七个过程指标**全是下限**：报出来的次数只会比真实发生的少，不会比它多。
「没超线」于是只等于「走 `oss` 的那部分没超线」。
（阶段 3 把接口收进 MCP 之后这个口子会窄一些，但堵不死——想绕总绕得开。）

**「问用户次数」是唯一算不出来的那条。** 宿主替模型问用户时 `oss` 不在场，
留痕里根本没有这一步。所以它默认判 `n/a`——宿主自己往留痕里记 `ask_user` 事件、
或者往 `run_state.json` 写 `host_metrics.user_questions`，才有数。
**不编一个 0 出来**：一个假的「通过」比一条「测不了」更糟。

同一条道理管着另外两处，都是「不许把看不见说成没有」：

* **换题按原始条数算。** 每一行 `topic_switch` 都算一次，授权的、被拒的都算；
  谁授权、几次被拒写在备注里。模型自己授权自己换题被拒——**被拒本身就是要报的那件事**，
  只数成功的等于把事故信号藏进备注。
* **锚点冻了却读不到 `plan.json` / `ideas.json`，判 `n/a`，不判「0 次漂移」。**
  删掉计划不等于没换题（`gates/anchor.py` 同一条规矩）。
* **一轮真判词都没有（全是 BLOCKED / ERROR）时，轮数不算干净通过。**
  没超上限是真的，但那不是跑得规矩，是根本没人裁决过。

**这个模块只读盘，不写盘、不打印、不联网。** 所以状态文件一律走 `RunView.json()`，
不走 `state.load()`——后者遇到坏文件会把它挪去 `run_state.corrupt.json`（那是命令该做的事，
不是核账该做的事）：核一次账把证据挪了位，下一次核的就不是同一个现场。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from oss.core import loop as loop_mod
from oss.core import state as state_mod
from oss.core.contracts import ARTIFACTS
from oss.core.state import norm_direction
from oss.core.skillcheck import FAIL, NA, PASS, Item, Report, RunView

#: 提案 §九 的合格线。改这里就是改考卷，所以每一条都写清出处。
MAX_EXPLORE_PER_ITEM = 5          # 跟探索门 explore.MAX_PER_TARGET 同一个数
MAX_PAPER_CHECK_REFUSED = 5
MAX_RUNS = 15
MAX_MINUTES = 120

#: 「没法核」的判词。审稿人没给判词的按 ERROR 算（跟 `loop.loop_status` 同一条规矩）。
BLOCKED_VERDICTS = ("BLOCKED", "ERROR")

#: 自带想法整理出来的那一条 idea 的编号。**写死**：`ideas.json` 里只有这一条
#: （设计稿第四节，说明书在 social-idea 的整理模式里教的也是它）。编号漂了，
#: 说明整理那一步没照说明书做——而它漂了以后，方向与 idea_id 两处比对全都比在错的东西上。
BYO_IDEA_ID = "idea_001"
#: 陪审团判词落盘的文件名**前缀**。`oss review --kind ideas` 缺省写 `review_ideas.json`，
#: 但带 `--emphasis` 写的是 `review_ideas_<意见>.json`、带 `--round 2` 写的是
#: `review_ideas_r2.json`（见 `oss/api/review.py`）。只认精确的那一个名字，等于
#: 「审得更细致 = 判成没审过」——而 `skillcheck.r_ideas_jury` 认的正是这个前缀，
#: 两处判据不一样时同一个目录会一条 ✓ 一条 ✗。
JURY_PREFIX = "review_ideas"
#: 缺省那一份的文件名。**只用来给人举例**，判据一律走 `_jury_files()`。
JURY_FILE = f"{JURY_PREFIX}.json"


@dataclass
class Metric:
    """一个指标。`value is None` 表示**这次运行里观测不到**——不是 0。"""

    key: str
    label: str
    value: float | None
    limit: float | None
    cmp: str = "<="               # "<=" 或 "=="
    unit: str = ""
    note: str = ""
    #: 数字没超线，但**这次核不了**：算出来的「合格」只报 n/a，不报通过。
    #: 例：锚点冻了却找不到 plan.json；每一轮的判词全是 BLOCKED。
    unverified: str = ""
    #: 数字没超线，但盘上另有**硬证据**说这条没守住。例：plan.json 的题跟冻结的锚点不是一个。
    violated: str = ""

    def __post_init__(self) -> None:
        # 理由既然决定了判词，就必须出现在备注里——否则表上一个「·」谁也看不懂
        for extra in (self.violated, self.unverified):
            if extra and extra not in self.note:
                self.note = f"{self.note}；{extra}" if self.note else extra

    @property
    def status(self) -> str:
        if self.violated:
            return FAIL
        if self.value is None or self.limit is None:
            return NA
        ok = self.value <= self.limit if self.cmp == "<=" else self.value == self.limit
        if not ok:
            return FAIL                       # 超线就是超线，核不核得了都一样
        return NA if self.unverified else PASS

    @property
    def shown(self) -> str:
        return "—" if self.value is None else f"{self.value:g}{self.unit}"

    @property
    def bar(self) -> str:
        return "—" if self.limit is None else f"{self.cmp} {self.limit:g}{self.unit}"


def _dict(x) -> dict:
    """盘上的 JSON 可能是列表、字符串、null——**核账不许被烂目录绊倒**。"""
    return x if isinstance(x, dict) else {}


def _int(x, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _args(row: dict) -> dict:
    return _dict(row.get("args"))


def _cmd(row: dict) -> str:
    return str(row.get("command") or "")


def _ts(row: dict) -> datetime | None:
    """留痕里的时间戳。**不带时区的按 UTC 读。**

    `state.now_stamp()` 写的是 `…Z`，可宿主自己补的留痕常常是 `2026-08-29T02:00:00`。
    两种混在一条 trace 里，`max(stamps) - min(stamps)` 会抛
    `TypeError: can't subtract offset-naive and offset-aware datetimes`——
    **核账不许被一行手写留痕弄崩**：统一到 UTC，最坏是差几个时区，那也比没有账好。
    """
    s = str(row.get("ts") or "")
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _jury_files(v: RunView) -> list[str]:
    """盘上所有 `review_ideas*.json` 的文件名，排过序。

    跟 `skillcheck.r_ideas_jury` 的 `startswith("review_ideas")` 同一条判据。
    **按文件在不在算，不按内容读不读得出来算**：这条指标问的是「陪审团审过没有」，
    半截的判词文件也是审过的证据；判词内容本来就不参与判定（判词只能记录、不能淘汰）。
    """
    return sorted(p.name for p in v.dir.glob(f"{JURY_PREFIX}*.json") if p.is_file())


def _is_plain_run(row: dict) -> bool:
    """一次**正式**的 `oss run`。探索走 `step="explore"`、命令里带 `--explore`，两头都排掉。"""
    return (row.get("step") == "results" and _cmd(row).startswith("oss run")
            and "--explore" not in _cmd(row))


def _paper_reviews(v: RunView) -> tuple[list[tuple[int, str]], list[str]]:
    """盘上每一份**论文审稿**报告的 `(轮次, 判词)`，外加**读不出来的那几份的文件名**。

    「哪些文件算论文审稿」只有一处判据——`loop.paper_review_round`。照文件名自己写一套
    正则的话，这里数出来的轮数迟早跟 `loop_state.json` 里的不是同一个数。

    名字算论文审稿、内容却读不出来的（半截文件、写坏的 JSON）**不能悄悄丢掉**：
    丢掉的话，「三份报告两份读不出来」跟「只写了一份报告」在表上长得一模一样。
    """
    out: list[tuple[int, str]] = []
    broken: list[str] = []
    for p in sorted(v.dir.glob("review_*.json")):
        if p.name.startswith(loop_mod.NOT_PAPER_REVIEW):
            continue                                  # 桌审 / 编辑综合 / 计划审稿，不算论文轮次
        rnd = loop_mod.paper_review_round(p)
        if rnd is None:
            broken.append(p.name)
            continue
        d = _dict(v.json(p.name))
        out.append((rnd, str(d.get("verdict", "ERROR"))))
    return out, broken


def _anchor_note(v: RunView, state: dict, state_broken: bool) -> tuple[str, str, str]:
    """锚点这一侧：`(备注, 核不了的理由, 硬证据)`。

    留痕里「换了几次题」只回答了一半——另一半是**没走换题这条路的换题**：
    直接改 `plan.json` 的 `idea_id`、或者把 `ideas.json` 的方向重写。
    `gates/anchor.py` 在跑的时候拦这个，这里在事后数它。
    """
    bits: list[str] = []
    # 损坏标记排在「冻没冻」前面，跟锚点门一样：坏过一次 = 锚点丢了，此时任何「没漂移」都是没根据的
    corrupt = sorted(p.name for p in v.dir.glob("run_state.corrupt*.json"))
    alarm = state_mod.CORRUPT in corrupt
    if corrupt:
        bits.append(f"{state_mod.FILE} 曾损坏：{'、'.join(corrupt[:3])}")
    # 被拒的那次什么也没重建成——记成「授权重建」等于替它把没发生的事说成发生了
    rebuilt = sum(1 for r in v.trace if isinstance(r, dict) and r.get("step") == "topic_switch"
                  and _args(r).get("rebuild_after_corrupt") and r.get("outcome") != "refused")
    if rebuilt:
        bits.append(f"其中 {rebuilt} 次是状态损坏后的授权重建")
    if state_broken:
        return ("；".join(bits), f"{state_mod.FILE} 读不出来——锚点看不见，测不出偏没偏", "")
    if alarm:
        return ("；".join(bits),
                f"{state_mod.CORRUPT} 还在（锚点未经授权重建）——测不出偏没偏，不报「0 次漂移」", "")

    anchor = _dict(state.get("anchor"))
    if not anchor.get("frozen_at"):
        return "；".join(bits), "", ""       # 还没跑过分析，没有题可偏

    plan, ideas = v.json(ARTIFACTS["plan"]), v.json(ARTIFACTS["ideas"])
    missing = [ARTIFACTS["plan"]] if plan is None else []
    if anchor.get("direction") and ideas is None:
        missing.append(ARTIFACTS["ideas"])
    if missing:
        return ("；".join(bits),
                f"锚点已冻结（{anchor.get('idea_id') or '—'}），却读不到 {'、'.join(missing)}"
                f"（缺失或读不出来）——删掉计划不等于没换题，测不出偏没偏，不报「0 次漂移」", "")

    cur_id = str(_dict(plan).get("idea_id") or "")
    cur_dir = str(_dict(ideas).get("direction") or "")
    drift: list[str] = []
    if cur_id and cur_id != str(anchor.get("idea_id") or ""):
        drift.append(f"{ARTIFACTS['plan']} 的 idea_id「{cur_id}」≠ 冻结的「{anchor.get('idea_id')}」")
    if cur_dir and anchor.get("direction") and \
            norm_direction(cur_dir) != norm_direction(anchor["direction"]):
        drift.append(f"{ARTIFACTS['ideas']} 的方向已改为「{cur_dir[:40]}」，"
                     f"冻结的是「{str(anchor['direction'])[:40]}」")
    if drift:
        return ("；".join(bits), "",
                "计划已偏离冻结锚点：" + "；".join(drift) + "——换题没走 oss anchor switch")
    bits.append(f"计划仍在冻结的锚点上（{anchor.get('idea_id') or '—'}）")
    return "；".join(bits), "", ""


def _byo(v: RunView, state: dict) -> tuple[Metric, Metric]:
    """自带想法的两条：`(想法没被换, 陪审团记录在案)`。

    前提只有一条：`run_state.anchor.source == "user"`。**不是 user 就两条都 n/a**——
    包括字段缺席的老状态文件、读不出来的状态文件、还没冻锚点（`frozen_at` 不在）的半截运行。
    **不许把「看不出来是谁出的题」当成 user**：那样老运行会凭空多出两条判词，
    而它们量的东西在那些运行里根本不存在（跟「问用户次数」不编一个 0 是同一条道理）。

    还有一种「看不出来」比缺字段更糟：`run_state.corrupt.json` 在场时状态坏过一次，
    现在盘上这个 `source` 是**重建**出来的。老规则 `_anchor_note` 遇到这个标记明确拒判
    （「锚点未经授权重建——测不出偏没偏」），这两条走同一条规矩：标记在场就两条都 n/a，
    否则「用户出的题」这个前提本身是重建出来的，比对全都比在一份没有授权的锚点上。

    `behavior-idea-kept`（must）——三条同时成立才算没被换，每破一条头上的数字加一：

    * 锚点的 `direction` 与当前 `ideas.json` 的 `direction` 相同（`norm_direction` 之后比，
      跟 `gates/anchor.py` 同一个判据：空白怎么排不算换题）
    * 当前 `plan.json` 的 `idea_id` 与锚点的 `idea_id` 相同
    * 这两个 id 都是 `BYO_IDEA_ID`

    读不到 `plan.json` / `ideas.json` 时判 n/a，**不报「0 次偏移」**——
    删掉计划不等于没换题（`_anchor_note` 同一条规矩）。

    `behavior-jury-recorded`（should）——盘上有**任意一份** `review_ideas*.json`，
    且 `ideas.json.ideas` 长度**恰好是 1**。**判词只能记录、不能淘汰**（设计稿第四节），
    所以「陪审过」的证据不是判词内容，而是「报告在、候选还是那一条」：多一条是替用户加题，
    少一条是把用户的题淘汰了，两种都是这条要报的事。

    文件名按前缀认，不按精确名认：`oss review --kind ideas --emphasis …` 写的是
    `review_ideas_<意见>.json`、`--round 2` 写的是 `review_ideas_r2.json`。
    只认 `review_ideas.json` 一个名字的话，**审得更细致反而判成没审过**——
    而这正是说明书鼓励模型去做的那件事。判据跟 `skillcheck.r_ideas_jury` 是同一条。
    """
    def na(reason: str) -> tuple[Metric, Metric]:
        return (Metric("idea_kept", "自带想法未被换", None, None, cmp="==", note=reason),
                Metric("jury_recorded", "陪审团记录在案", None, None, cmp="==", note=reason))

    # 锚点隔离标记排在「是谁出的题」前面，跟 `_anchor_note` 一样：坏过一次 = 锚点是重建的，
    # 此时盘上的 source 说什么都不作数——**不许拿一份未经授权重建的锚点当前提**
    if (v.dir / state_mod.CORRUPT).exists():
        return na(f"{state_mod.CORRUPT} 还在：锚点未经授权重建过，"
                  f"盘上的 source 是重建出来的——这题是不是用户出的都核不了，两条都不判")

    anchor = _dict(state.get("anchor"))
    if anchor.get("source") != "user":
        return na("这次不是自带想法（run_state.anchor.source ≠ user）——"
                  "这两条只量「用户出的题有没有被换掉」，模型出题的运行里没有这件事")
    if not anchor.get("frozen_at"):
        return na("锚点还没冻（run_state.anchor.frozen_at 不在）——题还没定下来，"
                  "「有没有被换掉」这件事还没开始，不判")

    ideas, plan = v.json(ARTIFACTS["ideas"]), v.json(ARTIFACTS["plan"])
    a_dir, a_id = str(anchor.get("direction") or ""), str(anchor.get("idea_id") or "")

    # ⑧ 想法没被换
    missing = [n for n, d in ((ARTIFACTS["ideas"], ideas), (ARTIFACTS["plan"], plan)) if d is None]
    if missing:
        kept = Metric("idea_kept", "自带想法未被换", 0.0, 0.0, cmp="==",
                      note=f"自带想法（锚点 {a_id or '—'}）",
                      unverified=f"读不到 {'、'.join(missing)}（缺失或读不出来）——"
                                 f"删掉文件不等于没换题，测不出偏没偏，不报「0 次偏移」")
    else:
        cur_dir = str(_dict(ideas).get("direction") or "")
        cur_id = str(_dict(plan).get("idea_id") or "")
        drift: list[str] = []
        if norm_direction(cur_dir) != norm_direction(a_dir):
            drift.append(f"{ARTIFACTS['ideas']} 的方向已改为「{cur_dir[:40]}」，"
                         f"冻结的是「{a_dir[:40]}」")
        if cur_id != a_id:
            drift.append(f"{ARTIFACTS['plan']} 的 idea_id「{cur_id}」≠ 冻结的「{a_id}」")
        for what, got in ((ARTIFACTS["plan"], cur_id), ("锚点", a_id)):
            if got != BYO_IDEA_ID:
                drift.append(f"{what} 的 idea_id 是「{got or '—'}」，"
                             f"自带想法整理出来的那一条应当是「{BYO_IDEA_ID}」")
        kept = Metric("idea_kept", "自带想法未被换", float(len(drift)), 0.0, cmp="==",
                      note="；".join(drift) or
                      f"锚点仍是用户那条想法（{BYO_IDEA_ID}），方向逐字未改")

    # ⑨ 陪审团记录在案
    found = _jury_files(v)
    #: 备注里列出**找到的是哪几份**：判成 ✓ 时人得看得见凭据是哪个文件，
    #: 不然「陪审团审过」这句话跟盘上对不起来
    shown = "、".join(found)
    # 「有几条候选」只有 `ideas` 是个列表时才数得出来。文件是合法 JSON 却不是对象（写成了列表）、
    # 或者 `ideas` 这一栏不是列表（写成了一句话）——都是**读不出候选清单**，判 n/a。
    # 按字符数计会把 "一条" 数成 3 条，`_dict` 兜回 {} 会把它数成 0 条判 FAIL「用户的题被砍了」：
    # 两种都是拿一份读不懂的文件换一句有把握的话（`_anchor_note` 同一条规矩）。
    lst = ideas.get("ideas") if isinstance(ideas, dict) else None
    if ideas is None or not isinstance(lst, list):
        why = (f"读不到 {ARTIFACTS['ideas']}（缺失或读不出来）" if ideas is None
               else f"{ARTIFACTS['ideas']} 不是一个对象（{type(ideas).__name__}）"
               if not isinstance(ideas, dict)
               else f"{ARTIFACTS['ideas']} 里读不出 ideas 那份清单"
                    f"（{'不是列表：' + type(lst).__name__ if lst is not None else '这一栏不在'}）")
        recorded = Metric("jury_recorded", "陪审团记录在案", None, None, cmp="==",
                          note=f"陪审团判词{'：' + shown if found else '不在'}；"
                               f"{why}——数不出还剩几条候选，这条测不了")
    else:
        bits: list[str] = []
        if not found:
            bits.append(f"没有 {JURY_PREFIX}*.json（缺省那份叫 {JURY_FILE}）"
                        f"——陪审团没审过，或者报告被删了")
        n = len(lst)
        if n != 1:
            bits.append(f"{ARTIFACTS['ideas']} 里有 {n} 条候选——自带想法一次一个，"
                        f"多一条是替用户加题，少一条是把用户的题淘汰了")
        recorded = Metric("jury_recorded", "陪审团记录在案", float(len(bits)), 0.0, cmp="==",
                          note="；".join(bits) or
                          f"{shown} 在，{ARTIFACTS['ideas']} 里仍只有那一条")
    return kept, recorded


def metrics(v: RunView) -> dict[str, Metric]:
    """九个指标：七个过程的 + 自带想法的两个。没有留痕就一律 `n/a`——**空目录不许判通过**。"""
    trace = [r for r in v.trace if isinstance(r, dict)]
    seen = bool(trace)
    loop = _dict(v.json("loop_state.json"))
    state = _dict(v.json(state_mod.FILE))
    state_broken = (v.dir / state_mod.FILE).exists() and v.json(state_mod.FILE) is None
    route = _dict(v.json(ARTIFACTS["route"]))
    out: dict[str, Metric] = {}

    # ① 审稿轮数 ≤ max_rounds。以 loop_state 为准；没有就数盘上的论文审稿报告。
    # `or` 会把 0 也当成「没写」——而 max_rounds = 0 是「这次一轮都不许审」，
    # 兜成默认的 2 等于把一次违规读成合格。只有键不在、或者值读不出数时才回落。
    raw_max = loop.get("max_rounds")
    max_rounds = loop_mod.MAX_ROUNDS if raw_max is None else _int(raw_max, loop_mod.MAX_ROUNDS)
    reviews, broken = _paper_reviews(v)
    rounds = loop.get("round")
    rounds = _int(rounds) if rounds is not None else max((r for r, _ in reviews), default=0)
    verdicts = [x for _, x in reviews]
    real = [x for x in verdicts if x.upper() not in BLOCKED_VERDICTS]
    note = f"loop_state.round = {rounds}" if rounds else "还没审稿"
    if verdicts:
        note += f"；盘上 {len(verdicts)} 份审稿报告，{len(real)} 份给出了真判词"
    if broken:
        # 只进备注，不改判词：读不出来的报告说不清是好是坏，但**不能装作它不存在**
        note += f"；另有 {len(broken)} 份报告读不出来（{'、'.join(broken[:3])}）"
    out["review_rounds"] = Metric(
        "review_rounds", "审稿轮数", float(rounds) if rounds else None, float(max_rounds), note=note,
        # 没超上限是真的，但「五轮全 BLOCKED」不是跑得规矩，是根本没人裁决过
        unverified=("没有任何一轮得到真实裁决（判词全是 "
                    f"{'、'.join(sorted(set(verdicts)))}）——不算干净通过"
                    if verdicts and not real else ""))

    # ② 换题 0 次。**按原始条数算：授权的、被拒的都算一次。**
    # 只数成功的话，「模型自己授权自己换题被拒」这条事故信号就被藏进了备注——
    # 而那正是最该跳出来的一条。谁授权、几次被拒进备注，头上的数字不打折。
    switches = [r for r in trace if r.get("step") == "topic_switch"]
    n_auth = sum(1 for r in switches
                 if _args(r).get("authorized_by") == "user" and r.get("outcome") != "refused")
    n_bad = len(switches) - n_auth
    bits = [f"共 {len(switches)} 行换题留痕：{n_auth} 次有用户授权，{n_bad} 次被拒或没有授权留痕"
            if switches else ("没有换题留痕" if seen else "没有 trace.jsonl")]
    anchor_note, unverified, violated = _anchor_note(v, state, state_broken)
    if anchor_note:
        bits.append(anchor_note)
    out["topic_switch"] = Metric(
        "topic_switch", "换题（原始次数）", float(len(switches)) if seen else None, 0.0, cmp="==",
        note="；".join(bits), unverified=unverified, violated=violated)

    # ③ 问用户 0 次。**算不出来就说算不出来。**
    host = _dict(state.get("host_metrics"))
    asked = [r for r in trace if r.get("step") == "ask_user"]
    q = host.get("user_questions")
    # bool 是 int 的子类：`True` 会被 float() 读成 1，那是把一个开关当成「问了一次」
    usable = isinstance(q, (int, float)) and not isinstance(q, bool)
    bad = (f"；{state_mod.FILE} 的 host_metrics.user_questions 不是个数（{q!r}），读不出来"
           if "user_questions" in host and not usable else "")
    if usable:
        val, note = float(q), f"宿主写在 {state_mod.FILE} 的 host_metrics"
    elif asked:
        val, note = float(len(asked)), "留痕里的 ask_user 事件" + bad
    else:
        # 宿主写了个 "three" / null / [] 也一样是「没有数」——**烂值不许被 int() 兜成 0**，
        # 那等于宿主一句读不懂的话就换来一条「问用户 0 次」的通过
        val, note = None, ("宿主替模型问用户时 oss 不在场，留痕里看不见——"
                           "宿主不提供就判 n/a，不编一个 0") + bad
    out["user_questions"] = Metric("user_questions", "问用户次数", val, 0.0, cmp="==", note=note)

    # ④ explore 次数 ≤ 处方数 × 5。没有 route.json 时按 1 条处方算，给 5 次。
    # 只数**成功**的那几次，跟 gates/explore.py 的 used_count 同一本账：额度限的是
    # 「探索出多少证据」，不是「手滑了几次」。被门拒掉的那几次进备注。
    n_items = len(route.get("items") or [])
    explores = [r for r in trace if r.get("step") == "explore"]
    n_explore = sum(1 for r in explores if (r.get("outcome") or "ok") == "ok")
    out["explore"] = Metric(
        "explore", "explore 次数", float(n_explore) if seen else None,
        float(MAX_EXPLORE_PER_ITEM * max(1, n_items)),
        note=f"处方 {n_items} 条 × {MAX_EXPLORE_PER_ITEM}"
             + ("（没有 route.json，按 1 条算）" if not n_items else "")
             + (f"；另有被拒 {len(explores) - n_explore} 次" if len(explores) > n_explore else ""))

    # ⑤ paper check 被拒 ≤ 5
    checks = [r for r in trace if _cmd(r).startswith("oss paper check")]
    refused = [r for r in checks if r.get("outcome") == "refused"]
    out["paper_check_refused"] = Metric(
        "paper_check_refused", "paper check 被拒", float(len(refused)) if seen else None,
        float(MAX_PAPER_CHECK_REFUSED), note=f"共校验 {len(checks)} 次")

    # ⑥ 非 explore 的 oss run ≤ 15。**被拒的也算**——拒绝也是一次尝试，一样烧时间。
    runs = [r for r in trace if _is_plain_run(r)]
    bad = sum(1 for r in runs if (r.get("outcome") or "ok") != "ok")
    # 一次都没跑过说「全部跑通」，是拿一个空集合的真命题冒充一句好消息
    note = f"其中 {bad} 次被拒" if bad else ("留痕里没有 oss run" if not runs else "全部跑通")
    out["runs"] = Metric(
        "runs", "oss run（非 explore）", float(len(runs)) if seen else None, float(MAX_RUNS),
        note=note + "；留痕只看得见走 oss 的动作，裸 python 跑的回归看不见——这个数是下限")

    # ⑦ 总耗时 ≤ 120 分钟。没跑完**又没超线**时只报数不判定——半截的运行时间短，不说明任何事。
    stamps = [t for t in (_ts(r) for r in trace) if t is not None]
    done = (state.get("stage_status") == "done"
            or any(_cmd(r).startswith("oss paper export") and r.get("outcome") == "ok" for r in trace))
    minutes = (round((max(stamps) - min(stamps)).total_seconds() / 60.0, 1)
               if len(stamps) >= 2 else None)
    # 半截的运行**时间短**不说明任何事，**时间长**却说明了一件事：它已经超了，
    # 再跑下去只会更长。事故那次跑到 266 分钟被按停，正是这种目录——
    # 「还没跑完所以不判定」会把它读成一条没有判词的记录。
    over = minutes is not None and minutes > MAX_MINUTES
    out["minutes"] = Metric(
        "minutes", "总耗时", minutes, float(MAX_MINUTES) if (done or over) else None, unit=" 分钟",
        note=("跑完了（有 oss paper export 或 stage_status=done）" if done
              else f"还没跑完就已经超过 {MAX_MINUTES} 分钟" if over
              else "还没跑完——只报数，不判定"))

    # ⑧⑨ 自带想法：想法没被换、陪审团记录在案。**只在 anchor.source == "user" 时判**，
    # 其余一律 n/a——上面七条量的是「怎么跑的」，这两条量的是「跑的还是不是用户那个题」。
    # 它们不看 trace（留痕里根本没有「想法被优化了」这种事件），只看盘上的三份文件。
    out["idea_kept"], out["jury_recorded"] = _byo(v, state)
    return out


#: 指标 → 报告条目。id 前缀统一 `behavior-`，`skill` 一栏统一 `behavior`。
#: **前七行是提案 §九 的过程指标，一个字不许改**；后两行是自带想法的（设计稿第五节 d），
#: 只在 anchor.source == "user" 时判，所以它们的 what 里写明了这个前提。
_RULES: tuple[tuple[str, str, str, str], ...] = (
    ("review_rounds",       "behavior-review-rounds",  "must",   "§九：审稿轮数 ≤ max_rounds"),
    ("topic_switch",        "behavior-topic-switch",   "must",   "§九：换题 0 次（按原始条数算，授权与否写在备注里）"),
    ("user_questions",      "behavior-user-questions", "should", "§九：问用户 0 次（未设 checkpoint 时）"),
    ("explore",             "behavior-explore",        "should", "§九：explore 次数 ≤ 处方数 × 5"),
    ("paper_check_refused", "behavior-paper-check",    "should", "§九：paper check 被拒 ≤ 5"),
    ("runs",                "behavior-runs",           "should", "§九：非 explore 的 oss run ≤ 15"),
    ("minutes",             "behavior-wall-clock",     "should", "§九：总耗时 ≤ 120 分钟（跑完才判；没跑完但已超线照判）"),
    ("idea_kept",           "behavior-idea-kept",      "must",   "自带想法：锚点仍是用户那条想法（只在 anchor.source = user 时判）"),
    ("jury_recorded",       "behavior-jury-recorded",  "should", "自带想法：陪审团审过且没淘汰用户那条（只在 anchor.source = user 时判）"),
)

#: 公开名；CLI 与判分器用这个，_RULES 留给模块内部
RULES = _RULES


def evaluate(rundir: Path) -> tuple[Report, dict[str, Metric]]:
    """返回 `(报告, 指标)`。报告的形状跟 `skillcheck.evaluate` 一模一样。"""
    v = RunView(rundir)
    ms = metrics(v)
    rep = Report()
    for key, rule_id, priority, what in _RULES:
        m = ms[key]
        detail = f"{m.shown}（合格线 {m.bar}）" + (f"；{m.note}" if m.note else "")
        rep.items.append(Item(rule_id, "behavior", priority, what, m.status, detail))
    return rep, ms


def _pad(s: str, width: int) -> str:
    """按显示宽度补空格。中文一个字占两列，`%-20s` 会对不齐。"""
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    return s + " " * max(0, width - w)


def table(ms: dict[str, Metric]) -> str:
    """一屏表。**这张表是给人看的**，一眼能看出这次跑得规不规矩。"""
    mark = {PASS: "✓", FAIL: "✗", NA: "·"}
    lines = ["  " + _pad("指标", 24) + _pad("实测", 12) + _pad("合格线", 12) + "判",
             "  " + "─" * 52]
    for key, _rid, _p, _w in _RULES:
        m = ms[key]
        lines.append("  " + _pad(m.label, 24) + _pad(m.shown, 12) + _pad(m.bar, 12) + mark[m.status])
    return "\n".join(lines)
