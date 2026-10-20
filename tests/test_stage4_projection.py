"""投影：规范源只有一份，各宿主的安装形状是从它算出来的。

借 AERS 的 plugins/build_plugins.py——那边把 skills/00.x 投影成插件形状，
源目录一个字不动。这里同理：`.claude/skills/` 是唯一的源，
dsh 的安装树是投影结果，**不是一份分叉的副本**。分叉的副本会漂，投影不会。
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from oss import hosts
from oss.hosts import doc as doc_mod
from oss.hosts import project as proj

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / ".claude" / "skills"


def _digest(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ─────────────────────────────────────────────── 投影结果

def test_claude_code的投影就是原样复制(tmp_path: Path) -> None:
    """开发宿主的投影必须是恒等变换——否则源头与它的安装树会各说各话。"""
    out = tmp_path / "cc"
    proj.build(hosts.load("claude-code"), SRC, out)
    assert _digest(out) == _digest(SRC)


def test_dsh的投影把allowed_tools降级成注释(tmp_path: Path) -> None:
    out = tmp_path / "dsh"
    proj.build(hosts.load("dsh"), SRC, out)
    for name in ("social-research", "social-paper", "social-idea",
                 "social-review", "social-identification"):
        t = (out / name / "SKILL.md").read_text(encoding="utf-8")
        assert "\nallowed-tools:" not in "\n" + t, f"{name}：dsh 不认这个键，不该留在行首"
        assert "# allowed-tools:" in t, f"{name}：原值要留成注释，不能凭空消失"
        assert "dsh" in t.split("# allowed-tools:")[1].splitlines()[0], "注释里要说明是谁忽略它"
        # 其余 frontmatter 一个字不许动
        assert "name: " + name in t and "description:" in t and "argument-hint:" in t


def test_dsh的投影不动SKILL之外的任何文件(tmp_path: Path) -> None:
    out = tmp_path / "dsh"
    proj.build(hosts.load("dsh"), SRC, out)
    a, b = _digest(out), _digest(SRC)
    assert set(a) == set(b), "文件集合不该变"
    diff = sorted(k for k in a if a[k] != b[k])
    assert diff == sorted(k for k in b if k.endswith("SKILL.md")), diff


def test_投影是幂等的(tmp_path: Path) -> None:
    """第二次跑不该有任何内容变化——投影有随机性就没法做 drift 检查。"""
    out = tmp_path / "dsh"
    prof = hosts.load("dsh")
    proj.build(prof, SRC, out)
    first = _digest(out)
    proj.build(prof, SRC, out)
    assert _digest(out) == first


def test_投影后的相对链接仍然指得到(tmp_path: Path) -> None:
    out = tmp_path / "dsh"
    rep = proj.build(hosts.load("dsh"), SRC, out)
    assert rep.files > 40
    for f in sorted(out.rglob("*.md")):
        for m in proj._LINK.finditer(f.read_text(encoding="utf-8")):
            t = m.group(1).split("#")[0].strip()
            if not t or "://" in t or t.startswith("/"):
                continue
            assert (f.parent / t).exists(), f"{f.relative_to(out)} → {t}"


# ─────────────────────────────────────────────── 拒绝

def _fake_src(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "demo"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\nargument-hint: \"x\"\n"
        "allowed-tools: Bash(oss *), Read\n---\n\n输入：$ARGUMENTS\n"
        "如果上面显示的是字面的 `$ARGUMENTS`，说明宿主不替换占位符。\n"
        "见 [notes](notes.md)。\n", encoding="utf-8")
    (src / "notes.md").write_text("# notes\n", encoding="utf-8")
    return src.parent


def test_不替换占位符的宿主要求说明书写明这件事(tmp_path: Path) -> None:
    """dsh 不替换 $ARGUMENTS。说明书没写这句注释，模型就会把字面量当参数。
    **这不该由投影自动补**——那句话要写在上下文里才有用，补一行进去只是安慰。"""
    src = _fake_src(tmp_path)
    p = src / "demo" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8").replace("宿主不替换占位符", "随便写点别的"),
                 encoding="utf-8")
    with pytest.raises(proj.BuildError) as exc:
        proj.build(hosts.load("dsh"), src, tmp_path / "out")
    assert "宿主不替换占位符" in str(exc.value)


def test_断链会拒绝构建(tmp_path: Path) -> None:
    src = _fake_src(tmp_path)
    (src / "demo" / "notes.md").unlink()
    with pytest.raises(proj.BuildError) as exc:
        proj.build(hosts.load("dsh"), src, tmp_path / "out")
    assert "notes.md" in str(exc.value)


def test_frontmatter认不出时拒绝而不是静默不降级(tmp_path: Path) -> None:
    """CRLF 换行（或 BOM）会让 frontmatter 正则匹配不上。
    原来「认不出就原样返回」= 把带 allowed-tools 的说明书原样铺给不认它的宿主：
    **静默降级变成了静默不降级**，而安装和 drift 检查全绿。"""
    src = _fake_src(tmp_path)
    p = src / "demo" / "SKILL.md"
    p.write_bytes(p.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    with pytest.raises(proj.BuildError) as exc:
        proj.build(hosts.load("dsh"), src, tmp_path / "out")
    assert "frontmatter" in str(exc.value), exc.value


def test_frontmatter认不出但也没有白名单时原样放行(tmp_path: Path) -> None:
    """没有 allowed-tools 就没有什么可降级的，认不出也不该拦——拦了等于把
    「没有 frontmatter 的普通 md」也变成安装失败。"""
    assert proj.strip_allowed_tools("就是一段正文\n", "dsh") == "就是一段正文\n"


def test_拒绝时目标目录不留残骸(tmp_path: Path) -> None:
    """半棵坏树比没有树更糟：宿主会加载它，而所有检查都以为装过了。"""
    src = _fake_src(tmp_path)
    out = tmp_path / "out"
    proj.build(hosts.load("dsh"), src, out)
    good = _digest(out)
    (src / "demo" / "notes.md").unlink()
    with pytest.raises(proj.BuildError):
        proj.build(hosts.load("dsh"), src, out)
    assert _digest(out) == good, "构建失败却动了上一版安装树"


def test_多行写法的allowed_tools宁可拒绝也不乱删(tmp_path: Path) -> None:
    src = _fake_src(tmp_path)
    p = src / "demo" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "allowed-tools: Bash(oss *), Read", "allowed-tools:\n  - Bash(oss *)\n  - Read"),
        encoding="utf-8")
    with pytest.raises(proj.BuildError) as exc:
        proj.build(hosts.load("dsh"), src, tmp_path / "out")
    assert "多行" in str(exc.value)


def test_投影到源头上会被拒绝(tmp_path: Path) -> None:
    """`--project` 在本仓库里跑就是这个情形：目标正好是规范源，写下去等于自己覆盖自己。"""
    src = _fake_src(tmp_path)
    with pytest.raises(proj.BuildError) as exc:
        proj.build(hosts.load("claude-code"), src, src)
    assert "源头" in str(exc.value)


def test_不force时跳过已存在的技能(tmp_path: Path) -> None:
    src = _fake_src(tmp_path)
    out = tmp_path / "out"
    proj.build(hosts.load("dsh"), src, out)
    (out / "demo" / "SKILL.md").write_text("我改过了\n", encoding="utf-8")
    rep = proj.build(hosts.load("dsh"), src, out, force=False)
    assert rep.skipped == ["demo"] and rep.skills == []
    assert (out / "demo" / "SKILL.md").read_text(encoding="utf-8") == "我改过了\n"


# ─────────────────────────────────────────────── 断链判定的两条边界

def _two_skills(tmp_path: Path) -> Path:
    """技能 a 里的链接指向技能 b 的文件——跨技能的相对链接，说明书里真有这种写法。"""
    src = tmp_path / "src"
    for name in ("a", "b"):
        d = src / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n\n输入：$ARGUMENTS\n"
            "如果上面显示的是字面的 `$ARGUMENTS`，说明宿主不替换占位符。\n"
            + ("见 [x](../b/x.md)。\n" if name == "a" else ""), encoding="utf-8")
    (src / "b" / "x.md").write_text("# x\n", encoding="utf-8")
    return src


def test_链接指向这一轮被跳过的技能不算断链(tmp_path: Path) -> None:
    """b 这一轮没重投（盘上已有、又没给 --force），它的文件仍然在盘上——
    这时候把 a → b 的链接判成断链，会让「只补装一个技能」永远装不了。"""
    src = _two_skills(tmp_path)
    out = tmp_path / "out"
    prof = hosts.load("dsh")
    proj.build(prof, src, out)
    shutil.rmtree(out / "a")                       # 只有 a 要重投
    rep = proj.build(prof, src, out, force=False)
    assert rep.skills == ["a"] and rep.skipped == ["b"], rep
    assert (out / "b" / "x.md").exists()


def test_逃出技能根的链接一律算断链(tmp_path: Path) -> None:
    """盘上真有那个 README，开发机上点得开——但技能只按技能根整棵搬走，
    根外的东西不会跟过去。**这种链接在别人机器上才会断，所以必须在这儿断。**"""
    src = _fake_src(tmp_path)
    (tmp_path / "README.md").write_text("# 真在盘上\n", encoding="utf-8")
    assert (src / "demo" / ".." / ".." / "README.md").exists()
    p = src / "demo" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8") + "见 [根外](../../README.md)。\n",
                 encoding="utf-8")
    with pytest.raises(proj.BuildError) as exc:
        proj.build(hosts.load("dsh"), src, tmp_path / "out")
    assert "../../README.md" in str(exc.value), exc.value


def test_索引文件按档案要求写出(tmp_path: Path) -> None:
    """四个真档案都不要索引（dsh 会把技能根下的平铺 .md 当成一份技能），
    但这条通道得能用，且得是确定性的。"""
    src = _fake_src(tmp_path)
    prof = hosts.HostProfile(name="fake", label="Fake", skills_dir="~/x",
                             index_file="SKILLS.md")
    out = tmp_path / "out"
    proj.build(prof, src, out)
    assert (out / "SKILLS.md").read_text(encoding="utf-8") == \
        "# OSS 技能索引（Fake）\n\n- `demo`：/demo\n"


def test_四个真宿主都不要索引文件() -> None:
    for name in hosts.available():
        assert hosts.load(name).index_file == "", f"{name}：改成要索引之前先想清楚 dsh 那条"


# ─────────────────────────────────────────────── 校验器与投影树的关系

def test_校验器对源头仍然全绿() -> None:
    """投影会删掉 allowed-tools，而 validate_skills 要求它在。
    **两者不冲突，因为校验器核的是源头**——这条测试就是那句话的凭据。"""
    spec = importlib.util.spec_from_file_location(
        "validate_skills", ROOT / "scripts" / "validate_skills.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.validate_all(SRC) == []


# ─────────────────────────────────────────────── 托管块

DOC = "# 项目说明\n\n随便写点什么。\n"


def test_托管块插入更新删除都幂等() -> None:
    once = doc_mod.upsert(DOC, "第一版")
    assert doc_mod.BEGIN in once and doc_mod.END in once and "第一版" in once
    assert doc_mod.upsert(once, "第一版") == once, "重复插入不该长出第二块"

    twice = doc_mod.upsert(once, "第二版")
    assert "第一版" not in twice and "第二版" in twice
    assert twice.count(doc_mod.BEGIN) == 1

    gone = doc_mod.upsert(twice, "")
    assert doc_mod.BEGIN not in gone and "# 项目说明" in gone
    assert doc_mod.upsert(gone, "") == gone


def test_托管块不碰块外的正文() -> None:
    text = "前面\n\n<!-- OSS:BEGIN -->\n旧\n<!-- OSS:END -->\n\n后面\n"
    out = doc_mod.upsert(text, "新")
    assert out.startswith("前面\n") and out.endswith("后面\n") and "新" in out


def test_两个托管块时拒绝改而不是乱猜() -> None:
    text = DOC + doc_mod.upsert("", "A") + doc_mod.upsert("", "B")
    with pytest.raises(doc_mod.DocConflict):
        doc_mod.upsert(text, "C")


def test_托管块正文含反斜杠也不会被当成转义() -> None:
    """re.sub 的替换串里 \\g、\\1 有特殊含义；技能目录在 Windows 上就带反斜杠。"""
    once = doc_mod.upsert(DOC, r"装在：C:\Users\x\.dsh\skills")
    assert r"C:\Users\x\.dsh\skills" in doc_mod.upsert(once, r"装在：C:\Users\x\.dsh\skills")


def test_插入时块前恰好一个空行不出现三连换行() -> None:
    """正文已经以 `\\n\\n` 收尾（末尾已经空出一行）时，插入逻辑不该再叠一个换行，
    否则块前会空出两行、字符串里连续出现三个 `\\n`。"""
    out = doc_mod.upsert("abc\n\n", "B")
    assert "\n\n\n" not in out
    assert out.startswith("abc\n\n" + doc_mod.BEGIN), out

    out_one_nl = doc_mod.upsert("abc\n", "B")
    assert "\n\n\n" not in out_one_nl
    assert out_one_nl.startswith("abc\n\n" + doc_mod.BEGIN), out_one_nl

    out_no_nl = doc_mod.upsert("abc", "B")
    assert "\n\n\n" not in out_no_nl
    assert out_no_nl.startswith("abc\n\n" + doc_mod.BEGIN), out_no_nl


def test_托管块正文是确定性的() -> None:
    prof = hosts.load("dsh")
    names = ["social-idea", "social-research"]
    a = doc_mod.block_body(prof, names, Path("~/.dsh/skills"))
    b = doc_mod.block_body(prof, names, Path("~/.dsh/skills"))
    assert a == b
    assert "DeepSeek Harness" in a and "/social-idea" in a
    assert "oss install-skills --host dsh --force" in a
