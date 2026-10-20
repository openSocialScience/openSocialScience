"""检索完先筛再读：模型逐篇判「纳入 / 排除」，工具核每篇都判过、排除有理由，再盖到语料上。"""

from __future__ import annotations

import pytest

from oss.api import errors
from oss.api import lit as lit_api
from oss.core import literature as lit
from oss.core.contracts import LiteratureCorpus, PaperRecord, Screening
from oss.core.reading import rank


def rec(pid: str, **kw) -> PaperRecord:
    return PaperRecord(**{"paper_id": pid, "title": f"Paper {pid}", "source": "test", **kw})


def corpus(*recs: PaperRecord) -> LiteratureCorpus:
    return LiteratureCorpus(corpus_id="c", queries=["education fertility"], records=list(recs))


# ─────────────────────────── 核


def test_每篇都要判_漏的拒绝() -> None:
    c = corpus(rec("W1"), rec("W2"))
    problems, _ = lit.apply_screening(c, Screening(verdicts=[{"paper_id": "W1", "verdict": "纳入"}]))
    assert any("没判" in p and "W2" in p for p in problems)
    assert not c.screened and c.records[0].screen == ""  # 有问题时不动语料


def test_排除要写理由() -> None:
    c = corpus(rec("W1"))
    problems, _ = lit.apply_screening(c, Screening(verdicts=[{"paper_id": "W1", "verdict": "排除"}]))
    assert any("没写理由" in p for p in problems)


def test_判定只认纳入排除() -> None:
    c = corpus(rec("W1"))
    problems, _ = lit.apply_screening(c, Screening(verdicts=[{"paper_id": "W1", "verdict": "maybe"}]))
    assert any("不是 纳入/排除" in p for p in problems)


def test_不在语料里的id和判两次的都拒绝() -> None:
    c = corpus(rec("W1"))
    problems, _ = lit.apply_screening(c, Screening(verdicts=[
        {"paper_id": "W1", "verdict": "纳入"}, {"paper_id": "W1", "verdict": "排除", "reason": "x"},
        {"paper_id": "W9", "verdict": "纳入"}]))
    assert any("判了两次" in p for p in problems) and any("W9" in p and "不在语料里" in p for p in problems)


def test_通过后盖到语料并数排除() -> None:
    c = corpus(rec("W1"), rec("W2"))
    problems, warnings = lit.apply_screening(c, Screening(verdicts=[
        {"paper_id": "W1", "verdict": "纳入"}, {"paper_id": "W2", "verdict": "排除", "reason": "流行病学队列"}]))
    assert problems == [] and c.screened and c.n_excluded == 1
    assert c.records[1].screen == "排除" and c.records[1].screen_reason == "流行病学队列"


def test_空语料不警告没排除() -> None:
    """空语料上「一篇都没排除」是假警报——没有可排除的东西。"""
    c = corpus()
    problems, warnings = lit.apply_screening(c, Screening())
    assert problems == [] and c.screened
    assert warnings == ["语料是空的，没什么可筛"]


def test_全纳入要提醒_排除过七成也要提醒() -> None:
    c = corpus(rec("W1"), rec("W2"))
    _, w1 = lit.apply_screening(c, Screening(verdicts=[
        {"paper_id": "W1", "verdict": "纳入"}, {"paper_id": "W2", "verdict": "纳入"}]))
    assert any("一篇都没排除" in w for w in w1)
    c2 = corpus(*[rec(f"W{i}") for i in range(10)])
    _, w2 = lit.apply_screening(c2, Screening(verdicts=[
        {"paper_id": f"W{i}", "verdict": "排除" if i else "纳入", "reason": "跑题"} for i in range(10)]))
    assert any("排除了 90%" in w and "重搜" in w for w in w2)


def test_增量_已判过的不在新文件里不算漏() -> None:
    """滚雪球之后只有新增的没判。让模型把 200 篇重判一遍是浪费，也容易改口。"""
    c = corpus(rec("W1", screen="纳入"), rec("W2", screen="排除", screen_reason="跑题"), rec("W3"))
    c.screened = False
    problems, _ = lit.apply_screening(c, Screening(verdicts=[{"paper_id": "W3", "verdict": "纳入"}]))
    assert problems == [] and c.screened and c.n_excluded == 1
    assert {r.paper_id: r.screen for r in c.records} == {"W1": "纳入", "W2": "排除", "W3": "纳入"}


