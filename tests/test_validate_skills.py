"""说明书校验器的测试。**故意写坏一份，对应检查必须变红**——那是这些检查的检查。"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".claude" / "skills"


def _mod():
    spec = importlib.util.spec_from_file_location("validate_skills", ROOT / "scripts" / "validate_skills.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


V = _mod()


def test_真正的说明书全部合法():
    problems = V.validate_all(SKILLS)
    assert problems == [], "\n".join(problems)


def _copy(tmp_path: Path) -> Path:
    dst = tmp_path / "skills"
    shutil.copytree(SKILLS, dst)
    return dst


def test_frontmatter缺字段会红(tmp_path):
    d = _copy(tmp_path)
    p = d / "social-idea" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8").replace("argument-hint:", "hint:"), encoding="utf-8")
    assert any("argument-hint" in x for x in V.validate_all(d))


def test_写了不存在的oss命令会红(tmp_path):
    d = _copy(tmp_path)
    p = d / "social-research" / "stages" / "6-run.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n\n    oss route --apply x\n    oss doctorx --run <目录>\n",
                 encoding="utf-8")
    bad = V.validate_all(d)
    assert any("doctorx" in x for x in bad), bad
    assert not any("route --apply" in x for x in bad), "带 -- 选项的不该被当成子命令"


def test_断链会红(tmp_path):
    d = _copy(tmp_path)
    p = d / "social-research" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8").replace("stages/6-run.md", "stages/6-runn.md"), encoding="utf-8")
    assert any("6-runn.md" in x for x in V.validate_all(d))


def test_manifest里指到不存在的文件会红(tmp_path):
    d = _copy(tmp_path)
    p = d / "social-research" / "manifest.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace("rules/explore.md", "rules/explore2.md"), encoding="utf-8")
    assert any("explore2.md" in x for x in V.validate_all(d))


def test_四栏缺一栏会红(tmp_path):
    d = _copy(tmp_path)
    p = d / "social-research" / "stages" / "5-plan.md"
    p.write_text(p.read_text(encoding="utf-8").replace("## 禁止", "## 别做"), encoding="utf-8")
    assert any("禁止" in x and "5-plan" in x for x in V.validate_all(d))


def test_必备句子被删会红(tmp_path):
    d = _copy(tmp_path)
    p = d / "social-research" / "rules" / "explore.md"
    p.write_text(p.read_text(encoding="utf-8").replace("不许用 python 直接跑回归", "尽量别自己跑回归"),
                 encoding="utf-8")
    assert any("不许用 python 直接跑回归" in x for x in V.validate_all(d))


def test_路由器超一百行会红(tmp_path):
    d = _copy(tmp_path)
    p = d / "social-review" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n" * 200, encoding="utf-8")
    assert any("100 行" in x for x in V.validate_all(d))


def test_裸的相对指路断了也会红(tmp_path):
    """散文里 `（见 ../rules/stop-conditions.md）` 比 `[…](…)` 还常见。
    文件一改名，它同样指空——原来的检查只看 Markdown 链接，一条都拦不住。"""
    d = _copy(tmp_path)
    p = d / "social-research" / "stages" / "8-review.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n（循环规则见 ../rules/nope.md）\n",
                 encoding="utf-8")
    bad = V.validate_all(d)
    assert any("nope.md" in x and "指路" in x for x in bad), bad


def test_裸指路只报一次(tmp_path):
    """同一处既是 Markdown 链接又长得像裸指路——按 span 去重，不许报两遍。"""
    d = _copy(tmp_path)
    p = d / "social-research" / "stages" / "8-review.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n[../rules/nope.md](../rules/nope.md)\n",
                 encoding="utf-8")
    assert len([x for x in V.validate_all(d) if "nope.md" in x]) == 1


def test_禁句复活会红(tmp_path):
    """「这份数据回答不了这个问题」是阶段 1 拿掉的换题借口。
    删掉容易、悄悄写回来也容易——所以这条得有反例钉着。"""
    d = _copy(tmp_path)
    p = d / "social-research" / "stages" / "4-feasibility.md"
    p.write_text(p.read_text(encoding="utf-8") + "\n\n功效不够就说明这份数据回答不了这个问题。\n",
                 encoding="utf-8")
    bad = V.validate_all(d)
    assert any("这份数据回答不了这个问题" in x for x in bad), bad


def test_name跟目录名对不上会红(tmp_path):
    """`name:` 是宿主认技能用的键。跟目录名一错位，`/social-idea` 就叫不起来。"""
    d = _copy(tmp_path)
    p = d / "social-idea" / "SKILL.md"
    p.write_text(p.read_text(encoding="utf-8").replace("name: social-idea", "name: social-ideas", 1),
                 encoding="utf-8")
    bad = V.validate_all(d)
    assert any("跟目录名对不上" in x and "social-ideas" in x for x in bad), bad
