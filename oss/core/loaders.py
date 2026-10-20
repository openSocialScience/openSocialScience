"""数据加载。把各种格式读成一张表，连同它自带的语义元数据。

从旧实现抢救（PLAN.md §7）。值得保住的两条：

**① 变量标签与值标签是免费的语义信息。**
Stata / SPSS 自带，CSV 没有——知道 `q23a` 的标签是「家庭年总收入（元）」，
和只知道列名叫 `q23a`，是完全不同的两件事。这直接决定变量绑定的准确率。

**② CSV 的编码要嗅探。**
中文数据的编码地雷太多（GBK / GB18030 / UTF-8-SIG），猜错会让所有列名变成乱码，
而绑定完全依赖列名和标签。
"""

from __future__ import annotations

import csv as _csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd



@dataclass
class LoadedTable:
    """一份加载好的数据，连同它自带的语义元数据。

    `variable_labels` 与 `value_labels` 是关键：Stata / SPSS 文件自带这些，
    CSV 没有。它们直接决定 变量绑定的准确率——
    知道 `q23a` 的标签是"家庭年总收入（元）"，和只知道列名叫 `q23a`，
    是完全不同的两件事。
    """

    frame: pd.DataFrame
    path: Path
    fmt: str
    variable_labels: dict[str, str] = field(default_factory=dict)
    value_labels: dict[str, dict[str, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        """文件内容哈希。复现包需要它来确认"用的是同一份数据"。"""
        h = hashlib.sha256()
        with self.path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:32]

    @property
    def size_bytes(self) -> int:
        return self.path.stat().st_size


class UnsupportedFormat(ValueError):
    def __init__(self, path: Path, hint: str = "") -> None:
        super().__init__(f"不支持的数据格式：{path.name}。{hint}".strip())
        self.path = path


class MissingDependency(ImportError):
    """缺可选依赖。明确告诉用户装什么，而不是抛一个裸的 ImportError。"""

    def __init__(self, fmt: str, package: str, extra: str) -> None:
        super().__init__(
            f"读取 {fmt} 需要 {package}。安装：pip install '.[{extra}]'"
        )
        self.package = package


def _sniff_csv(path: Path) -> tuple[str, str]:
    """嗅探编码与分隔符。

    中文数据的编码地雷太多（GBK / GB18030 / UTF-8-SIG），
    猜错会让所有列名变成乱码，而变量绑定完全依赖列名和标签。
    """
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            with path.open("r", encoding=encoding) as fh:
                sample = fh.read(64_000)
            break
        except UnicodeDecodeError:
            continue
    else:
        return "utf-8", ","

    try:
        delimiter = _csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except _csv.Error:
        delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    return encoding, delimiter


def read_csv(path: Path) -> LoadedTable:
    encoding, delimiter = _sniff_csv(path)
    frame = pd.read_csv(path, encoding=encoding, sep=delimiter, low_memory=False)
    warnings = []
    if encoding != "utf-8-sig" and encoding != "utf-8":
        warnings.append(f"按 {encoding} 解码；若列名异常请显式指定编码")
    warnings.append("CSV 不带变量标签，变量绑定只能依赖列名——若有 codebook 请一并提供")
    return LoadedTable(frame=frame, path=path, fmt="csv", warnings=warnings)


def read_excel(path: Path) -> LoadedTable:
    try:
        sheets = pd.read_excel(path, sheet_name=None)
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise MissingDependency("Excel", "openpyxl", "excel") from exc
    if not sheets:
        raise UnsupportedFormat(path, "文件里没有工作表")
    name, frame = next(iter(sheets.items()))
    warnings = [] if len(sheets) == 1 else [
        f"文件有 {len(sheets)} 个工作表，只读了第一个「{name}」"
    ]
    return LoadedTable(frame=frame, path=path, fmt="excel", warnings=warnings)


#: 「解出来像不像话」的判据。拉丁补充区（U+00C0–U+00FF）那一段字符在真实的
#: 变量标签里极少成片出现，而 GBK 文本按 latin-1 解开之后**整段都是它们**
#: （「问卷编号」→「ÎÊ¾í±àºÅ」）。占比过半就当解错了。
#:
#: **判据不能是「抛没抛异常」**：latin-1 能把任何字节解开，一个异常都不抛。
_MOJIBAKE_RANGE = (0x00C0, 0x00FF)
_MOJIBAKE_SHARE = 0.5


def _mojibake(labels) -> bool:
    text = "".join(str(v) for v in labels if v)
    if not text:
        return False
    hits = sum(1 for ch in text if _MOJIBAKE_RANGE[0] <= ord(ch) <= _MOJIBAKE_RANGE[1])
    return hits / len(text) > _MOJIBAKE_SHARE


def read_stata(path: Path) -> LoadedTable:
    """Stata。变量标签与值标签是免费的语义信息，必须抽出来。

    **值标签要按列名接，不能按标签集名接。**

    Stata 里值标签是一张张具名的表，变量再挂到某张表上；
    `StataReader.value_labels()` 给的是 `{标签集名: {码: 含义}}`——**不是列名**。
    照列名用它，接上的全是碰巧同名的那几个。CGSS2017 的 `.dta` 上量过：
    619 组值标签**只接上 1 组**（`isurban`，因为它恰好小写）。

    后果不是报错，是**今天刚做的缺失码探测在真 CGSS 上整个失效**——
    个人年收入的 `9999997=不适用` 认不出来，均值又变回 58 万。

    pyreadstat 的 `variable_value_labels` 本来就是按列名给的（`datapacks`
    那边一直用的是这个），所以元数据走它，数据仍走 pandas（更耐脏）。
    """
    with pd.io.stata.StataReader(str(path), convert_categoricals=False) as reader:
        frame = reader.read()
        variable_labels = dict(reader.variable_labels() or {})
        try:
            raw_value_labels = reader.value_labels() or {}
        except Exception:  # 有些 .dta 没有值标签表
            raw_value_labels = {}

    warnings: list[str] = []
    value_labels: dict[str, dict[str, str]] = {}
    try:
        import pyreadstat  # type: ignore[import-untyped]

        # GSS 累积文件的值标签里有非 UTF-8 字节，默认解码会炸——先 UTF-8，再 GBK，最后 latin-1。
        #
        # **中间那一档是 2026-09-25 补的，而且判据必须换。**原先只试 UTF-8 与 latin-1，
        # 靠「抛不抛 UnicodeDecodeError」来选：CGSS2005 的 `.dta` 是 GBK 存的，
        # **latin-1 能把任何字节解开、一个异常都不抛**，于是循环停在那儿，
        # 拿到的是「问卷编号」→「ÎÊ¾í±àºÅ」这种**不报错的乱码**。
        #
        # 乱码的后果不是报错，是**后面所有靠标签认变量的活全部失效**，
        # 而画像看起来一切正常。所以判据从「抛没抛异常」换成「解出来像不像话」
        # （`_mojibake`：latin-1 段里全是拉丁补充区的字符，几乎一定是解错了）。
        for enc in (None, "gb18030", "latin1"):
            try:
                _, meta = pyreadstat.read_dta(str(path), metadataonly=True, encoding=enc)
            except UnicodeDecodeError:
                continue
            except Exception as exc:  # noqa: BLE001 —— 编码名不认的往下试，别的照抛
                if "encoding" not in str(exc).lower():
                    raise
                continue
            if enc != "latin1" and _mojibake((meta.column_names_to_labels or {}).values()):
                continue        # 解得开但不像话，换下一档
            value_labels = {
                col: {str(k): str(v) for k, v in mapping.items()}
                for col, mapping in (meta.variable_value_labels or {}).items()
            }
            variable_labels = {
                k: str(v) for k, v in (meta.column_names_to_labels or {}).items() if v
            } or variable_labels
            break
    except ImportError:
        pass

    if not value_labels and raw_value_labels:
        # 退路：按标签集名跟列名对（大小写不敏感——CGSS 的标签集就是列名的大写）。
        # **接不上的要说出来**，别让人以为这份数据本来就没有值标签。
        by_lower = {str(c).lower(): str(c) for c in frame.columns}
        for name, mapping in raw_value_labels.items():
            if (col := by_lower.get(str(name).lower())) is not None:
                value_labels[col] = {str(k): str(v) for k, v in mapping.items()}
        unmatched = len(raw_value_labels) - len(value_labels)
        if unmatched:
            warnings.append(
                f"{unmatched} 组值标签没能接到列上（装了 pyreadstat 才接得准）。"
                f"**缺失值代码、跨轮编码这些检查会因此失效**，"
                f"而它们失效的时候不报错")

    return LoadedTable(
        frame=frame,
        path=path,
        fmt="stata",
        variable_labels={k: v for k, v in variable_labels.items() if v},
        value_labels=value_labels,
        warnings=warnings,
    )


def read_pyreadstat(path: Path, fmt: str) -> LoadedTable:
    try:
        import pyreadstat  # type: ignore[import-untyped]
    except ImportError as exc:
        raise MissingDependency(fmt, "pyreadstat", "stats-formats") from exc

    reader = {"spss": pyreadstat.read_sav, "sas": pyreadstat.read_sas7bdat}[fmt]
    frame, meta = reader(str(path))
    value_labels: dict[str, dict[str, str]] = {}
    for col, label_name in (meta.variable_to_label or {}).items():
        mapping = (meta.value_labels or {}).get(label_name, {})
        if mapping:
            value_labels[col] = {str(k): str(v) for k, v in mapping.items()}

    return LoadedTable(
        frame=frame,
        path=path,
        fmt=fmt,
        variable_labels={k: v for k, v in (meta.column_names_to_labels or {}).items() if v},
        value_labels=value_labels,
    )


def read_parquet(path: Path) -> LoadedTable:
    return LoadedTable(frame=pd.read_parquet(path), path=path, fmt="parquet")


_BY_SUFFIX = {
    ".csv": read_csv,
    ".tsv": read_csv,
    ".txt": read_csv,
    ".xlsx": read_excel,
    ".xls": read_excel,
    ".dta": read_stata,
    ".sav": lambda p: read_pyreadstat(p, "spss"),
    ".zsav": lambda p: read_pyreadstat(p, "spss"),
    ".sas7bdat": lambda p: read_pyreadstat(p, "sas"),
    ".parquet": read_parquet,
}


def load_any(path: str | Path) -> LoadedTable:
    """按扩展名分派。"""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"数据文件不存在：{p}")
    reader = _BY_SUFFIX.get(p.suffix.lower())
    if reader is None:
        raise UnsupportedFormat(p, f"支持：{'、'.join(sorted(_BY_SUFFIX))}")
    return reader(p)


def supported_formats() -> list[str]:
    return sorted(_BY_SUFFIX)