def test_增量_新增的没判照样拒绝() -> None:
    c = corpus(rec("W1", screen="纳入"), rec("W3"))
    problems, _ = lit.apply_screening(c, Screening(verdicts=[]))
    assert any("没判" in p and "W3" in p for p in problems)


def test_增量_可以改口() -> None:
    c = corpus(rec("W1", screen="纳入"))
    problems, warnings = lit.apply_screening(c, Screening(verdicts=[{"paper_id": "W1", "verdict": "排除", "reason": "细看是综述"}]))
    assert problems == [] and c.records[0].screen == "排除" and c.n_excluded == 1
    # 翻案合法，但要看得见：上一轮纳入的这一轮排除了，只有这条警告说得出来
    assert any("改口 1 篇" in w and "W1（纳入→排除）" in w for w in warnings)


def test_增量_本轮全纳入要提醒() -> None:
    """全语料算的「一篇都没排除」在增量之后不响了——上一轮排的还挂着。要按这一轮再问一遍。"""
    c = corpus(rec("W1", screen="纳入"), rec("W2", screen="排除", screen_reason="跑题"),
               rec("W3"), rec("W4"))
    problems, warnings = lit.apply_screening(c, Screening(verdicts=[
        {"paper_id": "W3", "verdict": "纳入"}, {"paper_id": "W4", "verdict": "纳入"}]))
    assert problems == []
    assert any("这轮判的 2 篇" in w for w in warnings)


def test_增量_本轮全纳入不遮住排除过七成() -> None:
    c = corpus(*[rec(f"W{i}", screen="排除", screen_reason="跑题") for i in range(8)], rec("W8"), rec("W9"))
    _, w = lit.apply_screening(c, Screening(verdicts=[
        {"paper_id": "W8", "verdict": "纳入"}, {"paper_id": "W9", "verdict": "纳入"}]))
    assert any("这轮判的 2 篇" in x for x in w) and any("排除了 80%" in x for x in w)


# ─────────────────────────── 排序只看纳入的


def test_排序跳过排除的() -> None:
    c = corpus(rec("keep", title="Education and fertility", citation_count=1),
               rec("drop", title="Education and fertility revisited", citation_count=999, screen="排除"))
    assert [r.paper_id for r in rank(c, 10)] == ["keep"]


# ─────────────────────────── API


def _save(run, records, verdicts):
    LiteratureCorpus(records=[PaperRecord(**r) for r in records]).save(run / "literature.json")
    Screening(verdicts=verdicts).save(run / "screening.json")


def test_api_缺筛选文件报Missing并指向schema(tmp_path) -> None:
    LiteratureCorpus(records=[]).save(tmp_path / "literature.json")
    with pytest.raises(errors.Missing) as e:
        lit_api.screen(run=str(tmp_path))
    assert e.value.artifact == "screening" and "oss schema screening" in e.value.summary


def test_api_过不了校验是Refused并留痕(tmp_path) -> None:
    _save(tmp_path, [{"paper_id": "W1", "title": "A"}, {"paper_id": "W2", "title": "B"}],
          [{"paper_id": "W1", "verdict": "纳入"}])
    before = (tmp_path / "literature.json").read_bytes()
    with pytest.raises(errors.Refused) as e:
        lit_api.screen(run=str(tmp_path))
    assert any("没判" in p for p in e.value.problems)
    assert "refused" in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert LiteratureCorpus.load(tmp_path / "literature.json").screened is False
    # 一个字节都没动：拒绝时语料是原封的，不是「盖了一半」
    assert (tmp_path / "literature.json").read_bytes() == before


def test_api_拒绝时不报上一次的排除数(tmp_path) -> None:
    """盘上的语料上一轮筛过（排除 1 篇），这一轮又滚进来一篇新的还没判。
    `n_excluded` 说的是**这次盖上了几篇**，也就是 0——照搬盘上的旧数字，
    调用方会以为这次筛成功了。（W2 已判过，增量筛选下不用重判，真正没判的是 W1）"""
    LiteratureCorpus(records=[PaperRecord(paper_id="W1", title="A"),
                              PaperRecord(paper_id="W2", title="B", screen="排除", screen_reason="跑题")],
                     screened=True, n_excluded=1).save(tmp_path / "literature.json")
    Screening(verdicts=[]).save(tmp_path / "screening.json")
    with pytest.raises(errors.Refused) as e:
        lit_api.screen(run=str(tmp_path))
    assert e.value.data["n_excluded"] == 0


