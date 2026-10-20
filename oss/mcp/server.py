"""oss 的 MCP 服务器：stdio 传输，只做 tools。

**为什么不引 `mcp` 那个包。**① 它为了 HTTP/SSE 传输带进 starlette + uvicorn +
sse-starlette 一整套 web 栈，而我们只要 stdio；`oss` 装在研究者自己的机器上，
`doctor` 已经要解释三个可选安装了。② 更要紧的是它只能当可选依赖，于是 MCP 的测试
在没装它的环境里被 `importorskip` 掉——**看起来验过，其实一次都没跑**（这个仓库栽过
同一个跟头，见 `_code_provenance` 的注释：查的地方跟跑的地方不是同一个，等于没查）。
③ stdio 上要实现的只有三个方法加一个通知，一百多行，零新依赖，测试永远真跑。
④ `reviewer.py` 就是这么干的：拿 httpx 直接打 OpenAI 兼容端点，不引 `openai` SDK。

换回官方 SDK 的口子留着：工具表在 `tools.py`，跟传输无关。

**stdout 只放协议。**任何一句散文都会把消息流搅坏，而表现是「客户端偶尔连不上」——
最难查的那一类。所以走这条路的时候 api 那一层一个字都不打（它本来就不打）。
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from oss import __version__
from oss.api.errors import ApiError
from oss.api.result import result
from oss.mcp.tools import TOOLS

#: 我们支持的协议版本。只做 tools，三个版本对 tools 的部分完全一样。
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL = PROTOCOL_VERSIONS[0]

INSTRUCTIONS = (
    "OpenSocialScience 的工具箱。多数工具吃一个 run（运行目录），"
    "返回 {ok, summary, data, decision?, problems?}。\n"
    "· ok 就是「退出码为零」，退出码在 data.exit_code。\n"
    "· decision 是程序给的下一步（oss_loop_status 的 review_first / continue / "
    "stop / export_with_open_items）——**照做，不要从 summary 里猜**。\n"
    "· problems 是没通过检测时的逐条理由，照着改，不要绕。\n"
    "· 一个运行目录一次研究；所有工具用同一个 run。"
)


def _reply(mid: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": payload}


def _error(mid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _content(res: dict, is_error: bool) -> dict:
    """双通道（借 research-plugins 的 content/details）：
    `content` 是给模型读的那一份文本，`structuredContent` 是给程序读的那一份结构。
    **两边是同一个字典**，不做两套——两套会漂。
    """
    return {"content": [{"type": "text",
                         "text": json.dumps(res, ensure_ascii=False, indent=1)}],
            "structuredContent": res,
            "isError": is_error}


def call(name: str, args: dict) -> dict:
    """调一个工具。**任何异常都变成一份能解析的返回，绝不让服务器挂掉。**

    `isError` 只在**调用没跑完**时为真（工具不存在、参数不对、门拒绝、崩了）。
    跑完了但退出码非零（`oss eval` 有 must 级没过、`oss loop status` 还要复审）
    **不是 isError**——那是成功地报告了一个坏消息。
    """
    tool = TOOLS.get(name)
    if tool is None:
        return _content(result(f"没有叫 {name} 的工具。可用：{'、'.join(sorted(TOOLS))}",
                               {"kind": "bad_input"}, exit_code=2), True)
    try:
        res = tool.fn(**args)
    except ApiError as exc:
        return _content(exc.to_result(), True)
    except TypeError as exc:
        return _content(result(f"参数不对：{exc}", {"tool": name, "kind": "bad_input"},
                               exit_code=2), True)
    except Exception as exc:  # noqa: BLE001 —— 崩了也要说出来，不能让宿主看见一个断掉的管道
        return _content(result(f"{type(exc).__name__}: {exc}",
                               {"tool": name, "kind": "error"}, exit_code=1), True)
    return _content(res, False)


def handle(msg: dict) -> dict | None:
    """一条请求 → 一条回复。通知（没有 id）返回 None。**纯函数，好测。**"""
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if mid is None or (isinstance(method, str) and method.startswith("notifications/")):
        # JSON-RPC 2.0 §4.1：没有 id 的就是**通知**，一律不回包。
        # 这个判断必须排在分发之前——排在后面的话，没有 id 的 `ping`
        # 会先被 ping 分支接走，回一个 `"id": null` 的包，那是协议不允许的
        return None
    if method == "initialize":
        want = str(params.get("protocolVersion") or "")
        return _reply(mid, {
            "protocolVersion": want if want in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "oss", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _reply(mid, {})
    if method == "tools/list":
        # 工具就这二十几个，不分页；不给 nextCursor 就是「没有下一页」
        return _reply(mid, {"tools": [t.describe() for t in TOOLS.values()]})
    if method == "tools/call":
        args = params.get("arguments")
        return _reply(mid, call(str(params.get("name") or ""),
                                dict(args) if isinstance(args, dict) else {}))
    return _error(mid, -32601, f"Method not found: {method}")


def _write(fout: TextIO, obj: dict) -> None:
    # 一行一条，行内不许有换行——`json.dumps` 缺省就不换行
    fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    fout.flush()


def serve(fin: TextIO | None = None, fout: TextIO | None = None) -> None:
    """在 stdin/stdout 上跑。一行一条 JSON（MCP 的 stdio 传输）。"""
    fin = fin if fin is not None else sys.stdin
    fout = fout if fout is not None else sys.stdout
    for stream in (fin, fout):
        # Windows 的缺省编码不是 UTF-8，而工具描述与 summary 全是中文
        try:
            stream.reconfigure(encoding="utf-8")     # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    for raw in fin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            _write(fout, _error(None, -32700, "Parse error"))
            continue
        if not isinstance(msg, dict):
            # 2025-06-18 取消了批量请求；列表一律拒绝，别装作支持
            _write(fout, _error(None, -32600, "Invalid Request: 只接受单条 JSON 对象"))
            continue
        reply = handle(msg)
        if reply is not None:
            _write(fout, reply)


# 故意不留 `if __name__ == "__main__": serve()`。`python -m oss.mcp.server` 会绕开
# `oss/cli/main.py` 顶部的 `_load_env()`，`.env` 就不会被加载——审稿三个环境变量会
# 静默地读不到。唯一受支持的入口是 `oss mcp serve`（`oss/cli/main.py` 里延迟 import
# 本模块的 `serve`）；确认过 docs/、tests/、README.md、config/、oss/ 里没有任何地方
# 依赖 `python -m oss.mcp.server` 这条路。
