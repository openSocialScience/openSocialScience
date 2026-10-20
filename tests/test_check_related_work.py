"""相关工作回验脚本的测试。**故意写坏一条，对应检查必须变红**——那是回验的回验。

测试里的条目全是虚构的（Toy Lab、2501.00001），网络一律走 MockTransport，不真联网。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import unicodedata
from pathlib import Path

import httpx
import pytest

from oss.core import literature
from oss.core.contracts import PaperRecord

ROOT = Path(__file__).resolve().parents[1]


def _mod():
    spec = importlib.util.spec_from_file_location("check_related_work", ROOT / "scripts" / "check_related_work.py")
    m = importlib.util.module_from_spec(spec)
    # 脚本里有 dataclass，又用了 from __future__ import annotations——不先登记，收集阶段就崩
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


C = _mod()

SOURCE = ("We present Toy Lab, an autonomous framework that completes the entire research process. "
          "In our ablation, removing the reviewer agent lowered paper scores from 4.1 to 3.2 on our benchmark. "
          "The system uses a multi-\nagent design with separate roles.")


def _entry(**kw) -> dict:
    e = {
        "id": "toylab",
        "category": "A",
        "title": "Toy Lab: Autonomous Research with Agents",
        "year": 2025,
        "ids": {"doi": "", "arxiv": "2501.00001", "url": ""},
        "source_level": "fulltext",
        "what": "端到端的研究助手",
        "claims": "查不到",
        "evaluation": "消融审稿智能体",
        "overlap": [{"m": "M7", "level": "部分", "note": "有审稿角色，没有互盲"}],
        "borrow": "审稿智能体的消融做法",
        "techniques": [{
            "name": "审稿智能体", "stage": "审稿", "what": "单设审稿角色",
            "measured_on": "机器学习论文", "evidence_level": "强",
            "evidence": {"quote": "removing the reviewer agent lowered paper scores from 4.1 to 3.2", "where": "§5"},
            "fits": ["W7"], "cost": "中"}],
        "evidence": [
            {"supports": "what", "quote": "an autonomous framework that completes the entire research process",
             "where": "Abstract"},
            {"supports": "evaluation", "quote": "In our ablation, removing the reviewer agent", "where": "§5"},
            {"supports": "overlap:M7", "quote": "multi-agent design with separate roles", "where": "§3"},
        ],
        "queries": ["autonomous research agent"],
    }
    e.update(kw)
    return e


# ══════════════════════════════ 一、格式


def test_格式齐全的条目没有问题() -> None:
    assert C.check_structure(_entry()) == []


def test_空栏会红_查不到要明写() -> None:
    assert any("borrow" in p for p in C.check_structure(_entry(borrow="")))


def test_某栏没有引句撑着会红() -> None:
    e = _entry()
    e["evidence"] = [x for x in e["evidence"] if x["supports"] != "what"]
    assert any("what 这一栏没有引句" in p for p in C.check_structure(e))


def test_overlap每个编号都要有引句_部分要写差在哪() -> None:
    e = _entry(overlap=[{"m": "M7", "level": "部分", "note": "有审稿角色，没有互盲"},
                        {"m": "M9", "level": "有"}])
    assert any("M9" in p and "引句" in p for p in C.check_structure(e))
    e = _entry(overlap=[{"m": "M7", "level": "部分"}])
    assert any("差在哪" in p for p in C.check_structure(e))


def test_编号不认识会红() -> None:
    assert any("M1–M18" in p for p in C.check_structure(_entry(overlap=[{"m": "M99", "level": "有"}])))
    e = _entry()
    e["techniques"][0]["fits"] = ["W42"]
    assert any("W1–W9" in p for p in C.check_structure(e))


def test_引句太短或带省略号会红() -> None:
    e = _entry()
    e["evidence"][0]["quote"] = "we propose"
    assert any("太短" in p for p in C.check_structure(e))
    e = _entry()
    e["evidence"][0]["quote"] = "an autonomous framework … the entire research process"
    assert any("省略号" in p for p in C.check_structure(e))


def test_判强却没引原文会红() -> None:
    e = _entry()
    e["techniques"][0]["evidence"] = {}
    assert any("没引撑得住" in p for p in C.check_structure(e))


def test_系统类的本事对不上短板会红() -> None:
    e = _entry()
    e["techniques"][0]["fits"] = []
    assert any("对不上" in p for p in C.check_structure(e))


def test_方法论类要写适用范围与落点() -> None:
    e = _entry(category="E2")
    e["techniques"][0]["fits"] = []
    assert any("applies_to" in p for p in C.check_structure(e))
    e["techniques"][0]["applies_to"] = {"disciplines": ["sociology"], "research_types": ["因果"]}
    e["techniques"][0]["landing"] = "随便写"
    assert any("landing" in p for p in C.check_structure(e))
    e["techniques"][0]["landing"] = "审稿侧重"
    assert C.check_structure(e) == []


def test_取值不在清单里会红() -> None:
    assert any("category" in p for p in C.check_structure(_entry(category="Z")))
    assert any("source_level" in p for p in C.check_structure(_entry(source_level="全文")))
    assert any("level 只认" in p for p in C.check_structure(_entry(overlap=[{"m": "M7", "level": "没看到"}])))
    for k, bad in (("evidence_level", "很强"), ("cost", "很大"), ("stage", "别的")):
        e = _entry()
        e["techniques"][0][k] = bad
        assert any(k in p for p in C.check_structure(e)), k


def test_标识一个都没有会红() -> None:
    assert any("ids" in p for p in C.check_structure(_entry(ids={"doi": "", "arxiv": "", "url": ""})))
    assert any("ids 要写成对象" in p for p in C.check_structure(_entry(ids="2501.00001")))


def test_年份要是整数() -> None:
    for bad in ("2025", "2025-01", "查不到", None, True):
        assert any("year" in p for p in C.check_structure(_entry(year=bad))), bad


def test_引句要写出处_ASCII省略号也算() -> None:
    e = _entry()
    e["evidence"][0]["where"] = ""
    assert any("where" in p for p in C.check_structure(e))
    e = _entry()
    e["evidence"][0]["quote"] = "an autonomous framework ... the entire research process"
    assert any("省略号" in p for p in C.check_structure(e))


def test_本事的必填栏与中档也要引原文() -> None:
    for k in ("name", "what", "measured_on", "cost"):
        e = _entry()
        e["techniques"][0][k] = ""
        assert any(f"{k} 是空的" in p for p in C.check_structure(e)), k
    e = _entry()
    e["techniques"][0]["evidence_level"] = "中"
    e["techniques"][0]["evidence"] = {"quote": "", "where": "§5"}
    assert any("没引撑得住" in p for p in C.check_structure(e))
    e = _entry()
    e["techniques"][0]["evidence"]["quote"] = "too short"
    assert any("太短" in p for p in C.check_structure(e))


def test_方法论的学科与研究类型要认得() -> None:
    e = _entry(category="E1")
    e["techniques"][0].update(landing="门", applies_to={"disciplines": ["history"], "research_types": ["因果"]})
    assert any("学科「history」" in p for p in C.check_structure(e))
    e["techniques"][0]["applies_to"] = {"disciplines": ["sociology"], "research_types": ["预测"]}
    assert any("研究类型「预测」" in p for p in C.check_structure(e))
    e["techniques"][0]["applies_to"] = {"disciplines": "sociology", "research_types": ["因果"]}
    got = C.check_structure(e)
    assert any("要写成列表" in p for p in got) and not any("学科「s」" in p for p in got)


def test_字符串写成列表的栏只报一句不按字拆() -> None:
    e = _entry()
    e["techniques"][0]["fits"] = "W7"
    got = C.check_structure(e)
    assert any("fits 要写成列表" in p for p in got) and not any("「W」" in p for p in got)


def test_supports对不上或overlap重复会红() -> None:
    e = _entry()
    e["evidence"].append({"supports": "overlap:M3", "quote": "multi-agent design with separate roles", "where": "§3"})
    assert any("supports「overlap:M3」" in p for p in C.check_structure(e))
    e = _entry(overlap=[{"m": "M7", "level": "有"}, {"m": "M7", "level": "部分", "note": "x"}])
    assert any("列了两次" in p for p in C.check_structure(e))


def test_形状不对只报问题不崩() -> None:
    bad_shapes = [
        "not an entry",
        _entry(evidence="a string"),
        _entry(evidence=["a string"]),
        _entry(techniques={"name": "x"}),
        _entry(overlap=["M7"]),
    ]
    e = _entry()
    e["techniques"][0]["evidence"] = "a string"
    bad_shapes.append(e)
    e = _entry()
    e["evidence"][0]["supports"] = ["what", "claims"]
    bad_shapes.append(e)
    for b in bad_shapes:
        assert C.check_structure(b), b


# ══════════════════════════════ 二、引句比对


def test_规范化抹平大小写空白连字弯引号与Markdown() -> None:
    assert C.quote_found("It\u2019s the \ufb01nal   step of the pipeline", "**it's** the final step of the pipeline")
    assert C.quote_found("see the docs for the full list", "see the [docs](https://x.org) for the full list")


def test_去掉HTML标签但不误伤小于号() -> None:
    assert C.quote_found("由团队打造，覆盖完整工作流程的一整套技能", "<sub>由团队打造，覆盖完整工作流程的一整套技能</sub>")
    assert C.quote_found("p < 0.05 and x > 3 in all models", "we find p < 0.05 and x > 3 in all models")


def test_PDF断行连字符两种写法都认() -> None:
    text = "the system uses a multi-\nagent design with separate roles"
    assert C.quote_found("multi-agent design with separate roles", text)
    assert C.quote_found("multiagent design with separate roles", text)


def test_原文里没有的话找不到() -> None:
    assert not C.quote_found("removing the critic agent lowered scores", SOURCE)
    assert not C.quote_found("", SOURCE)


def test_公式里的小于号不会吞掉后面的正文() -> None:
    text = ("the loss when y<t holds is bounded.\n" + "filler line.\n" * 50
            + "later sentence says the agent writes the paper itself > done")
    assert C.quote_found("later sentence says the agent writes the paper itself", text)


def test_数字与词被截了一截不算找到() -> None:
    assert not C.quote_found("lowered paper scores from 4.1 to 3.2", "lowered paper scores from 4.1 to 3.25 overall")
    assert not C.quote_found("5% of the generated papers were accepted", "15% of the generated papers were accepted")
    assert not C.quote_found("significant differences between the two arms",
                             "insignificant differences between the two arms")
    assert C.quote_found("lowered paper scores from 4.1 to 3.2", "lowered paper scores from 4.1 to 3.2. Overall")


def test_有意义的符号不抹掉() -> None:
    assert not C.quote_found("improves accuracy by >10% on the benchmark", "improves accuracy by 10% on the benchmark")
    assert not C.quote_found("ranked #1 on the public leaderboard", "ranked 1 on the public leaderboard")
    assert not C.quote_found("values ranged from 15 to 20 in all runs", "values ranged from 1-\n5 to 20 in all runs")


def test_PDF与网页里的特殊字符() -> None:
    assert C.quote_found("the coefficient is -0.35 in the main model", "the coefficient is \u22120.35 in the main model")
    assert C.quote_found("an automated reviewer agent grades the paper",
                         "an auto\u00admated reviewer agent grades the pa\u00ad per")
    assert C.quote_found("Tom & Jerry wrote the benchmark together", "Tom &amp; Jerry wrote the bench\u200bmark together")


def test_中文引句不受换行与中英空格影响() -> None:
    text = "**实证研究**的完整工作流：从数据清洗\n到顶刊投稿，全部由 AI 智能体完成"
    assert C.quote_found("实证研究的完整工作流：从数据清洗到顶刊投稿，全部由AI智能体完成", text)
    assert not C.quote_found("实证研究的完整工作流：从数据收集到顶刊投稿，全部由AI智能体完成", text)


def test_引句长度按规范形算() -> None:
    e = _entry()
    e["evidence"][0]["quote"] = '<a href="https://github.com/o/r">GitHub</a>'
    assert any("太短" in p for p in C.check_structure(e))


def test_连字符连着的否定与负号不能丢() -> None:
    assert not C.quote_found("significant differences across all specifications",
                             "non-significant differences across all specifications")
    assert not C.quote_found("significant differences across all specifications",
                             "non-\nsignificant differences across all specifications")
    for minus in ("-", chr(0x2212)):
        assert not C.quote_found("0.35 standard deviations lower in the treated group",
                                 f"was {minus}0.35 standard deviations lower in the treated group")
    assert C.quote_found("0.35 effect size in the pooled sample",
                         "|--:|:--|\n| 0.35 | effect size in the pooled sample |")


def test_PDF行首的大于号与井号() -> None:
    text = "the p-value was\n> 0.05 in both samples"
    assert C.quote_found("the p-value was > 0.05 in both samples", text)
    assert not C.quote_found("the p-value was 0.05 in both samples", text)
    assert C.quote_found("Installation takes thirty seconds on a laptop",
                         "## Installation takes thirty seconds on a laptop")


def test_强调记号删掉不留空格() -> None:
    assert C.quote_found("readers: see the guide for all details", "readers**:** see the guide for all details")


def test_白名单标签也不跨行() -> None:
    text = "if a<b then\nlater sentence says the agent writes the paper itself\n>"
    assert C.quote_found("later sentence says the agent writes the paper itself", text)


def test_数字后面接小数不算找到() -> None:
    assert not C.quote_found("scores in the treated group rose from 4 to 3",
                             "scores in the treated group rose from 4 to 3.5 points")


def test_中文去空白时不把英文数字粘起来() -> None:
    assert not C.quote_found("样本分为 15 两组进行比较分析", "样本分为 1 5 两组进行比较分析")


def test_守门规则各有一条会红的测试() -> None:
    assert not C.quote_found("the encoder in our model is a transformer",
                             "the encoder in our model is a transformer-based network")
    assert C.quote_found("ranked #1 among all the systems tested", "ranked\n#1 among all the systems tested")
    assert C.quote_found("中文说明(see docs)在这个版本里写得很清楚", "中文说明( see docs )在这个版本里写得很清楚")
    assert C.quote_found("the agent writes the whole paper itself",
                         "if a<b " + "x " * 300 + "the agent writes the whole paper itself >")
    assert not C.quote_found("5 points higher in the treated group", "was 3.5 points higher in the treated group")
    assert C.quote_found("the multiagent design with separate roles",
                         "the multi<b>agent</b> design with separate roles")
    assert C.quote_found("the multi-agent system is conceptually simple and robust",
                         "the multi-\nagent system is concep-\ntually simple and robust")


def test_引句写成列表会红() -> None:
    e = _entry()
    e["evidence"][0]["quote"] = ["an autonomous framework that completes", "the entire research process"]
    assert any("quote 要写成一个字符串" in p for p in C.check_structure(e))


# ══════════════════════════════ 三、取原文


def _client(routes: dict[str, tuple[int, str, bytes]]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        status, ctype, body = routes.get(str(req.url), (404, "text/plain", b""))
        return httpx.Response(status, headers={"content-type": ctype}, content=body)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_arXiv编号三种写法都认_别的DOI不认() -> None:
    assert C.arxiv_id("2408.06292") == "2408.06292"
    assert C.arxiv_id("arXiv:2408.06292v2") == "2408.06292v2"
    assert C.arxiv_id("10.48550/arXiv.2408.06292") == "2408.06292"
    assert C.arxiv_id("10.1177/00031224211004187") == ""


def test_编号写成网址或带前缀也认() -> None:
    assert C.arxiv_id("https://arxiv.org/abs/2408.06292") == "2408.06292"
    assert C.arxiv_id("https://arxiv.org/pdf/2408.06292v3.pdf") == "2408.06292v3"
    assert C.arxiv_id("https://doi.org/10.48550/arXiv.2408.06292") == "2408.06292"
    for doi in ("https://doi.org/10.1000/toy", "doi:10.1000/toy", "http://dx.doi.org/10.1000/toy"):
        assert C.locations(_entry(ids={"doi": doi})) == ["https://doi.org/10.1000/toy"], doi
    assert C.locations(_entry(ids={"arxiv": "2501.00001", "doi": "10.1000/toy"})) == [
        "https://arxiv.org/pdf/2501.00001", "https://arxiv.org/abs/2501.00001", "https://arxiv.org/html/2501.00001"]


def test_GitHub仓库转raw_README_blob转raw文件() -> None:
    assert C.github_raw("https://github.com/o/r") == [
        "https://raw.githubusercontent.com/o/r/HEAD/README.md",
        "https://raw.githubusercontent.com/o/r/HEAD/readme.md"]
    assert C.github_raw("https://github.com/o/r/blob/main/docs/x.md") == [
        "https://raw.githubusercontent.com/o/r/main/docs/x.md"]
    assert C.github_raw("https://example.org/o/r") == []


def test_原文位置按顺序_本地到arXiv到GitHub到网址到DOI() -> None:
    e = _entry(ids={"local": "skill reference/x/README.md", "arxiv": "2501.00001",
                    "url": "https://github.com/o/r", "doi": ""})
    assert C.locations(e) == [
        "skill reference/x/README.md",
        "https://arxiv.org/pdf/2501.00001",
        "https://arxiv.org/abs/2501.00001",
        "https://arxiv.org/html/2501.00001",
        "https://raw.githubusercontent.com/o/r/HEAD/README.md",
        "https://raw.githubusercontent.com/o/r/HEAD/readme.md",
        "https://github.com/o/r"]
    assert C.locations(_entry(ids={"doi": "10.1000/toy"})) == ["https://doi.org/10.1000/toy"]


def test_网页抽文字并缓存_第二次不联网(tmp_path) -> None:
    page = ("<html><script>var x=1;</script><p>Toy Lab &amp; friends</p><p>" + SOURCE + "</p></html>").encode()
    got = C.fetch_text("https://x.org/p", _client({"https://x.org/p": (200, "text/html", page)}), tmp_path)
    assert "Toy Lab & friends" in got.text and "var x" not in got.text
    offline = httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail(f"不该再联网：{req.url}")))
    assert "Toy Lab & friends" in C.fetch_text("https://x.org/p", offline, tmp_path).text


def test_网页转文字照浏览器的样子() -> None:
    text = C.html_text("<p>We use a multi<span>agent</span> design<em>,</em> not a pipeline.</p><p>Next paragraph.</p>")
    assert "multiagent design, not a pipeline." in text
    assert "pipeline.\n\nNext" in text


def test_只靠脚本显示内容的网页不算原文(tmp_path) -> None:
    url = "https://x.org/app"
    page = b"<html><body><div id=app>Loading...</div><script>boot()</script></body></html>"
    got = C.fetch_text(url, _client({url: (200, "text/html", page)}), tmp_path)
    assert got.text == "" and "跑脚本" in got.why
    assert not list(tmp_path.glob("*.txt"))


def test_空缓存当没有_写完不留临时文件(tmp_path) -> None:
    url = "https://x.org/p"
    (tmp_path / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.txt").write_text("", encoding="utf-8")
    page = ("<p>" + SOURCE + "</p>").encode()
    got = C.fetch_text(url, _client({url: (200, "text/html", page)}), tmp_path)
    assert "Toy Lab" in got.text
    assert not list(tmp_path.glob("*.tmp"))


def test_拿不到时说清原因_带状态码(tmp_path) -> None:
    got = C.fetch_text("https://x.org/gone", _client({}), tmp_path)
    assert got.text == "" and got.status == 404 and "HTTP 404" in got.why


def test_网络出错只说清原因(tmp_path) -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=req)
    got = C.fetch_text("https://x.org/slow", httpx.Client(transport=httpx.MockTransport(boom)), tmp_path)
    assert got.text == "" and "ConnectTimeout" in got.why


def test_本地副本只认参考目录(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ROOT", tmp_path)
    ok = tmp_path / "skill reference" / "x" / "README.md"
    ok.parent.mkdir(parents=True)
    ok.write_text(SOURCE, encoding="utf-8")
    for d in ("runs", "docs"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "fake.md").write_text(SOURCE, encoding="utf-8")
    client = _client({})
    assert "Toy Lab" in C.fetch_text("skill reference/x/README.md", client, tmp_path / "cache").text
    for bad in (str(ok), "runs/fake.md", "docs/fake.md", "skill reference/../runs/fake.md"):
        got = C.fetch_text(bad, client, tmp_path / "cache")
        assert got.text == "" and "本地路径不认" in got.why, bad


def test_PDF只留横排的行_竖排水印不插进正文() -> None:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "We present an autonomous framework for research.", fontsize=11)
    page.insert_text((20, 400), "arXiv:2501.00001v1 [cs.AI] 2 Jan 2025", fontsize=11, rotate=90)
    text = C.pdf_text(doc.tobytes())
    assert "autonomous framework" in text and "cs.AI" not in text


def test_PDF响应走PDF抽取(tmp_path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "We present an autonomous framework for research.", fontsize=11)
    pdf = doc.tobytes()
    routes = {"https://x.org/a.pdf": (200, "application/pdf", pdf),
              "https://x.org/b": (200, "application/octet-stream", pdf)}
    for url in routes:
        assert "autonomous framework" in C.fetch_text(url, _client(routes), tmp_path).text, url


def test_坏PDF不崩() -> None:
    assert C.pdf_text(b"%PDF-1.4 this is not really a pdf").strip() == ""


def test_arXiv编号只写在网址里也认() -> None:
    assert C.arxiv_id("https://huggingface.co/papers/2408.06292") == "2408.06292"
    assert C.arxiv_id("arXiv 2408.06292") == "2408.06292"
    assert C.arxiv_id("2408.062921") == ""
    for url in ("https://arxiv.org/abs/2501.00001", "https://huggingface.co/papers/2501.00001"):
        assert C.locations(_entry(ids={"url": url}))[:3] == [
            "https://arxiv.org/pdf/2501.00001", "https://arxiv.org/abs/2501.00001",
            "https://arxiv.org/html/2501.00001"], url


def test_本地PDF与网页按类型抽文字(tmp_path, monkeypatch) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    monkeypatch.setattr(C, "ROOT", tmp_path)
    folder = tmp_path / "skill reference" / "x"
    folder.mkdir(parents=True)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "We present an autonomous framework for research.", fontsize=11)
    (folder / "slides.pdf").write_bytes(doc.tobytes())
    (folder / "page.html").write_text("<p>We use a multi<span>agent</span> design.</p>", encoding="utf-8")
    client = _client({})
    assert "autonomous framework" in C.fetch_text("skill reference/x/slides.pdf", client, tmp_path / "c").text
    assert "multiagent design." in C.fetch_text("skill reference/x/page.html", client, tmp_path / "c").text


def test_别处网址里像编号的字样不算() -> None:
    assert C._arxiv_of({"url": "https://github.com/u/arxiv2408.06292-reproduction"}) == ""
    assert C._arxiv_of({"url": "https://huggingface.co/papers/2501.00001"}) == "2501.00001"
    assert C._arxiv_of({"arxiv": "2501.00001", "url": "https://huggingface.co/papers/2408.06292"}) == "2501.00001"


# ══════════════════════════════ 四、标识

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>{title}</title><published>{year}-01-02T00:00:00Z</published><summary>{summary}</summary></entry></feed>"""
ATOM_EMPTY = '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
API = "https://export.arxiv.org/api/query?id_list=2501.00001"


