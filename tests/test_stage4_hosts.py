"""阶段 4：宿主是参数，不是假设。

事故形状（轻，但每天发生）：dsh 用户跑 `oss doctor`，最后一行永远是
`✗ Claude Code CLI  未在 PATH 中找到 claude`——**一条他修不了、也不该修的红线**。
长期挂着一条假红线，下一条真红线就没人看了。

更重的一条在执行者身份上：宿主不同，`EXECUTOR_MODEL` 从哪来也不同。
不知道执行者是谁，「跨家族审稿」这句话就没有内容——所以问不到时拒绝，不猜。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from oss import hosts
from oss.hosts import checks

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"claude-code", "dsh", "codex", "openclaw", "workbuddy"}


# ─────────────────────────────────────────────── 档案本身

def test_五个宿主档案都在() -> None:
    assert set(hosts.available()) == EXPECTED


def test_每份档案都能加载且字段自洽() -> None:
    for name in hosts.available():
        p = hosts.load(name)
        assert p.name == name, f"{name}：档案里的 name 跟文件名对不上"
        assert p.label, f"{name}：label 不许空"
        assert p.skills_dir.startswith(("~/", "${")), \
            f"{name}：skills_dir 该是用户级路径（带 ~，或 ${{VAR:-~/缺省}}）"
        assert p.invocation == "/name"
        assert p.limits, f"{name}：limits 不许空——没有限制也要写「无」"
        assert p.source, f"{name}：source 不许空——宿主事实必须有出处"
        for c in p.doctor_checks:
            assert c in checks.CHECKS, f"{name}：doctor_checks 里的 {c} 没有实现"


def test_不认识的宿主报错里带已知清单() -> None:
    with pytest.raises(hosts.UnknownHost) as exc:
        hosts.load("cursor")
    assert "dsh" in str(exc.value), "报错要告诉用户有哪些宿主可选"


def test_档案里写错字段名不会被静默吃掉(tmp_path: Path, monkeypatch) -> None:
    """**拼错一个键就静默走缺省**，是配置文件最常见的坑：
    `honors_allowed_tool`（少个 s）会让 dsh 悄悄拿到带 allowed-tools 的说明书。"""
    (tmp_path / "fake.json").write_text(json.dumps(
        {"name": "fake", "label": "F", "skills_dir": "~/x", "skils_dir": "~/typo"},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(hosts, "HERE", tmp_path)
    with pytest.raises(hosts.UnknownHost) as exc:
        hosts.load("fake")
    assert "skils_dir" in str(exc.value)


def test_doctor检查名写错也不会被静默吃掉(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "fake.json").write_text(json.dumps(
        {"name": "fake", "label": "F", "skills_dir": "~/x",
         "doctor_checks": ["claude-cli-on-pathh"]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(hosts, "HERE", tmp_path)
    with pytest.raises(hosts.UnknownHost) as exc:
        hosts.load("fake")
    assert "claude-cli-on-pathh" in str(exc.value)


# ─────────────────────────────────────────────── 技能目录里的环境变量

def test_dsh的技能目录跟着DSH_HOME走(monkeypatch) -> None:
    """dsh 的用户级技能根是 `$DSH_HOME/skills`（skill-filesystem 的 rank 400），
    `~/.dsh` 只是 DSH_HOME 的缺省值。写死缺省值的话，设过 DSH_HOME 的用户会把技能
    **装到宿主根本不看的地方**——而 install-skills 会显示全绿。"""
    monkeypatch.setenv("DSH_HOME", "/opt/dsh")
    assert hosts.load("dsh").user_dir() == Path("/opt/dsh/skills")


def test_没设DSH_HOME时落到缺省的家目录(monkeypatch) -> None:
    monkeypatch.delenv("DSH_HOME", raising=False)
    assert hosts.load("dsh").user_dir() == Path("~/.dsh/skills").expanduser()


def test_DSH_HOME不影响别的宿主(monkeypatch) -> None:
    """展开只认档案里真写了 `${...}` 的那一份；别的宿主的路径一个字节不许动。"""
    monkeypatch.setenv("DSH_HOME", "/opt/dsh")
    for name in ("claude-code", "codex", "openclaw", "workbuddy"):
        p = hosts.load(name)
        assert p.user_dir() == Path(p.skills_dir).expanduser(), name
        assert "/opt/dsh" not in str(p.user_dir()), name


def test_空的环境变量也走缺省(monkeypatch) -> None:
    """`export DSH_HOME=` 是「设了但空」——按空串算就成了 `/skills`。"""
    monkeypatch.setenv("DSH_HOME", "")
    assert hosts.load("dsh").user_dir() == Path("~/.dsh/skills").expanduser()


# ─────────────────────────────────────────────── 探测

def test_探测_环境变量比目录标记硬(tmp_path: Path) -> None:
    """目录标记说的是「这个项目配过谁」，环境变量说的是「现在谁在跑我」。后者才是问题的答案。"""
    (tmp_path / ".claude").mkdir()
    assert hosts.detect(tmp_path, env={"DSH_SHELL": "1"}) == "dsh"


def test_探测_嵌套时内层宿主赢(tmp_path: Path) -> None:
    """在 Claude Code 的终端里起 dsh：两个变量都在，真正跑技能的是 dsh。"""
    assert hosts.detect(tmp_path, env={"CLAUDECODE": "1", "DSH_SHELL": "1"}) == "dsh"


def test_探测_两个目录标记同时在时罕见的那个赢(tmp_path: Path) -> None:
    """`.claude/` 几乎每个被 Claude Code 碰过的仓库都有（本仓库就有），
    `.dsh/` 只有专门配过的项目才有——**罕见的标记是更强的证据**。
    但二义必须说出来，不能让用户以为只有一种可能。"""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".dsh").mkdir()
    name, why = hosts.detect_reason(tmp_path, env={})
    assert name == "dsh"
    assert ".claude" in why and "--host" in why, why


def test_探测_只有agents目录时是codex(tmp_path: Path) -> None:
    (tmp_path / ".agents").mkdir()
    assert hosts.detect(tmp_path, env={}) == "codex"


def test_探测_什么都没有就按缺省(tmp_path: Path) -> None:
    name, why = hosts.detect_reason(tmp_path, env={})
    assert name == hosts.DEFAULT_HOST == "claude-code"
    assert "缺省" in why


def test_探测_OSS_HOST盖过一切(tmp_path: Path) -> None:
    (tmp_path / ".dsh").mkdir()
    assert hosts.detect(tmp_path, env={"DSH_SHELL": "1", "OSS_HOST": "codex"}) == "codex"


def test_优先级表覆盖全部宿主() -> None:
    """加了新档案却忘了排进 HOST_ORDER，探测就永远看不见它。"""
    assert set(hosts.HOST_ORDER) == set(hosts.available())


# ─────────────────────────────────────────────── 用户文档不许漂移

def test_用户文档里的宿主表跟档案一致() -> None:
    """表是手写的，但**不许跟档案对不上**：这条测试就是那份表的门。"""
    doc = (ROOT / "docs" / "user" / "hosts.md").read_text(encoding="utf-8")
    for name in hosts.available():
        p = hosts.load(name)
        for bit in (p.name, p.label, p.skills_dir, p.limits):
            assert bit in doc, f"docs/user/hosts.md 少了 {name} 的「{bit}」"


# ─────────────────────────────────────────────── WorkBuddy：未实测，取值必须保守

def test_workbuddy的三个未知项都取保守方向() -> None:
    """2026-09-07 在 5.5.3 上初步实测过：技能认得出、bash 跑得了本机命令行、
    说明书的判断规则会被执行。但**还有字段是猜的，猜的就得取「猜错了也不伤人」的那一边**：

    - `$ARGUMENTS`：那次是空参数进来的，没测到 → 按不替换准备。只要求说明书带上
      那句注释，宿主真会替换也无害；反过来（按替换准备而宿主不替换）模型就拿不到参数
    - `allowed-tools`：有出处（frontmatter 认 Read/Write/Bash）→ 保留原行
    - 项目指令文件：无出处 → 不写。往宿主根本不读的文件里写托管块，用户会以为配过了
    - `executor_model_env`：扫过 app.asar 全部 `process.env.*`，没有上报当前模型的
      变量 → 留空，执行者身份只能靠 `.env` 的 EXECUTOR_MODEL
    """
    p = hosts.load("workbuddy")
    assert p.substitutes_arguments is False
    assert p.honors_allowed_tools is True
    assert p.managed_doc == "", "读不读 CLAUDE.md / AGENTS.md 没有出处，不许写"
    assert p.executor_model_env == (), "没有出处说它暴露当前模型"
    assert "$ARGUMENTS 仍未测到" in p.limits, (
        "substitutes_arguments 还是猜的，limits 就必须说出来——"
        "字段改成实测之前，这句话不许从文档里消失"
    )


def test_workbuddy按项目标记探得到(tmp_path: Path) -> None:
    (tmp_path / ".workbuddy").mkdir()
    assert hosts.detect(tmp_path, env={}) == "workbuddy"


def test_workbuddy的标记比claude的通用标记硬(tmp_path: Path) -> None:
    """`.claude/` 几乎每个仓库都有，`.workbuddy/` 只有专门配过的项目才有。"""
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".workbuddy").mkdir()
    assert hosts.detect(tmp_path, env={}) == "workbuddy"
