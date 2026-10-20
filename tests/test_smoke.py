"""冒烟测试：链条能不能从头走到尾。

**只检查「通了」，不检查「好不好」**（PLAN.md 三）。
每个阶段换掉一个假实现之后，这个测试必须还是绿的——
不允许出现「链条断了正在修」的状态。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oss.cli.main import app
from oss.core.contracts import (
    ARTIFACTS,
    AnalysisPlan,
    AnalysisResults,
    BindingReport,
    DataProfile,
    IdeaSet,
    LiteratureCorpus,
    PowerReport,
)

runner = CliRunner()

LOADERS = {
    "profile": DataProfile,
    "literature": LiteratureCorpus,
    "ideas": IdeaSet,
    "binding": BindingReport,
    "power": PowerReport,
    "plan": AnalysisPlan,
    "results": AnalysisResults,
}


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """跑一次完整链条，返回产出目录。"""
    target = tmp_path / "smoke"
    result = runner.invoke(app, ["smoke", "--run", str(target), "--keep"])
    assert result.exit_code == 0, result.output
    return target


# ------------------------------------------------------------- 链条通不通


def test_离线冒烟不碰网络(run_dir: Path) -> None:
    """冒烟必须能在断网、断额度时跑——否则它就不是「链条通不通」的可靠判据。"""
    corpus = LiteratureCorpus.load(run_dir / ARTIFACTS["literature"])
    assert corpus.hits_by_source == {"fake": len(corpus.records)}


def test_阶段1_画像已是真的(run_dir: Path) -> None:
    """smoke 跑的是内置 tiny.csv：25 单位 × 4 期的平衡面板，unit_id 嵌套于 region。"""
    prof = DataProfile.load(run_dir / ARTIFACTS["profile"])
    assert "FAKE_" not in prof.model_dump_json(), "画像还是假的"
    assert prof.structure.kind == "panel_balanced"
    assert prof.structure.unit_id == "unit_id"
    assert prof.structure.time_id == "year"
    assert prof.structure.nesting_levels == ["unit_id", "region"]
    assert "const_col" in prof.constant_columns
    assert prof.var("treat").within_variation not in (None, 0.0), "treat 应当有组内变异"


def test_全部产出物都在(run_dir: Path) -> None:
    for key, name in ARTIFACTS.items():
        if key == "trace":
            continue
        assert (run_dir / name).exists(), f"缺 {name}"


def test_每个产出物都合契约(run_dir: Path) -> None:
    for key, cls in LOADERS.items():
        cls.load(run_dir / ARTIFACTS[key])  # 不合 schema 会抛异常


def test_论文至少五节(run_dir: Path) -> None:
    paper = (run_dir / ARTIFACTS["paper"]).read_text(encoding="utf-8")
    assert paper.count("\n## ") >= 5


def test_留痕每行都是合法_json(run_dir: Path) -> None:
    lines = (run_dir / ARTIFACTS["trace"]).read_text(encoding="utf-8").splitlines()
    assert lines, "trace.jsonl 是空的"
    for line in lines:
        event = json.loads(line)
        assert {"seq", "ts", "step"} <= event.keys()


# --------------------------------------------------- 阶段 0：产出应当全是假的


def test_阶段0_产出带FAKE前缀(run_dir: Path) -> None:
    """假数据要一眼认得出（PLAN.md 纪律 ④）。

    换成真实现之后这个断言会失败——**那正是它的用途**：
    它标记了「这一段还没换真」。换真的同一个提交里把对应的 key 从这里删掉。
    """
    # 每换真一个就从这里删掉，是「进度」的可执行标记。
    # literature 例外：真实现已就绪，但离线 smoke 刻意用假语料——
    # 冒烟测试不该依赖网络（要测真的：oss smoke --online）
    still_fake = ["ideas", "binding", "power", "plan", "results"]
    for key in still_fake:
        text = (run_dir / ARTIFACTS[key]).read_text(encoding="utf-8")
        assert "FAKE_" in text, f"{ARTIFACTS[key]} 里没有 FAKE_ 标记——它换成真的了吗？"


# ---------------------------------------------------------- 单个命令能不能跑


FIXTURE = str(Path(__file__).parent / "fixtures" / "tiny.csv")


def test_profile_的shape只给形状不给变量名(tmp_path: Path) -> None:
    """DECISIONS.md 5.1：合成 idea 的阶段只该看见形状。"""
    result = runner.invoke(app, ["profile", FIXTURE, "--shape", "--run", str(tmp_path / "r")])
    assert result.exit_code == 0, result.output
    assert "结构：" in result.output
    # 实质变量一个都不许出现——它们才是「顺着列名凑问题」的来源
    for leaked in ("income", "edu_years", "age", "hh_size", "treat", "urban"):
        assert leaked not in result.output, f"形状摘要泄漏了实质变量 {leaked}"
    # 结构列反而必须给：不给的话模型判断不了层次匹配和期数够不够
    assert "unit_id" in result.output and "region" in result.output


def test_profile_读不了的文件给出可读的拒绝(tmp_path: Path) -> None:
    r = runner.invoke(app, ["profile", "不存在.csv", "--run", str(tmp_path / "r")])
    assert r.exit_code == 1 and "不存在" in r.output

    bad = tmp_path / "x.docx"
    bad.write_text("")
    r = runner.invoke(app, ["profile", str(bad), "--run", str(tmp_path / "r")])
    assert r.exit_code == 1 and "不支持" in r.output


def test_bind_在缺ideas时给出可读的拒绝(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["bind", "--run", str(empty)])
    assert result.exit_code == 1
    assert "ideas.json" in result.output


def test_bind_打错路径时拒绝而不是把目录建出来(tmp_path: Path) -> None:
    """建出来的话报的是「缺 ideas.json」——看着像「这一步还没做」，
    而真相是「这个目录根本不是你要的那个」。**凭空造出来的空运行目录最难查。**"""
    typo = tmp_path / "runs" / "currnet"
    result = runner.invoke(app, ["bind", "--run", str(typo)])
    assert result.exit_code == 1 and "运行目录不存在" in result.output
    assert not typo.exists()


def test_run_在缺plan时给出可读的拒绝(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["run", "--run", str(empty)])
    assert result.exit_code == 1
    assert "plan.json" in result.output


def test_run_打错路径时拒绝而不是把目录建出来(tmp_path: Path) -> None:
    """报「缺 plan.json」等于把「目录错了」说成「这一步还没做」——
    而那个空目录是这条命令自己刚造出来的。**凭空造出来的运行目录最难查。**"""
    typo = tmp_path / "runs" / "currnet"
    result = runner.invoke(app, ["run", "--run", str(typo)])
    assert result.exit_code == 1 and "运行目录不存在" in result.output
    assert not typo.exists()


def test_doctor_能跑(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    # 环境不全时退出码为 1，但不该崩
    assert result.exit_code in (0, 1), result.output
    assert "Python 版本" in result.output


# ------------------------------------------- smoke 只删自己建的那个目录


def test_smoke_不删不是自己建的目录(tmp_path: Path) -> None:
    """smoke 跑完 `rmtree` 整个 --run 目录。打错一个字指到一次真跑上，
    原先就是把那次运行的全部产出连同 trace.jsonl 一起删掉，没有第二次机会。
    所以目录已经在了就直接拒绝——不赌用户记得加 --keep。"""
    existing = tmp_path / "真跑"
    existing.mkdir()
    survivor = existing / "results.json"
    survivor.write_text("真跑的产出", encoding="utf-8")

    result = runner.invoke(app, ["smoke", "--run", str(existing)])

    assert result.exit_code == 1, result.output
    assert "已经存在" in result.output
    assert survivor.read_text(encoding="utf-8") == "真跑的产出", "别人的产出被动了"
    assert [f.name for f in existing.iterdir()] == ["results.json"], \
        "拒绝跑的时候不该往别人的目录里写任何东西"


def test_smoke_自己建的目录跑完删掉(tmp_path: Path) -> None:
    """没有 --keep 的老行为一个字不变——前提是这个目录是 smoke 自己建的。"""
    fresh = tmp_path / "fresh"
    assert not fresh.exists()

    result = runner.invoke(app, ["smoke", "--run", str(fresh)])

    assert result.exit_code == 0, result.output
    assert not fresh.exists(), "自己建的目录，跑完该删干净"


# ---------------------------------------------------- idea 契约的两个字段


def test_idea_带透镜来源() -> None:
    """丢了 lens 就没法回答「哪个角度产出高」，
    也没法在某个透镜系统性产出垃圾时把它关掉（阶段 3 真跑时发现缺失）。"""
    from oss.core.fake import ideas

    assert all(i.lens for i in ideas("方向").ideas)


def test_迁移类透镜的强制门字段存在() -> None:
    """context-transfer / temporal-shift 的候选必须带「为什么结果该不同」的论证。

    「没人在 Z 国做过」不是贡献。为空即高风险，但不淘汰——淘汰是陪审团的事。
    """
    from oss.core.contracts import Idea

    i = Idea(idea_id="x", title="t", research_question="q")
    assert i.transfer_rationale is None, "默认应为 None（未提供论证）"
    assert Idea(
        idea_id="x", title="t", research_question="q",
        transfer_rationale="该理论假设劳动力市场 X，Z 国不成立，符号应反转",
    ).transfer_rationale


# ------------------------------------------------------- 灰区与契约查询


def test_功效判词由灰区推出而不是写死() -> None:
    """写死 verdict 会让 skill 的灰区分支永远走不到（阶段 0 验证时发现）。"""
    from oss.core.fake import _verdict, power

    assert _verdict(0.3, (0.5, 0.9)) == "fail"
    assert _verdict(0.95, (0.5, 0.9)) == "pass"
    assert _verdict(0.82, (0.5, 0.9)) == "gray"

    p = power("idea_001", 0.2, 1000)
    low, high = p.gray_band
    expected = "fail" if p.power < low else "pass" if p.power > high else "gray"
    assert p.verdict == expected, "verdict 与 gray_band 自相矛盾"


def test_schema_能查到每个产出物的格式() -> None:
    """skill 靠这个知道 ideas/plan 怎么写——不该让它去读 Python 源码。"""
    for name in ("profile", "literature", "ideas", "binding", "power", "plan", "results"):
        r = runner.invoke(app, ["schema", name, "--example"])
        assert r.exit_code == 0, f"{name}: {r.output}"
        json.loads(r.stdout)  # 样例必须是合法 JSON

    r = runner.invoke(app, ["schema", "paper"])
    assert r.exit_code == 0 and "占位符" in r.output

    r = runner.invoke(app, ["schema", "nonexistent"])
    assert r.exit_code == 1


# ------------------------------------------------------------------ 分层


def test_core不许import_host() -> None:
    """core/ 必须宿主无关——迁移时它一行都不用改（DECISIONS.md 1.4）。"""
    import ast

    core = Path(__file__).resolve().parents[1] / "oss" / "core"
    offenders = []
    for py in core.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mod = ""
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            if "oss.host" in mod or mod.startswith("host"):
                offenders.append(f"{py.name}:{node.lineno}")
    assert not offenders, "core/ import 了 host/：" + "、".join(offenders)