def _atom(title: str = "Toy Lab: Autonomous\n  Research with Agents", year: int = 2025, summary: str = "") -> bytes:
    return ATOM.format(title=title, year=year, summary=summary).encode()


def test_标题比对不计较副标题标点大小写() -> None:
    assert C.title_matches("Toy Lab: Autonomous Research with Agents",
                           "TOY LAB — autonomous research with agents (extended version)")
    assert not C.title_matches("Toy Lab: Autonomous Research with Agents", "A Completely Different Paper")
    assert C.title_matches("实证研究技能大全", "实证研究技能大全（中文版）")


def test_登记处不存副标题或arXiv改了标题也算对上() -> None:
    assert C.title_matches("Counterfactuals and Causal Inference: Methods and Principles for Social Research",
                           "Counterfactuals and Causal Inference")
    assert C.title_matches("Mostly Harmless Econometrics: An Empiricist's Companion", "Mostly Harmless Econometrics")
    assert C.title_matches("Difference-in-Differences with Multiple Time Periods and an Application on the Minimum Wage",
                           "Difference-in-Differences with Multiple Time Periods")


def test_只靠虚词凑出来的不算对上() -> None:
    assert not C.title_matches("The Logic of the Survey", "The Logic of Collective Action")
    assert not C.title_matches("A Theory of Social Research", "A Theory of Justice")
    assert not C.title_matches("Causal Inference: What If", "Causality: Models, Reasoning, and Inference")


