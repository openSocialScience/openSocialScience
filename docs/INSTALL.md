# 安装说明（给 Agent 读）

用户让你安装 OpenSocialScience 时，按下面的步骤做。每一步做完再做下一步；
哪一步失败了，先告诉用户卡在哪、报错是什么，不要跳过。

## 1. 克隆仓库

```bash
git clone https://github.com/openSocialScience/openSocialScience
cd openSocialScience
```

已经在仓库目录里就跳过这一步。

## 2. 检查 Python 和 pandoc

- Python 要 3.11 或更高：`python3 --version`
- pandoc 用来导出 Word：`pandoc --version`

缺哪个就告诉用户怎么装（macOS 用 `brew install pandoc`，其他系统见 https://pandoc.org/installing.html），
**不要替用户装系统级软件**，等用户确认装好再继续。

## 3. 建虚拟环境并安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,figures,stats-formats,excel,causal,reading]"
```

后面的命令都用 `.venv/bin/oss` 调用，不依赖 `source activate` 是否生效。

## 4. 配置 .env

照 `.env.example` 复制一份 `.env`，然后**向用户要**下面这几项，填进去：

| 变量 | 是什么 | 必填 |
|---|---|---|
| `REVIEWER_BASE_URL` | 审稿模型的接口地址，任何 OpenAI 兼容端点 | 是 |
| `REVIEWER_MODEL` | 审稿模型名 | 是 |
| `REVIEWER_API_KEY` | 审稿模型的 API key | 是 |
| `EXECUTOR_MODEL` | 你自己（当前 Agent）用的模型名，你知道就直接填 | 否 |
| `OPENALEX_API_KEY` | 文献检索用，免费注册：https://openalex.org/settings/api | 是 |

规则：

- **API key 只能由用户提供，不许编造、不许留示例值。**用户暂时没有，就留空，并告诉用户之后去哪填。
- 提醒用户：审稿模型最好和你不是同一家公司的模型，否则审稿不算独立验证。
- `.env` 不要提交到 git（它已经在 `.gitignore` 里）。

## 5. 体检

```bash
.venv/bin/oss doctor
```

有红项就按提示修；修不了的（比如缺 key、缺 pandoc）告诉用户。

## 6. 安装说明书

```bash
.venv/bin/oss install-skills
```

它会自动认出当前 Agent。认不出时加 `--host`：`claude-code`、`dsh`、`codex`、`workbuddy`、`openclaw`。

## 7. 告诉用户怎么开始

装完后告诉用户：在项目目录里打开 Agent，发一句

```
/social-research <研究方向> --- data: <数据文件> --- discipline: <学科>
```

学科可选 `economics` / `sociology` / `political-science` / `psychology` / `communication`。
没有自己的数据可以写 `data: pack:qog` 用内置数据集。更多用法见 README 的「快速开始 · 3 · 跑」。