def test_api_通过后语料落盘带判定(tmp_path) -> None:
    _save(tmp_path, [{"paper_id": "W1", "title": "A"}, {"paper_id": "W2", "title": "B"}],
          [{"paper_id": "W1", "verdict": "纳入"}, {"paper_id": "W2", "verdict": "排除", "reason": "跑题"}])
    res = lit_api.screen(run=str(tmp_path))
    c = LiteratureCorpus.load(tmp_path / "literature.json")
    assert c.screened and c.n_excluded == 1 and {r.paper_id: r.screen for r in c.records} == {"W1": "纳入", "W2": "排除"}
    assert res["data"]["n_excluded"] == 1 and "排除 1" in res["summary"]


# ─────────────────────────── 下游：精读拿的是不是筛过的


def _corpus_at(run, *recs: PaperRecord, screened: bool = False, n_excluded: int = 0) -> None:
    LiteratureCorpus(queries=["education fertility"], records=list(recs),
                     screened=screened, n_excluded=n_excluded).save(run / "literature.json")


def test_fetch_top_未筛的语料要提醒(tmp_path, monkeypatch) -> None:
    """`--top` 排的是全部记录。语料没筛过时排头的可能是「世界科学家警告」，
    而排序自己看不出来——所以要说出来。"""
    _corpus_at(tmp_path, rec("W1"))
    monkeypatch.setattr(lit_api.reading_mod, "fetch", lambda corpus, out_dir, **kw: [])
    res = lit_api.fetch(run=str(tmp_path), top=5)
    assert any("还没筛" in w for w in res["data"]["warnings"])


def test_fetch_top_筛过就不提醒(tmp_path, monkeypatch) -> None:
    _corpus_at(tmp_path, rec("W1", screen="纳入"), screened=True)
    monkeypatch.setattr(lit_api.reading_mod, "fetch", lambda corpus, out_dir, **kw: [])
    res = lit_api.fetch(run=str(tmp_path), top=5)
    assert res["data"]["warnings"] == []


def test_fetch_ids_点名排除的要提醒(tmp_path, monkeypatch) -> None:
    """点名就是判断，工具不替它翻案——照拿，但留痕里要看得见。"""
    _corpus_at(tmp_path, rec("W1", screen="纳入"),
               rec("W2", screen="排除", screen_reason="跑题"), screened=True, n_excluded=1)
    monkeypatch.setattr(lit_api.reading_mod, "fetch", lambda corpus, out_dir, **kw: [])
    res = lit_api.fetch(run=str(tmp_path), ids="W2")
    assert any("筛选时排除的" in w and "W2" in w for w in res["data"]["warnings"])
    assert "W2" in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


def test_lit_add_筛过的语料补入的记录标纳入(tmp_path, monkeypatch) -> None:
    """筛过的语料里补一篇经典进来：不标判定的话它 `screen` 是空的，
    既不是纳入也不是排除——而 `oss lit add` 补它进来这个动作本身就是「要它」。"""
    _corpus_at(tmp_path, rec("W1", screen="纳入"), screened=True)
    monkeypatch.setattr(lit_api.lit_module, "fetch_by_doi",
                        lambda doi: PaperRecord(paper_id="W9", title="经典", doi=doi, source="test"))
    lit_api.add("10.1/x", run=str(tmp_path))
    added = {r.paper_id: r for r in LiteratureCorpus.load(tmp_path / "literature.json").records}["W9"]
    assert added.screen == "纳入" and "oss lit add" in added.screen_reason


def test_lit_add_没筛过的语料不硬标(tmp_path, monkeypatch) -> None:
    """没筛过的语料上标「纳入」是撒谎——那会让 `oss lit screen` 以为这篇判过了。"""
    _corpus_at(tmp_path, rec("W1"))
    monkeypatch.setattr(lit_api.lit_module, "fetch_by_doi",
                        lambda doi: PaperRecord(paper_id="W9", title="经典", doi=doi, source="test"))
    lit_api.add("10.1/x", run=str(tmp_path))
    added = {r.paper_id: r for r in LiteratureCorpus.load(tmp_path / "literature.json").records}["W9"]
    assert added.screen == ""


