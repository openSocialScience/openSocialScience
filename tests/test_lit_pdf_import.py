"""用户自己下载的 PDF 进语料：三层往下退。

批 4（设计稿第五节②）。用户手里有 PDF 是常态——他检索过、筛过、下载过，
这批文献**确定相关**，精读的性价比比按排名抓前 20 篇高得多，而且绕开了开放获取
拿不到全文那个老问题。

但 PDF 里没有结构化题录，元数据只能从正文里找，容易找错。所以三层往下退，
跟 `oss data` 查构念同构，也沿用同一条纪律：**查不到不等于不存在**。

| 层 | 条件 | 做法 | 标注 |
|---|---|---|---|
| 1 | 首页印着 DOI | 取 DOI → 反查权威题录 | 正常 |
| 2 | 没有 DOI | 抽标题 → 按标题检索 | 正常 |
| 3 | 仍不可得（多数中文 PDF） | 记下标题猜测，交用户确认 | `[UNVERIFIED]` |

**离线，不叫模型，也不联网**：DOI 反查与标题检索都打桩。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss.api import lit as lit_api
from oss.core import importers
from oss.core.contracts import LiteratureCorpus, PaperRecord

FIRST_PAGE = """互联网使用与政治信任：基于 CGSS2017 的实证分析

张三  李四
北京大学社会学系

摘要：本文利用 CGSS2017 数据……

doi:10.1234/shxyj.2021.02.003
"""

#: 参考文献段。**2026-09-25 补进夹具**：导入这条路上加了「是不是研究论文」的门
#: （`literature.looks_like_article`），判据是有没有参考文献列表。
#: 原来的夹具只有标题加两千遍「正文。」——**真论文不长这样**，
#: 真论文必带参考文献，而活动通讯、院系动态、法规原文必不带。
#: 夹具不带参考文献，等于在测一种现实里不存在的文件。
REFERENCES = "\n参考文献\n" + "\n".join(
    f"作者{i}、合作者{i}，{2000 + i}，《一个足够长的中文篇名{i}》，《某刊》第{i}期。"
    for i in range(12)
)

#: 够长才算「拿到了全文」——门槛在 `reading._MIN_FULLTEXT_CHARS`，防的是扫描件和只有版权页的 PDF。
LONG_PAGE = FIRST_PAGE + "正文。" * 2000 + REFERENCES

NO_DOI = """数字鸿沟的代际差异

王五
复旦大学新闻学院

摘要：本文考察……
"""

#: 查不到题录、**而且文件里也没印作者**（只有单位）。两份真文件就是这样。
NO_DOI_NO_AUTHOR = """变迁社会中的社会信任：存量与变化

（1．北京大学政府管理学院，北京 100871）

