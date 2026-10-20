"""`oss install-skills --host X` / `oss doctor --host X`。

阶段 2 收尾时装 dsh 的命令是 `oss install-skills --target ~/.dsh/skills`——
**它铺过去的是 Claude Code 形状的说明书**（带 allowed-tools）。
路径对了，形状没对。`--host` 要的就是把形状也一起对上。
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from oss.cli.main import _skills_src, app

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


def test_按dsh装出来的是dsh形状(tmp_path: Path) -> None:
    out = tmp_path / "skills"
    r = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out)])
    assert r.exit_code == 0, r.output
    assert "DeepSeek Harness" in r.output
    assert (out / "social-research" / "stages" / "6-run.md").exists(), "子目录要整棵铺过去"
    assert (out / "social-paper" / "contract.md").exists()
    t = (out / "social-research" / "SKILL.md").read_text(encoding="utf-8")
    assert "# allowed-tools:" in t and "\nallowed-tools:" not in "\n" + t


def test_装完之后drift检查是绿的(tmp_path: Path) -> None:
    """**这条是 --host 的意义所在**：drift 要跟「这个宿主的投影」比，
    不是跟源头字节比——否则 dsh 装完立刻显示不一致，而它明明刚装。"""
    from oss import hosts
    from oss.cli.main import _skills_drift

    out = tmp_path / "skills"
    runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out)])
    ok, msg = _skills_drift(dst=out, profile=hosts.load("dsh"))
    assert ok, msg
    # 拿 claude-code 的投影去比同一棵树，必须报不一致——否则这条检查等于没有
    ok2, msg2 = _skills_drift(dst=out, profile=hosts.load("claude-code"))
    assert not ok2 and "social-research" in msg2


def test_不给host时打印探测到的宿主和依据(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["install-skills", "--target", str(tmp_path / "s")])
    assert r.exit_code == 0, r.output
    assert "宿主：" in r.output and "依据：" in r.output


def test_不认识的宿主给出已知清单() -> None:
    r = runner.invoke(app, ["install-skills", "--host", "cursor", "--target", "/tmp/x"])
    assert r.exit_code == 2
    assert "dsh" in r.output


def test_不force时跳过已存在(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "skills"
    runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out)])
    r = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out)])
    assert "跳过 social-research" in r.output
    r2 = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out), "--force"])
    assert "✓ social-research" in r2.output


def test_project铺到项目级目录(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["install-skills", "--host", "dsh", "--project"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / ".dsh" / "skills" / "social-research" / "SKILL.md").exists()


def test_project在本仓库里用claude_code会被拦(monkeypatch) -> None:
    """本仓库的 .claude/skills 就是规范源。往那儿投影 = 自己覆盖自己。"""
    monkeypatch.chdir(ROOT)
    # `oss/skills` 一旦存在（打包版），`_skills_src` 会优先返回它，这条测试就会
    # **先把打包版覆盖进规范源、再失败**。先断言，别给它这个机会。
    assert _skills_src() == ROOT / ".claude" / "skills"
    r = runner.invoke(app, ["install-skills", "--host", "claude-code", "--project", "--force"])
    assert r.exit_code == 1
    assert "源头" in r.output


def test_没有项目级目录的宿主明确拒绝(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["install-skills", "--host", "openclaw", "--project"])
    assert r.exit_code == 2 and "项目级" in r.output


# ─────────────────────────────────────────────── 托管块

def test_托管块写进宿主的指令文件(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# 我的项目\n\n正文。\n", encoding="utf-8")
    r = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(tmp_path / "s")])
    assert r.exit_code == 0, r.output
    t = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- OSS:BEGIN -->" in t and "social-research" in t and "# 我的项目" in t

    runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(tmp_path / "s"),
                        "--force"])
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == t, "第二次跑不该改动文件"

    r3 = runner.invoke(app, ["install-skills", "--host", "dsh", "--clear-doc"])
    assert r3.exit_code == 0
    t3 = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- OSS:BEGIN -->" not in t3 and "# 我的项目" in t3


def test_clear_doc撞两块托管块给人话而不是裸异常(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    two_blocks = (
        "# 我的项目\n\n"
        "<!-- OSS:BEGIN -->\n块一\n<!-- OSS:END -->\n\n"
        "<!-- OSS:BEGIN -->\n块二\n<!-- OSS:END -->\n"
    )
    (tmp_path / "AGENTS.md").write_text(two_blocks, encoding="utf-8")
    r = runner.invoke(app, ["install-skills", "--host", "dsh", "--clear-doc"])
    assert r.exit_code == 1, r.output
    assert "托管块没动" in r.output
    assert "Traceback" not in r.output
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == two_blocks


def test_target与project同给拒绝(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "s"
    r = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out), "--project"])
    assert r.exit_code == 2, r.output
    assert "--target" in r.output and "--project" in r.output
    assert not out.exists()
    assert not (tmp_path / ".dsh").exists()


def test_指令文件不存在就跳过而不是新建(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(tmp_path / "s")])
    assert "AGENTS.md 不在，跳过" in r.output
    assert not (tmp_path / "AGENTS.md").exists(), "不许凭空造别人的指令文件"


def test_部分安装与全量重装的托管块内容一致(tmp_path: Path, monkeypatch) -> None:
    """名单不整体排序的话，「新装的 + 跳过的」跟「全都是新装的」拼出来的顺序不同，
    托管块就会在一个**什么都没变**的项目里来回改。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# 我的项目\n", encoding="utf-8")
    out = tmp_path / "s"
    (out / "social-research").mkdir(parents=True)          # 先占一个位，让它被跳过
    (out / "social-research" / "SKILL.md").write_text("占位\n", encoding="utf-8")

    r = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out)])
    assert r.exit_code == 0, r.output
    assert "跳过 social-research" in r.output
    partial = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    r2 = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out), "--force"])
    assert r2.exit_code == 0, r2.output
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == partial