# ─────────────────────────── snowball API


def _fake_snowball(monkeypatch, added: int = 2):
    """把核心打成桩：API 层只管挑种子、落盘、留痕。"""
    seen: dict = {}

    def fake(corpus, seeds, **kw):
        seen["seeds"] = [s.paper_id for s in seeds]
        seen["kw"] = kw
        for i in range(added):
            corpus.records.append(PaperRecord(paper_id=f"W9{i}", title=f"New {i}", via=f"snowball:backward:{seeds[0].paper_id}"))
        if added:
            corpus.screened = False
        from oss.core.contracts import SnowballStats
        return SnowballStats(seeds=[s.paper_id for s in seeds], added=added, backward=added, rounds=1)

    monkeypatch.setattr(lit_api.lit_module, "snowball", fake)
    return seen


def test_snowball_缺语料报Missing(tmp_path) -> None:
    with pytest.raises(errors.Missing) as e:
        lit_api.snowball(run=str(tmp_path))
    assert e.value.artifact == "literature"


def test_snowball_缺省按排序取种子_没筛要提醒(tmp_path, monkeypatch) -> None:
    seen = _fake_snowball(monkeypatch)
    LiteratureCorpus(queries=["education fertility"], records=[
        PaperRecord(paper_id="W1", title="Education and fertility", citation_count=5),
        PaperRecord(paper_id="W2", title="Unrelated protein folding", citation_count=900),
    ]).save(tmp_path / "literature.json")
    res = lit_api.snowball(run=str(tmp_path), top=8)
    assert seen["seeds"] == ["W1"]  # W2 跟检索式一个词都不沾，进不了种子
    assert seen["kw"] == {"backward": 50, "forward": 15}
    d = res["data"]
    assert d["n_added"] == 2 and d["n_records"] == 4 and any("还没筛" in w for w in d["warnings"])
    assert "oss lit screen" in d["next"]
    c = LiteratureCorpus.load(tmp_path / "literature.json")
    assert len(c.records) == 4 and c.screened is False
    assert "oss lit snowball" in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


def test_snowball_筛过的语料种子只在纳入里_不提醒(tmp_path, monkeypatch) -> None:
    seen = _fake_snowball(monkeypatch)
    LiteratureCorpus(queries=["education fertility"], screened=True, records=[
        PaperRecord(paper_id="W1", title="Education and fertility", screen="纳入"),
        PaperRecord(paper_id="W2", title="Education fertility revisited", screen="排除", screen_reason="x"),
    ]).save(tmp_path / "literature.json")
    res = lit_api.snowball(run=str(tmp_path))
    assert seen["seeds"] == ["W1"] and res["data"]["warnings"] == []


def test_snowball_点名种子(tmp_path, monkeypatch) -> None:
    seen = _fake_snowball(monkeypatch)
    LiteratureCorpus(queries=["q"], records=[
        PaperRecord(paper_id="W1", title="A", doi="10.1/a"), PaperRecord(paper_id="W2", title="B")]
    ).save(tmp_path / "literature.json")
    res = lit_api.snowball(run=str(tmp_path), seeds="10.1/a,W7", backward=10, forward=5)
    assert seen["seeds"] == ["W1"] and seen["kw"] == {"backward": 10, "forward": 5}
    assert any("W7" in w for w in res["data"]["warnings"])


def test_snowball_点名排除的种子要提醒(tmp_path, monkeypatch) -> None:
    """点名就是判断，工具不替它翻案——照滚，但留痕里要看得见：
    从一篇筛掉的文献往外滚，拉进来的多半也跑题。"""
    _fake_snowball(monkeypatch)
    LiteratureCorpus(queries=["education fertility"], screened=True, records=[
        PaperRecord(paper_id="W1", title="Education and fertility", screen="排除", screen_reason="跑题"),
        PaperRecord(paper_id="W2", title="Education fertility revisited", screen="纳入"),
    ]).save(tmp_path / "literature.json")
    res = lit_api.snowball(run=str(tmp_path), seeds="W1")
    assert any("W1" in w and "排除" in w for w in res["data"]["warnings"])
    assert "种子里有筛选时排除的 1 篇" in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


