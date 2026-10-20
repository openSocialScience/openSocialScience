"""说明书里的硬话必须在。它们堵的是 2026-08-28 事故的那几个口子。

阶段 2 把说明书拆成了路由器 + `rules/` + `stages/`，**句子没变，位置变了**——
所以每条断言现在点名文件。点名比"整份技能里找找看"严：
句子挪错地方（比如提问白名单掉进某个环节的契约里）也会红。

`social-research` 已经拆完了，所以它点名到具体文件；
兄弟技能（`social-review` / `social-identification` / `social-paper`）还在拆，
句子今天在 `SKILL.md`、明天在 `reference/`——那几条用 rglob 把整份技能的 `.md` 拼起来，
**让断言跨过那次搬家**：拆之前绿，拆之后还绿；句子真被删掉才红。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / ".claude" / "skills"
RESEARCH = ROOT / "social-research"


def _t(rel: str) -> str:
    """`social-research` 里点名的一份文件。"""
    p = RESEARCH / rel
    assert p.exists(), f"缺 {p}"
    return p.read_text(encoding="utf-8")


def _all(skill: str) -> str:
    """一份技能里全部 `.md` 拼起来（含 `rules/` `stages/` `reference/`）。"""
    d = ROOT / skill
    assert d.is_dir(), f"缺 {d}"
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(d.rglob("*.md")))


def test_开工先读状态():
    """路由器的第三步——压缩、中断、换模型接手都从这个文件续。"""
    t = _t("SKILL.md")
    assert "run_state.json" in t and "续跑" in t


def test_提问白名单():
    t = _t("rules/ask-user.md")
    assert "什么时候可以问用户" in t
    assert "手册没列的情况" in t


def test_穷尽之后保留设计不换题不问用户():
    t = _t("rules/stop-conditions.md")
    assert "保留原设计" in t and "降级为关联" in t
    assert "不换题目、不换数据、不问用户" in t


def test_探索只走explore不许自写回归():
    t = _t("rules/explore.md")
    assert "oss run --explore --for" in t
    assert "不许用 python 直接跑回归" in t


def test_循环以决定为准():
    assert "export_with_open_items" in _t("rules/stop-conditions.md")
    review = _all("social-review")
    assert "export_with_open_items" in review
    assert "停下来告诉用户哪里改不动" not in review


def test_功效那句不再泛化():
    """整份技能里都不许再出现——挪来挪去最容易把旧句子带回来。"""
    assert "这份数据回答不了这个问题" not in _all("social-research")


def test_审稿退出码表有轮次门():
    review = _all("social-review")
    assert "--extra-round" in review
    assert "| 4 |" in review or "退出码 4" in review


def test_读状态命令路径不嵌套():
    """`<运行目录>` 本身已经含 `runs/`，再套一层就是那次 `runs/xxx/runs/xxx/` 事故。"""
    t = _t("SKILL.md")
    assert "cat <运行目录>/run_state.json" in t
    assert "runs/<运行目录>/run_state.json" not in t


def test_审计后不问用户除非checkpoint():
    ident = _all("social-identification")
    assert "没带这个开关就不要问" in ident
    assert "每一种都不可行**的，必须留这条痕" in ident


def test_探索通道在审稿处也能找到():
    """审稿人说「加个控制变量看看」时，手边就得有这条通道，不能只写在环节 6。"""
    assert "oss run --explore --for" in _t("rules/explore.md")
    assert "oss run --explore --for review-rN" in _t("stages/8-review.md")
    assert "oss run --explore --for review-rN" in _t("stages/8-review.notes.md")
    assert "oss run --explore --for review-rN" in _all("social-review")


def test_遗留意见一节名字统一():
    """写稿的、审稿的、主链的得叫同一个名字，不然谁也对不上号。"""
    assert "Open questions" in _t("stages/8-review.md")
    assert "Open questions" in _t("rules/stop-conditions.md")
    for skill in ("social-review", "social-paper"):
        assert "Open questions" in _all(skill), skill
    assert "Unresolved review items" not in _all("social-paper")


def test_功效FAIL的换题出路带授权():
    """换题是用户的决定，不是执行者的。"""
    assert "**换题目**——**这一条要用户点头**" in _t("stages/4-feasibility.md")
    assert "挑陪审团留下的别的候选。**这一条要用户点头**" in _t("stages/4-feasibility.notes.md")
    assert "--authorized-by user" in _t("rules/stop-conditions.md")


def test_锚点看冻结的run_state不看重算的loop_state():
    """`loop_state.json` 的 anchor 是每次 `loop status` 按**当前** plan/ideas 重算的——
    对着它看漂移，永远看不出漂移。冻结的那份在 `run_state.json` 的 `anchor`。"""
    assert "`run_state.json` 的 `anchor`" in _t("stages/8-review.md")
    assert "不看 `loop_state.json` 里那个" in _t("stages/8-review.md")
    assert "`run_state.json` 的 `anchor`" in _t("stages/8-review.notes.md")
    assert "`run_state.json` 的 `anchor`" in _all("social-identification")
    assert "有 `anchor` → 那就是本次要回答的问题，**不许改**" in _t("SKILL.md")
    assert "`loop_state.json` 里记着原始方向" not in _all("social-research")
    assert "`loop_state.json` 里的方向与 idea" not in _all("social-identification")
