"""自带想法模式的说明书硬话必须在。**句子没了，代码那边的门就没人照着走。**

跟 `tests/test_stage1_skill_rules.py` 同一套办法，也同一个理由：这些句子不是文档，
是**判据**。`design_locked` 的键名映射表没了，模型就会把用户的 `fe=` / `cluster=`
原样抄进 `plan.json`，`oss run` 退 2；「判词只能记录，不能淘汰」没了，
陪审团一句「理论钩子偏弱」就能让用户交上来的题被换掉——
而这两件事在产出物上都是一个字都看不出来的。

**逐句点名到文件**，不做「整份技能里找找看」：句子挪错地方（映射表掉进 social-idea 的
整理模式里、禁止行掉进主链）同样会红。挪家搬得对不对，是搬完之后要重新钉的事。

**离线，不叫模型，不读运行目录。**只读 `.claude/skills/` 下的 `.md`。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".claude" / "skills"


def _t(skill: str, rel: str) -> str:
    """点名到某份技能里的某一份文件。"""
    p = ROOT / skill / rel
    assert p.exists(), f"缺 {p}"
    return p.read_text(encoding="utf-8")


# ─────────────────────── social-idea：整理模式（不出题，只整理）

def test_判词只能记录不能淘汰():
    """这一条撑着整个自带想法模式：陪审团回来说「理论钩子偏弱」时，
    模型该做的是把判词抄进 `jury_verdict`，不是换一个题。

    没了这句话，`behavior-jury-recorded` 那条指标量的东西（报告在、候选还是那一条）
    就成了一条没人教过的规矩——**门还在，可是没人知道门为什么在**。
    """
    t = _t("social-idea", "reference/tidy-mode.md")
    assert "判词只能记录，不能淘汰" in t


def test_不许拿相近变量顶替绑不上的构念():
    """`needs_extra_data` 非空时停下来告诉用户，不拿手边差不多的列顶上。
    顶上去之后论文里量的是另一个构念，而占位符机制一个字都拦不住。"""
    t = _t("social-idea", "reference/tidy-mode.md")
    assert "拿相近变量顶替绑不上的构念" in t


def test_整理模式两个写死的字面量():
    """`source: "user"` 与 `lens: "user-idea"`——**两个都漏不得**：
    前者是两条自带想法指标开判的开关（漏了全判 n/a，等于这次没人核），
    后者是 `oss eval` 的 `ideas-jury` 规则要的（留空判 FAIL）。"""
    t = _t("social-idea", "reference/tidy-mode.md")
    assert 'source: "user"' in t
    assert 'lens: "user-idea"' in t


def test_整理模式照跑ideas陪审():
    """整理模式跳过十个透镜，**但陪审这一步一个字不变**——
    「不出题」不等于「不受审」。文件里它是完整的一行命令（中间夹着文件路径），
    断整行比断一个拼接出来的片段严。"""
    t = _t("social-idea", "reference/tidy-mode.md")
    assert "oss review <运行目录>/ideas.json --kind ideas --level quick --run <运行目录>" in t


def test_禁止淘汰或改写用户那条记录():
    """契约里的禁止行。`contract.md` 是这份技能对外的硬约束，
    整理模式的正文可以改写、可以搬家，这一行不行。"""
    t = _t("social-idea", "contract.md")
    assert ("- **淘汰或改写 `source = user` 的那条记录**：它是用户自己交上来的题，"
            "陪审团的判词只能记录进 `jury_verdict` / `jury_reason` 与论文局限，"
            "删它、改它的题、在它旁边再加一条候选，都是换题——换题只能由用户发起") in t


# ─────────────────────── social-research：输入怎么拆、`design_locked` 怎么写

def test_design五个键的映射表五行都在():
    """用户那边写 `fe=` / `cluster=`，`plan.json` 里的字段名是 `fixed_effects` /
    `cluster_level`。**表少一行，模型就得猜那一行**——而猜错的表现是
    `oss run` 退 2（`design_locked` 禁未知键），不是一句提醒。"""
    t = _t("social-research", "rules/input-and-rundir.md")
    for row in (
        "| `outcome=Risk` | `outcome` | 原样 |",
        "| `treatment=Misprice` | `treatment` | 原样 |",
        '| `fe=firm+year` | `fixed_effects` | 按 `+` 拆成列表 `["firm", "year"]`，顺序不算数 |',
        "| `cluster=firm` | `cluster_level` | 原样 |",
        "| `estimator=ols_fe` | `estimator` | 原样 |",
    ):
        assert row in t, row


def test_没有design就不要写design_locked():
    """**「锁了但五个键全空」跟「没锁」在代码里都不比对，可它们在盘上意思不一样**：
    只有后者该出现在没有自带想法的运行里。这句话是这个区别唯一的出处。"""
    t = _t("social-research", "rules/input-and-rundir.md")
    assert "没有 `design:` 的输入，`design_locked` 这个字段压根不要写" in t


def test_换题两分支写清楚了():
    """换题在两个时刻是两件事：锚点冻结之前改 `ideas.json` 就行（要用户点头），
    冻结之后只有 `oss anchor switch` 一条路（留痕、带授权人）。
    只写后一条，模型在环节 3 就会以为自己无路可走；只写前一条，跑过分析之后
    改 `ideas.json` 会绕过锚点门——**两条分支都得在**。"""
    t = _t("social-research", "stages/3-ideas.md")
    assert ("还没跑过 `oss run`（锚点没冻结）的，把要换的内容和理由告诉用户，"
            "拿到一句同意再改 `ideas.json`；已经跑过分析的，只能 "
            "`oss anchor switch --to <idea_id> --why ... --authorized-by user`") in t


def test_路由器的输入格式列全四个checkpoint():
    """`idea`（单数，自带想法的整理稿确认）与 `ideas`（复数，陪审团砍完题）
    **不是一回事**，而它们只差一个字母。格式行少列一个，用户就写不出那个开关。"""
    t = _t("social-research", "SKILL.md")
    assert "checkpoint: idea,ideas,design,none" in t
