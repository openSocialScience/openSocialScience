"""CLI 只是门面：散文一句、退出码透传。用 typer 的 CliRunner，不起子进程。"""
from __future__ import annotations

from typer.testing import CliRunner

from oss.cli.main import app
from tests.test_simbox import make_box

runner = CliRunner()


def test_sim组有五条命令():
    res = runner.invoke(app, ["sim", "--help"])
    for cmd in ("init", "check", "pack", "import", "doctor"):
        assert cmd in res.output


def test_check通过打勾退出0(tmp_path):
    res = runner.invoke(app, ["sim", "check", str(make_box(tmp_path))])
    assert res.exit_code == 0 and "✓" in res.output


def test_check拒收打叉逐条退出7(tmp_path):
    box = make_box(tmp_path, drop_meta_key="model")
    res = runner.invoke(app, ["sim", "check", str(box)])
    assert res.exit_code == 7 and "model" in res.output


def test_import渲染降级警告(tmp_path):
    box = make_box(tmp_path, reps=("1",), meta_overrides={"n_reps": 1})
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    res = runner.invoke(app, ["sim", "import", str(box), "--run", str(run)])
    assert res.exit_code == 0 and "⚠" in res.output and "重复不足" in res.output


def test_json口径与api一致(tmp_path):
    import json as _json
    res = runner.invoke(app, ["sim", "check", str(make_box(tmp_path)), "--json"])
    out = _json.loads(res.output.strip().splitlines()[-1])
    assert out["ok"] is True and out["data"]["exit_code"] == 0


def test_pack的simulator与from是同一个旗别名(tmp_path):
    """`--simulator` 是正名，`--from` 留作向后兼容的别名——两条旗填出同一个结果。"""
    for flag in ("--simulator", "--from"):
        res = runner.invoke(app, ["sim", "pack", flag, "nope", str(tmp_path)])
        assert res.exit_code == 1 and "不认识的模拟器" in res.output


def test_doctor散文summary先于逐条打勾():
    res = runner.invoke(app, ["sim", "doctor", "--simulator", "oasis"])
    assert res.exit_code == 0
    lines = [ln for ln in res.output.splitlines() if ln.strip()]
    assert lines[0].startswith("体检")   # summary 打头，不带 ✓/✗ 前缀
    assert not lines[0].startswith(("✓", "✗"))
    glyph_lines = [ln for ln in lines[1:] if ln.startswith(("✓", "✗"))]
    assert len(glyph_lines) == 3   # 三条体检项都渲染在 summary 之后


def test_doctor_agentsociety渲染三条勾叉退出0():
    res = runner.invoke(app, ["sim", "doctor", "--simulator", "agentsociety"])
    assert res.exit_code == 0
    lines = [ln for ln in res.output.splitlines() if ln.strip()]
    assert lines[0].startswith("体检") and not lines[0].startswith(("✓", "✗"))
    glyph_lines = [ln for ln in lines[1:] if ln.startswith(("✓", "✗"))]
    assert len(glyph_lines) == 3


def test_doctor样本缺席仍退出0且叉行有人话(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))   # 空家目录：哪家克隆都不在
    res = runner.invoke(app, ["sim", "doctor", "--simulator", "agentsociety"])
    assert res.exit_code == 0                   # 体检是报告不是门
    bad = [ln for ln in res.output.splitlines() if ln.startswith("✗")]
    assert any("样本数据在" in ln and "不在" in ln for ln in bad)
