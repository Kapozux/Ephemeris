# Ephemeris 研究版（演示）

把很多人的 AI 聊天记录，按一份研究方案变成研究者能直接用的数据：只算这个人自己打的话（贴进来的不算），
按量表日期切期，每个数都能点回支撑它的原话。现在是给 Stanford 的 Merve Cerit 看的演示版，参与者全是虚构的。

## 跑一遍

```bash
../venv/bin/python -m research.synth gen       # 生成虚构聊天（研究方案 models 里的模型，演示版是 DeepSeek 官方；约 65 次调用；生成过的跳过）
../venv/bin/python -m research.synth load      # 装进 data/<pid>.db，写 data/cesd.csv
../venv/bin/python -m research.export          # 算指标（「想法谁先提」要调 DeepSeek，判过的缓存着）→ data/out/
../venv/bin/python -m research.stability      # 「想法谁先提」再判 2 遍算稳定性（DeepSeek，跑过的跳过）
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
| `export.py` | 对齐、导出 `data_clean.csv` / `chat_features.csv` / `evidence.json` / `evidence.csv` / `manifest.json` |
| `selfcheck.py` | 找回埋的故事线、对照组误报、「想法谁先提」的一致率、盲评、逐字核对三个数、按平台 / 语言分开报 |
| `stability.py` | 「想法谁先提」同一提示再判几遍，算 Krippendorff α |
| `report.py` + `report_template.html` | 演示页 |

只许引用 `research.CORE` 里的 Ephemeris 模块（`tests/test_research.py` 查）。「打的还是贴的、主动还是复述」用根目录的 `speech.py`。

## 方法上的几条约定（照 `devtools/research/hai_tools.md` 调研里研究者要的）

- **模型只用研究方案写明的那几家**（`models: ["厂商:模型", ...]`，按顺序；10-29）：研究版代码里调模型只走 `research.route`，
  它只调 `llm.ask_only`（一家一个模型，失败就报错，不重试、不换）。Ephemeris 自己的 `llm.ask` 会在 Claude / 百炼 / DeepSeek 官方 /
  OpenRouter / Gemini 之间自动换，研究里这等于违反写给 IRB 的数据流，所以研究版不许用（`tests/test_research.py` 会扫）。
  每次尝试记进 `data/flow.jsonl`（不记内容），导出时汇总成 manifest 的 `data_flow`：数据经过哪些厂商、多少字、多少参与者。
  没写 models 的方案不调模型；`local:` 留给本机模型，还没接。
- **参与者先看再交**（10-29，`research/submit.py`，`research/consent.py`）：`python -m research.submit P01` 在参与者电脑上开
  127.0.0.1:5066，原样摆出要交出去的东西（每期的数；做法 A 还有原话证据）。能拿掉整个窗口（从所有指标里拿掉，不只是不给看）、
  删掉单条原话、退出（已生成的包删掉，全体导出跳过他）；确认后才在 `data/submit/<pid>/` 生成提交包和回执（各文件 sha256）。
  决定只存在参与者这边（`data/<pid>.consent.json`），研究者从 manifest 的 `participant_review` 只看得到计数。
  改了决定，之前的确认作废。交出去这一步（上传 / 邮件）不在这里。
- **覆盖报告**（10-29，`research/coverage.py`）：每期报有数据的天数、最长空白、主平台和占比、回复里知道是哪个模型写的占比
  （`chat_cov_*` 列），`coverage.csv` 一期 × 一个平台一行列出各模型几条。指标变了先看这几列：换平台、换模型、一段没数据都会让指标变，
  那是数据变了不是人变了；缺的是缺，不是 0。模型来自 Ephemeris 的 `msg_models`：Claude Code / Codex 每条有，claude.ai 同步按窗口
  （10-29 起），ChatGPT 每条有；Gemini、Cursor、claude.ai 官方导出不记，报「不知道」。参与者在提交前审阅页自报还用过哪些没导进来的
  账号 / App，进 manifest。
- **说话人只看导出文件的结构**（role 字段），不交给模型；贴进来的内容按 `speech.typed` 判掉，只当上下文。
- **ChatGPT / DeepSeek 导出是分支树**：取最后显示的那一支（ChatGPT 从 current_node 往上，DeepSeek 每层取最后一个孩子），写在 manifest 里。
- **逐字核对报三个数**：模型给的原话对不上被丢掉的比例；丢掉的里模糊匹配找得到原文的（改了措辞，不是编的）；
  留下的里原话确实支持结论的（拿盲评近似）。原话逐字存在不等于支持结论。
- **`evidence.csv` 把三样分列**：`verbatim_text`（库里原样）、`selection_reason`（规则写的）、`llm_*`（模型生成的）；
  `gold_sample` 标出盲评样本，以后做下游校正（Egami 的 DSL）要用。
- 跨平台导入字段可以对照开源的 AgenticSearchLens（ChatGPT / Claude / Grok / DeepSeek 四家，没有 Gemini）。

**对外说法**：不说「没人做过 X」——每一项单独看都有接近的工作。说「把本地、多平台、逐字核对、按人的时间序列、
想法来源、导出长表这几样组合起来的可复用工具」。

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
- **「想法谁先提」不稳定**：同一提示跑 3 遍，492 个窗口里每遍都一样的只有 313 个（64%），Krippendorff α = 0.56，
  低于常用的 0.667 / 0.8。翻来翻去的多是「用户 ↔ AI」：用户问、AI 给几个选项、用户挑一个。先跟研究者把定义说清，
  再考虑多遍投票（投票会把定义上的模糊藏起来，所以现在不做）。
- 分平台 / 语言：中文、gemini 的一致率和稳定性略高；但演示里平台是生成模型分配的，这个差别不代表真实导出。
- 会话数（隔 30 分钟算新的一段）：虚构数据里多数日子只有 1 段，标准差很小，对照 P02 有一期 z = 3.46，是这个原因。
- 逐字核对：模型给了原话的 434 个判断里丢掉 2 个（0.5%），其中 1 个模糊匹配找得到；留下的里盲评支持结论 16/17。
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
验证上还差（调研里研究者要的）：人工金标准和人与人一致性（AC1 / F1）——要人来标；换几种措辞再跑的稳定性；
按会话汇总「命中」类指标时按会话长度校正（现在还没有这类指标）。
