"""散文冻结：阶段 3 是**纯搬家**——不给 `--json` 时，每条命令的输出与退出码
一个字节都不许变。

做法：搬家**之前**把每条命令在同一份夹具运行目录上的输出录成
`tests/golden/<名>.txt`（第一行 `EXIT n`，然后 `---`，然后原样输出），
之后每一步都对着它比。运行目录是 tmp_path，每次都不一样，所以路径统一
替换成 `<RUN>`、仓库根替换成 `<REPO>`。

重录（**只在确实要改散文时用，并在提交信息里说清改了哪一行、为什么**）：

    OSS_GOLDEN_REGEN=1 .venv/bin/pytest tests/test_stage3_prose_frozen.py -q

重录那一趟**一定是红的**（写完基线就 `pytest.fail`）：重录是要人复查 diff 的动作，
不该有一种「全绿」的跑法让它悄悄过去。复查完把环境变量去掉再跑一遍才算数。

**冻的是「附加依赖都装着」时的输出。**基线录自装了 `statspai`（分批 DiD）与
`matplotlib`（画图）的环境：挡掉 `statspai` 会翻 6 份，挡掉 `matplotlib` 翻 1 份
（`figure_coef`）——那不是散文改了，是能力缺失时本来就该说另一句话。
缺了哪个，对应的几条按 `_REQUIRES_OPTIONAL` 自动跳过、不判红——CI 不装 statspai，
靠的就是这一条；依赖装齐时照样逐字比，一条不跳。

**golden 里的一行不区分它走的是 stdout 还是 stderr。**`CliRunner.output` 是两路
按写入顺序合起来的一条流，冻的就是这条合并流：顺序变了会红，而「某一行从 stdout
挪到了 stderr」不会。要钉住某一行落在哪一路，去 `tests/test_stage3_json.py`
（那边 `js.stderr` 是单独查的）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oss.cli.main import app

runner = CliRunner()

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
REGEN = os.getenv("OSS_GOLDEN_REGEN") == "1"


# ─────────────────────────── 夹具：一个走到「跑完分析」的运行目录


def _seed(run: Path, data: str = "did_clean.csv") -> None:
    """离线造一个到环节 6 为止的运行目录。**全部本地文件，一次网络都不连。**"""
    run.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIX / data, run / "data.csv")
    r = runner.invoke(app, ["profile", str(run / "data.csv"), "--run", str(run)])
    assert r.exit_code == 0, r.output
    (run / "ideas.json").write_text(json.dumps({
        "direction": "政策冲击与产出",
        "ideas": [{"idea_id": "edu_001", "title": "T", "research_question": "Q",
                   "constructs_needed": ["处理时点", "结局"]}]},
        ensure_ascii=False), encoding="utf-8")
    (run / "plan.json").write_text(json.dumps({
        "plan_id": "p001", "idea_id": "edu_001", "estimator": "did", "outcome": "y",
        "unit": "unit", "time": "year", "treat_time": "treat_time",
        "cluster_level": "unit"}, ensure_ascii=False), encoding="utf-8")
    r = runner.invoke(app, ["run", "--run", str(run)])
    assert r.exit_code == 0, r.output


def _seed_pretrend(run: Path) -> None:
    """同一条链，数据换成**处理前真的在分化**的那份——这样 `pretrend_fail` 才开得起来。

    2026-09-17 之前 `did_clean` 上也开，那是逐（批 × 期）格子联合检验的误报。处置那两条
    用例冻的是「must 级处方被驳回／写进局限之后长什么样」，**得由真触发来演**。
    """
    _seed(run, "did_pretrend.csv")


def _wide(run: Path) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "wide.csv").write_text(
        "pid,y_w1,y_w2,x_w1,x_w2\n1,1.0,2.0,3.0,4.0\n2,5.0,6.0,7.0,8.0\n", encoding="utf-8")


def _two_tables(run: Path) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "left.csv").write_text("country,year,y\nCHN,2000,1.0\nUSA,2000,2.0\n", encoding="utf-8")
    (run / "right.csv").write_text("country,year,z\nCHN,2000,9.0\nJPN,2000,8.0\n", encoding="utf-8")


def _reviews_exhausted(run: Path) -> None:
    _seed(run)
    for name, rnd in (("review_paper_rendered_a.json", 1), ("review_paper_rendered_a_r2.json", 2)):
        (run / name).write_text(json.dumps(
            {"verdict": "FAIL", "findings": ["[设计] 平行趋势不成立"], "round": rnd},
            ensure_ascii=False), encoding="utf-8")


def _paper_bad(run: Path) -> None:
    """有五节、但正文手打了一个裸小数——这正是最危险的失败模式。

    小数要挑**产出里找不到出处的**：裸小数禁令对 known_numbers 有例外，
    照抄 results/route 里的数（比如 3.026）会被放行，就踩不到这条线了。
    """
    _seed(run)
    (run / "paper.md").write_text(
        "# 摘要\n\n本文估计政策冲击对产出的影响。\n\n"
        "# 引言\n\n先行研究只到县一级。\n\n"
        "# 数据与方法\n\n面板数据，双重差分，按单位聚类。\n\n"
        "# 结果\n\n处理效应为 0.473，在常规水平上显著。\n\n"
        "# 讨论\n\n结论对窗口长度稳健。\n", encoding="utf-8")


def _paper_cited(run: Path) -> None:
    """跑完分析的目录，再补一篇**带 {{cite:...}} 的稿子**和一份语料。

    三条引用刚好压在 `oss lit verify` 的三种判词上：有摘要且命中 → 支持、
    有摘要但一个都没命中 → 存疑、根本没有摘要 → 核不了。
    第四条指向语料里没有的编号，走的是「找不到就跳过」那一支。
    """
    _seed(run)
    (run / "paper.md").write_text(
        "# 引言\n\n"
        "已有研究指出，教育扩张提高了社会流动 {{cite:W001}}。\n\n"
        "也有人认为，最低工资上调并不减少就业 {{cite:W002}}。\n\n"
        "另有综述给出相反的证据 {{cite:W003}}。\n\n"
        "还有一条指向语料里没有的编号 {{cite:W404}}。\n",
        encoding="utf-8")
    (run / "literature.json").write_text(json.dumps({
        "corpus_id": "c001",
        "queries": ["educational expansion social mobility"],
        "records": [
            {"paper_id": "W001", "title": "Educational expansion and social mobility",
             "year": 1997, "authors": ["Breen, R."], "doi": "10.1000/w001",
             "abstract": "We ask whether educational expansion raises social mobility "
                         "across cohorts.", "source": "openalex"},
            {"paper_id": "W002", "title": "A field experiment on commuting",
             "year": 1994, "authors": ["Card, D."], "doi": "10.1000/w002",
             "abstract": "A case study of coastal weather patterns.",
             "source": "openalex"},
            {"paper_id": "W003", "title": "A review without an abstract",
             "year": 2001, "authors": ["Duncan, O. D."], "doi": "10.1000/w003",
             "source": "crossref"},
        ],
        "hits_by_source": {"openalex": 2, "crossref": 1},
        "abstract_coverage": 2 / 3, "citation_coverage": 0.0,
    }, ensure_ascii=False), encoding="utf-8")


def _cards_bad(run: Path) -> None:
    run.mkdir(parents=True, exist_ok=True)
    (run / "reading_cards.json").write_text("{ 不是 JSON", encoding="utf-8")


def _nothing(run: Path) -> None:
    run.mkdir(parents=True, exist_ok=True)


# ─────────────────────────── 用例表：(名字, 准备, 参数)


CASES: list[tuple[str, object, list]] = [
    ("schema_plan",             _nothing, ["schema", "plan"]),
    ("schema_plan_example",     _nothing, ["schema", "plan", "--example"]),
    ("schema_paper",            _nothing, ["schema", "paper"]),
    ("schema_rules",            _nothing, ["schema", "rules"]),
    ("data_list",               _nothing, ["data", "list"]),
    ("profile",                 _seed,    ["profile", "<RUN>/data.csv", "--run", "<RUN>", "--resume"]),
    ("reshape",                 _wide,    ["reshape", "<RUN>/wide.csv", "--run", "<RUN>"]),
    ("merge",                   _two_tables,
     ["merge", "<RUN>/left.csv", "<RUN>/right.csv", "--on", "country,year", "--run", "<RUN>"]),
    ("bind_candidates",         _seed,    ["bind", "--run", "<RUN>", "--idea", "edu_001"]),
    ("bind_emit",               _seed,
     ["bind", "--run", "<RUN>", "--idea", "edu_001",
      "--map", "处理时点=treat_time:treatment", "--map", "结局=y:outcome"]),
    ("power",                   _seed,    ["power", "--run", "<RUN>", "--idea", "edu_001"]),
    ("run",                     _seed,    ["run", "--run", "<RUN>"]),
    ("run_explore_refused",     _seed,
     ["run", "--explore", "--for", "pretrend_fail", "--plan", "<RUN>/plan.json", "--run", "<RUN>"]),
    ("route",                   _seed,    ["route", "--run", "<RUN>"]),
    # 处置三种走法。每条都跟一次**平白的** route，把处置**留下来的**状态一起冻上：
    # ○ pending / ✓ applied / ✗ dismissed / ≈ disclosed / · resolved 这五个标记
    # 是模型判断「还剩什么没治」的唯一依据，落盘没落上不会报错，只会安静地少一行。
    ("route_apply",             _seed_pretrend,
     [["route", "--run", "<RUN>", "--apply", "staggered_twfe"],
      ["route", "--run", "<RUN>"]]),
    ("route_dismiss",           _seed_pretrend,
     [["route", "--run", "<RUN>", "--dismiss", "pretrend_fail", "--why", "对照组不同期"],
      ["route", "--run", "<RUN>"]]),
    ("route_disclose",          _seed_pretrend,
     [["route", "--run", "<RUN>", "--disclose", "pretrend_fail", "--why", "写进局限"],
      ["route", "--run", "<RUN>"]]),
    ("figure_coef",             _seed,    ["figure", "coef", "--run", "<RUN>"]),
    ("eval",                    _seed,    ["eval", "--run", "<RUN>"]),
    ("eval_list",               _nothing, ["eval", "--list"]),
    ("loop_status_fresh",       _seed,    ["loop", "status", "--run", "<RUN>"]),
    ("loop_status_exhausted",   _reviews_exhausted, ["loop", "status", "--run", "<RUN>"]),
    ("paper_check_missing",     _seed,    ["paper", "check", "--run", "<RUN>"]),
    ("paper_check_bad",         _paper_bad, ["paper", "check", "--run", "<RUN>"]),
    ("anchor_switch_refused",   _seed,
     ["anchor", "switch", "--to", "edu_007", "--why", "试试", "--run", "<RUN>"]),
    ("lit_cards_broken",        _cards_bad, ["lit", "cards", "--run", "<RUN>"]),
    ("lit_verify",              _paper_cited, ["lit", "verify", "--run", "<RUN>"]),
]


#: pydantic 报错里带它自己的版本号（errors.pydantic.dev/2.13/…）。那是第三方的元数据，
#: 不是本工具的散文——升了依赖就判「散文变了」只会教人麻木地重录基线。
_PYDANTIC_VER = re.compile(r"(errors\.pydantic\.dev/)\d+(?:\.\d+)*/")


def _norm(text: str, run: Path) -> str:
    text = text.replace(str(run), "<RUN>").replace(str(REPO), "<REPO>")
    return _PYDANTIC_VER.sub(r"\1<PYDANTIC>/", text)


#: 这几条的基线录自装了对应附加依赖的环境。缺了它，命令本来就该说另一句话
#: （did_cs 用不了、画不出图）——那不是散文变了，跳过、不判红。
#: 表是在不装 statspai 的环境里逐条跑出来的（挂的正好这 6 条），matplotlib 那条卸了验过。
_REQUIRES_OPTIONAL: dict[str, str] = {
    "run": "statspai",
    "route": "statspai",
    "route_apply": "statspai",
    "route_dismiss": "statspai",
    "route_disclose": "statspai",
    "paper_check_bad": "statspai",
    "figure_coef": "matplotlib",
}


def test_跳过表里的名字都是真用例():
    """表里写错一个名字，那条就永远不会跳——缺依赖时照样判红，还看不出为什么。"""
    assert set(_REQUIRES_OPTIONAL) <= {c[0] for c in CASES}


@pytest.mark.parametrize("name,prepare,argv", CASES, ids=[c[0] for c in CASES])
def test_散文一个字节都没变(name, prepare, argv, tmp_path, monkeypatch):
    need = _REQUIRES_OPTIONAL.get(name)
    if need and importlib.util.find_spec(need) is None:
        pytest.skip(f"基线是装了 {need} 时录的；没装 {need}，这条命令本来就该说另一句话")
    # `data list` 会读**当前目录**下的 data/（本机已下好的数据集），
    # 在仓库根上录，基线就跟着本机状态走，换台机器就不一样了。
    # 用例里的路径全是绝对的，所以统一切到 tmp_path 底下跑，跟本机彻底隔开。
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "r"
    prepare(run)
    # argv 可以是一条命令，也可以是几条按顺序跑的命令（处置类用例要跑完再复看状态）。
    # 单条命令的渲染跟以前**逐字节相同**，老基线不用重录。
    steps = argv if isinstance(argv[0], list) else [argv]
    blocks = []
    for step in steps:
        args = [a.replace("<RUN>", str(run)) for a in step]
        r = runner.invoke(app, args)
        blocks.append(f"EXIT {r.exit_code}\n---\n" + _norm(r.output, run))
    got = "\n--- status ---\n".join(blocks)

    path = GOLDEN / f"{name}.txt"
    if REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got, encoding="utf-8")
        # 写完就红。从前这里 `return`，于是 `OSS_GOLDEN_REGEN=1` 那一趟 27 条
        # **无条件全绿**——基线已经被悄悄改写，而跑的人看到的是一片绿。
        # 重录是要人复查 diff 的动作，不该有一种「全绿」的跑法让它溜过去。
        pytest.fail(f"基线已重录到 {path}，"
                    f"请复查 diff 后去掉 OSS_GOLDEN_REGEN 再跑")
    assert path.exists(), (
        f"缺基线 {path}。搬家之前先录一次："
        f"OSS_GOLDEN_REGEN=1 .venv/bin/pytest tests/test_stage3_prose_frozen.py -q")
    want = path.read_text(encoding="utf-8")
    assert got == want, f"{name} 的散文变了。想改就得说清改了哪一行、为什么，然后重录基线"
