# OASIS 安装单

`sim: oasis` 这条插头要连得上真的 OASIS 才能跑模拟。这份单子只管把环境装起来、
把电通上；跑单模板（怎么填八个 `{{}}` 空位——七个必填、一个可选 `{{SEMAPHORE}}`，不填缺省 8）是另一回事，在 `oss/sims/oasis/runner_template.py`。

---

## 一、建一个 Python 3.11 的 conda 环境

```bash
conda create -n oasis python=3.11 -y
conda activate oasis
```

OASIS 的 `pyproject.toml` 写死要 `>=3.10.0,<3.12`——3.11 最稳妥。别拿系统自带的
或别的项目在用的 Python 凑合，版本对不上大概率装不上，装上了也可能跑着跑着崩。

---

## 二、clone 下来装成 editable 包

```bash
git clone https://github.com/camel-ai/oasis.git ~/Documents/oasis
cd ~/Documents/oasis
git checkout v0.2.5      # 见「六、已知坑」最后一条——别只 clone 完就直接装
pip install -e .
```

**克隆目录必须正好是 `~/Documents/oasis`**——下面第④、⑤节要用到的路径是 `oss/sims/oasis/profile.json` 的 `sample_base` 里写死的，
换个目录名或换个位置，装得再对 `oss sim doctor` 也会说样本数据不在。

---

## 三、中转站配在项目根的 `.env` 里

OSS 自己审稿用的中转站变量，跟跑模拟时用的是同一个网关、但**变量名不一样**，别弄混：

```bash
# 项目根 .env——OSS 自己审稿用这两个名字
REVIEWER_BASE_URL=https://api.deepseek.com/v1
REVIEWER_API_KEY=sk-...
```

跑单模板认的是 `BASE` / `KEY` 这两个环境变量名（V0 那份脚本沿用下来的），
`.env` 不会替你把值转过去，跑之前自己 export（通常填跟 `REVIEWER_*` 一样的值）：

```bash
export BASE=https://api.deepseek.com/v1
export KEY=sk-...
python raw/A_1.py     # 跑单模板填好空位另存的那份
```

---

## 四、样本数据在哪

`git clone` 已经把样本数据一起拉下来了，不用另外下载：

```
~/Documents/oasis/data/twitter_dataset/anonymous_topic_200_1h/False_Business_0.csv
```

`oss/sims/oasis/profile.json` 的 `sample_data` 字段记的就是这条相对路径
（`data/twitter_dataset/...`），`oss sim doctor` 会自动拼上同文件 `sample_base` 记的 `~/Documents/oasis/` 前缀去找它（各插头按自家 `sample_base` 解析，互不牵连）。

另存跑单脚本（按跑单模板开头说明书的要求，另存进货箱 `raw/` 下，如 `raw/A_1.py`）时，把这份 CSV **复制**
一份到脚本同目录、改名成 `profiles_full.csv`——`runner_template.py` 的 `trim_profiles()`
就认这一个文件名：

```bash
cp ~/Documents/oasis/data/twitter_dataset/anonymous_topic_200_1h/False_Business_0.csv \
   <货箱>/raw/profiles_full.csv
```

复制而不是留着引用原路径，是因为**箱子必须自含**——论文方法节引用的是箱子里的文件，
不是你机器上 `~/Documents/oasis` 这个跑完可能就删掉、换机器也不在的临时环境。

---

## 五、体检：`oss sim doctor --simulator oasis`

```bash
oss sim doctor --simulator oasis
```

**这条命令的退出码永远是 0**——不管三条体检过没过，都不会报错退出；doctor 只出体检报告，
不做通过检测。看每一行前面的 `✓`/`✗` 自己判断，别看命令没报错就以为都通过了。（唯一会让它
非 0 退出的情形是 `--simulator` 拼错成不认识的名字，那是另一种"输入错误"，不是体检没过。）

三条分别查什么、容易踩的坑：