摘要：本文利用十二个全国性调查……
"""


# --------------------------------------------------------------- 纯函数

def test_首页印着的doi找得出来() -> None:
    assert importers.doi_in(FIRST_PAGE) == "10.1234/shxyj.2021.02.003"


@pytest.mark.parametrize("raw", [
    "https://doi.org/10.1234/abc.2021.001",
    "DOI: 10.1234/abc.2021.001",
    "10.1234/abc.2021.001",
])
def test_doi的几种印法都认(raw: str) -> None:
    assert importers.doi_in(f"标题\n\n{raw}\n") == "10.1234/abc.2021.001"


def test_没有doi就返回空串() -> None:
    assert importers.doi_in(NO_DOI) == ""


def test_参考文献里的doi不算() -> None:
    """只看首页。正文末尾参考文献里每条都有 DOI，抓到的会是**别人的**文章。"""
    body = "标题\n\n" + ("正文。" * 4000) + "\n参考文献\n[1] 某某. doi:10.9999/other.2019.7\n"
    assert importers.doi_in(body) == ""


def test_标题猜的是第一段像标题的行() -> None:
    assert importers.title_guess(FIRST_PAGE).startswith("互联网使用与政治信任")


# --------------------------------------------------------------- 导入

def _seed(tmp_path: Path) -> Path:
    run = tmp_path / "r"
    run.mkdir()
    LiteratureCorpus(records=[]).save(run / "literature.json")
    return run


#: 夹具的字节填充。**2026-09-25 补**：导入这条路加了「文件小到根本不是一份文献」
#: 的判据（HTTP 错误页实测 85–2516 字节），而夹具写的是几百字节的纯文本假 PDF——
#: **现实里不存在 200 字节的 PDF**。填充让文件大小接近真实，
#: 抽文那一侧按 `_PAD` 切掉，每一条既有断言的取值都不变。
_PAD = "<<<PAD>>>"


def _pdfs(tmp_path: Path, pages: dict[str, str]) -> Path:
    d = tmp_path / "refs"
    d.mkdir()
    for name, text in pages.items():
        # 内容无所谓，抽文本是打桩的；但**大小要像一份真文件**
        (d / name).write_bytes((text + _PAD + "0" * 40_000).encode("utf-8"))
    return d


@pytest.fixture
def stub(monkeypatch):
    """抽文本按文件名取回原文；DOI 反查只认一个编号；标题检索一律查不到。"""
    store: dict[str, str] = {}

    def _text(raw: bytes) -> str:
        return raw.decode("utf-8").split(_PAD)[0]   # 切掉只为撑大小的填充

    def _by_doi(doi: str, **kw):
        if doi == "10.1234/shxyj.2021.02.003":
            return PaperRecord(paper_id=doi, title="互联网使用与政治信任", year=2021,
                               authors=["张三", "李四"], doi=doi, venue="社会学研究")
        return None      # fetch_by_doi 查不到时返回 None（源坏了才抛 SourceUnavailable）

    monkeypatch.setattr("oss.core.reading._pdf_text", _text)
    monkeypatch.setattr("oss.core.literature.fetch_by_doi", _by_doi)
    monkeypatch.setattr("oss.core.literature.search_by_title", lambda t, **kw: None)
    return store


def test_第一层_有doi的进语料且元数据是查来的(tmp_path: Path, stub) -> None:
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"a.pdf": FIRST_PAGE})
    res = lit_api.import_file(str(d), run=str(run))
    corpus = LiteratureCorpus.load(run / "literature.json")
    assert len(corpus.records) == 1
    r = corpus.records[0]
    assert r.doi == "10.1234/shxyj.2021.02.003"
    assert r.venue == "社会学研究", "元数据该是反查来的，不是从正文猜的"
    assert res["data"]["tiers"]["doi"] == 1


def test_第三层_查不到的标UNVERIFIED但照样进语料(tmp_path: Path, stub) -> None:
    """**查不到不等于这篇不存在**——它是用户亲手下载的，必须进语料，只是标记存疑。

    文件里连作者都没印（只有单位）时落到最后一层：作者空着，篇名带 `[UNVERIFIED]`。
    """
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"b.pdf": NO_DOI_NO_AUTHOR})
    res = lit_api.import_file(str(d), run=str(run))
    corpus = LiteratureCorpus.load(run / "literature.json")
    assert len(corpus.records) == 1
    assert "[UNVERIFIED]" in corpus.records[0].title
    assert corpus.records[0].authors == []
    assert res["data"]["tiers"]["unverified"] == 1


def test_查不到题录但版面上印着作者_推作者_篇名仍存疑(tmp_path: Path, stub) -> None:
    """中文核心刊在 OpenAlex / Crossref 里基本查不到，前两层对它们几乎全废。
    版面上「题名 → 作者 → 摘要」是通例，按位置能把作者推出来。

    **但这一层只推作者、不核篇名**，所以篇名继续带 `[UNVERIFIED]`，
    而且那条「要人确认」的警告必须把这一层也算进去——否则一批标着存疑的记录
    会在「导入成功」里静默通过。
    """
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"b.pdf": NO_DOI})
    res = lit_api.import_file(str(d), run=str(run))
    corpus = LiteratureCorpus.load(run / "literature.json")
    assert corpus.records[0].authors == ["王五"]          # 「复旦大学新闻学院」是单位，不算
    assert "[UNVERIFIED]" in corpus.records[0].title
    assert res["data"]["tiers"]["layout"] == 1
    assert any("UNVERIFIED" in w for w in res["data"]["warnings"])


def test_全文落进精读目录_并且不冲掉已有的索引(tmp_path: Path, stub) -> None:
    """用户 PDF 的价值就在全文。`oss lit fetch` 也写 index.json，**得合并不能覆盖**。"""
    run = _seed(tmp_path)
    (run / "reading").mkdir()
    (run / "reading" / "index.json").write_text(json.dumps(
        [{"paper_id": "W1", "title": "早先抓的", "level": "fulltext", "chars": 9000, "path": "", "reason": ""}]),
        encoding="utf-8")
    lit_api.import_file(str(_pdfs(tmp_path, {"a.pdf": LONG_PAGE})), run=str(run))
    idx = json.loads((run / "reading" / "index.json").read_text(encoding="utf-8"))
    ids = {i["paper_id"] for i in idx}
    assert "W1" in ids, "早先抓的那篇被冲掉了"
    assert len(ids) == 2
    assert any(i["level"] == "fulltext" and i["paper_id"] != "W1" for i in idx)


def test_抽出来太短的不叫全文(tmp_path: Path, stub) -> None:
    """扫描件、只有版权页的 PDF 抽出来是几十个字。**标成全文，读书卡的来源层级核对就失效了**——
    卡里自报「全文」，核对器一看索引也写着全文，双双放过一张其实没读到原文的卡。"""
    run = _seed(tmp_path)
    lit_api.import_file(str(_pdfs(tmp_path, {"a.pdf": FIRST_PAGE})), run=str(run))
    idx = json.loads((run / "reading" / "index.json").read_text(encoding="utf-8"))
    assert idx[0]["level"] != "fulltext"
    assert "扫描件" in idx[0]["reason"] or "太短" in idx[0]["reason"]


def test_单个pdf文件也认(tmp_path: Path, stub) -> None:
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"a.pdf": FIRST_PAGE})
    res = lit_api.import_file(str(d / "a.pdf"), run=str(run))
    assert res["data"]["tiers"]["doi"] == 1


def test_题录导出文件那条老路没坏(tmp_path: Path) -> None:
    run = _seed(tmp_path)
    ris = tmp_path / "x.ris"
    ris.write_text("TY  - JOUR\nTI  - 老路还在\nAU  - 某某\nPY  - 2020\nER  - \n", encoding="utf-8")
    lit_api.import_file(str(ris), run=str(run))
    corpus = LiteratureCorpus.load(run / "literature.json")
    assert [r.title for r in corpus.records] == ["老路还在"]


# --------------------------------------------------------------- 命令行那一层

#: 上面十一项全在 api 层，**命令行这一层一项都没有**——于是 `oss lit import <PDF 目录>`
#: 一用就 `KeyError: 'next'`，而且 `[UNVERIFIED]` 警告被 `.get("warnings", [])` 整条吃掉，
#: 从 api 层怎么测都测不出来。功能是好的，路是断的，六篇真论文实跑一次才撞见。
#:
#: 根因不是少写了一个键，是**同一条命令的两条分支返回了不同的形状**。
#: 所以这里钉的是那个不变量，不是两个症状。


def _cli(*args: str):
    from typer.testing import CliRunner

    from oss.cli.main import app

    return CliRunner().invoke(app, list(args))


def test_命令行_导入pdf目录不崩(tmp_path: Path, stub) -> None:
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"a.pdf": LONG_PAGE})
    res = _cli("lit", "import", str(d), "--run", str(run))
    assert res.exit_code == 0, res.output + repr(res.exception)
    assert "接着跑" in res.output, f"没提示下一步该筛：{res.output}"


def test_命令行_存疑那条警告要打出来(tmp_path: Path, stub) -> None:
    """`[UNVERIFIED]` 是这条路最要紧的一句话——元数据是从正文猜的，引用前要人确认。
    它被静默吞掉时，用户看到的是一条干干净净的「导入成功」。"""
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"没有doi.pdf": NO_DOI})
    res = _cli("lit", "import", str(d), "--run", str(run))
    assert res.exit_code == 0, res.output + repr(res.exception)
    assert "UNVERIFIED" in res.output, f"存疑警告没打出来：{res.output}"


def test_两条分支返回同样的键(tmp_path: Path, stub) -> None:
    """**这是真正的判据。**命令行对两条分支一视同仁地取 `warnings` 与 `next`，
    那两条分支就必须给同样的键。缺哪个键、将来谁再加一条分支，这里都会响。"""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    run_pdf, run_ris = _seed(a), _seed(b)
    d = _pdfs(tmp_path, {"a.pdf": LONG_PAGE})
    ris = tmp_path / "x.ris"
    ris.write_text("TY  - JOUR\nTI  - 老路还在\nAU  - 某某\nPY  - 2020\nER  - \n", encoding="utf-8")

    pdf_keys = set(lit_api.import_file(str(d), run=str(run_pdf))["data"])
    ris_keys = set(lit_api.import_file(str(ris), run=str(run_ris))["data"])

    shared = {"run", "path", "format", "camp", "n_added", "n_skipped",
              "n_records", "warnings", "next", "exit_code"}
    assert shared <= pdf_keys, f"PDF 分支缺：{shared - pdf_keys}"
    assert shared <= ris_keys, f"题录分支缺：{shared - ris_keys}"


# ─────────────────────────── 是不是研究论文这道门（2026-09-25）

#: 活动通讯长什么样。**OpenAlex 真的会把这种当文献返回**——实测按「宗教 捐赠」
#: 检索，19 条里 17 条是这类：感谢信、档案接收通知、捐赠仪式报道。
BULLETIN = ("十万大米送乡亲 道德风尚洒人间——南昌万寿宫、香港蓬瀛仙馆捐赠大米活动散记。"
            * 200)


def test_活动通讯被门拦下_不进语料(tmp_path: Path, stub) -> None:
    """门接上没有。**这是这道门存在的全部理由**：中文文献只能走网页搜索，
    而网页搜索必然捞回通讯。"""
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"good.pdf": LONG_PAGE, "bulletin.pdf": BULLETIN})
    res = lit_api.import_file(str(d), run=str(run))
    assert res["data"]["n_added"] == 1, "只有那篇真论文该进去"
    assert res["data"]["n_rejected"] == 1
    assert "bulletin.pdf" in res["data"]["rejected"][0]
    titles = [r.title for r in LiteratureCorpus.load(run / "literature.json").records]
    assert not any("大米" in t for t in titles), f"通讯混进语料了：{titles}"


def test_拒收的理由要逐条留下(tmp_path: Path, stub) -> None:
    """**被拒的不能只报个数。**真论文被误拒时，用户得看得见是哪一份、为什么，
    才追得回来——否则这道门就成了静默丢文献的地方。"""
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"bulletin.pdf": BULLETIN})
    res = lit_api.import_file(str(d), run=str(run))
    reason = res["data"]["rejected"][0]
    assert "bulletin.pdf" in reason and "参考文献" in reason
    assert any("要是其中有真论文" in w for w in res["data"]["warnings"]), \
        "警告里要留一条找回来的路"


def test_抽不出正文的照收_但要写进语料的notes(tmp_path: Path, stub) -> None:
    """扫描件、下载截断、解析失败——**判不了不等于判它不是**，照收。

    但要写进 `corpus.notes` 而不是只写命令行警告：写论文那一步读的是 notes，
    这几篇做不出读书卡，局限那节得照实说。
    """
    run = _seed(tmp_path)
    d = _pdfs(tmp_path, {"scan.pdf": "封面"})
    res = lit_api.import_file(str(d), run=str(run))
    assert res["data"]["n_rejected"] == 0, "抽不出正文时不许拒"
    assert res["data"]["n_undecided"] == 1
    notes = LiteratureCorpus.load(run / "literature.json").notes
    assert any("判不了" in n and "局限" in n for n in notes), notes


def test_网页全文也能导入(tmp_path: Path, stub) -> None:
    """《社会》《南方经济》《公共行政评论》的全文是网页不是 PDF。
    只认 `.pdf` 等于把这批中文核心刊全挡在外面。"""
    run = _seed(tmp_path)
    d = tmp_path / "web"
    d.mkdir()
    body = ("<html><head><style>p{}</style></head><body>"
            + FIRST_PAGE.replace("\n", "<br>")
            + "<p>正文。</p>" * 900
            + REFERENCES.replace("\n", "<br>")
            + "</body></html>")
    (d / "shehui.htm").write_text(body, encoding="utf-8")
    res = lit_api.import_file(str(d), run=str(run))
    assert res["data"]["n_added"] == 1, res["data"]
    assert res["data"]["n_rejected"] == 0


def test_网页里的脚本样式不算正文(tmp_path: Path, stub) -> None:
    """`<script>` 里几千字的 JS 会把「抽出多少字」撑起来，
    让一个本该判「抽不出正文」的页面看起来正文很长。"""
    from oss.api.lit import _doc_text

    f = tmp_path / "x.htm"
    f.write_text("<html><script>" + "var a=1;" * 2000 + "</script><body>短</body></html>",
                 encoding="utf-8")
    assert len(_doc_text(f)) < 200, "脚本没剥干净"


def test_抓取失败与判不了要分开(tmp_path: Path, stub) -> None:
    """**端到端真跑逮到的。**403 错误页只有 162 字节、抽出 49 个字，
    原先被当成「判不了」照收，于是七个错误页全进了语料。

    错误页不是「我看不出它是不是论文」，是**它压根不是一份文献**。
    扫描件也抽不出字，但它是真文件（几百 KB 到几 MB）——那种才该放行。
    判据是「文件大小 + 抽出多少字」两个都算得出的事实。
    """
    run = _seed(tmp_path)
    d = tmp_path / "mix"
    d.mkdir()
    (d / "err403.htm").write_text("<html><body>403 Forbidden</body></html>", encoding="utf-8")
    # 扫描件：**文件几十 KB，抽出来只有封面那两个字**——这正是该放行的那一类
    (d / "scan.pdf").write_bytes(("封面" + _PAD + "0" * 40_000).encode("utf-8"))
    res = lit_api.import_file(str(d), run=str(run))
    assert res["data"]["n_broken"] == 1, res["data"]
    assert "err403" in res["data"]["broken"][0]
    assert res["data"]["n_undecided"] == 1, "大文件抽不出字仍该是「判不了」，放行"
    assert res["data"]["n_added"] == 1, "只有那份大文件该进语料"
    titles = [r.title for r in LiteratureCorpus.load(run / "literature.json").records]
    assert not any("403" in t for t in titles), f"错误页混进语料了：{titles}"


def test_没下下来的不算被门拒(tmp_path: Path, stub) -> None:
    """混进 `rejected` 里，人会以为是门判错了，跑去调阈值——
    而真正该做的是重下那一份。"""
    run = _seed(tmp_path)
    d = tmp_path / "e"
    d.mkdir()
    (d / "err.htm").write_text("<html>404</html>", encoding="utf-8")
    res = lit_api.import_file(str(d), run=str(run))
    assert res["data"]["n_rejected"] == 0 and res["data"]["n_broken"] == 1
    assert any("重下" in w for w in res["data"]["warnings"])


def test_线性化PDF能算出下载截没截断(tmp_path: Path) -> None:
    """**「可能是扫描件、下载截断或解析失败」三选一，对用户没用**——三者的处置完全不同：
    截断了重下就好，扫描件重下也没用。

    线性化 PDF 在文件头里自报全长（`<</Linearized 1/L 1052261/...>>`），
    跟实际大小一比就知道。真跑时 `jpa.sysu.edu.cn` 下来的八份里有两份认得出来。
    """
    from oss.api.lit import truncated_pdf

    head = b"%PDF-1.6\r\n330 0 obj <</Linearized 1/L 1052261/O 332/E 388254/N 18>>\r\n"
    short = tmp_path / "short.pdf"
    short.write_bytes(head + b"0" * 1000)
    assert truncated_pdf(short) == 1052261

    full = tmp_path / "full.pdf"
    full.write_bytes(head + b"0" * 1_052_261)
    assert truncated_pdf(full) == 0, "下全了就不该报截断"

    plain = tmp_path / "plain.pdf"
    plain.write_bytes(b"%PDF-1.4\r\n" + "没有线性化字典".encode("utf-8"))
    assert truncated_pdf(plain) == 0, "没自报全长的不能瞎猜"

    assert truncated_pdf(tmp_path / "x.htm") == 0, "网页没有这回事"


def test_截断的报法要跟扫描件分开(tmp_path: Path, stub) -> None:
    run = _seed(tmp_path)
    d = tmp_path / "t"
    d.mkdir()
    head = b"%PDF-1.6\r\n1 0 obj <</Linearized 1/L 900000/O 3/E 1/N 9>>\r\n"
    (d / "cut.pdf").write_bytes(head + "封面".encode("utf-8") + _PAD.encode() + b"0" * 40_000)
    res = lit_api.import_file(str(d), run=str(run))
    assert res["data"]["n_undecided"] == 1
    why = res["data"]["undecided"][0]
    assert "下载截断" in why and "重下" in why, why
    assert "扫描件" not in why.split("不是扫描件")[0], "别再说可能是扫描件"


def test_fetch不许冲掉import写的索引(tmp_path: Path, stub, monkeypatch) -> None:
    """**真跑逮到的**：`oss lit import` 先把 12 篇中文全文落进 `reading/`，
    再跑 `oss lit fetch`，索引里只剩 fetch 的 6 篇——磁盘上 18 个 txt，索引里 6 条。

    后果是 `oss lit cards` 按索引校验局限引文，那 12 篇全文的卡全判成
    「自报全文、实际没拿到」。**冲掉之后好卡会被判成坏卡。**

    `merge_index` 这个函数当初就是为这件事写的，注释里写着「fetch 是整批重写……
    不合并就互相冲掉」——**但只在导入那一侧调了它**。这条测的是另一个方向。
    """
    from oss.core import reading as R

    run = _seed(tmp_path)
    lit_api.import_file(str(_pdfs(tmp_path, {"mine.pdf": LONG_PAGE})), run=str(run))
    before = {i["paper_id"] for i in json.loads(
        (run / "reading" / "index.json").read_text(encoding="utf-8"))}
    assert before, "导入这一步本来就该写索引"

    # fetch 拿到一篇别的；不联网，直接喂一条 FetchItem 走它写索引那一段
    R.merge_index(run / "reading", [R.FetchItem(
        paper_id="W_new", title="抓来的", level=R.LEVEL_ABSTRACT, chars=500,
        path=str(run / "reading" / "W_new.txt"), reason="")])
    after = {i["paper_id"] for i in json.loads(
        (run / "reading" / "index.json").read_text(encoding="utf-8"))}
    assert before <= after, f"导入的条目被冲掉了：少了 {before - after}"
    assert "W_new" in after


def test_fetch这条路本身调的是合并不是覆盖() -> None:
    """上一条测的是 `merge_index` 的行为，这一条钉的是 **`fetch` 真的调了它**——
    原先的 bug 正是「函数写对了、调用方没调」。"""
    import inspect

    from oss.core import reading as R

    src = inspect.getsource(R.fetch)
    assert "merge_index" in src, "fetch 没调 merge_index，索引又会被整批覆盖"
    assert 'index.json").write_text' not in src, "fetch 还在直接覆盖索引"
