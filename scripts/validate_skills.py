#!/usr/bin/env python3
"""说明书自己的门。

工具箱有五百多个测试，说明书原来一个都没有——`oss eval` 补上了「执行者照没照说明书做」，
这里补的是另一半：**说明书本身写得合不合法**。

七类检查，全是机械的：
1. frontmatter 四个字段齐、`name` 跟目录名一致
2. 提到的每个 `oss <命令>` 都真的存在（从 typer 的命令树里取，不是硬编码清单）
3. 每个相对链接都指得到文件——`[…](…)` 和散文里裸写的 `../rules/x.md` 都算（借 research-plugins 的 validate.ts）
4. 每份契约（stages/*.md、contract.md）都有四栏
5. 阶段 1 定下的六句硬话在它们该在的文件里
6. 每份 SKILL.md 都保留 $ARGUMENTS；路由器（分层技能的）另外不超过 100 行
7. 每份技能都有 manifest.yaml，里面点到的路径都存在

**它检查的是文件，不是运行目录**——运行目录归 oss/core/skillcheck.py。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS = ROOT / ".claude" / "skills"

#: 路由器的行数上限。超过就不是路由器了。
MAX_ROUTER_LINES = 100

#: frontmatter 必须有的四个字段
FRONTMATTER_KEYS = ("name", "description", "argument-hint", "allowed-tools")

#: 契约必须有的四栏（提案第六节）。按前缀匹配——「完成条件」后面常带补充说明。
FOUR_COLUMNS = ("## 输入", "## 产出", "## 完成条件", "## 禁止")

#: 阶段 1 的六句硬话，各自该在哪个文件里。**句子一个字都不能改。**
#: 这张表跟 tests/test_stage1_skill_rules.py 点的名必须一致：那份测试在阶段 2 拆完之后
#: 重新点了名（路由器 / rules/ / reference/），这里照抄它点的位置。
REQUIRED_SENTENCES: dict[str, tuple[str, ...]] = {
    "social-research/SKILL.md": ("run_state.json", "续跑", "$ARGUMENTS", "宿主不替换占位符"),
    "social-research/rules/ask-user.md": ("什么时候可以问用户", "手册没列的情况"),
    "social-research/rules/stop-conditions.md": (
        "保留原设计", "降级为关联", "不换题目、不换数据、不问用户", "export_with_open_items"),
    "social-research/rules/explore.md": ("oss run --explore --for", "不许用 python 直接跑回归"),
    "social-review/reference/after-verdict.md": ("export_with_open_items",),
}

#: 这些句子一个字都不许再出现（整份技能范围内）
FORBIDDEN_SENTENCES: dict[str, tuple[str, ...]] = {
    "social-research": ("这份数据回答不了这个问题",),
    "social-review": ("停下来告诉用户哪里改不动",),
}

#: `oss <命令> [<子命令>]`。分隔符只认同行的空格与制表符——跨行的 `oss`\n`某个小写词`
#: 不是一条命令，是两句话。`oss 命令` / `oss *)` / 行尾孤零零的 `oss` 也都不会进来：
#: 命令名必须是小写 ASCII，中文和 `*` 都不满足。
_OSS = re.compile(r"(?<![\w./-])oss[ \t]+([a-z][a-z-]*)(?:[ \t]+([a-z][a-z-]*))?")
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
#: 裸的相对指路：`../rules/stop-conditions.md`、`（见 stages/8-review.md）`。
#: 散文和行内代码里这么写的比 Markdown 链接还多——文件一改名，它们同样指空，
#: 而原来的检查只看 `[…](…)`，一条都拦不住。目录名限定在这三个，
#: 免得把正文里顺口提到的 `.md` 当成指路。
_BARE_LINK = re.compile(r"(?<![\w./-])(?:\.\./)?(?:rules|stages|reference)/[\w.\-]+\.md")
_FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def typer_commands() -> set[str]:
    """从 typer 的命令树里取全部命令名，`oss --help` 出什么这里就有什么。

    typer 0.27 里 `c.name` 常是 None（没在 `@app.command()` 里显式给名字的那些），
    真正的命令名由函数名推出来：`profile()` → `profile`、`install_skills()` → `install-skills`。
    显式给了的以显式的为准：`run_analysis()` 挂在 `@app.command("run")` 下，命令是 `run`。
    """
    if str(ROOT) not in sys.path:      # 直接 `python scripts/validate_skills.py` 时 sys.path[0] 是 scripts/
        sys.path.insert(0, str(ROOT))
    from oss.cli.main import app

    def walk(t, prefix: str = "") -> list[str]:
        out = []
        for c in t.registered_commands:
            n = c.name or (c.callback.__name__.replace("_", "-") if c.callback else "")
            out.append((prefix + n).strip())
        for g in t.registered_groups:
            out += walk(g.typer_instance, prefix + (g.name or "") + " ")
        return out

    return set(walk(app))


def _md_files(skill: Path) -> list[Path]:
    return sorted(skill.rglob("*.md"))


def _is_layered(skill: Path) -> bool:
    """分层技能 = 有 manifest 且真的分了层（四栏契约或阶段轴）。

    只有分层技能的 SKILL.md 才是「路由器」，才受 100 行上限约束。
    `social-identification` 有 manifest（声明「不分层」），但正文就在 SKILL.md 里，不受这条管。
    """
    return (skill / "manifest.yaml").exists() and (
        (skill / "contract.md").exists() or (skill / "stages").exists())


def _check_commands(skill: Path, known: set[str]) -> list[str]:
    out = []
    for f in _md_files(skill):
        for m in _OSS.finditer(f.read_text(encoding="utf-8")):
            a, b = m.group(1), m.group(2)
            if (b and f"{a} {b}" in known) or a in known:
                continue
            out.append(f"{f.relative_to(skill.parent)}：`{m.group(0)}` 不是 oss 的命令")
    return out


def _check_links(skill: Path) -> list[str]:
    """Markdown 链接和裸的相对指路都核。

    裸指路在 `[…](…)` 里的那份已经核过了——按 span 去重，不重复报同一处。
    """
    out = []
    for f in _md_files(skill):
        text = f.read_text(encoding="utf-8")
        spans: list[tuple[int, int]] = []
        for m in _LINK.finditer(text):
            spans.append(m.span())
            target = m.group(1).split("#")[0].strip()
            if not target or "://" in target or target.startswith("/"):
                continue
            if not (f.parent / target).exists():
                out.append(f"{f.relative_to(skill.parent)}：链接 `{target}` 指不到文件")
        for m in _BARE_LINK.finditer(text):
            if any(a <= m.start() < b for a, b in spans):
                continue                      # 这一处是 Markdown 链接，上面核过了
            if not (f.parent / m.group(0)).exists():
                out.append(f"{f.relative_to(skill.parent)}：指路 `{m.group(0)}` 指不到文件")
    return out


def _check_frontmatter(skill: Path) -> list[str]:
    p = skill / "SKILL.md"
    if not p.exists():
        return [f"{skill.name}：没有 SKILL.md"]
    m = _FM.match(p.read_text(encoding="utf-8"))
    if not m:
        return [f"{skill.name}/SKILL.md：没有 frontmatter"]
    body = m.group(1)
    out = [f"{skill.name}/SKILL.md：frontmatter 缺 {k}"
           for k in FRONTMATTER_KEYS if not re.search(rf"^{k}:", body, re.M)]
    name = re.search(r"^name:\s*(\S+)", body, re.M)
    if name and name.group(1) != skill.name:
        out.append(f"{skill.name}/SKILL.md：name 是 {name.group(1)}，跟目录名对不上")
    return out


def _check_router(skill: Path) -> list[str]:
    """`$ARGUMENTS` 每份 SKILL.md 都要有；100 行上限只管分层技能的路由器。

    拿不到参数是所有技能共有的失败方式，跟分不分层没关系——不分层的技能同样是被
    `/social-xxx <参数>` 叫起来的。行数不一样：不分层的技能正文就在 SKILL.md 里，长是应该的。
    """
    p = skill / "SKILL.md"
    if not p.exists():
        return []
    t = p.read_text(encoding="utf-8")
    out = []
    if _is_layered(skill):
        n = len(t.splitlines())
        if n > MAX_ROUTER_LINES:
            out.append(f"{skill.name}/SKILL.md：{n} 行，超过 {MAX_ROUTER_LINES} 行——路由器该短")
    if "$ARGUMENTS" not in t:
        out.append(f"{skill.name}/SKILL.md：没有 $ARGUMENTS 行，Claude Code 侧会拿不到参数")
    return out


def _contracts(skill: Path) -> list[Path]:
    out = [p for p in sorted((skill / "stages").glob("*.md")) if not p.name.endswith(".notes.md")]
    if (skill / "contract.md").exists():
        out.append(skill / "contract.md")
    return out


def _check_four_columns(skill: Path) -> list[str]:
    out = []
    for p in _contracts(skill):
        lines = p.read_text(encoding="utf-8").splitlines()
        for h in FOUR_COLUMNS:
            if not any(l.startswith(h) for l in lines):
                out.append(f"{p.relative_to(skill.parent)}：四栏缺「{h.removeprefix('## ')}」")
    return out


def _check_manifest(skill: Path) -> list[str]:
    p = skill / "manifest.yaml"
    if not p.exists():
        return [f"{skill.name}：缺 manifest.yaml"]
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return [f"{skill.name}/manifest.yaml：解析不了——{exc}"]
    out = []
    if d.get("name") != skill.name:
        out.append(f"{skill.name}/manifest.yaml：name 是 {d.get('name')}，跟目录名对不上")
    paths = list(d.get("always_load") or [])
    for axis in (d.get("axes") or {}).values():
        paths += list(((axis or {}).get("values") or {}).values())
    for r in ((d.get("references") or {}).get("on_demand") or []):
        paths.append(r.get("path", ""))
    # 跨目录指向 docs/ 正典是**有意允许**的（T3 判例：social-sim 的 on_demand
    # 指 ../../../docs/user/sim-box.md 等正典文件——复制进技能目录只会漂移）。
    # 下面只查「指到的文件在不在」，不查「越没越界」；别把这里「修复」成
    # 边界检查，会砸掉 social-sim。
    for rel in paths:
        if rel and not (skill / rel).exists():
            out.append(f"{skill.name}/manifest.yaml：指到的 `{rel}` 不存在")
    return out


def _check_sentences(skills_dir: Path) -> list[str]:
    out = []
    for rel, sentences in REQUIRED_SENTENCES.items():
        p = skills_dir / rel
        if not p.exists():
            out.append(f"{rel}：文件不在——阶段 1 的硬话没地方放")
            continue
        t = p.read_text(encoding="utf-8")
        out += [f"{rel}：少了必备句子「{s}」" for s in sentences if s not in t]
    for skill_name, sentences in FORBIDDEN_SENTENCES.items():
        d = skills_dir / skill_name
        if not d.exists():
            continue
        blob = "\n".join(p.read_text(encoding="utf-8") for p in _md_files(d))
        out += [f"{skill_name}：不许再出现的句子还在——「{s}」" for s in sentences if s in blob]
    return out


def validate_all(skills_dir: Path = DEFAULT_SKILLS) -> list[str]:
    known = typer_commands()
    problems: list[str] = []
    for skill in sorted(p for p in Path(skills_dir).iterdir() if p.is_dir()):
        problems += _check_frontmatter(skill)
        problems += _check_router(skill)
        problems += _check_manifest(skill)
        problems += _check_four_columns(skill)
        problems += _check_commands(skill, known)
        problems += _check_links(skill)
    problems += _check_sentences(Path(skills_dir))
    return problems


def main() -> int:
    problems = validate_all()
    if not problems:
        n = len([p for p in DEFAULT_SKILLS.iterdir() if p.is_dir()])
        print(f"✓ {n} 份说明书全部合法")
        return 0
    for x in problems:
        print(f"✗ {x}")
    print(f"\n{len(problems)} 处问题")
    return 1


if __name__ == "__main__":
    sys.exit(main())
