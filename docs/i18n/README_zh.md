<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/title-dark.png">
  <img src="../images/title.png" width="640" alt="OpenSocialScience">
</picture>
<br/>

### 开源的社科定量研究 Agent

<p align="center">
<a href="../CHANGELOG.md"><img src="https://img.shields.io/badge/version-0.5.0-184878?style=flat-square&logo=github&logoColor=white" alt="Version"/></a>
<a href="../使用说明.md"><img src="https://img.shields.io/badge/docs-guide-0969da?style=flat-square&logo=readthedocs&logoColor=white" alt="Docs"/></a>
<a href="#-实例"><img src="https://img.shields.io/badge/examples-3_papers-ef476f?style=flat-square&logo=googledocs&logoColor=white" alt="Examples"/></a>
<a href="../../LICENSE"><img src="https://img.shields.io/badge/license-Apache_2.0-D22128?style=flat-square&logo=apache&logoColor=white" alt="License"/></a>
</p>

<p align="center">
<img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat-square&labelColor=333&logo=python&logoColor=FFD43B" alt="Python"/>
<img src="https://img.shields.io/badge/Claude_Code-skill-D97757?style=flat-square&labelColor=333&logo=claude&logoColor=D97757" alt="Claude Code"/>
<img src="https://img.shields.io/badge/DeepSeek-Harness-4D6BFE?style=flat-square&labelColor=333&logo=deepseek&logoColor=4D6BFE" alt="DeepSeek Harness"/>
<img src="https://img.shields.io/badge/MCP-server-00a890?style=flat-square&labelColor=333&logo=modelcontextprotocol&logoColor=white" alt="MCP"/>
<img src="https://img.shields.io/badge/export-Word-2B579A?style=flat-square&labelColor=333&logo=pandoc&logoColor=white" alt="Word"/>
</p>

<p align="center"><a href="../../README.md">English</a> · <b>简体中文</b></p>

</div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/hero-dark.jpg">
  <img src="../images/hero-light.jpg" width="100%" alt="三篇实例论文的真实页面排成扇形，分别是因果、描述、测量三类研究">
</picture>

OpenSocialScience 是一套在 Claude Code、Codex 等 Agent 中使用的社科定量研究工具。输入研究方向和数据，它依次完成文献检索、选题、计量分析、写作和跨模型审稿，导出按目标期刊排版的 Word 稿。

## 🔥 动态

- 🔥 **2026-10-07** 我们发布了 [v0.5.0](../CHANGELOG.md#050---2026-10-07)，欢迎试用。
- **2026-10-04** 新增 Agent 自动安装。
- **2026-09-27** 我们放出了三篇实例论文。
- **2026-09-14** 我们发布了 [v0.4.0](../CHANGELOG.md#040---2026-09-14)。

## 🚀 快速开始

### 让 Agent 安装

把下面这句话发给你的 Agent。

```text
帮我安装 https://github.com/openSocialScience/openSocialScience ，照仓库里的 docs/INSTALL.md 做。
```

### 手动安装

需要 Python 3.11 以上和 [pandoc](https://pandoc.org)。

```bash
git clone https://github.com/openSocialScience/openSocialScience
cd openSocialScience
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,figures,stats-formats,excel,causal,reading]"

cp .env.example .env    # 填 API key
oss doctor
oss install-skills
```

### 开始一项研究

在项目目录里打开 Agent，输入研究方向、数据和学科。

```text
/social-research 教育扩张是否削弱了家庭背景对地位获得的影响 --- data: 你的数据.csv --- discipline: sociology
```

生成的论文在 `runs/<运行目录>/paper.docx`，更多参数见[使用说明](../使用说明.md)。

## 🤖 支持的 Agent

| Agent | `--host` |
|---|---|
| Claude Code | `claude-code` |
| Codex / OpenCode | `codex` |
| DeepSeek Harness | `dsh` |
| WorkBuddy | `workbuddy` |
| OpenClaw | `openclaw` |

其他客户端可以通过 `oss mcp serve` 接入，见 [MCP 接入](../user/mcp.md)。

## 📄 实例

三篇论文均由 OpenSocialScience 生成，源稿、分析计划和结果见 [`examples/`](../../examples/)。

| 论文 | 类型 | 数据 | 体例 |
|---|---|---|---|
| [差序格局的城乡与代际分布](../../examples/descriptive-chaxu-geju/) | <img src="https://img.shields.io/badge/Descriptive-f28e2b?style=flat-square" alt="Descriptive"> | CGSS 2015 | 社会学研究 |
| [公共服务获得感测的是谁的获得](../../examples/measurement-huodegan/) | <img src="https://img.shields.io/badge/Measurement-00a890?style=flat-square" alt="Measurement"> | CGSS 2013 | 管理世界 |
| [宗教参与、捐赠与对陌生人的信任](../../examples/causal-religion-trust/) | <img src="https://img.shields.io/badge/Causal-ef476f?style=flat-square" alt="Causal"> | CGSS 2012 | 中国社会科学 |

## 🔍 数字溯源

论文里的统计数字只能写成 `{{result:main.coef}}` 这样的占位符，渲染时从 `results.json` 填入，手打的数字过不了检查。各环节的检查见[设计与架构](../ARCHITECTURE.md)。

<p align="center">
  <img src="../images/no-fabricated-numbers.png" width="880" alt="正文里手打的统计数字被检查拒绝，写成占位符才由程序填入真实数字">
</p>

## 📦 内置数据

QoG、V-Dem、GSS、CGSS 可以直接调用，例如 `--- data: pack:qog`。世界银行、OECD 和 Our World in Data 的指标用 `oss data search` 和 `oss data fetch` 检索下载。仿真数据由 `/social-sim` 生成，见[仿真说明](../user/sim-box.md)。

## 📜 许可证

[Apache 2.0](../../LICENSE)

## 📝 引用

```bibtex
@software{opensocialscience,
  author  = {{OpenSocialScience contributors}},
  title   = {OpenSocialScience: An Open-Source Agent for Quantitative Social Science Research},
  year    = {2026},
  version = {0.5.0},
  url     = {https://github.com/openSocialScience/openSocialScience}
}
```
