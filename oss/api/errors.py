"""api 的失败。**退出码跟着异常走**，CLI 只负责把它交出去。

退出码是说明书写死的判据。抽逻辑时最容易发生的回归不是报错，是
「某条路径的退出码从 6 变成了 1」——模型照着说明书判，判错了还不报错。
所以异常自己带 `code`，而每条拒绝路径都由 `tests/test_stage1_gates.py`
与 `tests/test_stage3_prose_frozen.py` 两头钉住。

`summary` 的约定：**它就是 CLI 打在 `✗ ` 后面的那一整行。**
这样「散文一个字都不变」是机械可核的，而不是每条命令各凭记忆抄一遍。
带冒号的（`稿件过不了校验：`）冒号也算在里面。
"""

from __future__ import annotations

from oss.api.result import result


class ApiError(Exception):
    """api 层的失败基类。**kind 是给机器看的分类，summary 是给人看的那一行。**"""

    kind = "error"

    def __init__(self, summary: str, *, code: int = 1,
                 problems: list[str] | None = None, data: dict | None = None) -> None:
        if code == 0:
            # 「失败但退出码 0」会把 `ok = 退出码为零` 这条唯一的规则撕开
            raise ValueError("失败的退出码不能是 0")
        super().__init__(summary)
        self.summary = summary
        self.code = code
        self.problems = list(problems or [])
        self.data = dict(data or {})

    def to_result(self) -> dict:
        return result(self.summary, self.data | {"kind": self.kind},
                      problems=self.problems, exit_code=self.code)


class Refused(ApiError):
    """门拒绝：轮次门 / 锚点门 / 探索门 / 校验没过 / 处方没处置。

    **判断已经做完了**，调用方只该照着 `problems` 去改，不该再解释一遍。
    """

    kind = "refused"


class Missing(ApiError):
    """缺前置产出物。**跟「拒绝」分开**：一个是还没做，一个是做了但不合格。"""

    kind = "missing"

    def __init__(self, artifact: str, summary: str, *, code: int = 1,
                 data: dict | None = None) -> None:
        super().__init__(summary, code=code, data=(data or {}) | {"artifact": artifact})
        self.artifact = artifact


class BadInput(ApiError):
    """参数不对：认不出的图名、没给键、`--map` 格式看不懂。缺省退出码 2。"""

    kind = "bad_input"

    def __init__(self, summary: str, *, code: int = 2,
                 problems: list[str] | None = None, data: dict | None = None) -> None:
        super().__init__(summary, code=code, problems=problems, data=data)


class Unavailable(ApiError):
    """外部的东西用不了：审稿后端、数据源、pandoc、画图依赖。

    **这不是「通过」。**审稿后端不可用当成通过是这套系统最危险的失败模式，
    所以它有自己的类型，不跟 `Refused` 混。
    """

    kind = "unavailable"
