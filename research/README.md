# Ephemeris 研究版（演示）

把很多人的 AI 聊天记录，按一份研究方案变成研究者能直接用的数据：只算这个人自己打的话（贴进来的不算），
按量表日期切期，每个数都能点回支撑它的原话。现在是给 Stanford 的 Merve Cerit 看的演示版，参与者全是虚构的。

## 跑一遍

```bash
../venv/bin/python -m research.synth gen       # 生成虚构聊天（DeepSeek，约 65 次调用；生成过的跳过）
../venv/bin/python -m research.synth load      # 装进 data/<pid>.db，写 data/cesd.csv
../venv/bin/python -m research.export          # 算指标（「想法谁先提」要调 DeepSeek，判过的缓存着）→ data/out/
../venv/bin/python -m research.selfcheck       # 流程自检 → data/out/selfcheck.json
../venv/bin/python -m research.report          # 演示页 → data/out/report.html（自包含，双击打开）
```

模型调用的花费记在 `data/_usage.db`（`research.ledger()`），不进真的 `chats.db`。

## 结构

| 文件 | 干什么 |
|---|---|
| `study.demo.json` | 研究方案：参与者、来源、要哪些指标、深夜的钟点、隐私做法（A / B）、导出格式 |
| `personas.py` | 5 个虚构参与者、每期状态、埋的故事线（PLANTS）、对照组 |
| `synth.py` | 生成虚构聊天、装库、写 CES-D |
| `participant.py` | 读一个参与者，按量表切期；「这个人自己打的话」只在 `Period.said()` |
| `measures/*.py` | 指标插件，一个文件一个指标，文件名 = 导出的列名 |
| `export.py` | 对齐、导出 `data_clean.csv` / `chat_features.csv` / `evidence.json` / `manifest.json` |
| `selfcheck.py` | 找回埋的故事线、对照组误报、「想法谁先提」的一致率和盲评 |
| `report.py` + `report_template.html` | 演示页 |

只许引用 `research.CORE` 里的 Ephemeris 模块（`tests/test_research.py` 查）。「打的还是贴的、主动还是复述」用根目录的 `speech.py`。

## 跟 Merve 的代码怎么接（github.com/mediacontentatlas/within-person-cesd-screenome）

- `data_clean.csv` 的列名照她的输入：`pid, period_number, survey_date, period_start, period_end, cesd_score, days_in_period,
  active_days, active_day_ratio`，另加 `tz` 和 `chat_*`。日期是**不带偏移的当地时间**（跨夏令时偏移会混，pandas 新版
  `to_datetime` 会报错），时区在 `tz` 列。
- 她的特征列表是写死的：`modeling_table.py` 和 `delta_table.py` 的 `behavioral_cols`（delta 还有 `keep_behavioral_cols`）、
  `prepare_features.py` 的 `CONTINUOUS_UNSCALED` / `CONTINUOUS_STANDARDIZED`。**不加进去，`chat_*` 会被静默丢掉**。
- 要并进她自己的表用 `chat_features.csv`：全是 `chat_` 前缀，按 `pid + period_number` 合并，不会跟她截图算的 `active_days` 撞名。
- **还没在她的代码里真跑过**（2026-10-28：在本机跑外部仓库的代码被权限拦了），上面是读她代码对出来的。

## 现在的结果（演示数据，2026-10-28）

- 5 人、492 个窗口、约 3100 条消息、每人 13 期。
- 埋的 5 条故事线全找回（第一人称那条勉强：z = 0.54，门槛 0.5）；对照 P02 的 52 个数里 4 个 |z| > 2，跟随机波动差不多。
- 「想法谁先提」：判断跟生成标签只有 55% 一致；20 个随机窗口盲评（标的是 Claude，**不是人**），判断模型对 18 个、生成标签对 12 个。
  分歧多在「照着 AI 的建议做了」算不算一个决定——要跟研究者商量定义。这个定义下大家的 AI 先提比例本来就高（0.4–0.9）。

## 局限（演示时要说）

- 故事线、聊天、分数全是我们配的：自检只说明流程没坏，不说明指标有效。所以不画相关、不报 p 值、不跑预测。
- 13 期太少，做不了个人内模型（她论文里每人约 20 期都说受限）。
- 第一人称：英文按词、中文按汉字算分母，跨语言的人只能本人内部比。
- 「想法谁先提」靠模型判断，原话逐字对上才计入；人工抽查还没做。

## 还没做

定性编码 / 多人标注、参与者端（做法 B）的安装程序、第三方信息打码、研究方案里 `sources` 以外的过滤、真实数据的导入路径
（核心模块有，研究版还没接）。