def test_arXiv查得到且对得上就没问题(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    assert C.check_ids(_entry(), _client({API: (200, "application/atom+xml", _atom())})) == ([], [], [], [])


def test_arXiv查不到或标题对不上会红_年份差得多只记一笔(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    bad, _, _, _ = C.check_ids(_entry(), _client({API: (200, "application/atom+xml", ATOM_EMPTY.encode())}))
    assert any("查不到" in b for b in bad)
    bad, _, _, _ = C.check_ids(_entry(), _client({API: (200, "application/atom+xml", _atom("Another Paper Entirely"))}))
    assert any("标题对不上" in b for b in bad)
    bad, unk, notes, _ = C.check_ids(_entry(), _client({API: (200, "application/atom+xml", _atom(year=2021))}))
    assert bad == [] and unk == [] and any("年份差得多" in n for n in notes)


def test_arXiv接口不通或返回限流页只算未核(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    for status, ctype, body in ((503, "text/plain", b""), (200, "text/plain", b"Rate exceeded."),
                                (200, "text/html", b"<html><body>Service unavailable</body></html>")):
        bad, unk, _, _ = C.check_ids(_entry(), _client({API: (status, ctype, body)}))
        assert bad == [] and any("不可用" in u for u in unk), body


def test_DOI走文献源(monkeypatch) -> None:
    e = _entry(ids={"doi": "10.1000/toy"}, title="Seven Rules for Toy Research", year=2008)
    monkeypatch.setattr(literature, "fetch_by_doi", lambda doi, client=None: PaperRecord(
        paper_id="W1", title="Seven Rules for Toy Research", year=2008))
    assert C.check_ids(e, _client({})) == ([], [], [], [])
    monkeypatch.setattr(literature, "fetch_by_doi", lambda doi, client=None: None)
    assert any("查不到" in b for b in C.check_ids(e, _client({}))[0])

    def down(doi, client=None):
        raise literature.SourceUnavailable("openalex+crossref", "都挂了")
    monkeypatch.setattr(literature, "fetch_by_doi", down)
    bad, unk, _, _ = C.check_ids(e, _client({}))
    assert bad == [] and unk


def test_接口给的摘要也算原文(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    abstract = "We present Toy Lab, an autonomous framework for research."
    client = _client({API: (200, "application/atom+xml", _atom(summary=abstract))})
    assert C.check_ids(_entry(), client)[3] == [abstract]
    e = _entry(ids={"doi": "10.1000/toy"}, title="Seven Rules for Toy Research", year=2008)
    monkeypatch.setattr(literature, "fetch_by_doi", lambda doi, client=None: PaperRecord(
        paper_id="W1", title="Seven Rules for Toy Research", year=2008, abstract=abstract))
    assert C.check_ids(e, _client({}))[3] == [abstract]


DATACITE = "https://api.datacite.org/dois/10.48550/arXiv.2501.00001"
LIMITED = (429, "text/plain", b"Rate exceeded.")


def _datacite(title: str = "Toy Lab: Autonomous Research with Agents", year: int = 2025, abstract: str = "") -> bytes:
    """DataCite 上那条 arXiv DOI 记录的样子：标题、首版年份、摘要（descriptionType 是 Abstract 的那段）。"""
    desc = [{"description": abstract, "descriptionType": "Abstract"}] if abstract else []
    return json.dumps({"data": {"id": "10.48550/arxiv.2501.00001", "attributes": {
        "titles": [{"title": title}], "publicationYear": year, "descriptions": desc}}}).encode()


def test_arXiv接口限流时改查DataCite_核上了要记一笔是谁核的(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    abstract = "We present Toy Lab, an autonomous framework for research."
    client = _client({API: LIMITED, DATACITE: (200, "application/json", _datacite(abstract=abstract))})
    bad, unk, notes, texts = C.check_ids(_entry(), client)
    assert bad == [] and unk == [] and texts == [abstract]
    assert any("DataCite" in n and "HTTP 429" in n for n in notes)


def test_DataCite那一路跟arXiv同一把尺子(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    routes = {API: LIMITED, DATACITE: (200, "application/json", _datacite("Another Paper Entirely"))}
    bad, unk, notes, _ = C.check_ids(_entry(), _client(routes))
    assert any("标题对不上" in b for b in bad) and unk == [] and any("DataCite" in n for n in notes)
    routes[DATACITE] = (200, "application/json", _datacite(year=2021))
    bad, unk, notes, _ = C.check_ids(_entry(), _client(routes))
    assert bad == [] and unk == [] and any("年份差得多" in n for n in notes)


def test_DataCite查的是去掉版本号的DOI(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    e = _entry(ids={"doi": "", "arxiv": "2501.00001v2", "url": ""})
    bad, unk, notes, _ = C.check_ids(e, _client({DATACITE: (200, "application/json", _datacite())}))
    assert bad == [] and unk == [] and any("DataCite" in n for n in notes)


def test_arXiv查不到就是查不到_不去DataCite找补(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    routes = {API: (200, "application/atom+xml", ATOM_EMPTY.encode()),
              DATACITE: (200, "application/json", _datacite())}
    bad, unk, notes, _ = C.check_ids(_entry(), _client(routes))
    assert any("查不到" in b for b in bad) and unk == [] and notes == []


def test_两个源都不可用只算未核_两个都点名(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    for dc in ((503, "text/plain", b""), (200, "text/html", b"<html><body>maintenance</body></html>"),
               (200, "application/json", b'{"errors": [{"status": "500"}]}')):
        bad, unk, _, _ = C.check_ids(_entry(), _client({API: LIMITED, DATACITE: dc}))
        assert bad == [] and any("arXiv" in u and "DataCite" in u and "不可用" in u for u in unk), dc

    def dc_down(req: httpx.Request) -> httpx.Response:
        if str(req.url) == DATACITE:
            raise httpx.ConnectTimeout("timed out", request=req)
        return httpx.Response(429, content=b"Rate exceeded.")
    bad, unk, _, _ = C.check_ids(_entry(), httpx.Client(transport=httpx.MockTransport(dc_down)))
    assert bad == [] and any("HTTP 429" in u and "ConnectTimeout" in u for u in unk)


def test_DataCite上没有这条也只算未核_它不是arXiv本身(monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    bad, unk, _, _ = C.check_ids(_entry(), _client({API: LIMITED}))
    assert bad == [] and any("DataCite 上没有 10.48550/arXiv.2501.00001" in u for u in unk)


# ══════════════════════════════ 五、整条与命令行

PDF_URL = "https://arxiv.org/pdf/2501.00001"


def _routes() -> dict:
    return {API: (200, "application/atom+xml", _atom()), PDF_URL: (200, "text/plain", SOURCE.encode())}


def test_整条通过(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    r = C.check_entry(_entry(), _client(_routes()), tmp_path)
    assert (r.status, r.problems, r.unverified) == ("通过", [], [])


def test_编造的引句会被抓(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    e = _entry()
    e["techniques"][0]["evidence"]["quote"] = "removing the reviewer agent lowered paper scores from 4.1 to 2.0"
    r = C.check_entry(e, _client(_routes()), tmp_path)
    assert r.status == "不过" and any("找不到" in p for p in r.problems)


def test_原文拿不到只算未核_不算通过(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    routes = _routes()
    del routes[PDF_URL]
    r = C.check_entry(_entry(), _client(routes), tmp_path)
    assert r.status == "未核" and any("一处都拿不到" in u for u in r.unverified)


def test_项目网址404会红(tmp_path) -> None:
    r = C.check_entry(_entry(ids={"url": "https://github.com/ghost/nothing"}), _client({}), tmp_path)
    assert r.status == "不过" and any("HTTP 404" in p for p in r.problems)


def test_引句另有出处时去那一页核(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    e = _entry(claims="完全开源")
    e["evidence"].append({"supports": "claims", "quote": "our docs page says the lab is fully open",
                          "where": "docs", "url": "https://x.org/docs"})
    routes = _routes()
    routes["https://x.org/docs"] = (200, "text/plain", b"Our docs page says the lab is fully open.")
    assert C.check_entry(e, _client(routes), tmp_path).status == "通过"


def test_只拿到接口摘要时找不到的引句只算未核(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    summary = "We present Toy Lab, an autonomous framework that completes the entire research process."
    r = C.check_entry(_entry(), _client({API: (200, "application/atom+xml", _atom(summary=summary))}), tmp_path)
    assert r.status == "未核" and r.problems == [] and any("只拿到接口给的摘要" in u for u in r.unverified)


def test_年份差得多只记一笔不影响结果(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    routes = _routes()
    routes[API] = (200, "application/atom+xml", _atom(year=2021))
    r = C.check_entry(_entry(), _client(routes), tmp_path)
    assert r.status == "通过" and any("年份差得多" in n for n in r.notes)


def test_arXiv接口限流时DataCite核上标识_整条照样通过(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    routes = _routes()
    routes[API] = LIMITED
    routes[DATACITE] = (200, "application/json", _datacite())
    r = C.check_entry(_entry(), _client(routes), tmp_path)
    assert (r.status, r.problems, r.unverified) == ("通过", [], []) and any("DataCite" in n for n in r.notes)


def test_形状写坏的条目也进报告_不会一条崩掉整批(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    rs = C.check_all(["not an entry", _entry(id="bad", ids="2501.00001"), _entry()], _client(_routes()), tmp_path)
    assert [r.status for r in rs] == ["不过", "不过", "通过"]


def test_id重复会红(tmp_path) -> None:
    rs = C.check_all([_entry(), _entry()], None, tmp_path, offline=True)
    assert all(r.status == "不过" and any("重复" in p for p in r.problems) for r in rs)


def test_命令行_离线只核格式_写报告(tmp_path) -> None:
    f = tmp_path / "A.json"
    f.write_text(json.dumps([_entry(), _entry(id="bad", borrow="")], ensure_ascii=False), encoding="utf-8")
    report = tmp_path / "report.json"
    assert C.main([str(f), "--offline", "--report", str(report)]) == 1
    got = {r["id"]: r["status"] for r in json.loads(report.read_text(encoding="utf-8"))}
    assert got == {"toylab": "未核", "bad": "不过"}


def test_命令行_顶层不是列表或不是JSON就拒(tmp_path) -> None:
    f = tmp_path / "A.json"
    f.write_text("{}", encoding="utf-8")
    assert C.main([str(f), "--offline"]) == 1
    f.write_text("{not json", encoding="utf-8")
    assert C.main([str(f), "--offline"]) == 1


def test_引句另有出处时两处都核_那一页拿不到先看主来源(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(C, "ARXIV_DELAY", 0)
    routes = _routes()
    routes["https://x.org/abs"] = (200, "text/plain", b"An abstract page with entirely different words in it.")
    e = _entry()
    e["evidence"][1]["url"] = "https://x.org/abs"
    assert C.check_entry(e, _client(routes), tmp_path).status == "通过"
    e = _entry()
    e["evidence"][1]["url"] = "https://x.org/gone"
    assert C.check_entry(e, _client(routes), tmp_path).status == "通过"
    e["evidence"][1]["quote"] = "a sentence that appears nowhere in any source at all"
    r = C.check_entry(e, _client(routes), tmp_path)
    assert r.status == "未核" and any("出处拿不到" in u for u in r.unverified)


def test_本事的证据写成字符串会红() -> None:
    e = _entry()
    e["techniques"][0]["evidence_level"] = "弱"
    e["techniques"][0]["evidence"] = "a string"
    assert any("evidence 要写成对象" in p for p in C.check_structure(e))


def test_源码里没有看不见或长得像的字符() -> None:
    """软连字符、零宽字符、长得像连字符与引号的字符，源码里一律写成转义——
    工具链会悄悄把它们抹掉或换成普通字符，那时常量和测试一起失效，还看不出来。"""
    looks_alike = {0x2010, 0x2011, 0x2012, 0x2212, 0x02BC, *range(0x2018, 0x2020)}
    bad = []
    for path in (ROOT / "scripts" / "check_related_work.py", Path(__file__)):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for ch in line:
                cat = unicodedata.category(ch)
                if cat in ("Cf", "Mn", "Co") or (cat == "Zs" and ch != " ") or ord(ch) in looks_alike:
                    bad.append(f"{path.name}:{n} U+{ord(ch):04X}")
    assert bad == [], bad
