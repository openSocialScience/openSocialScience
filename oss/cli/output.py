"""`--json` 的唯一出口。

规矩：给了 `--json` 就**只有一行 JSON 落在 stdout，别的什么都不打**——
散文不打，stderr 也不打。想看的东西全在 `data` 里（`data.warnings` / `data.notes`）。

不给 `--json` 时这个模块一行都不执行——**散文那条路一个字节都没变。**
"""

from __future__ import annotations

import json
from typing import NoReturn

import typer

from oss.api.errors import ApiError


def ok(res: dict) -> NoReturn:
    """成功（或「成功地报告了一个非零状态」）：打一行，按 data.exit_code 退。"""
    typer.echo(json.dumps(res, ensure_ascii=False))
    raise typer.Exit(res["data"]["exit_code"])


def fail(exc: ApiError) -> NoReturn:
    """失败：同样打一行，按异常自带的退出码退。**退出码是契约，不在这里改。**"""
    typer.echo(json.dumps(exc.to_result(), ensure_ascii=False))
    raise typer.Exit(exc.code)
