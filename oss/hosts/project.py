"""把规范源投影成某个宿主的安装形状。

借 AERS `plugins/build_plugins.py` 的思路：**源头只有一份**，各宿主的差异是投影，
不是分叉的副本。副本会漂——漂了以后没人知道哪份是对的；投影不会，它每次都从源头算。

三条规则，全部机械可查：

1. 整目录复制，字节不动——除非下面两条要求改
2. `honors_allowed_tools: false`：SKILL.md 的 `allowed-tools:` 行降级成注释（原值留着）
3. `substitutes_arguments: false`：SKILL.md 必须同时有 `$ARGUMENTS` 与「宿主不替换占位符」，
   **没有就拒绝构建**——不自动补：那句话要写在上下文里才有用，补一行进去只是安慰

之后再走一遍相对链接校验。整目录搬走之后链接指不到文件，是最容易「在别人机器上才发现」
的一类错：源头里 `[x](rules/y.md)` 是对的，投影时漏掉 `rules/` 就成了断链，
而模型读到断链只会安静地跳过那份规则。

**先算全、再落盘**：所有文件的投影结果先在内存里算完，任何一条规则不过就抛，
一个字节都不写。半棵坏树比没有树更糟——宿主会加载它，而所有检查都以为装过了。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from oss.hosts import HostProfile

#: 说明书里必须出现的占位符（Claude Code 会替换它）
ARGUMENTS_TOKEN = "$ARGUMENTS"
#: 不替换的宿主要求说明书里带上的那句注释（阶段 2 已写进五份路由器，validate_skills 也查）
NO_SUBSTITUTION_NOTE = "宿主不替换占位符"

_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
#: 复制时跳过的垃圾。**drift 检查必须用同一个过滤器**，否则源头多一个 .DS_Store 就永远「不一致」
_IGNORED = (".DS_Store", "Thumbs.db")


class BuildError(RuntimeError):
    """投影拒绝继续。**宁可不出安装树，也不出一棵坏的。**"""


@dataclass
class BuildReport:
    host: str
    out: Path
    #: 这次真投影了的技能
    skills: list[str] = field(default_factory=list)
    #: 已存在且没给 --force，跳过的
    skipped: list[str] = field(default_factory=list)
    files: int = 0
    #: 相对源头被改写过的文件数（目前只有 SKILL.md 的 allowed-tools 行）
    rewritten: int = 0


def ignored(name: str) -> bool:
    return name in _IGNORED


def strip_allowed_tools(text: str, host: str) -> str:
    """把 frontmatter 的 `allowed-tools:` 行降级成注释，**原值留着**。

    留着是为了可追：读投影树的人要能看出「源头写过什么、被谁忽略了」。
    多行写法（值在下面几行）逐行删会把后续行留成孤儿 YAML，那种情况直接拒绝——
    本项目五份说明书目前都是单行写法，真改成多行时应该有人重新想一遍这里。

    **frontmatter 认不出也拒绝**（只要文里有 `allowed-tools:`）：`_FRONTMATTER` 认的是
    「开头一行 `---`、LF 换行、再一行 `---`」，CRLF 换行或者开头一个 BOM 都会匹配不上。
    原来那种「认不出就原样返回」等于把带白名单的说明书原样铺给一个不认白名单的宿主
    ——**静默降级成了静默不降级**，而安装、drift 检查全是绿的。
    """
    m = _FRONTMATTER.match(text)
    if not m:
        if "allowed-tools:" in text:
            raise BuildError(
                "SKILL.md 的 frontmatter 认不出（CRLF 换行或 BOM？）——"
                "不敢降级 allowed-tools，拒绝"
            )
        return text
    head, front, tail = text[:m.start(1)], text[m.start(1):m.end(1)], text[m.end(1):]
    lines = front.split("\n")
    out = []
    for i, line in enumerate(lines):
        if not line.startswith("allowed-tools:"):
            out.append(line)
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.strip() == "allowed-tools:" or nxt[:1] in (" ", "\t", "-"):
            raise BuildError(
                "allowed-tools 是多行写法，逐行降级会留下孤儿 YAML——"
                "改回单行，或者到 oss/hosts/project.py 里把这段写全"
            )
        out.append(f"# {line}    # {host} 不认工具白名单，这行只作存档")
    return head + "\n".join(out) + tail


def projected_bytes(profile: HostProfile, rel: Path, data: bytes) -> bytes:
    """一个文件在这个宿主下该长什么样。**投影与 drift 检查共用这一个函数。**"""
    if rel != Path("SKILL.md"):
        return data
    text = data.decode("utf-8")
    if not profile.substitutes_arguments:
        missing = [x for x in (ARGUMENTS_TOKEN, NO_SUBSTITUTION_NOTE) if x not in text]
        if missing:
            raise BuildError(
                f"{profile.name} 不替换 {ARGUMENTS_TOKEN}，说明书里必须同时写上 "
                f"{ARGUMENTS_TOKEN} 和「{NO_SUBSTITUTION_NOTE}」这句注释——现在少了：{missing}"
            )
    if not profile.honors_allowed_tools:
        text = strip_allowed_tools(text, profile.name)
    return text.encode("utf-8")


def _dead_links(plan: dict[str, dict[Path, bytes]], out: Path,
                skipped: list[str]) -> list[str]:
    """在**投影结果**里查链接，不在源头里查：搬家之后指不到才是问题。

    只有这一轮跳过没重投的技能（在 skipped 里），才认盘上的旧文件为「在」；
    这一轮重投的技能一律以本轮源头（plan）为准，盘上的旧文件不算数。

    **逃出技能根的链接一律算断链**（`../../README.md` 之类）：盘上也许真有那个文件，
    但技能只按技能根整棵搬走，根外的东西不会跟过去——在开发机上点得开，装到别人机器上就没了。
    """
    have = {Path(name) / rel for name, files in plan.items() for rel in files}
    skipped_set = set(skipped)
    bad: list[str] = []
    for name, files in sorted(plan.items()):
        for rel, data in sorted(files.items()):
            if rel.suffix != ".md":
                continue
            here = (Path(name) / rel).parent
            for m in _LINK.finditer(data.decode("utf-8")):
                t = m.group(1).split("#")[0].strip()
                if not t or "://" in t or t.startswith("/"):
                    continue
                target = Path(os.path.normpath(here / t))
                if target in have:
                    continue
                if target.parts[0] in skipped_set and (out / target).exists():
                    continue
                bad.append(f"{name}/{rel} → {t}")
    return bad


def _index_text(profile: HostProfile, names: list[str]) -> str:
    lines = [f"# OSS 技能索引（{profile.label}）", ""]
    lines += [f"- `{n}`：{profile.invocation.replace('name', n)}" for n in sorted(names)]
    return "\n".join(lines) + "\n"


def build(profile: HostProfile, src: Path | str, out: Path | str,
          *, force: bool = True) -> BuildReport:
    """把 src 下的每个技能目录投影到 out。确定性、幂等。"""
    src = Path(src).resolve()
    out = Path(out).expanduser()
    if out.exists() and out.resolve() == src:
        raise BuildError(f"目标目录就是源头（{src}）——投影会覆盖规范源，拒绝")
    if not src.exists():
        raise BuildError(f"源头不在：{src}")

    plan: dict[str, dict[Path, bytes]] = {}
    skipped: list[str] = []
    rewritten = 0
    for skill in sorted(p for p in src.iterdir() if p.is_dir()):
        if (out / skill.name).exists() and not force:
            skipped.append(skill.name)
            continue
        files: dict[Path, bytes] = {}
        for f in sorted(skill.rglob("*")):
            if not f.is_file() or ignored(f.name):
                continue
            rel = f.relative_to(skill)
            raw = f.read_bytes()
            got = projected_bytes(profile, rel, raw)     # 不合规则在这里就抛，此刻还没落盘
            files[rel] = got
            rewritten += 1 if got != raw else 0
        plan[skill.name] = files

    dead = _dead_links(plan, out, skipped)
    if dead:
        raise BuildError("投影之后这些相对链接指不到文件：\n  " + "\n  ".join(dead))

    out.mkdir(parents=True, exist_ok=True)
    files_written = 0
    for name, files in sorted(plan.items()):
        dest = out / name
        if dest.exists():
            shutil.rmtree(dest)
        for rel, data in sorted(files.items()):
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            files_written += 1
    if profile.index_file:
        (out / profile.index_file).write_text(
            _index_text(profile, sorted(plan) + skipped), encoding="utf-8")

    return BuildReport(host=profile.name, out=out, skills=sorted(plan),
                       skipped=skipped, files=files_written, rewritten=rewritten)
