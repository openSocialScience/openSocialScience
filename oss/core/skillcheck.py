"""说明书的测试：对着一个运行目录，核执行者有没有照说明书做。

工具箱有五百多个测试，**说明书一个都没有**——说明书靠模型听话，听不听话只能事后看对话记录。
验收跑就是这样：说明书写了「分批实施用 did_cs」「先开方再写论文」「没读全文不许说首次」，
执行者没做，没有任何东西报错。

借 AERS 的 eval-harness 思路（场景 + 规则，规则能机械核的机械核），但我们核的不是
模型写的散文，是**留在运行目录里的产物**：trace.jsonl 里命令的先后、plan.json 的估计量、
route.json 的处置状态、paper.md 里有没有表、review_*.json 有没有三位审稿人。
产物是客观的，规则就能是机械的。

每条规则：属于哪份说明书、must / should、怎么核。`must` 不过 = 说明书没被遵守。
故意改坏一条（比如把 plan.json 的 did_cs 改回 did），对应规则必须变红——那是这些规则的测试。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from oss.core import binding as binding_mod
from oss.core.contracts import ARTIFACTS, RESEARCH_TYPES

PASS, FAIL, NA = "pass", "fail", "n/a"


@dataclass
class Rule:
    id: str
    skill: str
    priority: str          # must / should
    what: str              # 说明书里的那句话
    check: object          # (RunView) -> tuple[status, detail]


@dataclass
class Item:
    id: str
    skill: str
    priority: str
    what: str
    status: str
    detail: str = ""


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)

    @property
    def must_failed(self) -> list[Item]:
        return [i for i in self.items if i.priority == "must" and i.status == FAIL]

    def to_dict(self) -> dict:
        return {"items": [i.__dict__ for i in self.items],
                "must_failed": [i.id for i in self.must_failed],
                "summary": {s: sum(1 for i in self.items if i.status == s) for s in (PASS, FAIL, NA)}}


class RunView:
    """运行目录的只读视图：文件按需读、读不出来就是 None，规则只管判。"""

    def __init__(self, rundir: Path) -> None:
        self.dir = Path(rundir)
        self._cache: dict[str, object] = {}

    def json(self, name: str):
        if name not in self._cache:
            p = self.dir / name
            try:
                self._cache[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
            except (OSError, json.JSONDecodeError):
                self._cache[name] = None
        return self._cache[name]

    def text(self, name: str) -> str | None:
        p = self.dir / name
        try:
            return p.read_text(encoding="utf-8") if p.exists() else None
        except OSError:
            return None

    @property
    def trace(self) -> list[dict]:
        if "trace" not in self._cache:
            out = []
            p = self.dir / ARTIFACTS["trace"]
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
            self._cache["trace"] = out
        return self._cache["trace"]  # type: ignore[return-value]

    def seqs(self, command_prefix: str) -> list[int]:
        return [int(r.get("seq", 0)) for r in self.trace
                if str(r.get("command", "")).startswith(command_prefix) and r.get("outcome") == "ok"]

    def run_seqs(self) -> list[int]:
        """**正式**分析的 seq。按 step 认，不按命令前缀认。

        `oss run --explore --for <处方>` 的命令也以「oss run」开头——用前缀数，
        一次合法的探索就被当成新一次正式分析，「跑完分析要开方」于是在探索之后
        误报（探索本来就是开方之后的诊断动作）。留痕里正式分析的 step 是 results，
        探索是 explore，两者机械可分。
        """
        return [int(r.get("seq", 0)) for r in self.trace
                if r.get("step") == "results" and r.get("outcome", "ok") == "ok"]

    def reviews(self) -> dict[str, dict]:
        out = {}
        for p in sorted(self.dir.glob("review_*.json")):
            d = self.json(p.name)
            if d:
                out[p.name] = d
        return out

    @property
    def plan(self) -> dict | None:
        return self.json(ARTIFACTS["plan"])

    @property
    def results(self) -> dict | None:
        return self.json(ARTIFACTS["results"])

    @property
    def paper(self) -> str | None:
        return self.text(ARTIFACTS["paper"])

    @property
    def ran(self) -> bool:
        """分析跑过没有：results.json 在盘上就算。**读不读得出来不论**——坏掉的结果文件
        不能让一个跑过的目录冒充成「一次都还没跑」的新运行。"""
        return (self.dir / ARTIFACTS["results"]).exists()

    @property
    def anchor(self) -> dict:
        """run_state.json 里的锚点；没有、读不出来、形状不对，一律空字典。"""
        st = self.json("run_state.json")
        a = st.get("anchor") if isinstance(st, dict) else None
        return a if isinstance(a, dict) else {}

    def declared_types(self) -> set[str]:
        """锚点冻结时的类型与计划写的类型里，**合法**的那几个（两处取并集）。"""
        p = self.plan or {}
        return {str(x or "") for x in (self.anchor.get("research_type"), p.get("research_type"))} & set(RESEARCH_TYPES)

    def type_locked(self) -> bool:
        """声明的类型是不是**事先定的、锁住了**——只有这样，一个非因果的声明才压得过估计量：

        - 锚点冻结了、这一格是合法值（新运行冻结那一刻就写着，或首次声明即锁），
          而且不是「看过结果才补」才锁上的（`declared_late`）
        - 或者一次都还没跑：没有冻结的锚点、也没有 results.json——计划上的类型就是事先定的

        其余一律锁不住：legacy 锚点（没有这一格，`refill_plan_sha` 永远不补）、锚点出现之前
        就跑过分析的老目录、这一格空着（写了没重跑，或换题后新计划还没跑）、看过结果才补。
        """
        from oss.gates import research_type as rt_gate

        a = self.anchor
        if not a.get("frozen_at"):
            return not self.ran
        return str(a.get("research_type") or "") in RESEARCH_TYPES and not rt_gate.declared_late(self.dir)

    def is_causal(self) -> bool:
        """要不要做识别审计。**研究类型优先**（设计稿 4.3，按结论触发、不按方法触发）：
        锚点冻结时的类型与计划写的类型，取**两处合法值的并集**——任一处是 causal 就要审计；
        两处都没有合法值（老运行目录，或两边都是手改坏的非法字符串）才按估计量兜底。

        取并集而不是「锚点优先、有就用」：锚点里一个无效或者只是**不同**的值，
        不能把计划里明明白白写着的 causal 声明压没——审计该做的时候不能因为另一处的噪音而漏做。

        **非因果的声明只有锁定了才压得过估计量**（`type_locked`）。复审 2026-09-14 先抓出
        「看过结果才补」「写了没重跑」两种；重放验收 2026-09-14 又抓出老目录——锚点出现之前
        就跑完的、legacy 锚点的，在 plan.json 上补一个 descriptive、不重跑，11 个真目录里
        识别审计 9 个 pass→n/a、2 个 FAIL→n/a。锁不住的时候改成「声明是 causal，或者估计量
        本身长得像因果设计」二选一即可触发审计：把 `did` / `did_cs` 这种因果形状的设计
        标成描述、不重跑就想免检，不能得逞。
        """
        p = self.plan or {}
        declared = self.declared_types()
        estimator_rule = (p.get("estimator") in ("did", "did_cs", "event_study", "iv", "rdd")
                          or bool(p.get("treat_time")))
        if not declared:
            return estimator_rule
        if "causal" in declared:
            return True
        return False if self.type_locked() else estimator_rule


# ------------------------------------------------------------------ 规则


def _cohorts(v: RunView) -> int:
    m = re.search(r"(\d+)\s*批", ((v.results or {}).get("diagnostics") or {}).get("实施批次", ""))
    return int(m.group(1)) if m else 0


def _external(v: RunView) -> bool:
    """结果是外部导入的吗（`oss results import`）。

    那几条查「有没有真跑过」的规则对它**不适用**——但要**明说不适用**，
    不能顺着「还没跑分析」判 n/a 然后显示一片绿：报告绿着，其实什么都没查。
    """
    return bool((v.results or {}).get("imported"))


def r_profile_first(v: RunView):
    if not v.trace:
        return NA, "没有留痕"
    prof = v.seqs("oss profile")
    if not prof:
        return FAIL, "留痕里没有 oss profile——数据画像是第一步"
    plan_seq = min((int(r.get("seq", 0)) for r in v.trace if ARTIFACTS["plan"] in (r.get("artifacts") or [])), default=None)
    if plan_seq is not None and min(prof) > plan_seq:
        return FAIL, "分析计划写在数据画像之前"
    return PASS, f"oss profile 在 seq {min(prof)}"


def r_lit_camps(v: RunView):
    lit = v.json(ARTIFACTS["literature"])
    if lit is None:
        return NA, "没有 literature.json"
    camps = set((lit.get("camps") or {}).values())
    if len(camps) >= 2:
        return PASS, f"{len(camps)} 个阵营：{'、'.join(sorted(camps))}"
    return FAIL, f"检索式没按阵营写（{len(camps)} 个阵营）——理论对话要有对立面，至少两派各一条"


def r_lit_fetch(v: RunView):
    idx = v.json("reading/index.json")
    cards = v.json(ARTIFACTS["reading"])
    if idx is None and cards is None:
        return FAIL, "没有精读记录（reading/index.json）也没有读书卡"
    if not cards:
        return FAIL, "拿了文献没写读书卡（reading_cards.json）"
    full = sum(1 for i in (idx or []) if i.get("level") == "fulltext")
    return PASS, f"精读 {len(idx or [])} 篇，全文 {full} 篇；读书卡 {len(cards.get('cards', cards) if isinstance(cards, dict) else cards)} 张"


def r_ideas_jury(v: RunView):
    ideas = v.json(ARTIFACTS["ideas"])
    if ideas is None:
        return NA, "没有 ideas.json"
    if v.json("review_ideas.json") is None and not any(n.startswith("review_ideas") for n in v.reviews()):
        return FAIL, "候选 idea 没经过跨模型陪审团（没有 review_ideas.json）"
    lst = ideas.get("ideas") or []
    no_lens = [i.get("idea_id") for i in lst if not i.get("lens")]
    if no_lens:
        return FAIL, f"{len(no_lens)} 个 idea 没记透镜来源（lens）：{'、'.join(map(str, no_lens[:5]))}"
    return PASS, f"{len(lst)} 个候选，都有透镜来源，陪审团审过"


def r_ideas_candidates_monotone(v: RunView):
    """发散段产出多少条，往后就该一直是多少条。

    守**不变量二**（生成与淘汰的权限不对称）。收敛段 ④ 第一次看到完整变量清单，
    而清单是能勾起联想的：看见两列有意思的变量就顺手添一个题——**那就是数据在出题**。
    淘汰这个动作本身产生不了新题，所以条数只要没涨，不对称就没破。

    反方向同样是违规：被砍的候选要**留在文件里带理由**，它是下次探索的反重复记忆，
    删掉就白挖了一次。所以判据是**相等**，不是「不大于」。

    看不出来就不判：老运行目录没有 `generated_n`，不能因为「没记」判它违规。
    """
    ideas = v.json(ARTIFACTS["ideas"])
    if ideas is None:
        return NA, "没有 ideas.json"
    n = int(ideas.get("generated_n") or 0)
    if n <= 0:
        return NA, "没记发散段产出数（generated_n），看不出来——**看不出来不等于违规**"
    lst = ideas.get("ideas") or []
    if len(lst) > n:
        return FAIL, (
            f"发散段产出 {n} 条，文件里却有 {len(lst)} 条——**多出 {len(lst) - n} 条**。"
            "收敛段只许拿变量清单砍题，不许拿它造题（数据出题）")
    if len(lst) < n:
        return FAIL, (
            f"发散段产出 {n} 条，文件里只剩 {len(lst)} 条——**{n - len(lst)} 条被删出了文件**。"
            "砍掉的候选要留着并标注理由，它是下次探索的反重复记忆")
    return PASS, f"{n} 条候选自发散段起没增没删"


def r_ideas_two_lists(v: RunView):
    """数据做不了的题要分流到清单乙，不是砍掉。

    守**不变量三**。验证段一重排，「能跑的题」会自动占据前排——如果做不了的直接丢掉，
    整个研究就被系统性降级成「这份数据恰好允许的那些题」，而且降级过程看起来非常客观。

    两件事要核：那些题**还在文件里**（不然就是被删了），以及**写清了缺什么**
    （只标「做不了」不说缺什么，用户就没有去拿数据的路）。
    """
    ideas = v.json(ARTIFACTS["ideas"])
    if ideas is None:
        return NA, "没有 ideas.json"
    un = [str(x) for x in (ideas.get("unreachable") or [])]
    if not un:
        return NA, "没有标记为数据不可及的候选——「都做得了」和「这一步没做」分不出来，不判"
    lst = ideas.get("ideas") or []
    have = {str(i.get("idea_id") or ""): i for i in lst}
    gone = [x for x in un if x not in have]
    if gone:
        return FAIL, (
            "清单乙点名了 " + "、".join(gone) + "，文件里却没有这几条——"
            "数据做不了的题被**删掉**了，而不是分流保留")
    silent = [x for x in un if not str(have[x].get("needs_extra_data") or "").strip()]
    if silent:
        return FAIL, (
            "清单乙的 " + "、".join(silent) + " 没写缺什么数据（needs_extra_data 为空）——"
            "只标「做不了」不说缺什么，用户就没有去拿数据的路")
    return PASS, f"清单乙 {len(un)} 条都在文件里，且都写清了缺什么数据"


def r_byo_idea_declared(v: RunView):
    """进了自带想法模式，输入里就得真有 `--- idea:`。

    WorkBuddy 验收跑 r20260907_1938 的形状：用户只给了一句方向，模型自己认定
    「这算你带了题」，于是**跳过十个透镜、去重、可行性筛这一整套**，抓着那一句
    直接送审——陪审团一砍就一个不剩，那四个「怎么抢救」的选项才会长出来。

    而管这事的 `ideas-jury` 只问「送审没有、记透镜没有」：1 个候选、记了透镜、
    审过了，**全过**。判据只能是**独立于模型自述**的东西，所以看留痕里记的原始输入。

    看不出来就不判：老运行目录没有 `input` 留痕，不能因为「没记」判它违规。
    """
    ideas = v.json(ARTIFACTS["ideas"])
    if ideas is None:
        return NA, "没有 ideas.json"
    lst = ideas.get("ideas") or []
    if not lst or not all((i.get("lens") or "") == "user-idea" for i in lst):
        return NA, "模型自己出的题，这条不适用"
    raw = next((str(r.get("note") or "") for r in v.trace if r.get("step") == "input"), None)
    if raw is None:
        return NA, "留痕里没记原始输入，看不出用户带没带题——**看不出来不等于违规**"
    if "idea:" in raw:
        return PASS, "输入里确实带了 --- idea:"
    return FAIL, (
        "候选全是 user-idea，但原始输入里**没有 `--- idea:`**——"
        "这是模型自己认定「用户带了题」，把十个透镜、去重、可行性筛整套跳过了。"
        "只给方向时应当走出题流程")


def r_research_type_declared(v: RunView):
    """计划要写明研究类型——要不要审计、论文许不许下因果结论都靠它。

    看不出来就不判，两种「看不出来」都算：
    - 老运行目录的锚点在这一栏出现之前就冻结了（锚点里**没有** `research_type` 这一格）
    - 更老的运行目录：连锚点这个功能都还没有（没有冻结的锚点），但 `results.json` 在——
      分析是在研究类型这道轴线出现之前跑完的，同样看不出来，不该判违规

    有这一格却是空串，是新运行没写——那要判不过；没有冻结的锚点、也没跑过分析的新运行同理判不过，
    不能让「看不出来」的例外把「就是没写」也一起放过去。（用户授权换题会把老锚点重写成带这一格的
    新形状，之后没写类型的计划就从「不适用」变成「判不过」——这是有意的：换题之后就是新运行。）

    计划这一格写着合法值，也不等于当场判过，有三种情况：
    - 第一次有计划声明类型的那次跑才补上（用户 2026-09-14 决定 2A），补的这一刻就是
      「看过结果才定」——`oss/gates/research_type.py::declared_late` 认得出那一行
      门自己写的留痕，见到就判不过
    - 补是补了（`plan.json` 上写着），但从没再跑一次 `oss run` 让它真的写进锚点、锁上——
      锚点这一格还留着空（`declared_unlocked`）；不重跑就想让这一条规则判过，
      是比走 `oss run` 更省事的免检近路，一样判不过
    - 老运行目录（legacy 锚点、锚点出现之前就跑过分析）：计划上的类型只能是分析跑过之后
      补写的——锁不住（legacy 锚点 `refill_plan_sha` 永远不补，老目录压根没有锚点），也看不出
      是事先定的，**不给判过**，判不适用（重放验收 2026-09-14：以前判过，同一个补写还让识别
      审计免检）。外部导入的结果（`oss results import` 不冻锚点）是同一个形状，只是说法不同
    """
    p = v.plan
    if p is None:
        return NA, "没有 plan.json"
    rt = str(p.get("research_type") or "")
    a = v.anchor
    if rt in RESEARCH_TYPES:
        from oss.gates import research_type as rt_gate

        if rt_gate.declared_late(v.dir):
            return FAIL, "类型是看过结果之后才补上的（锚点冻结时计划没声明研究类型）——研究类型要在第一次 oss run 之前写明"
        if rt_gate.declared_unlocked(a, rt):
            return FAIL, ("类型是第一次 oss run 之后才写上的，还没锁定——"
                          "重跑一次 oss run（会记为「看过结果才补」，合规检查会一直判不过）")
        if a.get("frozen_at") and "research_type" not in a:
            return NA, ("老运行目录（锚点冻结时还没有研究类型这一栏）：研究类型是分析跑过之后补写的，"
                        "锁不住，也看不出是事先定的")
        if not a.get("frozen_at") and v.ran:
            if _external(v):
                return NA, "结果是外部导入的，没冻结过锚点：研究类型锁不住，也看不出是事先定的"
            return NA, ("老运行目录（锚点出现之前就跑过分析）：研究类型是分析跑过之后补写的，"
                        "锁不住，也看不出是事先定的")
        return PASS, f"研究类型：{rt}"
    if rt:
        return FAIL, f"research_type「{rt}」不在四类里（causal / descriptive / measurement / simulation）"
    if a.get("frozen_at"):
        if "research_type" not in a:
            return NA, "老运行目录（锚点冻结时还没有研究类型这一栏）——看不出来不等于违规"
    elif v.results is not None:
        return NA, "老运行目录（锚点出现之前就跑过分析）——看不出来不等于违规"
    return FAIL, "plan.json 没声明 research_type——要不要做识别审计、论文许不许下因果结论都靠它"


#: 「尚未解决」那一节的标题。认标题，**不认散文里的同一句话**——
#: r20260830_mode1 的讨论里有一句「Open questions remain.」接着聊了两个未来方向，
#: 宽松匹配会把它当成有交代，而那正是要抓的形态。
_OPEN_HEAD = re.compile(
    r"^#{1,4}\s*(?:[（(]?[一二三四五六七八九十\d]+[)）、.]?\s*)?"
    r"(?:Open questions|Unresolved|尚未解决|未解决|待解决)", re.I | re.M)
#: 逐条的记号：`- `、`1.`、`第一，`、`①`、`一、`
_OPEN_ITEM = re.compile(
    r"^\s*(?:[-*+]|\d+[.)]|第[一二三四五六七八九十]+[，、]|[①-⑩]|[一二三四五六七八九十][、.])", re.M)


def r_open_items_accounted(v: RunView):
    """复审收尾还剩的意见，要在论文里**逐条**交代，不是写一段「未来研究方向」。

    审稿只给两轮，所以第二轮那三位审稿人**新提的问题按设计就没有第三轮去处理**，
    直接带着交货。说明书给了出口（另起一节写清哪条、为什么不改），但没有门。

    六次跑的实测：五次里四次，5 到 16 条未决意见等于没交代——两次根本没这一节，
    两次有节但通篇是泛泛的展望，**一条具体的都没点**。只有中文那次真逐条写了。

    所以判据是「**逐条**」：它正好把「写了一段未来展望」挡在外面，而那是实际形态。
    """
    ls = v.json("loop_state.json")
    if not ls or not (ls.get("rounds") or []):
        return NA, "还没走审稿循环"
    tail = (ls.get("rounds") or [{}])[-1]
    n_open = sum(int(tail.get(k) or 0) for k in
                 ("design_open", "data_open", "text_open", "new_open"))
    if n_open == 0:
        return PASS, "收尾时没有未决意见"
    paper = v.paper
    if paper is None:
        return NA, "还没写论文"
    m = _OPEN_HEAD.search(paper)
    if not m:
        return FAIL, (
            f"收尾还剩 {n_open} 条未决意见，论文里**没有交代它们的那一节**——"
            f"另起一节「尚未解决的问题」，逐条写：哪条、为什么不改")
    rest = paper[m.end():]
    nxt = re.search(r"^#{1,4}\s", rest, re.M)
    sec = rest[:nxt.start()] if nxt else rest
    n_item = len(_OPEN_ITEM.findall(sec))
    if n_item == 0:
        return FAIL, (
            f"收尾还剩 {n_open} 条未决意见，那一节里**没有逐条**——"
            f"一段泛泛的「未来研究方向」不算交代。一条一段，说清是什么、为什么没解决")
    detail = f"未决 {n_open} 条，逐条写了 {n_item} 条"
    if n_item < n_open:
        detail += "（条数少于未决数，可能有合并——合并没问题，漏掉才是问题）"
    return PASS, detail


def r_power_before_run(v: RunView):
    if not v.trace:
        return NA, "没有留痕"
    pw, run = v.seqs("oss power"), v.run_seqs()
    if not run:
        return NA, ("结果是外部导入的，本链没跑过分析——这条查不了"
                    if _external(v) else "还没跑分析")
    if not pw:
        return FAIL, "没算功效就跑了分析"
    if min(pw) > min(run):
        return FAIL, f"功效（seq {min(pw)}）算在第一次分析（seq {min(run)}）之后——功效是事前的"
    return PASS, f"功效 seq {min(pw)} 在分析 seq {min(run)} 之前"


def r_power_fail_stop(v: RunView):
    pw = v.json(ARTIFACTS["power"])
    if pw is None:
        return NA, "没有 power.json"
    if pw.get("mode") == "evidence":
        # 测量类判「证据做不做得出来」，不出功效判词；做不出来的在 oss power 当场就停了（测量骨架设计稿第四节）
        return NA, "测量类判「证据做不做得出来」：不算功效，做不出来的在环节 4 当场停"
    if pw.get("mode") == "precision":
        # 只看计划上写的类型、不看锚点（从因果降为描述的，锚点里还留着 causal）；没写类型的老计划不判
        rtype = str((v.plan or {}).get("research_type") or "")
        if rtype and rtype != "descriptive":
            return FAIL, f"power.json 按精度算、计划是 {rtype}：重跑 oss power"
        return NA, "描述类按精度判：精度不足不停（处方 precision_low）"
    if pw.get("verdict") != "fail":
        return PASS, f"功效判 {pw.get('verdict')}"
    if v.paper is not None:
        return FAIL, "功效判 fail 却写了论文——数据回答不了这个问题就该停：换结局 / 样本 / 题目"
    return PASS, "功效判 fail，没有往下写"


def r_self_review_disclosed(v: RunView):
    """自审就得在论文里说出来。

    2026-09-07 起缺省不再拦「审稿人与执行者同家族」——门槛压在跑起来之前，
    新用户凑不齐两家模型就看不见这套东西好在哪，开源换不来这个成本。
    但**放松拒绝不等于放松披露**：同家族的模型倾向于认同自己写的东西，
    而「这个新不新」「因果表述站不站得住」正是最吃这一口的判断。
    读者有权知道这篇稿子的审稿是不是独立的。

    判据取判词文件里的 `cross_family`（`validation` 一栏是给人读的同一句话）。
    """
    revs = v.reviews()
    if not revs:
        return NA, "还没审稿"
    self_reviewed = [n for n, d in revs.items() if d.get("cross_family") is False]
    if not self_reviewed:
        return PASS, "审稿是跨模型的"
    if v.paper is None:
        return NA, "还没写论文"
    hit = any(k in v.paper for k in ("同一模型", "同家族", "自审", "不构成独立验证",
                                     "self-review", "not independent"))
    if hit:
        return PASS, f"{len(self_reviewed)} 份判词是自审，论文里写明了"
    return FAIL, (
        f"{len(self_reviewed)} 份判词是自审（审稿人与执行者同家族或判不出家族），"
        f"论文里没有交代——局限里要写明本文的审稿不构成独立验证")


def r_state_not_hand_written(v: RunView):
    """状态文件归工具箱，模型只写 ideas.json / plan.json / paper.md。

    判据是**工具箱写不出的形状**：`state._anchor()` 六格一起写、`frozen_at` 必是时间戳，
    没冻过的运行 anchor 就是空字典。有方向或题号却没有冻结时间，只能是有人手写过。

    实例：WorkBuddy 验收跑 r20260907_1938 就是这个形状，同时 ideas.json 的 direction
    被写成一串列名而不是研究问题——两处的「方向」对不上，而当时没有任何一道门看得见。
    """
    st = v.json("run_state.json")
    if st is None:
        return NA, "没有 run_state.json"
    a = st.get("anchor") or {}
    if not a.get("frozen_at") and (a.get("direction") or a.get("idea_id")):
        return FAIL, ("run_state.json 的锚点是半冻结的（有方向或题号却没有冻结时间）——"
                      "工具箱写不出这个形状，只能是被手工改过")
    return PASS, "状态文件的锚点形状正常"


def r_power_effect_source(v: RunView):
    """效应量得有出处。

    **整个功效判词建立在这一个数上**，而链上别的数都能追回结果文件，唯独它是外来的。
    验收跑 r20260907_1938 与 r20260907_edufert_zh 都没传 `--effect`，
    拿命令行缺省的 0.2 算出「功效 0.83 / pass」，论文里却写「按文献报告的效应量」——
    那句话没有任何东西对得上。同一份 power.json 里，影响小得多的 ρx 反而一直记着来源。
    """
    pw = v.json(ARTIFACTS["power"])
    if pw is None:
        return NA, "没有 power.json"
    if pw.get("mode") == "evidence":
        # 测量类那一路不算效应量，也就没有效应量的出处可核（测量骨架设计稿第四节）
        return NA, "测量类判「证据做不做得出来」：不算效应量，没有效应量出处可核"
    src = (pw.get("effect_size_source") or "").strip()
    if src:
        return PASS, f"效应量 {pw.get('effect_size_raw')} 有出处：{src[:60]}"
    return FAIL, (
        f"效应量 {pw.get('effect_size_raw')} 没有出处——判词整个建立在它上面。"
        "补：oss power --effect <值> --effect-source \"<哪条语料记录、哪一页、原话>\"")


def r_staggered_estimator(v: RunView):
    """分批实施要用 `did_cs`。**判据是诊断里的批次数，不是计划里写的估计量名字。**

    原先按名字判：不是 `did` / `event_study` 就直接 n/a。于是
    **`ols_fe` + 一个 post 哑变量 + 单位与时间双向固定效应**这条路整道绕过——
    它算的跟 `did` 一模一样，坑也一模一样（考卷 A 实测主系数 2.129、
    真实平均处理效应 3.56，偏了四成）。

    计划那一侧早就不认名字了（`_twfe_staggered_cohorts` 专判这条路，
    批次数也写进了 `diagnostics["实施批次"]`），**只有合规规则没跟上**——
    跟 `ols_fe` 绕过分批门是同一个形状，换个写法就全放行。
    """
    p = v.plan
    if not p:
        return NA, "没有 plan.json"
    est = p.get("estimator")
    if est == "did_cs":
        return PASS, "estimator = did_cs"
    n = _cohorts(v)                       # 来自诊断，`did` 与 `ols_fe` 两条路都写
    if n > 1:
        how = f"{est}（双向固定效应）" if est == "ols_fe" else f"{est}（TWFE）"
        return FAIL, (f"处理分 {n} 批却用 {how}——说明书：分批实施主规格用 did_cs。"
                      f"（判据是诊断里的批次数，不是估计量的名字：`ols_fe` 加一个 post 哑变量"
                      f"与双向固定效应，算的跟 did 一样，坑也一样）")
    if n == 1:
        return PASS, "单批"
    # 批次数算不出来：面板才谈得上分批。**判不了就不判**，不要顺着判通过
    if est in ("did", "event_study") or p.get("treat_time"):
        return PASS, f"estimator = {est}，诊断里没有批次数（未分批或判不出）"
    return NA, f"estimator = {est}，不是面板设计，谈不上分批"


def r_route_after_run(v: RunView):
    if not v.trace:
        return NA, "没有留痕"
    run, route = v.run_seqs(), v.seqs("oss route")
    if not run:
        return NA, ("结果是外部导入的，本链没跑过分析——这条查不了"
                    if _external(v) else "还没跑分析")
    if not route:
        return FAIL, "跑了分析没开方（留痕里没有 oss route）——诊断出病不治等于没诊断"
    if max(route) < max(run):
        return FAIL, f"最后一次分析（seq {max(run)}）之后没有再 oss route 复查（最后一次 seq {max(route)}）"
    return PASS, f"最后一次 oss route seq {max(route)} 在最后一次分析之后"


def r_route_disposed(v: RunView):
    rt = v.json(ARTIFACTS["route"])
    if rt is None:
        return NA, "没有 route.json"
    pend = [i["id"] for i in rt.get("items", []) if i.get("priority") == "must" and i.get("status") == "pending"]
    if pend:
        return FAIL, f"must 级处方没处置：{'、'.join(pend)}"
    should = [i["id"] for i in rt.get("items", []) if i.get("status") == "pending"]
    if should:
        return FAIL, f"处方没处置（应用 / 驳回 / 写进局限三选一）：{'、'.join(should[:8])}"
    return PASS, f"{len(rt.get('items', []))} 条处方都有处置"


def r_robustness_locked(v: RunView):
    p = v.plan
    if not p:
        return NA, "没有 plan.json"
    rob = p.get("robustness") or []
    if not rob:
        return FAIL, "计划里没有稳健性清单——事前锁定至少 3 项"
    # **整条写成一句散文的，本来就是这条规则要拦的东西**，而原先直接 `r.get(...)`
    # 会在它身上抛 AttributeError——**一条规则崩掉带垮整份合规报告**，
    # 于是「写得最不规范的那份计划」反而一条意见都收不到。
    # 把它归进「跑不了」那一档：判据不变，只是先认出这种形状。
    loose = [r for r in rob if not isinstance(r, dict)]
    prose = [str(r)[:30] for r in loose]
    prose += [r.get("label") for r in rob
              if isinstance(r, dict) and not r.get("executable", True)]
    if prose:
        return FAIL, f"稳健性有散文式条目跑不了：{'、'.join(map(str, prose))}"
    # 上限从 5 放到 12。**这条原来在惩罚好的做法。**
    #
    # 三次互不相通的自由跑各自锁了 8、9、10 项，全被判不合格；作为参照的那篇
    # 已发表论文锁了 13 项。更要紧的是**那 9 项正是价值所在**：其中一次拿到了
    # 显著的主结果，靠自己的稳健性套件发现「加上教育控制就不显著、只留受雇者符号还翻」，
    # 于是在摘要里放弃了那个发现。**清单短一点，那次发现就不会发生。**
    #
    # 这条要挡的从来不是「项数多」，是**堆一墙没人读的检验**。所以上限留着，
    # 但要放到真实做法之上；下限 3 不动（少于 3 项是薄）。
    # **上限只管事前锁定的那些。**跑过分析之后按审稿意见补做的诊断不算——
    # 原先算，于是照审稿人的「改完标准」补做反而被判不合格（因果那篇补了 23 项、
    # 测量那篇补了 13 项，两篇就此都卡在这一条）。**罚照要求补做，方向是反的。**
    # 「哪些是事前锁定的」挂在留痕上（第一次成功跑分析时清单里就有的），不挂在声明字段上。
    from oss.core.loop import prelocked_robustness

    pre = prelocked_robustness(v.dir)
    locked = rob if pre is None else [r for r in rob
                                      if isinstance(r, dict) and r.get("label") in pre]
    extra = len(rob) - len(locked)
    if len(locked) > 12:
        if pre is None:
            # 留痕里没记项名（这次分析之前的运行目录）——**分不出哪些是事后补的，
            # 那就照总数说，别谎称这 31 项都是事前锁定的。**
            return FAIL, (f"稳健性 {len(rob)} 项——超过 12 项就成了没人读的一墙检验。"
                          f"（这个运行目录的留痕里没有项名，分不出哪些是跑过分析之后按审稿意见"
                          f"补做的；那些本不该算进上限。新的运行目录会自动记下。）")
        tail = f"（另有 {extra} 项是跑过分析之后才加的，不算进上限）" if extra else ""
        return FAIL, (f"事前锁定的稳健性清单 {len(locked)} 项{tail}——"
                      f"超过 12 项就成了没人读的一墙检验；挑出真正会威胁结论的那些")
    if extra:
        return PASS, f"事前锁定 {len(locked)} 项，另有 {extra} 项是跑过分析之后补做的；都可执行"
    return PASS, f"{len(rob)} 项，都可执行"


def r_proxy_gap(v: RunView):
    """三栏（`construct_def` / `measures` / `gap`）填没填。**两条触发条件，不是两套机制**：

    ① 绑定判成代理 / 构造变量——老口径，一个字不动
    ② **测量类研究的主构念**——绑得再准也要填（测量骨架设计稿 5.3，判据见
       `binding.measurement_gap_targets`）
    """
    b = v.json(ARTIFACTS["binding"])
    if b is None:
        return NA, "没有 binding.json"
    rows = [x for x in b.get("bindings", []) if isinstance(x, dict)]
    unfilled = [x for x in rows if not (x.get("gap") and x.get("measures"))]
    bad = [x.get("construct_name") for x in unfilled if x.get("status") in ("proxy", "derived")]
    if bad:
        return FAIL, f"代理 / 构造变量没填代理差距三栏：{'、'.join(map(str, bad))}"
    targets = binding_mod.measurement_gap_targets((v.plan or {}).get("measurement"))
    core = [x.get("construct_name") for x in unfilled
            if targets & {str(x.get("construct_name") or ""), str(x.get("column") or "")}]
    if core:
        return FAIL, (f"测量类的主构念没填代理差距三栏：{'、'.join(map(str, core))}"
                      f"——测量研究里这三栏绑得再准也要填")
    return PASS, "代理差距都填了（或没有代理变量）"


def r_identification_audit(v: RunView):
    if not v.is_causal():
        return NA, "研究类型不是因果" if v.declared_types() else "老计划没写研究类型：估计量不是因果设计"
    if v.json("review_plan.json") is None and not any(n.startswith(("review_plan", "review_identification")) for n in v.reviews()):
        return FAIL, "因果研究没经过识别审计（没有 review_plan.json）"
    return PASS, "审计过"


def r_audit_disposed(v: RunView):
    rt = v.json(ARTIFACTS["route"])
    if rt is None:
        return NA, "没有 route.json"
    aud = [i for i in rt.get("items", []) if str(i.get("id", "")).startswith("audit_")]
    if not aud:
        return NA, "没有审计意见"
    pend = [i["id"] for i in aud if i.get("status") == "pending"]
    if pend:
        return FAIL, f"审计意见没处置：{'、'.join(pend)}"
    return PASS, f"{len(aud)} 条审计意见都有处置"


def r_causal_estimand(v: RunView):
    if not v.is_causal():
        return NA, "研究类型不是因果"
    p = v.plan or {}
    if not p.get("research_type"):
        return NA, "老计划没写研究类型：估计量三步按老规矩不查"
    e = p.get("estimand") or {}
    miss = [k for k in ("unit_quantity", "target_population", "link") if not str(e.get(k, "")).strip()]
    if not [a for a in (e.get("assumptions") or []) if str(a).strip()]:
        miss.append("assumptions")
    if miss:
        return FAIL, f"估计量三步没写全：{'、'.join(miss)}"
    roles = p.get("control_roles") or {}
    unlabeled = [c for c in (p.get("controls") or []) if c not in roles]
    if unlabeled:
        return FAIL, f"控制变量没标角色：{'、'.join(unlabeled)}"
    bad = [c for c, r in roles.items() if r in ("mediator", "collider")]
    if bad:
        return FAIL, f"控制了中介或碰撞变量：{'、'.join(bad)}"
    return PASS, "估计量三步写全了，控制变量都标了角色"


_CAUSAL_DIAG_KEYS = ("AR 区间", "有效 F", "预检功效", "breakdown 值", "稳健值", "Oster δ", "E 值")


def r_causal_diagnostics_cited(v: RunView):
    if not v.is_causal():
        return NA, "研究类型不是因果"
    if v.paper is None:
        return NA, "还没写论文"
    d = (v.results or {}).get("diagnostics") or {}
    have = [k for k in _CAUSAL_DIAG_KEYS if k in d]
    if not have:
        return NA, "结果里没有因果骨架的诊断（老结果文件）"
    if any(f"{{{{diag:{k}}}}}" in v.paper for k in have):
        return PASS, "正文引了因果诊断的数"
    return FAIL, f"正文一个因果诊断的数都没引（可引：{'、'.join(have)}）"


def r_survey_design_declared(v: RunView):
    if not ((v.json(ARTIFACTS["profile"]) or {}).get("survey") or {}).get("likely"):
        return NA, "画像没有调查信号"
    p = v.plan or {}
    if not p.get("research_type"):
        return NA, "老计划没写研究类型：抽样设计按老规矩不查"
    s = p.get("survey") or {}
    if str(s.get("not_survey_reason") or "").strip():
        return PASS, "写了不按调查处理的理由"
    if str(s.get("design_source") or "").strip() and s.get("weight_use"):
        return PASS, "声明了抽样设计"
    return FAIL, "画像有调查信号，计划没声明抽样设计（plan.survey），也没写不按调查处理的理由"


def r_survey_descriptive_weighted(v: RunView):
    if not ((v.json(ARTIFACTS["profile"]) or {}).get("survey") or {}).get("likely"):
        return NA, "画像没有调查信号"
    p = v.plan or {}
    s = p.get("survey") or {}
    if str(s.get("not_survey_reason") or "").strip():
        return NA, "写了不按调查处理的理由"
    if not (p.get("research_type") == "descriptive" or s.get("weight_use") == "descriptive"):
        return NA, "不是描述性估计"
    if p.get("weights"):
        return PASS, f"描述性估计按 {p['weights']} 加权"
    return FAIL, "描述性估计没加权——Deaton 1997：描述性估计必须加权、标准误按设计校正"


def r_descriptive_estimand(v: RunView):
    p = v.plan or {}
    if p.get("research_type") != "descriptive":
        return NA, "研究类型不是描述"
    e = p.get("estimand") or {}
    miss = [k for k in ("unit_quantity", "target_population") if not str(e.get(k) or "").strip()]
    if miss:
        return FAIL, f"描述类计划没写清描述的是什么：缺 {'、'.join(miss)}"
    kind = (p.get("description") or {}).get("kind")
    if p.get("estimator") == "describe" and kind not in ("level", "distribution", "gap", "trend"):
        return FAIL, "估计量是 describe，description.kind 没写或写成了 association"
    return PASS, "描述量与目标总体都写了"


def r_descriptive_magnitude(v: RunView):
    p = v.plan or {}
    if p.get("research_type") != "descriptive" or p.get("estimator") != "describe":
        return NA, "不是 describe 的描述研究"
    if v.paper is None:
        return NA, "还没写论文"
    if "{{table:groups}}" in v.paper:
        return PASS, "论文引了分组描述表"
    return FAIL, "描述类论文没引分组描述表 {{table:groups}}——报大小与区间，不只显著性（Firebaugh）"


def _paper_checks(v: RunView):
    from oss.core import manuscript as ms
    paper = v.paper
    if paper is None:
        return None
    return ms, paper


def r_tables(v: RunView):
    got = _paper_checks(v)
    if got is None:
        return NA, "还没写论文"
    ms, paper = got
    missing = [t for t in ("main", "descriptives", "robustness") if f"{{{{table:{t}}}}}" not in paper]
    # 描述论文（结果里有分组表）引了 {{table:groups}} 就算有主表——跟导出、`manuscript.check` 同一口径
    grouped = bool((v.results or {}).get("groups"))
    if "main" in missing and grouped:
        if "{{table:groups}}" not in paper:
            return FAIL, "没有分组描述表 {{table:groups}}——" + ms.GROUPS_MAIN_TABLE
        missing.remove("main")
    if "main" in missing:
        return FAIL, "没有主结果表 {{table:main}}——量化论文没有回归表是硬伤"
    if missing:
        return FAIL, f"缺表：{'、'.join('{{table:' + t + '}}' for t in missing)}"
    return PASS, "三张表都有" + ("（主表是分组描述表）" if grouped and "{{table:main}}" not in paper else "")


def r_figures_captioned(v: RunView):
    got = _paper_checks(v)
    if got is None:
        return NA, "还没写论文"
    ms, paper = got
    n_fig = len(re.findall(r"\{\{fig:", paper))
    if n_fig < 2:
        return FAIL, f"只嵌了 {n_fig} 张图——至少两张"
    probs = ms._caption_problems(paper)
    if probs:
        return FAIL, probs[0]
    return PASS, f"{n_fig} 张图都有图注"


def r_no_process_language(v: RunView):
    got = _paper_checks(v)
    if got is None:
        return NA, "还没写论文"
    ms, paper = got
    hits = ms.process_language(paper)
    if hits:
        return FAIL, f"{len(hits)} 处研究过程的痕迹，如：{hits[0][:90]}"
    return PASS, "正文没有过程痕迹"


def r_causal_wording(v: RunView):
    """研究类型不是因果的论文，正文没有明确的因果说法（设计稿 4.4 硬拦档）。重跑一遍扫描，不信留痕。
    判哪种类型、算不算降级，跟 `oss paper check` / `export` 用同一个 `research_type.wording_scope`。

    **`oss eval` 只读不改**：`wording_scope` 顺着 `research_type.check()` 一路会走到
    `state.load()`——那个函数读到读不出来的 `run_state.json` 会**就地把它改名**成
    `run_state.corrupt.json`（那是锚点门自己的报警文件名）。判一条合规规则不能顺手
    把运行目录写脏，所以文件在但读不出来时直接判不过，不替它走这条链路。
    """
    got = _paper_checks(v)
    if got is None:
        return NA, "还没写论文"
    if (v.dir / "run_state.json").exists() and v.json("run_state.json") is None:
        return FAIL, "run_state.json 读不出来——这条判不了，先修状态文件（oss eval 不动它）"
    from oss.core import causal_wording as cw
    from oss.gates import research_type as rt_gate
    wtype, _ = rt_gate.wording_scope(v.dir)
    if not wtype:
        return NA, "因果研究或没声明类型——这条只管非因果类型"
    hits = cw.scan(got[1], wtype)
    hard = [h for h in hits if h.tier == "hard"]
    if hard:
        return FAIL, f"{len(hard)} 处明确的因果说法，如第 {hard[0].line} 行「{hard[0].word}」"
    return PASS, f"没有明确的因果说法（模糊的 {len(hits)} 处交审稿人判）"


def r_abstract_numbers(v: RunView):
    got = _paper_checks(v)
    if got is None:
        return NA, "还没写论文"
    ms, paper = got
    return (FAIL, "摘要没有具体数字") if ms._abstract_numbers(paper) else (PASS, "摘要有数字")


def r_novelty(v: RunView):
    got = _paper_checks(v)
    if got is None:
        return NA, "还没写论文"
    ms, paper = got
    idx = v.json("reading/index.json")
    ft = (sum(1 for i in idx if i.get("level") == "fulltext"), len(idx)) if idx is not None else None
    probs, _ = ms._novelty_problems(paper, ft)
    if probs:
        return FAIL, probs[0][:160]
    return PASS, "没有站不住的新颖性主张"


def r_review_structure(v: RunView):
    revs = v.reviews()
    if not revs:
        return NA if v.paper is None else FAIL, "论文没经过任何审稿"
    refs = [n for n in revs if n.startswith("review_paper") and "_r2" not in n and "_r3" not in n]
    if len(refs) < 2:
        return FAIL, f"第 1 轮只有 {len(refs)} 位审稿人——至少两位互盲，说明书要求三位（识别 / 替代解释 / 局限）"
    alt = any("替代解释" in n or "alternatives" in n.lower() for n in refs)
    if not alt:
        return FAIL, f"第 1 轮 {len(refs)} 位审稿人里没有替代解释审稿人（oss review --alternatives）"
    return PASS, f"第 1 轮 {len(refs)} 位审稿人，含替代解释"


def r_desk_before_referees(v: RunView):
    revs = v.reviews()
    if not any(n.startswith("review_paper") for n in revs):
        return NA, "还没审稿"
    if "review_desk.json" not in revs:
        return FAIL, "没做桌审（oss review --desk）就送了审稿人"
    return PASS, "桌审过"


def r_editor(v: RunView):
    revs = v.reviews()
    refs = [n for n in revs if n.startswith("review_paper")]
    if len(refs) < 2:
        return NA, "审稿人不足两位"
    if not any(n.startswith("review_editor") for n in revs):
        return FAIL, "几位审稿人审完没做编辑综合（oss review --editor）"
    return PASS, "有编辑综合"


def r_fail_rereviewed(v: RunView):
    revs = v.reviews()
    r1 = [d for n, d in revs.items() if n.startswith("review_paper") and int(d.get("round") or 1) == 1]
    if not r1:
        return NA, "还没审稿"
    if any(d.get("verdict") in ("FAIL", "ERROR") for d in r1):
        r2 = [n for n, d in revs.items() if n.startswith("review_paper") and int(d.get("round") or 1) >= 2]
        if not r2:
            return FAIL, "第 1 轮有 FAIL / ERROR，没有复审就结束了"
        return PASS, f"第 1 轮有 FAIL，复审了 {len(r2)} 份"
    return PASS, "第 1 轮没有 FAIL"


def r_export_gates(v: RunView):
    if not v.trace:
        return NA, "没有留痕"
    exp = v.seqs("oss paper export")
    if not exp:
        return NA, "没有导出"
    rt = v.json(ARTIFACTS["route"])
    if rt is None:
        return FAIL, "导出了论文，却从没开过方（没有 route.json）"
    pend = [i["id"] for i in rt.get("items", []) if i.get("priority") == "must" and i.get("status") == "pending"]
    if pend:
        return FAIL, f"导出时还有 must 级处方没处置：{'、'.join(pend)}"
    return PASS, "导出前处方清了"


RULES: list[Rule] = [
    Rule("profile-first", "social-research", "must", "环节 1：先画像，再写计划", r_profile_first),
    Rule("lit-camps", "social-research", "should", "环节 2：检索式按阵营写，至少两派", r_lit_camps),
    Rule("lit-fetch", "social-research", "must", "环节 2 之二：精读并写读书卡", r_lit_fetch),
    Rule("ideas-jury", "social-idea", "must", "步骤 6：候选 idea 交给跨模型陪审团；每个 idea 记透镜", r_ideas_jury),
    Rule("ideas-candidates-monotone", "social-idea", "must",
         "不变量二：④ 不得凭空造题，被砍的候选不得删出文件", r_ideas_candidates_monotone),
    Rule("ideas-two-lists", "social-idea", "must",
         "不变量三：数据不可及的候选分流到清单乙，不降权、写清缺什么", r_ideas_two_lists),
    Rule("power-before-run", "social-research", "must", "环节 4：功效在分析之前算", r_power_before_run),
    Rule("power-fail-stop", "social-research", "must", "环节 4：功效 FAIL 即停，不写论文", r_power_fail_stop),
    Rule("power-effect-source", "social-research", "must",
         "环节 4：功效的效应量要有出处，不许用缺省值冒充文献值", r_power_effect_source),
    Rule("state-not-hand-written", "social-research", "must",
         "铁律三：状态文件归工具箱，模型只写 ideas/plan/paper", r_state_not_hand_written),
    Rule("self-review-disclosed", "social-review", "must",
         "自审（审稿人与执行者同家族）要写进论文局限", r_self_review_disclosed),
    Rule("byo-idea-declared", "social-idea", "must",
         "步骤 0：只有输入里真带了 idea: 才走自带想法模式", r_byo_idea_declared),
    Rule("research-type-declared", "social-research", "must",
         "环节 5：计划写明研究类型（因果 / 描述 / 测量 / 仿真）", r_research_type_declared),
    Rule("open-items-accounted", "social-review", "must",
         "收尾：还剩的审稿意见要在论文里逐条交代", r_open_items_accounted),
    Rule("staggered-did-cs", "social-research", "must", "环节 5：分批实施主规格用 did_cs，不用 TWFE", r_staggered_estimator),
    Rule("robustness-locked", "social-research", "should", "环节 5：稳健性事前锁定、可执行", r_robustness_locked),
    Rule("proxy-gap", "social-research", "should", "环节 4：代理变量填代理差距三栏", r_proxy_gap),
    Rule("route-after-run", "social-research", "must", "环节 6 之二：跑完分析先开方；改完重跑再复查", r_route_after_run),
    Rule("route-disposed", "social-research", "must", "环节 6 之二：每条处方都有处置", r_route_disposed),
    Rule("identification-audit", "social-identification", "must", "因果研究必须过识别审计", r_identification_audit),
    Rule("audit-disposed", "social-identification", "must", "审计每条意见都有处置", r_audit_disposed),
    Rule("causal-estimand", "social-research", "must", "环节 5：因果计划写了估计量三步、控制变量标了角色", r_causal_estimand),
    Rule("causal-diagnostics-cited", "social-research", "should",
         "环节 7：因果论文引了因果诊断的数（有效 F / AR、预检功效 / breakdown、稳健值 / Oster δ / E 值）",
         r_causal_diagnostics_cited),
    Rule("survey-design-declared", "social-research", "must",
         "环节 5：画像有调查信号的，计划声明了抽样设计或写了不按调查处理的理由", r_survey_design_declared),
    Rule("survey-descriptive-weighted", "social-research", "must",
         "环节 5：描述性估计加权了（调查层设计稿 4.1）", r_survey_descriptive_weighted),
    Rule("descriptive-estimand", "social-research", "must",
         "环节 5：描述类计划写了描述量与目标总体（describe 还要写 description.kind）", r_descriptive_estimand),
    Rule("descriptive-magnitude", "social-paper", "should",
         "环节 7：描述类论文引了分组描述表，报大小与区间", r_descriptive_magnitude),
    Rule("tables", "social-paper", "must", "三张表：描述统计、主结果、稳健性", r_tables),
    Rule("figures-captioned", "social-paper", "must", "至少两张图，图注在下一行", r_figures_captioned),
    Rule("no-process-language", "social-paper", "must", "铁律二：研究过程不进正文", r_no_process_language),
    Rule("causal-wording", "social-paper", "must", "研究类型不是因果的论文不下因果结论", r_causal_wording),
    Rule("abstract-numbers", "social-paper", "should", "摘要要有具体数字", r_abstract_numbers),
    Rule("novelty-gate", "social-paper", "must", "没读全文不许说首次", r_novelty),
    Rule("desk-before-referees", "social-review", "should", "0：先桌审", r_desk_before_referees),
    Rule("three-referees", "social-review", "must", "1：三位互盲审稿人，含替代解释", r_review_structure),
    Rule("editor-synthesis", "social-review", "should", "2：编辑综合", r_editor),
    Rule("fail-rereviewed", "social-review", "must", "FAIL 必须复审一次", r_fail_rereviewed),
    Rule("export-gates", "social-research", "must", "导出前 must 级处方清零", r_export_gates),
]


def evaluate(rundir: Path) -> Report:
    v = RunView(rundir)
    rep = Report()
    for r in RULES:
        try:
            status, detail = r.check(v)
        except Exception as exc:  # noqa: BLE001 —— 规则自己崩了要说出来，不能当通过
            status, detail = FAIL, f"规则出错：{type(exc).__name__}: {exc}"
        rep.items.append(Item(r.id, r.skill, r.priority, r.what, status, detail))
    return rep
