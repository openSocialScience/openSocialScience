# OASIS 安装单

`sim: oasis` 要连上真实的 OASIS 才能跑模拟。这份单子讲怎么装环境、配变量、放样本数据和体检。
跑单模板有八个 `{{}}` 空位，七个必填，可选的 `{{SEMAPHORE}}` 不填缺省 8，填法见
`oss/sims/oasis/runner_template.py` 开头的说明。

---

## 一、建一个 Python 3.11 的 conda 环境

```bash
conda create -n oasis python=3.11 -y
conda activate oasis
```

OASIS 要求 Python `>=3.10,<3.12`，用 3.11。不要用系统自带的或别的项目在用的环境。

---

## 二、clone 下来装成 editable 包

```bash
git clone https://github.com/camel-ai/oasis.git ~/Documents/oasis
cd ~/Documents/oasis
git checkout v0.2.5      # 原因见第六节
pip install -e .
```

**克隆目录必须是 `~/Documents/oasis`**。`oss sim doctor` 按这个位置找样本数据，换了位置会报样本数据不在。

---

## 三、API 网关的环境变量

跑单模板读 `BASE` 和 `KEY` 两个环境变量，跑之前自己 export。值通常跟项目根 `.env` 里审稿用的
`REVIEWER_BASE_URL`、`REVIEWER_API_KEY` 一样，`.env` 不会自动转过去：

```bash
export BASE=https://api.deepseek.com/v1
export KEY=sk-...
python raw/A_1.py     # 跑单模板填好空位另存的那份
```

---

## 四、样本数据在哪

clone 时样本数据已经一起下来了：

```
~/Documents/oasis/data/twitter_dataset/anonymous_topic_200_1h/False_Business_0.csv
```

另存跑单脚本时（放进货箱的 `raw/` 下，如 `raw/A_1.py`），把这份 CSV 复制到脚本同目录，
改名为 `profiles_full.csv`，跑单模板只认这个文件名：

```bash
cp ~/Documents/oasis/data/twitter_dataset/anonymous_topic_200_1h/False_Business_0.csv \
   <货箱>/raw/profiles_full.csv
```

货箱必须自带全部文件，论文方法节引用的是箱子里的这一份。

---

## 五、体检：`oss sim doctor --simulator oasis`

```bash
oss sim doctor --simulator oasis
```

这条命令只出报告，三条过没过退出码都是 0（只有 `--simulator` 写错时非 0）。看每行前面的 `✓` / `✗`。

- **包可导入且版本相符**：查的是运行 `oss` 的那个 Python 环境里有没有 `camel-oasis==0.2.5`。
  `oss` 和 OASIS 装在两个环境里时这一条显示 `✗`，OASIS 本身照样能用。想让它也过，把 `oss` 装进
  `oasis` 环境：`conda activate oasis && pip install -e <OSS 仓库路径>`。
- **样本数据在**：查第四节那条路径。
- **网关变量在**：只查找不找得到 `.env`（当前目录逐级往上、`~/.config/oss/.env`、OSS 仓库根目录）
  或已 export 的 `REVIEWER_BASE_URL`，不查值对不对。显示 `✓` 也要在真跑前自己核一遍 `.env` 里的值。

---

## 六、已知坑

| 坑 | 是什么 | 应对 |
|---|---|---|
| `twitter` 推荐档不能用 | 0.2.5 的个性化推荐取 `trace['post_id']`，帖子数超过上限就崩；没装嵌入模型时打分是随机数 | 用 `reddit`（热度）或 `random`；装好嵌入模型后可以用 `twhin-bert` |
| 档案 CSV 有跨行记录 | 简介字段常带换行，一条记录占好几行，`head -N` 这类按行取人的办法会取错 | 跑单模板按 CSV 记录数取前 N 人；自己写脚本读档案时也按记录读 |
| 库内时钟加速 60 倍 | `trace` 表的 `created_at` 是模拟时间，不能直接换算成第几轮 | 跑单模板每步记一次 `MAX(rowid)` 写进 `.steps.jsonl`，插头按它恢复轮次 |
| 版本号不可信 | main 分支的代码比 `v0.2.5` 新，`pyproject.toml` 里却仍写 `0.2.5`，`pip show` 和 doctor 看到的都是这个号 | 先 `git checkout v0.2.5` 再装 |
| 并发太高压垮网关 | 并发 32 时出现过整波连接错误和空回包。余额不足的 402 是另一类错误 | 缺省并发 8，不要轻易调大，先用小场试 |

---

## 七、费用参考

flash 模型经 API 网关的实测：

| 规模 | 并发 | 时间 | 总费用 |
|---|---|---|---|
| 46 人 × 10 轮，两个条件各跑一场 | 32 | 单场约 5 分钟 | 两场合计约 3 元 |
| 100 人 × 20 轮，跑 6 次 | 32 | 合计约 40 分钟 | 约二三十元 |
| 50 人 × 10 轮，两个条件各跑一场 | 8 | 单场约 13 到 14 分钟 | 两场合计约 3.3 元 |

费用按 token 计，跟并发无关；同样规模下并发 8 的时长大约是并发 32 的 2 到 3 倍。
这些都是单次实测，真跑前留足预算余量。
