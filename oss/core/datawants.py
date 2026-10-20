"""缺什么数据：把散在各候选字段里的话，汇总成一张能行动的表。

事故形状不是崩，是**浪费**。`ideas.json` 的每个候选都有一栏 `needs_extra_data`，
历次跑里它写得相当具体——「需申请 GSS 敏感数据版本（NORC 数据飞地）」
「TeleGeography 海底光缆图 / Hjort & Poulsen 2019 复制包」「WDI SE.COM.DURS」。
**然后整条链再没有一步用它。**用户要看，得自己打开文件翻十个候选的字段。
2026-09-07 重新查「GSS 有没有出生州」，查完才发现**八月份的运行目录里早写着同一句**。

这张表只做两件事：**合并同类项**（同一份数据被几个候选要，就值几分）、
**标可信度**。三档的差别是这里唯一真正重要的东西：

1. **目录里有** —— 一条命令就拿到，不用想
2. **精读的文献用过** —— 存在性有出处，而且本领域真在用。**这一档最值钱**
3. **只有模型说** —— 可能名字不对、甚至根本不存在。去找之前先自己搜一下

第三档必须**明确标出来**。模型凭记忆写数据集名跟凭记忆写文献一样，
听着都合理；按着去找，可能白折腾好几周才发现没这东西。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 「不缺」的各种说法。写了这些的候选不进清单。
_NONE = re.compile(r"^\s*(无|没有|不需要|空|none|n/?a|—|-)\b|^\s*(无|空)[。．.]?\s*$",
                   re.IGNORECASE)

#: 从一句话里挑出「像数据集名字」的片段：连续的大写缩写、拼音/英文专名、括号里的缩写。
#: 只用来做匹配，不用来展示——**展示一律用原话**，转述会把出处弄丢。
_NAME = re.compile(r"[A-Z][A-Za-z0-9\-]{2,}(?:\s+[A-Z][A-Za-z0-9\-]+){0,3}")


@dataclass
class Want:
    """一份缺的数据。`what` 是**候选里的原话**，不转述。"""

    what: str
    n_ideas: int
    idea_ids: list[str] = field(default_factory=list)
    tier: int = 3
    evidence: str = ""

    def line(self) -> str:
        """一行表。**一、二档带凭据**（那是具体的、每条都不一样的信息）；
        三、四档的说明是同一句样板话，逐行重复只会把表淹掉——放脚注里说一次。"""
        mark = {1: "① 目录里有", 2: "② 文献用过", 3: "③ 未核实", 4: "③ 没点名"}[self.tier]
        what = self.what if len(self.what) <= 58 else self.what[:57] + "…"
        head = f"{what}｜解锁 {self.n_ideas} 个候选｜{mark}"
        return f"{head}｜{self.evidence}" if self.tier in (1, 2) else head


def _names(text: str) -> set[str]:
    return {m.group(0).strip().lower() for m in _NAME.finditer(text or "")}


def _key(text: str) -> str:
    """合并同类项用的钥匙：优先用识别出的名字，认不出就用整句话的前 40 字。

    宁可**合不拢**也不要**乱合并**——把两份不同的数据并成一行，用户会照着去找错的东西。
    """
    names = sorted(_names(text))
    return names[0] if names else re.sub(r"\s+", "", (text or ""))[:40]


def collect_wants(ideas: dict, cards: dict, pack_names: list[str],
                  in_hand: set[str] | None = None) -> list[Want]:
    """汇总成表，按「解锁几个候选」降序、同分时**能立刻拿到的排前面**。

    排序的理由：用户的时间比清单的完整性值钱。两份数据都只解锁一个候选时，
    先给他那份一条命令就能拿到的。
    """
    # **手上已经有的数据包，永远不是解法。**这一轮用的就是 CGSS，那么无论候选
    # 怎么描述缺口，「`oss data get cgss` 直接拿」都是废话——他缺的正好是 CGSS 没有的东西。
    # 这是结构判据，不靠措辞，比下面那条 `_named_as_lacking` 的语言启发式牢靠。
    held = {p.lower() for p in (in_hand or set())}
    packs = [p.lower() for p in (pack_names or []) if p.lower() not in held]
    card_data = [(c.get("paper_id") or "?", str(c.get("data") or ""))
                 for c in (cards or {}).get("cards", []) if c.get("data")]

    grouped: dict[str, Want] = {}
    for idea in (ideas or {}).get("ideas", []):
        raw = str(idea.get("needs_extra_data") or "").strip()
        if not raw or _NONE.match(raw):
            continue
        k = _key(raw)
        w = grouped.get(k)
        if w is None:
            grouped[k] = w = Want(what=raw, n_ideas=0)
        w.n_ideas += 1
        w.idea_ids.append(str(idea.get("idea_id") or "?"))
        # 原话取最短的那条：几个候选说同一份数据时，短的通常最干净
        if len(raw) < len(w.what):
            w.what = raw

    for w in grouped.values():
        _rate(w, packs, card_data)
    return sorted(grouped.values(), key=lambda w: (-w.n_ideas, w.tier, w.what))


def _pack_in(pack: str, text: str) -> bool:
    """数据包名字在不在这句话里。**按词匹配，不许裸子串。**

    裸子串会把 **CGSS 匹配成 GSS**——`"gss" in "cgss2021"` 为真。
    三次自由跑同时撞上：候选说「CGSS2021 独有的主观加班阈值」，
    工具回一句「数据包 gss——`oss data get gss` 直接拿」，
    而 GSS 是美国的综合社会调查，补不了中国 CGSS 的题。

    **三个窗口全都自己识破了**（「我不会拿它顶替」），所以没造成错误分析；
    但一条明显错的建议摆在那儿，代价是使用者学会忽略整张缺数据清单。
    跟 `profiler._name_hits` 挡的 `"id" in "resident_income"` 是同一类错，
    项目里早有先例。
    """
    # 右边界只挡字母：`CGSS2021` 是 cgss 加轮次年份，该认；`cgss` 里的 `gss` 不该认。
    return re.search(rf"(?<![0-9A-Za-z]){re.escape(pack)}(?![A-Za-z])", text) is not None


#: 「这套数据不够用」的说法。挨着数据包名字出现时，它被点名是**问题**不是解法。
_LACKING = re.compile(r"只有|只在|仅有|仅在|没有|缺|不可得|无法|拿不到|未采集|未问|不含|不区分|不足")


def _named_as_lacking(pack: str, text: str) -> bool:
    """这句话里的数据包，是被当成**解法**还是被当成**不够用的那个东西**？

    「缺什么数据」这一栏里，用户常常先点名手上有的那套、再说它缺什么：

        没有中国的时间日志数据可对照——**CGSS 只有**自报工时
        **CGSS 2006 与 2008 两轮没有**可比的单位所有制变量

    照名字匹配的话，两条都会得到「数据包 cgss——`oss data get cgss` 直接拿」。
    **他们缺的正好不是它。**这比 CGSS→GSS 那个错更隐蔽：名字对上了，
    对象反了，而建议读起来完全通顺。

    判据：包名附近（前后各 20 字）出现「只有/没有/缺/不含……」就不算解法。
    `GSS 没有出生州` 同样被挡下——那也是对的，缺的东西 GSS 里没有。
    """
    for m in re.finditer(rf"(?<![0-9A-Za-z]){re.escape(pack)}(?![A-Za-z])", text):
        if _LACKING.search(text[max(0, m.start() - 20):m.end() + 20]):
            return True
    return False


def _rate(w: Want, packs: list[str], card_data: list[tuple[str, str]]) -> None:
    """定档。**一档二档都要给出凭据**，三档要说清「没有凭据」这件事本身。"""
    low = w.what.lower()
    hit = next((p for p in packs if p and _pack_in(p, low) and not _named_as_lacking(p, low)), "")
    if hit:
        w.tier, w.evidence = 1, f"数据包 {hit}——`oss data get {hit}` 直接拿"
        return

    names = _names(w.what)
    for pid, data in card_data:
        dl = data.lower()
        if any(n in dl for n in names if len(n) >= 3):
            shown = next(n for n in names if len(n) >= 3 and n in dl)
            w.tier = 2
            w.evidence = f"精读的 {pid} 用过（卡上写「{data[:40]}」，命中「{shown}」）"
            return

    if not any(len(n) >= 3 for n in names):
        # **这一档跟「未核实」不是一回事。**没点名数据集，只描述了缺什么——
        # 没有东西可核，问题是「哪套数据有这个」还没人回答。
        # 混在一起会让「名字可能不存在」这句警告贴到一段本来就没提名字的话上。
        w.tier = 4
        w.evidence = "**没点名具体数据集**——只说了缺什么，还得自己找哪套数据有"
        return
    w.tier = 3
    w.evidence = ("**未核实**——语料里没有文献用过它，目录里也没有。"
                  "名字可能不对甚至根本不存在，去找之前先自己搜一下")
