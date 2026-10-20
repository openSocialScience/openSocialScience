"""idea 重构批 1：数据契约三字段 + 两条机械判据。

设计稿 [docs/design/2026-09-16-idea-redesign.md] 第八节。判据先行——这套设计的
核心主张之一就是「可机械核查」，判据不先立，后面几批做没做到就只能靠自述。

两条判据各自守一条不变量：

- `ideas-candidates-monotone` 守**不变量二**（生成/淘汰权限不对称）。
  发散段 ③ 产出多少条，就该一直是多少条：**多出来**说明收敛段拿着变量清单
  回头造题（数据出题）；**少下去**说明被砍的候选被删出了文件，而它们是下次
  探索的反重复记忆。两个方向都是违规，判词要分得清是哪一种。

- `ideas-two-lists` 守**不变量三**（数据不可及不降权）。清单乙指向的候选必须
  还在文件里，而且要写清缺什么数据——只标「做不了」不说缺什么，等于没给用户
  去拿数据的路。

**离线，不叫模型。**
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.core import skillcheck as sc
from oss.core.contracts import Idea, IdeaSet


def _ideas(d: Path, payload: dict) -> Path:
    (d / "ideas.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return d


def _idea(idea_id: str, **over) -> dict:
    base = {"idea_id": idea_id, "title": "t", "research_question": "q", "lens": "mechanism-gap"}
    base.update(over)
    return base


# --------------------------------------------------------------- 数据契约

def test_老文件没有新字段也读得出来() -> None:
    """三个新字段是**只增不改**：老运行目录的 ideas.json 仍要能加载。"""
    s = IdeaSet.model_validate({"direction": "d", "ideas": [_idea("i1")]})
    assert s.ideas[0].source_lens == ""
    assert s.ideas[0].prescreen == {}
    assert s.ideas[0].novelty_check == ""
    assert s.generated_n == 0
    assert s.unreachable == []


def test_三个新字段与两份清单能写能读(tmp_path: Path) -> None:
    s = IdeaSet(
        direction="d",
        generated_n=2,
        unreachable=["i2"],
        ideas=[
            Idea(idea_id="i1", title="t", research_question="q", source_lens="anomaly",
                 prescreen={"bound": True, "mde": 0.18, "coverage": 0.92, "note": ""},
                 novelty_check="Crossref 检索 12 条，无同题"),
            Idea(idea_id="i2", title="t2", research_question="q2", source_lens="theory",
                 needs_extra_data="缺省级财政支出，需向财政部申请"),
        ],
    )
    s.save(tmp_path / "ideas.json")
    back = IdeaSet.load(tmp_path / "ideas.json")
    assert back.generated_n == 2
    assert back.unreachable == ["i2"]
    assert back.ideas[0].source_lens == "anomaly"
    assert back.ideas[0].prescreen["mde"] == pytest.approx(0.18)
    assert back.ideas[0].novelty_check.startswith("Crossref")


# --------------------------------------------------------------- 两条规则已注册

@pytest.mark.parametrize("rule_id", ["ideas-candidates-monotone", "ideas-two-lists"])
def test_两条判据都挂在规则表上(rule_id: str) -> None:
    ids = {r.id for r in sc.RULES}
    assert rule_id in ids, sorted(i for i in ids if i.startswith("ideas"))


# --------------------------------------------------------------- 候选数只减不增

def test_产出数与候选数相等_判过(tmp_path: Path) -> None:
    _ideas(tmp_path, {"generated_n": 3, "ideas": [_idea("i1"), _idea("i2"), _idea("i3")]})
    status, detail = sc.r_ideas_candidates_monotone(sc.RunView(tmp_path))
    assert status == sc.PASS, detail


def test_收敛段凭空多出候选_判不过(tmp_path: Path) -> None:
    """④ 拿到完整变量清单之后又生成了新题——不变量二禁止的正是这件事。"""
    _ideas(tmp_path, {"generated_n": 2, "ideas": [_idea("i1"), _idea("i2"), _idea("i3")]})
    status, detail = sc.r_ideas_candidates_monotone(sc.RunView(tmp_path))
    assert status == sc.FAIL, detail
    assert "多" in detail


def test_被砍的候选被删出文件_判不过(tmp_path: Path) -> None:
    """砍掉的候选要留在文件里带理由；删掉就等于白挖了一次。"""
    _ideas(tmp_path, {"generated_n": 5, "ideas": [_idea("i1"), _idea("i2")]})
    status, detail = sc.r_ideas_candidates_monotone(sc.RunView(tmp_path))
    assert status == sc.FAIL, detail
    assert "删" in detail


def test_老文件没声明产出数_这条不适用(tmp_path: Path) -> None:
    """看不出来就不判：老运行目录没有 generated_n，不能因为「没记」判它违规。"""
    _ideas(tmp_path, {"ideas": [_idea("i1")]})
    status, _ = sc.r_ideas_candidates_monotone(sc.RunView(tmp_path))
    assert status == sc.NA


def test_没有ideas文件_这条不适用(tmp_path: Path) -> None:
    status, _ = sc.r_ideas_candidates_monotone(sc.RunView(tmp_path))
    assert status == sc.NA


# --------------------------------------------------------------- 两份清单

def test_清单乙的候选都在且写了缺什么_判过(tmp_path: Path) -> None:
    _ideas(tmp_path, {"generated_n": 2, "unreachable": ["i2"],
                      "ideas": [_idea("i1"), _idea("i2", needs_extra_data="缺省级财政支出")]})
    status, detail = sc.r_ideas_two_lists(sc.RunView(tmp_path))
    assert status == sc.PASS, detail


def test_清单乙指向不存在的候选_判不过(tmp_path: Path) -> None:
    """乙栏点名了 i2，文件里却没有它——那条题被删了，不变量三守的就是这个。"""
    _ideas(tmp_path, {"generated_n": 2, "unreachable": ["i2"], "ideas": [_idea("i1")]})
    status, detail = sc.r_ideas_two_lists(sc.RunView(tmp_path))
    assert status == sc.FAIL, detail
    assert "i2" in detail


def test_清单乙没写缺什么数据_判不过(tmp_path: Path) -> None:
    """只标「做不了」不说缺什么，用户就没有去拿数据的路。"""
    _ideas(tmp_path, {"generated_n": 2, "unreachable": ["i2"],
                      "ideas": [_idea("i1"), _idea("i2")]})
    status, detail = sc.r_ideas_two_lists(sc.RunView(tmp_path))
    assert status == sc.FAIL, detail
    assert "i2" in detail


def test_本次没有数据不可及的候选_这条不适用(tmp_path: Path) -> None:
    """乙栏空着可能是「都做得了」，也可能是「没做这一步」，分不出来就不判。"""
    _ideas(tmp_path, {"generated_n": 2, "ideas": [_idea("i1"), _idea("i2")]})
    status, _ = sc.r_ideas_two_lists(sc.RunView(tmp_path))
    assert status == sc.NA
