"""现成数据集清单：需要时才下、下过就存、要申请的只说怎么申请。

`datasources.py` 是「拿一列指标」（世界银行 / OECD / OWID 有 API）；
这里是「拿一整套」——QoG、V-Dem、GSS 这类发布成一个文件的数据集。

三条规矩：

- **不打包数据进项目。**清单里只有链接、钥匙列、码本、引用格式。
  第一次用某个数据集时才下载，存在本机 `data/<名>/`，带出处文件；以后直接用。
- **要注册申请的一律不碰。**CGSS、CFPS、WVS 要签数据使用协议，那是人对着网站做的事。
  系统只告诉用户怎么申请、拿到后放哪；放好了它负责转格式、记出处。
- **清单只列候选，不替人选。**题目需要额外数据时用哪一套是判断，归 skill、写进计划、留痕。

每套数据都标着**钥匙列**（国家代码、年份、个人编号、轮次）——
国家-年的几套之间按 ISO3 直接能拼，上次手做 35 对国家名对照表的事不该再发生。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

#: 本机缓存目录（不进仓库，见 .gitignore）
CACHE = Path("data")

#: 超过这么多列的文件不整个转 CSV（GSS 累积文件 6000 多列、几百 MB），只缓存原件，让 skill 挑列
WIDE = 500


@dataclass
class Pack:
    name: str
    title: str
    cite_name: str                 # 论文里必须出现的名字，paper check 按它查有没有引
    level: str                     # country-year / individual-repeated-cross-section / individual-panel
    unit: str                      # 单位列（国家名 / 个人编号）
    time: str                      # 时间列
    iso3: str = ""                 # 国家代码列（国家-年数据才有）
    url: str = ""                  # 直接下载链接；空表示要申请
    inner: str = ""                # 压缩包里要的文件（glob）
    how_to: str = ""               # 要申请的：怎么拿
    homepage: str = ""
    codebook: str = ""
    citation: str = ""             # 推荐引用（以码本首页为准）
    license: str = ""
    weights: str = ""              # 调查权重列
    size_mb: int = 0
    note: str = ""
    # ── 抽样设计登记。每一格要么在 design_source 里有出处，要么写「待核」——跟「引用不许编」同一条规矩
    design: str = ""               # 抽样设计一句话（码本原意）；查不到写「待核」
    strata: str = ""               # 分层变量名；公开数据没给写「公开数据未提供」；查不到写「待核」
    psu: str = ""                  # 初级抽样单位变量名；同上
    weights_by_wave: dict[str, str] = field(default_factory=dict)   # 年份 / 轮次 → 权重变量（横截面、面板分开写）
    design_source: str = ""        # 以上各格的出处：网址 + 章节或页码

    @property
    def manual(self) -> bool:
        return not self.url


PACKS: dict[str, Pack] = {
    "qog": Pack(
        name="qog",
        title="Quality of Government Basic Dataset, time-series (Jan 2026)",
        cite_name="Quality of Government",
        level="country-year", unit="cname", time="year", iso3="ccodealp",
        url="https://www.qogdata.pol.gu.se/data/qog_bas_ts_jan26.csv",
        homepage="https://www.gu.se/en/quality-government",
        codebook="https://www.qogdata.pol.gu.se/data/codebook_bas_jan26.pdf",
        citation="The Quality of Government Institute, University of Gothenburg. 2026. "
                 "The Quality of Government Basic Dataset, version Jan26. "
                 "https://www.gu.se/en/quality-government（作者名单以码本首页的推荐引用为准）",
        license="免费使用，须引用",
        size_mb=16,
        note="治理、民主、经济、健康等几百个指标的汇编，跨国比较的起点。年份 1946 起",
    ),
    "vdem": Pack(
        name="vdem",
        title="V-Dem Country-Year Core (v15)",
        cite_name="V-Dem",
        level="country-year", unit="country_name", time="year", iso3="country_text_id",
        url="https://v-dem.net/media/datasets/V-Dem-CY-Core-v15_csv.zip", inner="*.csv",
        homepage="https://v-dem.net",
        codebook="https://v-dem.net/documents/",
        citation="Coppedge, Michael, John Gerring, Carl Henrik Knutsen, Staffan I. Lindberg, Jan Teorell, "
                 "et al. 2025. V-Dem Country-Year Dataset v15. Varieties of Democracy (V-Dem) Project. "
                 "https://v-dem.net（完整作者名单与 DOI 以码本首页为准）",
        license="免费使用，须引用",
        size_mb=15,
        note="民主的多维测量（选举、自由、参与、协商、平等），1789 起。country_text_id 大体是 ISO3，个别历史政体例外",
    ),
    "gss": Pack(
        name="gss",
        title="General Social Survey, cumulative file (1972–)",
        cite_name="General Social Survey",
        level="individual-repeated-cross-section", unit="id", time="year",
        url="https://gss.norc.org/documents/stata/GSS_stata.zip", inner="*.dta",
        homepage="https://gss.norc.org",
        codebook="https://gssdataexplorer.norc.org/variables/vfilter",
        citation="Davern, Michael, René Bautista, Jeremy Freese, Pamela Herd, and Stephen L. Morgan. "
                 "General Social Survey 1972–2024 [machine-readable data file]. NORC at the University "
                 "of Chicago（年份范围与版本以下载文件的码本为准）",
        license="公开，须引用",
        weights="wtssps（1972–2024，默认）/ wtssnrps（2004–2024，这些年 NORC 推荐）；旧的 wtssall 只到 2018",
        size_mb=48,
        note="美国成年人重复截面，六千多列——**不要整个转**，挑列：oss data get gss --columns year,id,wtssps,vstrat,vpsu,educ,paeduc,...",
        design="NORC 全国抽样框上的多阶段整群面积概率抽样：一阶全国框架区（NFA，一个或几个县）与二阶街区段（segment）"
               "按住户数 PPS 抽，三阶住户等概率抽，每户只访一名随机选中的成人；"
               "1972–1974 是街区配额的修正概率样本，1975–1976 配额与全概率各半，1977 起全概率；"
               "2004 起对无应答户再抽子样本；2021 改为按地址抽样、网上自填为主，2022 与 2024 回到面访、网上、电话混合",
        strata="vstrat",
        psu="vpsu",
        weights_by_wave={
            "1972–2024": "wtssps（码本推荐的默认权重：计入各阶段抽中概率，再按人口总量校准）",
            "2004–2024": "wtssnrps（在 wtssps 上多一步无应答调整；有它的年份 NORC 推荐用它）",
            "1972–2018（旧）": "wtssall（历史权重，留作记录；2004 起与 wtss 相同）",
            "2004–2018（旧）": "wtssnr（历史权重，加了区域无应答调整）",
        },
        design_source=(
            "GSS 2024 Codebook R3（https://gss.norc.org/content/dam/gss/get-documentation/pdf/codebook/"
            "GSS%202024%20Codebook%20R3.pdf）："
            "PDF 第 7 页（印刷页 1）2021 改按地址抽样与网上自填、2022 与 2024 回到混合方式；"
            "Sampling Design and Weights，PDF 第 45 页（印刷页 39）NFA 与 segment 按 PPS、住户等概率；"
            "GSS Post-stratification Weights，PDF 第 46 页（印刷页 40）wtssps 1972–2024 为默认、"
            "wtssnrps 2004–2024 有值的年份推荐；Table 8 Historical GSS Weights，PDF 第 52–53 页（印刷页 46–47）"
            "wtssall 1972–2018、wtssnr 2004–2018、2004 起无应答再抽子样本；"
            "Appendix C Sample Code，PDF 第 62–63 页（印刷页 56–57）分层用 vstrat、群用 vpsu，"
            "Stata 写法 svyset [pweight=wtssnrps], strata(vstrat) psu(vpsu) singleunit(scaled) 。"
            "GSS 码本 Appendix A: Sampling Design & Weighting，该版写到 1975–2016"
            "（https://gss.norc.org/content/dam/gss/get-documentation/pdf/codebook/GSS_Codebook_AppendixA.pdf）："
            "印刷页 3171 配额与全概率的年份，3185 户内每名成人等概率、每户只访一人，3186 2004 起无应答再抽子样本，"
            "3187–3188 每条访问都有 vstrat 与 vpsu、每个 vstrat 下两个 vpsu。"
            "Pedlow（NORC，2013）Calculating Design-Corrected Standard Errors for the General Social Survey, 1975-2012"
            "（https://gss.norc.org/content/dam/gss/get-documentation/pdf/other/GSS%20design%20variables.pdf）第 2 页："
            "vstrat 与 vpsu 交叉的每格至少 3 人，取子样本时容易出现只剩一个 vpsu 的层，办法是并相邻的 vstrat。"
        ),
    ),
    "cgss": Pack(
        name="cgss",
        title="中国综合社会调查 CGSS",
        cite_name="CGSS",
        level="individual-repeated-cross-section", unit="id", time="year",
        how_to="1. 到 http://cgss.ruc.edu.cn 注册账号，申请需要的年份（2003–2021 各年独立文件）\n"
               "2. 下载后把 .dta / .sav 原样放进 data/cgss/\n"
               "3. 跑 oss data get cgss --columns <要的列>：它会转 CSV、记出处、抽值标签",
        homepage="http://cgss.ruc.edu.cn",
        codebook="http://cgss.ruc.edu.cn（各年问卷与码本）",
        citation="中国人民大学中国调查与数据中心. 中国综合社会调查（CGSS）[年份]. （以 CGSS 网站的推荐引用为准）",
        license="须注册申请，签数据使用协议",
        weights="各年不同，看码本",
        note="各年是独立文件，拼多年要自己对齐变量名",
        design="2010 年起（第二期，官网写「2010 至今」）：多阶段分层 PPS 概率抽样。初级抽样单位是县级单位（区、县级市、县），"
               "二级是村 / 居委会，每个村居抽 25 户，户内用 Kish 表抽一名 18 岁以上成人；"
               "北京、上海、天津、广州、深圳五大都市为自代表层（第二期抽样方案里该层以街道为初级单位），"
               "其余县级单位按人均 GDP、城镇化率、人口密度综合排序后等分为 50 层（区、县分开排，19 层与 31 层），"
               "每层 PPS 抽 2 个，每个再 PPS 抽 4 个村居；目标 12000 户、每户一人。"
               "2003–2006 与 2008 各用另一套方案（抽样框、分层变量、抽样阶段不同）",
        strata="待核",
        psu="待核",
        weights_by_wave={"2010 起各年": "待核"},
        design_source=(
            "CGSS 官网 cgss.ruc.edu.cn 从这里直连超时，以下各页经 web.archive.org 存档读取，括号里是快照日期。"
            "《抽样设计》http://cgss.ruc.edu.cn/xmwd/cysj.htm（20250912）：三套方案都是多阶分层 PPS，"
            "抽样框、分层变量、抽样阶段不同；2010 方案 100 个县级单位加 5 大都市、480 个村 / 居委会、12000 人。"
            "Sampling Design http://cgss.ruc.edu.cn/English/Documentation/Sampling_Design.htm（20251219）："
            "2010 design 一段，初级单位为县级、村居为二级、每村居 25 户、Kish 表、五城市自代表层、"
            "其余 2695 个初级单位综合排序等分 50 层、每层 PPS 抽 2 个、每个抽 4 个村居。"
            "《中国综合社会调查（CGSS）第二期（2010-2019）抽样方案》，即《抽样设计》页所附 CGSS SamplingDesing(Cycle2)_C.doc"
            "（http://cgss.ruc.edu.cn/__local/A/7F/16/5C3BCCAF547145EE2F30E7737D9_A59E1948_82E00.doc，20230315）："
            "四（一）关于分层、表 1 各阶段抽样单元。"
            "分层、初级抽样单位、各年权重三格待核：官网没写公开数据里这些变量叫什么。"
            "《关于CGSS数据地理信息披露的说明》http://cgss.ruc.edu.cn/info/1014/1011.htm（20251119）只说"
            "公开数据的地理信息披露到省，县级与村 / 居委会级只标到属于哪个调查点，不给名称与行政编码；"
            "变量名要看当年码本，码本随数据在中国国家调查数据库（CNSDA，cnsda.ruc.edu.cn / www.cnsda.org）注册后下载"
            "（《CGSS项目组发布CGSS2017年的家户调查数据》http://cgss.ruc.edu.cn/info/1014/1019.htm，20260416），"
            "这次没有读到码本。"
        ),
    ),
    "cfps": Pack(
        name="cfps",
        title="中国家庭追踪调查 CFPS",
        cite_name="CFPS",
        level="individual-panel", unit="pid", time="wave",
        how_to="1. 到 https://www.isss.pku.edu.cn/cfps/ 注册、申请各轮数据（2010 起每两年一轮）\n"
               "2. 下载后把各轮的个人库 .dta 放进 data/cfps/\n"
               "3. 跑 oss data get cfps --columns <要的列>；多轮要拼成长表用 oss reshape",
        homepage="https://www.isss.pku.edu.cn/cfps/",
        codebook="https://www.isss.pku.edu.cn/cfps/（各轮问卷与码本）",
        citation="北京大学中国社会科学调查中心. 中国家庭追踪调查（CFPS）[轮次]. （以 CFPS 网站的推荐引用为准）",
        license="须注册申请",
        weights="各轮不同，看码本",
        note="真面板，pid 跨轮不变；家庭库、个人库分开",
        design="内隐分层、多阶段、多层次、与人口规模成比例（PPS）的概率抽样，覆盖 25 个省 / 市 / 自治区"
               "（约占全国人口 95%，不含港澳台）。分 6 个独立子样本框：上海、辽宁、河南、甘肃、广东五个「大省」"
               "各过度抽样 1600 户，其余 20 个省合成一个「小省」框抽 8000 户；每个框三阶段："
               "区 / 县（上海是街道、乡镇）→ 村 / 居委会 → 家户，以行政区划和人均 GDP 等社会经济水平排序作内隐分层。"
               "推断全国必须加权；只追踪基因成员，权数也只给基因成员",
        strata="subpopulation",
        psu="psu",
        weights_by_wave={
            "2010 横截面": "rswt_nat（个人）/ fswt_nat（家庭）；只用再抽样样本时换 rswt_res / fswt_res",
            "2012 横截面": "rswt_natcs12（个人）/ fswt_natcs12（家庭）",
            "2010–2012 面板": "rswt_natpn1012（个人）/ fswt_natpn1012（家庭）",
            "2014 横截面": "rswt_natcs14（个人）/ fswt_natcs14（家庭）",
            "2010–2014 面板": "rswt_natpn1014（个人）/ fswt_natpn1014（家庭）",
            "2016 横截面": "rswt_natcs16（个人）",
            "2016 横截面（家庭）": "待核",
            "2010–2016 面板": "rswt_natpn1016（个人）",
            "2010–2016 面板（家庭）": "待核",
            "2018 横截面": "rswt_natcs18n（个人）",
            "2018 横截面（家庭）": "待核",
            "2010–2018 面板": "rswt_natpn1018n（个人；家庭从 2018 起不给面板权数）",
            "2020 横截面": "rswt_natcs20n（个人，无回答调整）/ rswtps_natcs20n（个人，事后分层）/ "
                           "fswt_natcs20n、fswtps_natcs20n（家庭）",
            "2010–2020 面板": "rswt_natpn1020n（个人，无回答调整）/ rswtps_natpn1020n（个人，事后分层）",
            "2022 横截面": "rswt_natcs22n（个人）/ fswt_natcs22n（家庭）",
            "2010–2022 面板": "rswt_natpn1022n（个人）",
        },
        design_source=(
            "设计：CFPS 用户手册（第三版，2017.7.30，https://www.isss.pku.edu.cn/cfps/docs/20210511113545661703.pdf）"
            "§2.1 抽样设计，印刷页 17–18（PDF 第 20–21 页）；§6.1 印刷页 71 全国完全样本通过加权代表全国；"
            "§9.2 印刷页 118 只追踪基因成员。"
            "分层：《CFPS小课堂 | 权数使用Q&A》（2022-05-12，https://www.isss.pku.edu.cn/cfps/cjwt/qz/1201867cfps1356863.htm）"
            "说 Strata 是六个抽样框、由 subpopulation 标识，非基因成员没有权数；用户手册 §6.1 印刷页 71："
            "subpopulation=1–6 为上海、辽宁、河南、甘肃、广东与其他省市，subsample=1 为全国再抽样样本。"
            "初级抽样单位：同一页说 PSU 是区县（上海是街道乡镇），在基线库里由 PSU 变量标识；"
            "《Q&A》（2022-05-12，https://www.isss.pku.edu.cn/cfps/cjwt/qz/1201867cfps1356829.htm）工作人员答："
            "2014 年的样本用 fid10 回 2010 年库找对应的 PSU；《中国家庭追踪调查数据权数使用常见问题》（吕萍，2016-07-17，"
            "https://www.isss.pku.edu.cn/cfps/attachments/8e4cd41179154db491b22f62d954c076.pdf）第 29 页"
            "现有数据库只用 6 个抽样框作层、区县或街道乡镇作群，不计内隐分层（方差估高），"
            "第 37 页 Stata 写法 svyset [pweight=Fswt_Nat], strata(subpopulation) psu(psu) 。"
            "权重：用户手册 §9.1 表 50（印刷页 117，PDF 第 120 页）2010、2012、2014 各变量；"
            "《CFPS 权数使用说明》（http://www.isss.pku.edu.cn/cfps/docs/20180927112440657361.pdf）2012 各变量；"
            "上面那页《Q&A》2016 个人四个权数 rswt_natcs16、rswt_rescs16、rswt_natpn1016、rswt_respn1016，"
            "且 2010–2016 是总量权数（加权后样本量近似全国人口或户数）、2018 起改标准化权数；"
            "《2018 年数据库介绍及数据清理报告》（CFPS-39，https://www.isss.pku.edu.cn/cfps/docs/2026-01/"
            "647fe42a94784c5cb5746b6dfb64042d.pdf）§2.9 权数第 17–18 页，不再单独提供再抽样样本权数、家庭只给横截面权数；"
            "《2020 年数据库介绍及数据清理报告》（CFPS-43，https://www.isss.pku.edu.cn/cfps/docs/20230629171546061292.pdf）"
            "§2.7 加权第 11–12 页；《2022 年数据库介绍及数据清理报告》（CFPS-45，"
            "https://www.isss.pku.edu.cn/cfps/docs/20241102182535367192.pdf）§2.7 加权第 11 页，"
            "每个子总体内标准化，表 3 家庭关系库变量说明第 16 页另列 PSU（基线家庭初级抽样单位）。"
            "三格待核：2016 家庭横截面与面板权数，读到的官方文档没写名字；"
            "2018 家庭横截面权数只在《Q&A》的用户提问里出现过名字，工作人员的回答没有复述。"
        ),
    ),
    "wvs": Pack(
        name="wvs",
        title="World Values Survey (time-series 1981–2022)",
        cite_name="World Values Survey",
        level="individual-repeated-cross-section", unit="D_INTERVIEW", time="A_YEAR", iso3="COUNTRY_ALPHA",
        how_to="1. 到 https://www.worldvaluessurvey.org 注册，下载 Time-Series (1981–2022) 的 Stata 或 SPSS 文件\n"
               "2. 放进 data/wvs/\n"
               "3. 跑 oss data get wvs --columns <要的列>",
        homepage="https://www.worldvaluessurvey.org",
        codebook="https://www.worldvaluessurvey.org/WVSDocumentationWVL.jsp",
        citation="Inglehart, R., C. Haerpfer, A. Moreno, C. Welzel, K. Kizilova, J. Diez-Medrano, M. Lagos, "
                 "P. Norris, E. Ponarin & B. Puranen (eds.). World Values Survey Time-Series (1981–2022). "
                 "JD Systems Institute & WVSA Secretariat.（以 WVS 网站的推荐引用为准）",
        license="须注册",
        weights="S017 / W_WEIGHT，看码本",
        note="跨国个人层面，按 COUNTRY_ALPHA 能跟国家-年数据拼",
    ),
}

_INCOMING = (".dta", ".sav", ".csv", ".xlsx", ".zip")


class NeedsManualDownload(RuntimeError):
    """要申请的数据集本地没有。**这不是错误，是系统做不了的一步**——照实说怎么做。"""


@dataclass
class PackResult:
    name: str
    title: str
    path: str                      # 转好的 CSV（宽表没转时是原件）
    raw: str                       # 原件
    rows: int
    n_columns: int
    columns: list[str]
    years: str
    keys: dict
    source: str                    # url 或 本地文件
    sha256: str
    fetched_at: str
    citation: str
    codebook: str
    license: str
    weights: str
    converted: bool
    notes: list[str] = field(default_factory=list)


def pack_dir(name: str, cache: Path = CACHE) -> Path:
    return cache / name


def _meta_path(name: str, cache: Path = CACHE) -> Path:
    return pack_dir(name, cache) / f"{name}.meta.json"


def status(name: str, cache: Path = CACHE) -> str:
    """local（转好了）/ download（能直接下，还没下）/ incoming（用户放进来了，还没转）/ manual（要申请，还没放）"""
    p = PACKS[name]
    if _meta_path(name, cache).exists():
        return "local"
    if _incoming(name, cache):
        return "incoming"
    return "manual" if p.manual else "download"


def _incoming(name: str, cache: Path = CACHE) -> list[Path]:
    d = pack_dir(name, cache)
    if not d.exists():
        return []
    return sorted(f for f in d.iterdir()
                  if f.is_file() and f.suffix.lower() in _INCOMING and not f.name.startswith(name + "."))


def list_packs(cache: Path = CACHE) -> list[dict]:
    rows = []
    for name, p in PACKS.items():
        st = status(name, cache)
        rows.append({"name": name, "title": p.title, "level": p.level, "status": st,
                     "keys": _keys(p), "size_mb": p.size_mb, "manual": p.manual})
    return rows


def _keys(p: Pack) -> dict:
    return {k: v for k, v in (("unit", p.unit), ("time", p.time), ("iso3", p.iso3)) if v}


def _download(url: str, dest: Path, client: httpx.Client | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    c = client or httpx.Client(timeout=httpx.Timeout(600.0, connect=60.0), follow_redirects=True,
                               headers={"User-Agent": "oss-data/0.3"})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with c.stream("GET", url) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}：{url}")
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    tmp.replace(dest)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _unpack(raw: Path, inner: str, into: Path) -> Path:
    """压缩包里挑要的那个文件（按 glob，取最大的）。"""
    if raw.suffix.lower() != ".zip":
        return raw
    with zipfile.ZipFile(raw) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        want = [n for n in names if Path(n).match(inner or "*")] or names
        if not want:
            raise RuntimeError(f"压缩包里没有匹配 {inner!r} 的文件：{names[:5]}")
        pick = max(want, key=lambda n: z.getinfo(n).file_size)
        out = into / Path(pick).name
        if not out.exists():
            with z.open(pick) as src, out.open("wb") as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    dst.write(chunk)
    return out


def _header(path: Path) -> tuple[list[str], dict]:
    """只读列名（和值标签），不把整张表读进内存——GSS 累积文件读全会吃几个 GB。"""
    cols, value_labels, _ = header_full(path)
    return cols, value_labels


def header_full(path: Path) -> tuple[list[str], dict, dict[str, str]]:
    """列名、值标签、**变量标签**（一句话说这一列是什么）。只读表头，不读数据。"""
    suf = path.suffix.lower()
    if suf == ".dta":
        import pyreadstat

        # GSS 累积文件的值标签里有非 UTF-8 字节，pyreadstat 默认解码会炸——
        # 先按 UTF-8，不行换 latin-1，再不行退到 pandas 只拿列名（值标签就没有了，说出来）
        for enc in (None, "latin1"):
            try:
                _, meta = pyreadstat.read_dta(str(path), metadataonly=True, encoding=enc)
                return (list(meta.column_names), dict(meta.variable_value_labels or {}),
                        {k: str(v) for k, v in (meta.column_names_to_labels or {}).items() if v})
            except UnicodeDecodeError:
                continue
        with pd.io.stata.StataReader(str(path), convert_categoricals=False) as reader:
            cols = list(reader.varlist)
            try:
                labels = {k: {str(a): str(b) for a, b in v.items()} for k, v in (reader.value_labels() or {}).items()}
            except Exception:  # noqa: BLE001 —— 值标签读不出来不该挡住拿数据
                labels = {}
            try:
                var_labels = {k: str(v) for k, v in (reader.variable_labels() or {}).items() if v}
            except Exception:  # noqa: BLE001
                var_labels = {}
        return cols, labels, var_labels
    if suf == ".sav":
        import pyreadstat

        _, meta = pyreadstat.read_sav(str(path), metadataonly=True)
        return (list(meta.column_names), dict(meta.variable_value_labels or {}),
                {k: str(v) for k, v in (meta.column_names_to_labels or {}).items() if v})
    if suf == ".csv":
        return list(pd.read_csv(path, nrows=0).columns), {}, {}
    if suf == ".xlsx":
        return list(pd.read_excel(path, nrows=0).columns), {}, {}
    raise RuntimeError(f"不认识的格式 {suf}")


def index_columns(path: Path) -> list[dict]:
    """一份数据文件的列索引：名字、变量标签、值标签几档。按构念找列用。"""
    cols, value_labels, var_labels = header_full(path)
    out = []
    for c in cols:
        vl = value_labels.get(c) or {}
        out.append({"name": str(c), "label": var_labels.get(c, ""),
                    "values": {str(k): str(v) for k, v in list(vl.items())[:6]},
                    "n_values": len(vl)})
    return out


def search_columns(index: list[dict], terms: list[str]) -> list[dict]:
    """按关键词在列名、变量标签、值标签里找。**找到的是候选，不是答案**——
    「这一列是不是我要的构念」要看码本和口径，那是判断。多个词按「任一命中」算，命中多的排前面。"""
    terms = [t.lower() for t in terms if t.strip()]
    if not terms:
        return index
    scored = []
    for row in index:
        hay = " ".join([row["name"], row.get("label", ""), " ".join(row.get("values", {}).values())]).lower()
        hits = sum(1 for t in terms if t in hay)
        name_hit = any(t in row["name"].lower() for t in terms)
        if hits:
            scored.append((hits + (0.5 if name_hit else 0), row))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    return [r for _, r in scored]


def source_file(name: str, cache: Path = CACHE) -> Path:
    """一套数据在本机的原件（转 CSV 之前那份，表头最全）。没拿过就抛。"""
    if name not in PACKS:
        raise KeyError(f"没有叫 {name!r} 的数据集。可选：{'、'.join(PACKS)}")
    p = PACKS[name]
    d = pack_dir(name, cache)
    if p.manual:
        inc = _incoming(name, cache)
        if not inc:
            raise NeedsManualDownload(f"{p.title} 需要注册申请。\n{p.how_to}")
        return inc[-1]
    raw = d / "raw" / Path(p.url).name
    if not raw.exists():
        raise FileNotFoundError(f"{p.title} 还没拿过：先 oss data get {name}")
    return _unpack(raw, p.inner, d / "raw")


def _read(path: Path, columns: list[str] | None) -> pd.DataFrame:
    suf = path.suffix.lower()
    if suf == ".dta":
        return pd.read_stata(path, columns=columns, convert_categoricals=False)
    if suf == ".sav":
        import pyreadstat

        df, _ = pyreadstat.read_sav(str(path), usecols=columns)
        return df
    if suf == ".csv":
        return pd.read_csv(path, usecols=columns, low_memory=False)
    if suf == ".xlsx":
        return pd.read_excel(path, usecols=columns)
    raise RuntimeError(f"不认识的格式 {suf}")


def get(name: str, *, cache: Path = CACHE, client: httpx.Client | None = None,
        refresh: bool = False, file: Path | None = None,
        columns: list[str] | None = None, out: Path | None = None) -> PackResult:
    """拿一套数据：下载（或用用户放进来的文件）→ 转 CSV → 记出处。

    - 直接下载的：第一次下，之后用缓存；`refresh` 重下
    - 要申请的：本地没文件就抛 `NeedsManualDownload`，说清怎么拿
    - `columns` 给了就只抽这些列（宽表必须这样，值标签一并写到旁边的 _labels.json）
    """
    if name not in PACKS:
        raise KeyError(f"没有叫 {name!r} 的数据集。可选：{'、'.join(PACKS)}")
    p = PACKS[name]
    d = pack_dir(name, cache)
    d.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    # ① 原件从哪来
    if file is not None:
        raw, source = Path(file), str(file)
    elif p.manual:
        inc = _incoming(name, cache)
        if not inc:
            raise NeedsManualDownload(
                f"{p.title} 需要注册申请，系统不能替你下载。\n{p.how_to}\n（放进 data/{name}/ 之后再跑一次）")
        raw, source = inc[-1], str(inc[-1])
        notes.append(f"用的是你放进来的 {raw.name}")
    else:
        raw = d / "raw" / Path(p.url).name
        source = p.url
        if refresh or not raw.exists():
            _download(p.url, raw, client)
            notes.append(f"从 {p.url} 下载")
        else:
            notes.append("用本地缓存的原件（--refresh 重下）")

    # ② 压缩包里挑文件；只读表头
    src = _unpack(raw, p.inner, d / "raw" if not p.manual and file is None else d)
    all_cols, labels = _header(src)

    # ③ 转 CSV（宽表只在给了 columns 时转）
    converted = False
    if columns:
        missing = [c for c in columns if c not in all_cols]
        if missing:
            raise KeyError(f"这些列不在 {p.title} 里：{'、'.join(missing)}。列名看码本：{p.codebook}")
        df = _read(src, columns)
        target = out or (d / f"{name}_subset.csv")
        df.to_csv(target, index=False)
        converted = True
        picked = {c: labels[c] for c in columns if c in labels}
        if picked:
            lp = Path(str(target) + "._labels.json")
            lp.write_text(json.dumps(picked, ensure_ascii=False, indent=1), encoding="utf-8")
            notes.append(f"值标签写在 {lp.name}（数值码原样保留，怎么重编码是你的判断）")
    elif len(all_cols) > WIDE:
        df = None
        target = src
        notes.append(f"{len(all_cols)} 列，**没整个转 CSV**——挑列：oss data get {name} --columns <列名,列名,...>")
    else:
        df = _read(src, None)
        target = out or (d / f"{name}.csv")
        df.to_csv(target, index=False)
        converted = True

    years = ""
    rows = 0
    if df is not None:
        rows = int(len(df))
        if p.time in df.columns:
            t = pd.to_numeric(df[p.time], errors="coerce").dropna()
            if len(t):
                years = f"{int(t.min())}–{int(t.max())}"
        for k, v in _keys(p).items():
            if v not in df.columns:
                notes.append(f"⚠ 钥匙列 {k}={v} 不在转出来的表里（没选它？）")

    res = PackResult(
        name=name, title=p.title, path=str(target), raw=str(raw), rows=rows,
        n_columns=len(df.columns) if df is not None else len(all_cols),
        columns=list(map(str, df.columns)) if df is not None else all_cols[:50],
        years=years, keys=_keys(p), source=source, sha256=_sha(raw),
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        citation=p.citation, codebook=p.codebook, license=p.license, weights=p.weights,
        converted=converted, notes=notes,
    )
    # 出处跟数据放一起——没有出处的数据跟没有出处的数字一样
    _meta_path(name, cache).write_text(json.dumps(asdict(res), ensure_ascii=False, indent=1), encoding="utf-8")
    if out is not None:
        Path(str(out) + ".meta.json").write_text(json.dumps(asdict(res), ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
    return res


def pack_of(path: str | Path, cache: Path = CACHE) -> Pack | None:
    """一个数据文件是不是某个数据包转出来的（按目录或旁边的 meta 认）。论文引用的门用。"""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(cache.resolve())
        name = rel.parts[0]
        if name in PACKS:
            return PACKS[name]
    except (ValueError, IndexError):
        pass
    meta = Path(str(p) + ".meta.json")
    if meta.exists():
        try:
            name = json.loads(meta.read_text(encoding="utf-8")).get("name", "")
            if name in PACKS:
                return PACKS[name]
        except (json.JSONDecodeError, OSError):
            pass
    return None