def test_snowball_种子换不到W号要提醒(tmp_path, monkeypatch) -> None:
    """核心把换不到 OpenAlex 编号的种子悄悄跳过——不说出来，
    调用方只看见「新增 0 篇」，会以为是引文链到头了，其实是一篇都没滚。"""
    from oss.core.contracts import SnowballStats

    monkeypatch.setattr(lit_api.lit_module, "snowball",
                        lambda corpus, seeds, **kw: SnowballStats(seeds=[], added=0, rounds=0))
    LiteratureCorpus(queries=["education fertility"], records=[
        PaperRecord(paper_id="10.1/a", title="Education and fertility")]
    ).save(tmp_path / "literature.json")
    res = lit_api.snowball(run=str(tmp_path))
    assert any("没滚" in w for w in res["data"]["warnings"])


def test_snowball_点名的一个都不在时拒绝(tmp_path, monkeypatch) -> None:
    _fake_snowball(monkeypatch)
    LiteratureCorpus(queries=["q"], records=[PaperRecord(paper_id="W1", title="A")]).save(tmp_path / "literature.json")
    with pytest.raises(errors.Missing) as e:
        lit_api.snowball(run=str(tmp_path), seeds="W7")
    assert e.value.artifact == "records"


def test_snowball_OpenAlex不可用是Unavailable(tmp_path, monkeypatch) -> None:
    def boom(*a, **k):
        raise lit.SourceUnavailable("openalex", "HTTP 409")
    monkeypatch.setattr(lit_api.lit_module, "snowball", boom)
    # 检索式要给得排得出种子来：`_content_words` 把两个字符以内的词全滤掉，
    # 单字母检索式排不出任何种子，撞的会是「排不出种子」那道门，试不到源不可用这条路。
    LiteratureCorpus(queries=["education fertility"],
                     records=[PaperRecord(paper_id="W1", title="Education and fertility")]
                     ).save(tmp_path / "literature.json")
    with pytest.raises(errors.Unavailable):
        lit_api.snowball(run=str(tmp_path))


def test_lit_add_记来路(tmp_path, monkeypatch) -> None:
    LiteratureCorpus(records=[]).save(tmp_path / "literature.json")
    monkeypatch.setattr(lit_api.lit_module, "fetch_by_doi",
                        lambda *a, **k: PaperRecord(paper_id="W5", title="Classic", doi="10.1/c"))
    lit_api.add("10.1/c", run=str(tmp_path))
    assert LiteratureCorpus.load(tmp_path / "literature.json").records[0].via == "add"


# ─────────────────────────── import API

RIS_TWO = """TY  - JOUR
AU  - 张三
TI  - 数字经济对城乡收入差距的影响
JO  - 经济研究
PY  - 2021
DO  - 10.19744/j.cnki.11-1235/f.2021.0033
ER  -

TY  - JOUR
AU  - 李四
TI  - 农民工市民化
JO  - 社会
PY  - 2016
ER  -
"""


def test_import_导入两条_标阵营_翻假screened_留痕(tmp_path) -> None:
    LiteratureCorpus(queries=["q"], screened=True,
                     records=[PaperRecord(paper_id="W1", title="A", screen="纳入")]).save(tmp_path / "literature.json")
    f = tmp_path / "cnki.txt"; f.write_text(RIS_TWO, encoding="utf-8")
    res = lit_api.import_file(str(f), run=str(tmp_path), camp="本土研究")
    d = res["data"]
    assert d["n_added"] == 2 and d["n_skipped"] == 0 and d["format"] == "ris"
    c = LiteratureCorpus.load(tmp_path / "literature.json")
    assert len(c.records) == 3 and c.screened is False
    new = [r for r in c.records if r.via == "import"]
    assert {r.camp for r in new} == {"本土研究"} and {r.language for r in new} == {"zh"} and all(r.screen == "" for r in new)
    assert "oss lit import" in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
    assert "oss lit screen" in d["next"]
    # 中文条数写进 note：论文阶段按「语料里有没有中文记录」放行新颖性主张，
    # 读 notes 的人要一眼看见这一批里中文占几条（RIS_TWO 两条都是中文）。
    assert any("2 条进语料（中文 2 条）" in n for n in c.notes)


