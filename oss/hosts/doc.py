"""项目指令文件里的 OSS 托管块。借 ARIS `install_aris.sh` 的 `<!-- ARIS:BEGIN -->`。

为什么要它：宿主启动时会把 `CLAUDE.md` / `AGENTS.md` 读进上下文。装了技能却不在那儿留一行，
模型就不知道这个项目有一套 OSS 技能、装在哪儿、怎么重铺。ARIS 的做法是留一段带标记的块，
每次安装原地更新——**块外的正文一个字不动**，因为那是用户自己写的。

三条脾气，都跟 ARIS 一致：
- 文件不存在就跳过，**绝不新建**（往别人项目里凭空造一个 CLAUDE.md 是越界）
- 文件里有两块时拒绝改，不猜该改哪个
- 插 / 更新 / 删三种操作都幂等：跑一百次跟跑一次结果一样
"""

from __future__ import annotations

import re
from pathlib import Path

from oss.hosts import HostProfile

BEGIN = "<!-- OSS:BEGIN -->"
END = "<!-- OSS:END -->"

_BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
#: 删除时连着前后的空行一起吃掉，免得留下三连空行
_BLOCK_LOOSE = re.compile(r"\n*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n*", re.S)


class DocConflict(RuntimeError):
    """文件里不止一个托管块。**不猜该改哪个。**"""


def upsert(text: str, body: str) -> str:
    """插入 / 更新 / 删除（`body` 为空即删除）。三种都幂等。"""
    n = len(_BLOCK.findall(text))
    if n > 1:
        raise DocConflict(f"文件里有 {n} 个 OSS 托管块——不猜该改哪个，请先手工删到只剩一个")
    if not body.strip():
        return _BLOCK_LOOSE.sub("\n", text) if n else text
    block = f"{BEGIN}\n{body.rstrip()}\n{END}"
    if n == 1:
        # 用 lambda 做替换串：正文里可能有反斜杠（Windows 路径），
        # 直接传字符串会被 re 当成 \g / \1 之类的转义。
        return _BLOCK.sub(lambda _m: block, text)
    if not text.strip():
        return block + "\n"
    if text.endswith("\n\n"):
        sep = ""  # 已经空出一行，别再多加
    elif text.endswith("\n"):
        sep = "\n"  # 只差一个空行
    else:
        sep = "\n\n"  # 先补一个换行收尾，再空一行
    return text + sep + block + "\n"


def block_body(profile: HostProfile, names: list[str], target: Path | str) -> str:
    """托管块的正文。**确定性**：同样的输入永远同一串，否则每次安装都会改文件。"""
    first = names[0] if names else "name"
    invoke = profile.invocation.replace("name", first)
    note = f"（{profile.invocation_note}）" if profile.invocation_note else ""
    return "\n".join([
        "## OSS 技能范围",
        f"本项目按宿主 **{profile.label}**（`{profile.name}`）装了 {len(names)} 份 OSS 技能："
        + "、".join(f"`{n}`" for n in names),
        f"装在：`{target}`",
        f"调用：在消息里写 `{invoke}`，后面跟参数{note}",
        f"重铺：`oss install-skills --host {profile.name} --force`",
        "这一块由 oss 维护，手改会在下次重铺时被覆盖。",
    ])


def apply_to_file(path: Path, body: str) -> str:
    """落到文件上。返回一句给人看的结果。**文件不存在就跳过，不新建。**"""
    if not path.exists():
        return f"· {path.name} 不在，跳过托管块"
    text = path.read_text(encoding="utf-8")
    new = upsert(text, body)
    if new == text:
        return f"· {path.name} 的托管块已是最新"
    path.write_text(new, encoding="utf-8")
    return f"✓ {path.name} 的托管块已{'删除' if not body.strip() else '更新'}"