- **包可导入且版本相符**：查的是**运行 `oss` 这条命令本身所在的 Python 环境**里有没有装
  `camel-oasis`==0.2.5——不是你在第①节新建的 `oasis` 这个 conda 环境！这两个环境常常是分开的
  （`oss` 装在 OSS 仓库自己的环境里，OASIS 装在专门的 `oasis` 环境里），分开装的话这一条大概率
  显示 `✗`，**不代表 OASIS 没装好**，只是 doctor 在错的环境里找包。想让这条也过，把 `oss` 也装
  进 `oasis` 这个环境：`conda activate oasis && pip install -e <OSS 仓库路径>`。
- **样本数据在**：查上面④那条路径存在不存在。
- **中转站变量在**：只查"当前目录往上、一路到根目录的任何一层，或
  `~/.config/oss/.env`，或本仓库开发态检出自己的 `.env`，这几处有没有文件"
  （见 `oss/core/envfile.py`）或"`REVIEWER_BASE_URL` 这个环境变量已经 export 过"，
  **不检查内容对不对、也不管那个 `.env` 是不是这个项目的**——随便哪处有个不相干的
  `.env` 都能让这条显示 `✓`。显示通过不代表中转站真的配对了，真跑之前自己再核对
  一遍 `.env` 里的值。

---

## 六、已知坑

| 坑 | 是什么 | 应对 |
|---|---|---|
| `twitter` 推荐档是坏的 | 0.2.5 的个性化推荐代码取 `trace['post_id']`，帖子数超过上限必崩；没装嵌入模型时它的"个性化"打分其实是随机数，名不副实 | 正式研究别选 `twitter` 档；用 `reddit`（热度）或 `random`，装好嵌入模型后可以用 `twhin-bert` |
| 档案 CSV 的换行陷阱 | 简介字段常带换行，一条记录能跨好几个物理行，`head -N` 之类按物理行取人会漏人或取乱 | `runner_template.py` 已经按 CSV **记录数**（`csv.DictReader`）取前 N 人，不用自己处理；自己另写脚本读这份档案时留意这条 |
| 库内时钟是加速 60 倍的模拟时间 | `trace` 表的 `created_at` 跟真实时钟对不上，不能拿它直接换算"第几轮" | 轮次全靠跑单模板每步记一次 `MAX(rowid)`、写进 `.steps.jsonl` 来恢复；跑单模板与插头已经按这个契约实现好了 |
| pip 报的版本号可能滞后于 main 分支 | `~/Documents/oasis` 若不 checkout 到 `v0.2.5` 标签，`git clone` 默认拿的是 main 分支最新代码——本文写这份单子时已经比 `v0.2.5` 多跑了几十个提交，但 `pyproject.toml` 里的版本号还写着 `0.2.5`，`pip show` / doctor 看到的就是这串没跟着改的字符串，不代表代码真的停在 v0.2.5 那一刻 | 按上面②那样 `git checkout v0.2.5` 再装，doctor 显示 `✓` 才靠得住 |
| 并发拉太高会压垮中转 | V1 真跑在并发 32 下连砸两场：一场收尾波全是连接错误、一场返回空回包——都是中转站被并发洪峰压垮的形态（余额不够的 402 是另一回事，见跑单手册的三信号分辨） | 跑单模板与 `profile.json` 的缺省并发 8 就是这条教训换来的；别轻易调大，图快先用小场试水 |

---

## 七、费用参考（V0 2026-08-31 实测，flash 模型经中转站，并发 32）

| 规模 | 单场时间 | 总费用 |
|---|---|---|
| 46 人 × 10 轮，两个条件各跑一场 | 单场约 5 分钟 | 两场合计约 3 元 |
| 100 人 × 20 轮，跑 6 次（正式规模量级） | 合计约 40 分钟 | 约二三十元 |
| 50 人 × 10 轮，两个条件各跑一场（2026-09-01 验收轮，**并发 8**） | 单场约 13-14 分钟 | 两场合计估约 3.3 元 |

实测比事先预估快了约十倍。但这是 V0 那一次的数字，不是保证——真跑之前自己留足预算余量，
跑单模板超预算要能停下来，别指望这张表精确对应你的那次跑。
还有一条：前两行的**时长**按当年并发 32 实测，如今缺省并发已归 8（为什么见 §六 新坑），同样规模慢约 2-3 倍——第三行就是并发 8 的实测；**费用**按 token 计价，不随并发变，元数仍可参考。