def test_import_重复的按DOI或标题跳过(tmp_path) -> None:
    LiteratureCorpus(queries=["q"], records=[
        PaperRecord(paper_id="x", title="数字经济对城乡收入差距的影响", doi="10.19744/j.cnki.11-1235/f.2021.0033")]
    ).save(tmp_path / "literature.json")
    f = tmp_path / "cnki.txt"; f.write_text(RIS_TWO, encoding="utf-8")
    res = lit_api.import_file(str(f), run=str(tmp_path))
    assert res["data"]["n_added"] == 1 and res["data"]["n_skipped"] == 1
    assert any("跳过已有 1 条" in w for w in res["data"]["warnings"])


def test_import_认不出格式报BadInput(tmp_path) -> None:
    LiteratureCorpus(queries=["q"], records=[]).save(tmp_path / "literature.json")
    f = tmp_path / "x.txt"; f.write_text("不是导出文件", encoding="utf-8")
    with pytest.raises(errors.BadInput):
        lit_api.import_file(str(f), run=str(tmp_path))


def test_import_缺文件报Missing(tmp_path) -> None:
    LiteratureCorpus(queries=["q"], records=[]).save(tmp_path / "literature.json")
    with pytest.raises(errors.Missing):
        lit_api.import_file(str(tmp_path / "没有.txt"), run=str(tmp_path))


def test_import_GBK编码也能读(tmp_path) -> None:
    LiteratureCorpus(queries=["q"], records=[]).save(tmp_path / "literature.json")
    f = tmp_path / "gbk.txt"; f.write_bytes(RIS_TWO.encode("gbk"))
    assert lit_api.import_file(str(f), run=str(tmp_path))["data"]["n_added"] == 2


def test_import_没给camp要提醒(tmp_path) -> None:
    """不标阵营的导入记录不归任何阵营：环节 2 数「哪个阵营 0 命中」时看不见它们，
    滚雪球也没有阵营可传给后代。这是提醒不是拒绝——阵营是读的人定的，工具不替他定。"""
    LiteratureCorpus(queries=["q"], records=[]).save(tmp_path / "literature.json")
    f = tmp_path / "cnki.txt"; f.write_text(RIS_TWO, encoding="utf-8")
    assert any("--camp" in w for w in lit_api.import_file(str(f), run=str(tmp_path))["data"]["warnings"])
    other = tmp_path / "run2"; other.mkdir()
    LiteratureCorpus(queries=["q"], records=[]).save(other / "literature.json")
    assert not any("--camp" in w for w in
                   lit_api.import_file(str(f), run=str(other), camp="本土")["data"]["warnings"])


def test_search_重搜保留导入记录(tmp_path, monkeypatch) -> None:
    """换词重搜会整份覆盖 literature.json。检索源能再拉一遍，导入的记录不能——
    它们是用户在知网点导出、手工搬进来的，连同筛选判定一起冲掉就是把人的活儿丢了。"""
    LiteratureCorpus(queries=["旧检索式"], records=[
        PaperRecord(paper_id="imp_1", title="农民工市民化", via="import", source="import:ris",
                    screen="纳入", camp="本土"),
        PaperRecord(paper_id="W1", title="Old hit", source="openalex"),
    ]).save(tmp_path / "literature.json")
    monkeypatch.setattr(lit_api.lit_module, "search",
                        lambda *a, **k: LiteratureCorpus(records=[PaperRecord(paper_id="W9", title="New")],
                                                         hits_by_source={"openalex": 1}))
    lit_api.search(["q"], run=str(tmp_path))
    c = LiteratureCorpus.load(tmp_path / "literature.json")
    assert [r.paper_id for r in c.records] == ["W9", "imp_1"]  # 旧的检索记录该没了，导入的该在
    assert c.records[1].screen == "纳入" and c.records[1].camp == "本土"
    assert any("重搜保留了 1 条" in n for n in c.notes)


