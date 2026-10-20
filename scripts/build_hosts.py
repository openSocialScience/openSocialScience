#!/usr/bin/env python3
"""把 .claude/skills 投影成各宿主的安装树。

    python scripts/build_hosts.py                    # 全部宿主 → build/hosts/<宿主>/
    python scripts/build_hosts.py --host dsh         # 只投一个
    python scripts/build_hosts.py --host dsh --out /tmp/dsh-skills
    python scripts/build_hosts.py --check            # 只算不落盘：验链接与各宿主的规则

**投影逻辑不在这个文件里**，在 oss/hosts/project.py——`oss install-skills` 用的是同一份。
两条路各写一份的话，某天 CLI 会装出一棵跟 build/ 里不一样的树，而两边都「跑过了」。
这个文件只做三件事：解析参数、决定输出到哪儿、把结果打出来。

形状借自 AERS plugins/build_plugins.py：规范源不动，各宿主是投影结果。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oss import hosts                                   # noqa: E402
from oss.hosts import project as host_project           # noqa: E402

SRC = ROOT / ".claude" / "skills"
OUT = ROOT / "build" / "hosts"


def main() -> int:
    ap = argparse.ArgumentParser(description="把说明书投影成各宿主的安装树")
    ap.add_argument("--host", default="", help=f"只投一个宿主；可选：{'、'.join(hosts.available())}")
    ap.add_argument("--src", default=str(SRC), help="规范源目录")
    ap.add_argument("--out", default="", help="输出目录（缺省 build/hosts/<宿主>/）")
    ap.add_argument("--check", action="store_true", help="只算不落盘")
    args = ap.parse_args()

    # --out 是「投到这一个目录」，所以它只对一个宿主有意义。不给 --host 的话
    # 四个宿主会**依次投进同一个目录**，后一个盖掉前一个，最后剩下的是
    # available() 里最后那个宿主的形状——而命令看起来四行全是 ✓。
    if args.check and args.out:
        print("✗ --check 只算不落盘，跟 --out 不能同时给")
        return 2
    if args.out and not args.host:
        print("✗ --out 只能配合 --host 用：四个宿主会互相覆盖同一个目录")
        return 2

    names = [args.host] if args.host else hosts.available()
    rc = 0
    for name in names:
        try:
            prof = hosts.load(name)
        except hosts.UnknownHost as exc:
            print(f"✗ {exc}")
            return 2
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) if args.check else Path(args.out or OUT / name)
            try:
                rep = host_project.build(prof, Path(args.src), out)
            except host_project.BuildError as exc:
                print(f"✗ {name}：{exc}")
                rc = 1
                continue
            where = "（只算不落盘）" if args.check else f"→ {out}"
            print(f"✓ {name}：{len(rep.skills)} 份技能 / {rep.files} 个文件"
                  f"，改写 {rep.rewritten} 个 {where}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
