"""缺什么数据：把散在各候选字段里的话，汇总成一张能行动的表。

事故形状（不是崩，是浪费）：`ideas.json` 每个候选都有一栏 `needs_extra_data`，
历次跑里它写得相当具体——「需申请 GSS 敏感数据版本（NORC 数据飞地）」
「TeleGeography 海底光缆图」「WDI SE.COM.DURS」。**然后整条链再没有一步用它。**
用户要看，得自己打开文件翻十个候选的字段。2026-09-07 我重新查 GSS 有没有出生州，
查完才发现**八月份的运行目录里早写着同一句话**。

三档可信度是这张表的要害：
- 一档 目录里有 → 一条命令就拿到
- 二档 **你精读的文献用过** → 存在性有出处，而且本领域真在用
- 三档 只有模型说 → **可能名字不对，甚至根本不存在**，去找之前先自己搜一下
"""

from __future__ import annotations

from oss.core.datawants import Want, collect_wants


def _ideas(*needs: str) -> dict:
    return {"ideas": [{"idea_id": f"idea_{i:03d}", "needs_extra_data": n}
                      for i, n in enumerate(needs, start=1)]}


def _cards(*datas: str) -> dict:
    return {"cards": [{"paper_id": f"W{i}", "data": d} for i, d in enumerate(datas, start=1)]}


def test_把每个候选缺的东西收上来() -> None:
    wants = collect_wants(_ideas("需要 CFPS 四轮面板", "需要 CFPS 才能做交叉滞后"), {}, [])
    assert len(wants) == 1, [w.what for w in wants]
    assert wants[0].n_ideas == 2, "同一份数据被两个候选要，要合并计数"
    assert wants[0].idea_ids == ["idea_001", "idea_002"]


def test_按解锁几个候选排序() -> None:
    wants = collect_wants(
        _ideas("需要 CFPS", "需要 CFPS", "需要 CFPS", "需要夜间灯光数据"), {}, [])
    assert [w.n_ideas for w in wants] == [3, 1], [(w.what, w.n_ideas) for w in wants]


def test_目录里有的判一档() -> None:
    wants = collect_wants(_ideas("需要 CFPS 的四轮面板"), {}, ["qog", "gss", "cfps"])
    assert wants[0].tier == 1 and "cfps" in wants[0].evidence


def test_文献用过的判二档() -> None:
    """**这一档最值钱**：一篇真发表的论文真用过它，存在性有出处。"""
    wants = collect_wants(
        _ideas("需要人口与健康调查（DHS）的分国数据"),
        _cards("26 国人口与健康调查（DHS）", "美国 GSS 1972–2024"),
        ["qog"])
    assert wants[0].tier == 2, wants[0]
    assert "DHS" in wants[0].evidence and "W1" in wants[0].evidence


def test_谁也没见过的判三档并且说明白() -> None:
    wants = collect_wants(_ideas("需要 TeleGeography 海底光缆登陆年份"), _cards("GSS"), ["qog"])
    assert wants[0].tier == 3
    assert "没有出处" in wants[0].evidence or "未核实" in wants[0].evidence


def test_明说不缺的不进清单() -> None:
    """「无，所有构念都绑上了」这种话不是需求。"""
    for n in ("无", "无。", "空——所有构念都绑上 GSS 的列了", "None", "不需要"):
        assert collect_wants(_ideas(n), {}, []) == [], n


def test_没有候选时给空表不报错() -> None:
    assert collect_wants({}, {}, []) == []
    assert collect_wants({"ideas": []}, {"cards": []}, ["qog"]) == []


def test_一档排在三档前面() -> None:
    """同样解锁 1 个候选时，**先给能立刻拿到的**——用户的时间比清单的完整性值钱。"""
    wants = collect_wants(
        _ideas("需要 TeleGeography 海底光缆", "需要 cfps 面板"), {}, ["cfps"])
    assert [w.tier for w in wants] == [1, 3], [(w.what, w.tier) for w in wants]


def test_Want能变成一行表() -> None:
    w = Want(what="CFPS 四轮面板", n_ideas=2, idea_ids=["idea_001", "idea_002"],
             tier=1, evidence="数据包 cfps")
    assert "CFPS" in w.line() and "2" in w.line() and "数据包 cfps" in w.line()


def test_没点名数据集的跟未核实分开() -> None:
    """两件不同的事：

    - 「需要 TeleGeography 海底光缆图」——**点了名，没核过**，可能根本不存在
    - 「需要离散阶级类别（EGP/Wright 图式）」——**没点名**，只说了缺什么，
      没有东西可核；问题是「哪套数据有这个」还没人回答

    混成一档，「名字可能不存在」这句警告就会贴到一段本来没提名字的话上。
    """
    named = collect_wants(_ideas("需要 TeleGeography 海底光缆登陆年份"), {}, [])
    assert named[0].tier == 3 and "名字可能不对" in named[0].evidence

    described = collect_wants(_ideas("需要一份母亲职业信息覆盖更完整的数据"), {}, [])
    assert described[0].tier == 4, described[0]
    assert "没点名" in described[0].evidence
    assert "名字可能不对" not in described[0].evidence


def test_行太长要截断但原话留在数据里() -> None:
    long = "本数据只有四大普查区，没有州标识（州标识在受限使用版本中才有），也不含州层面的高校扩张时点。两者都需要另外获取。"
    w = collect_wants(_ideas(long), {}, [])[0]
    assert w.what == long, "原话一个字都不许改"
    assert len(w.line().split("｜")[0]) <= 58