def test_search_没有导入记录时不加保留note(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(lit_api.lit_module, "search",
                        lambda *a, **k: LiteratureCorpus(records=[PaperRecord(paper_id="W9", title="New")]))
    lit_api.search(["q"], run=str(tmp_path))
    assert not any("重搜保留" in n for n in LiteratureCorpus.load(tmp_path / "literature.json").notes)


def test_search_重搜保留的导入记录里排除的也算进n_excluded(tmp_path, monkeypatch) -> None:
    """`n_excluded` 是从记录里数出来的，重搜时新语料只有新命中、`n_excluded` 是 0，
    保留的导入记录带着「排除」并进来却不重数——落盘的语料就说「一篇没排除」，
    而 `oss lit screen` 的增量判定按记录上的 `screen` 走，两边对不上。"""
    LiteratureCorpus(queries=["旧检索式"], records=[
        PaperRecord(paper_id="imp_1", title="农民工市民化", via="import", source="import:ris",
                    screen="排除", screen_reason="不是同一议题"),
    ]).save(tmp_path / "literature.json")
    monkeypatch.setattr(lit_api.lit_module, "search",
                        lambda *a, **k: LiteratureCorpus(records=[PaperRecord(paper_id="W9", title="New")]))
    lit_api.search(["q"], run=str(tmp_path))
    assert LiteratureCorpus.load(tmp_path / "literature.json").n_excluded == 1


def test_search_重搜时导入记录与新命中重复的不保留(tmp_path, monkeypatch) -> None:
    """同 DOI 的导入记录被新命中盖住了，留下就是重复——一条都没保留时也不该加那条 note。"""
    LiteratureCorpus(queries=["旧检索式"], records=[
        PaperRecord(paper_id="imp_1", title="数字经济", doi="10.1/x", via="import", source="import:ris"),
    ]).save(tmp_path / "literature.json")
    monkeypatch.setattr(lit_api.lit_module, "search",
                        lambda *a, **k: LiteratureCorpus(
                            records=[PaperRecord(paper_id="W9", title="Digital economy", doi="10.1/X")]))
    lit_api.search(["q"], run=str(tmp_path))
    c = LiteratureCorpus.load(tmp_path / "literature.json")
    assert [r.paper_id for r in c.records] == ["W9"]
    assert not any("重搜保留" in n for n in c.notes)


RIS_三语 = """TY  - JOUR
TI  - 数字经济对城乡收入差距的影响
PY  - 2021
ER  -

TY  - JOUR
TI  - 日本の社会保障制度
PY  - 2020
ER  -

TY  - JOUR
TI  - 한국 사회복지
PY  - 2019
ER  -
"""


def test_import_跳过日文韩文记录(tmp_path) -> None:
    """用户只要中英文。日文、韩文题录进了语料只会占着筛选的工时——每一条都要判「纳入/排除」，
    而它们从一开始就不该在。解析器照样产出（解析不做取舍），这里挡下来并说清挡了几条。"""
    LiteratureCorpus(queries=["q"], records=[]).save(tmp_path / "literature.json")
    f = tmp_path / "cnki.txt"; f.write_text(RIS_三语, encoding="utf-8")
    d = lit_api.import_file(str(f), run=str(tmp_path), camp="本土")["data"]
    assert d["n_added"] == 1 and d["n_foreign"] == 2 and d["n_skipped"] == 0
    assert any("跳过 2 条日文/韩文" in w for w in d["warnings"])
    c = LiteratureCorpus.load(tmp_path / "literature.json")
    assert [r.language for r in c.records] == ["zh"]
    # 落盘的 note 与留痕也要说，warnings 只活在这一次返回里
    assert any("跳过 2 条日文/韩文" in n for n in c.notes)
    assert '"n_foreign": 2' in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


RIS_文件内重复 = """TY  - JOUR
TI  - 同一篇文章
JO  - 人口研究
PY  - 2012
ER  -

TY  - JOUR
TI  - 同一篇文章
JO  - 人口研究
PY  - 2012
ER  -

TY  - JOUR
TI  - 另一篇
JO  - 社会学研究
PY  - 2013
ER  -
"""


def test_import_文件内重复与语料已有分开数(tmp_path) -> None:
    """两种「跳过」的处置完全不同：文件内重复是**用户导出时勾重了**，改导出即可；
    语料已有是**这条已经在了**，本来就该跳。合成一个数字报出去，用户不知道该动哪一头。"""
    LiteratureCorpus(queries=["q"], records=[
        PaperRecord(paper_id="x", title="另一篇")]).save(tmp_path / "literature.json")
    f = tmp_path / "cnki.txt"; f.write_text(RIS_文件内重复, encoding="utf-8")
    d = lit_api.import_file(str(f), run=str(tmp_path), camp="本土")["data"]
    assert d["n_added"] == 1 and d["n_dup_in_file"] == 1 and d["n_skipped"] == 1
    w = " ".join(d["warnings"])
    assert "文件内重复 1 条" in w and "跳过已有 1 条" in w