def test_全部跳过时也刷新托管块(tmp_path: Path, monkeypatch) -> None:
    """真实顺序：先装技能（那会儿还没有 AGENTS.md，托管块被跳过），
    过一阵才建 AGENTS.md，再跑一次同一条命令。早退的话这个项目**永远**没有托管块，
    而两次都显示成功了。"""
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "s"
    r1 = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out)])
    assert r1.exit_code == 0 and "AGENTS.md 不在，跳过" in r1.output

    (tmp_path / "AGENTS.md").write_text("# 我的项目\n", encoding="utf-8")
    r2 = runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(out)])
    assert r2.exit_code == 0, r2.output
    assert "没有新装任何 skill" in r2.output
    t = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- OSS:BEGIN -->" in t and "social-research" in t and "# 我的项目" in t


def test_no_doc时不碰指令文件(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("原样\n", encoding="utf-8")
    runner.invoke(app, ["install-skills", "--host", "dsh", "--target", str(tmp_path / "s"),
                        "--no-doc"])
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "原样\n"


# ─────────────────────────────────────────────── doctor

def test_doctor按dsh自检时没有claude的红线() -> None:
    r = runner.invoke(app, ["doctor", "--host", "dsh"])
    assert r.exit_code in (0, 1), r.output
    assert "Claude Code CLI" not in r.output, "dsh 用户不该看见一条他修不了的红线"
    assert "dsh 配置目录" in r.output
    assert "DeepSeek Harness" in r.output


def test_doctor按claude_code自检时那条还在() -> None:
    r = runner.invoke(app, ["doctor", "--host", "claude-code"])
    assert r.exit_code in (0, 1), r.output
    assert "Claude Code CLI" in r.output


def test_doctor不给host时报告探测结果() -> None:
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code in (0, 1), r.output
    assert "宿主" in r.output and "依据" in r.output


def test_doctor不认识的宿主直接退出() -> None:
    r = runner.invoke(app, ["doctor", "--host", "cursor"])
    assert r.exit_code == 2 and "dsh" in r.output


def _doctor_with_refusal(monkeypatch, hint: str) -> list[str]:
    """让 doctor 的家族检查必定拒绝，返回它打出来的每一行。"""
    from oss.cli import main as cli_main
    from oss.host.reviewer import SameFamilyRefused

    class _Fake:
        def __init__(self, **kw) -> None:
            self.model = "m"
            self.executor_model = "e"
            self.executor_source = "none"

        def is_configured(self) -> bool:
            return True

        def check_family(self):
            raise SameFamilyRefused("a", "b", "deepseek", hint=hint)

    monkeypatch.setattr(cli_main, "Reviewer", _Fake)
    return runner.invoke(app, ["doctor", "--host", "dsh"]).output.splitlines()


def test_doctor在同家族那支不多打一行(monkeypatch) -> None:
    """「同家族」的拒绝信息本来就是两行，第二行是「改配哪个变量」，不是提示。
    从 str(exc) 里切 [1:] 当提示打，等于凭空多出一条缩进行。

    注：缺省档已不再拒绝同家族（2026-09-07），这里测的是**严格档**下的那条路——
    `REQUIRE_CROSS_MODEL=1` 时抛出来的信息还是得按老规矩打。"""
    lines = _doctor_with_refusal(monkeypatch, "")
    i = next(n for n, ln in enumerate(lines) if "审稿" in ln)
    assert lines[i].startswith("✗")
    assert not lines[i + 1].startswith("    "), lines[i:i + 3]


def test_doctor把提示逐行缩进打出来(monkeypatch) -> None:
    lines = _doctor_with_refusal(monkeypatch, "第一行提示\n第二行提示")
    assert "    第一行提示" in lines and "    第二行提示" in lines
