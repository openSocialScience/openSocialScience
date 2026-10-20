"""idea 重构的说明书硬话必须在。**句子没了，程序那边的门就没人照着走。**

跟 `tests/test_stage1_skill_rules.py`、`tests/test_byo_idea_skill_rules.py` 同一套办法，
也同一个理由：这些句子不是文档，是**判据**。

- 「只许重排，不许生成」没了，⑤ 看见真数据就会顺手改题，不变量二破在最后一段；
- 「做不了不扣分」没了，重排会以看起来很客观的方式，把研究降级成「这份数据恰好允许的那些题」；
- 「读 mde，不看 verdict」没了，预检就会退回拿命令行缺省的 0.2 算功效——
  那正是 `effect_size_source` 那一栏一直在拦的事；
- 「至多一次」没了，全灭回退会变成死循环。

设计稿：docs/design/2026-09-16-idea-redesign.md 第五节。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SKILLS = Path(__file__).resolve().parents[1] / ".claude" / "skills"
IDEA = SKILLS / "social-idea"
STAGES = SKILLS / "social-research" / "stages"


def _t(p: Path) -> str:
    assert p.exists(), f"缺 {p}"
    return p.read_text(encoding="utf-8")


# --------------------------------------------------------------- ⑤ 经验预检

def test_预检有自己的一份说明书() -> None:
    """⑤ 是全新的一段，规格厚，且只在走到它时才需要加载——正合按需加载的设计。"""
    assert (IDEA / "reference" / "prescreen.md").exists()


def test_预检挂进了路由表与加载表() -> None:
    m = yaml.safe_load(_t(IDEA / "manifest.yaml"))
    paths = [r["path"] for r in m["references"]["on_demand"]]
    assert "reference/prescreen.md" in paths, paths
    assert "reference/prescreen.md" in _t(IDEA / "SKILL.md"), "路由器没指向它，等于没有"


@pytest.mark.parametrize("sentence,why", [
    ("只许重排，不许生成", "不变量二的最后一段：⑤ 看得见真数据，权限却最窄"),
    ("做不了不扣分", "不变量三：否则重排会把研究系统性降级成数据允许的那些题"),
    ("最多前 8 个", "成本上限，不写死就会对全部候选跑"),
    ("至多一次", "全灭回退的硬上限，没有它就是死循环"),
    ("`mde`", "预检读最小可测效应，不需要先有效应量估计"),
    ("不看 `verdict`", "看判词就会退回拿缺省 0.2 算功效那条老路"),
    ("precision_halfwidth", "描述类不算功效、按精度判"),
])
def test_预检的硬话在(sentence: str, why: str) -> None:
    assert sentence in _t(IDEA / "reference" / "prescreen.md"), why


# --------------------------------------------------------------- 环节 3 吃下预检

@pytest.mark.parametrize("sentence,why", [
    ("generated_n", "发散段产出数要落盘，ideas-candidates-monotone 认的就是它"),
    ("unreachable", "清单乙要落盘，ideas-two-lists 认的就是它"),
    ("经验预检", "环节 3 吃下了原环节 4 的预检，对多个候选跑"),
    ("两份清单", "甲乙都要交付，不是只交能做的那份"),
])
def test_环节三的硬话在(sentence: str, why: str) -> None:
    assert sentence in _t(STAGES / "3-ideas.md"), why


def test_环节四写明预检已在环节三跑过() -> None:
    """环节 4 瘦身之后，它不再是这些题第一次被检验——写不明白就会重跑一遍。"""
    assert "经验预检" in _t(STAGES / "4-feasibility.md")


# --------------------------------------------------------------- 四条不变量

@pytest.mark.parametrize("sentence,why", [
    ("生成不是裁决", "不变量一：发散段一个都不许扔，否则等于预先过滤了后续所有判断的输入"),
    ("只许淘汰，不许生成", "不变量二：④ 第一次看见完整变量清单，看见了就想造题是本能"),
    ("做不了不扣分", "不变量三"),
    ("一个信号，不是裁决", "不变量四：外审降权。LLM 当裁判与人类专家判断低相关"),
    ("不许写死宿主", "老铁律三，仍然有效"),
])
def test_四条不变量的硬话在(sentence: str, why: str) -> None:
    assert sentence in _t(IDEA / "reference" / "iron-laws.md"), why


def test_被证伪的老铁律不许还立着() -> None:
    """「只看形状不看变量清单」已被实测证伪——形状层面几乎挡不住任何题，
    可行性门因此 0 淘汰。它跟不变量二直接冲突，留着就是两条打架的规矩。"""
    t = _t(IDEA / "reference" / "iron-laws.md")
    assert "只看数据的形状，不看变量清单" not in t


# --------------------------------------------------------------- ③④ 改写

@pytest.mark.parametrize("sentence,why", [
    ("异常现象", "四源之一"),
    ("识别机会", "四源之一"),
    ("理论张力", "四源之一"),
    ("文献局限", "四源之一——降为四分之一，不再是唯一矿脉"),
    ("15–20", "量先够：多样性坍塌只能靠量对抗"),
    ("追问工具", "透镜降级：不再是生成器"),
    ("无任何可用题项", "④ 新的淘汰条件，要完整变量清单才判得了"),
    ("generated_n", "发散段产出数要落盘"),
    ("**`research_type`**", "老的硬话，test_research_type 也盯着"),
])
def test_发散与收敛的硬话在(sentence: str, why: str) -> None:
    assert sentence in _t(IDEA / "reference" / "lenses.md"), why


def test_空转的那道门已撤掉且写明了为什么() -> None:
    """「完全没有识别路径」就是 0 淘汰的那道门：有面板数据时「面板结构」本身
    算一条识别路径，所有候选都过。识别路径强弱归 ⑤ 与环节 7.5。

    钉的是**淘汰条件表里不许再有它**，不是禁掉这个字符串——
    「为什么撤掉」恰恰该写下来，不然下一个人会照着老直觉再加回去。
    """
    t = _t(IDEA / "reference" / "lenses.md")
    table = t[t.index("## 第二步 · 机械处置"):]
    conditions = table[:table.index("> ⚠")]
    assert "完全没有识别路径" not in conditions, "它还立在淘汰条件表里"
    assert "面板结构" in t, "撤掉的理由没写，下个人会把它加回来"


# --------------------------------------------------------------- ② 文献双通道

@pytest.mark.parametrize("sentence,why", [
    ("双通道", "用户指定文献精读 ┊ 领域检索只扫题录"),
    ("不得替代", "自带文献不能替代领域检索，否则把收窄问题放大一轮"),
    ("覆盖偏差", "要报一句：你给的这批集中在哪派、检索另见哪几派"),
    ("[UNVERIFIED]", "PDF 第三层：查不到权威题录的标存疑，但照样进语料"),
    ("20", "≤20 当精选读物不筛，>20 当检索结果照筛"),
])
def test_文献双通道的硬话在(sentence: str, why: str) -> None:
    assert sentence in _t(STAGES / "2-literature.md"), why


def test_主链入口认得自带文献() -> None:
    assert "refs:" in _t(SKILLS / "social-research" / "SKILL.md"), "入口不认，用户给了也进不来"


# --------------------------------------------------------------- ⑥⑦ 核验与评议

@pytest.mark.parametrize("sentence,why", [
    ("定向查重", "⑥：拿具体候选去查有没有人做过，不是笼统检索"),
    ("novelty_check", "查重结论要落盘"),
    ("theory_hook", "理论对话归属在 ⑥ 才填得实，发散阶段填的是判断"),
    ("都没召回", "自检：连用户指定的文献都没召回，说明检索策略有问题"),
    ("一个信号，不是裁决", "⑦ 外审降权"),
    ("审不了不等于通过", "老硬话，最危险的失败模式"),
    ("被点名的题要是选中去做的那道", "老硬话，test_research_type 也盯着"),
])
def test_核验与评议的硬话在(sentence: str, why: str) -> None:
    assert sentence in _t(IDEA / "reference" / "jury.md"), why


def test_跨轮记忆那句空承诺已经处理() -> None:
    """设计稿未决三：jury.md 写着砍掉的候选「下次探索时是反重复的记忆」，
    但全仓没有任何一处读上一次运行的 ideas.json——这一轮不实现，就得说实话，
    不许把没做的事写成已有的能力。"""
    t = _t(IDEA / "reference" / "jury.md")
    assert "下次探索时它是反重复的记忆" not in t, "空承诺还立着"
    assert "还没实现" in t or "跨运行" in t, "得写明这个缺口，不然下个人以为有"


# --------------------------------------------------------------- 整理模式接上 ⑤

@pytest.mark.parametrize("sentence,why", [
    ("经验预检", "用户自带的题同样要体检——那正是这个模式该给他的东西"),
    ("只报告不淘汰", "预检发现做不了，也只能写进局限，不能换掉用户的题"),
    ("generated_n", "整理模式也要写，不然候选数判据在这条路上一律不适用"),
    ("user-idea", "source_lens 写死这个值"),
])
def test_整理模式的硬话在(sentence: str, why: str) -> None:
    assert sentence in _t(IDEA / "reference" / "tidy-mode.md"), why


# ═══════════════════════════════════════════════ 真跑 r20260916_netdem 逼出来的

@pytest.mark.parametrize("skill", ["social-idea", "social-paper", "social-review",
                                   "social-identification", "social-sim", "social-research"])
def test_frontmatter里不许出现三连字符(skill: str) -> None:
    """真跑撞出来的：宿主按 `---` 切 frontmatter 时是**按子串切、不按整行切**，
    所以 `argument-hint` 里写 `[--- discipline: ...]` 会把 frontmatter 从中间截断——
    name、description、allowed-tools 三行漏进正文，工具白名单可能因此没被读到。

    分隔符怎么用写在正文里就够，不必出现在 frontmatter。
    """
    t = _t(SKILLS / skill / "SKILL.md")
    assert t.startswith("---\n"), f"{skill} 的 frontmatter 不是以 --- 开头"
    body_start = t.index("\n---\n", 3)
    front = t[4:body_start]
    assert "---" not in front, f"{skill} 的 frontmatter 里还有 ---：{front[:200]}"


@pytest.mark.parametrize("sentence,why", [
    ("只有「伦理不可行」是淘汰", "④ 与不变量三的冲突：缺数据是分流不是淘汰"),
    ("分流", "缺变量、层次不匹配、期数不够——都是这份数据缺东西，换数据能做"),
    ("回数据包全集再搜一次", "真跑教训：V-Dem 1818 列里只取了 6 列，就在清单乙里写「本数据没有」，而 v2mecenefi 本来就在"),
])
def test_收敛段不再把缺数据当淘汰(sentence: str, why: str) -> None:
    assert sentence in _t(IDEA / "reference" / "lenses.md"), why


def test_取列这一步被管起来了() -> None:
    """真跑第七个发现：环节 1 取哪几列，决定了后面能出什么题——**取列出题**。
    它发生在流程管辖之外，四条不变量一条都没防住。"""
    t = _t(STAGES / "1-profile.md")
    assert "取列" in t and "宁宽勿窄" in t
    assert "oss trace save" in t, "取了哪些、为什么、漏了什么要留痕，否则事后查不出是取列出的题"
