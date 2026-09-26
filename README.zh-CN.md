# Ephemeris

[English](README.md) | **中文**

Built by **[Kapozux](https://github.com/Kapozux)**

**把你和 AI 的聊天记录变成每天一篇的日记。** Ephemeris 把你在 Claude 和 Gemini 里的全部对话按天重新整理，而不是按窗口；每天写一篇日记，再用一本账本记下你一直在推进的事。

*ἐφημερίς* 是希腊语的「逐日的记录」，后来被天文学借去指星历表：每天记下天体在哪。这个应用做的是同一件事，记的是你自己。

## 为什么要做

聊天软件按**窗口**存记录：一个对话可以拉好几个月，而一天的事散在十几个窗口里。两个很简单的问题就答不上来：

- 3 月 3 日那天我到底在干什么？
- 这件事我前后弄了多久，上次停在哪？

Ephemeris 把轴换过来：先把每一天从当天碰过的所有窗口里拼回来，再把一天一天串起来。

## 能做什么

- **每天一篇日记。** 用你自己发出的消息写成，第一人称：这天在忙什么、做完了什么、还有什么没做完。
- **主题账本。** 一件持续的事（一个项目、一份申请、一份作业）会跨天追踪。日记里标着「X 第 23 天」「隔了 41 天又回到 X」。「主题」面板把每条线放在同一条时间轴上看。
- **按窗口回顾。** 挑一个长对话，看它每个活跃日的一句小结，外加整条线的脉络。
- **全文搜索**你发过的所有消息。
- **每天 08:30 自动从 claude.ai 同步。** 拉新对话，补写缺的日记，不用点任何东西。
- **可选同步到 Notion。** 日记写进一个 Notion 数据库。那天你自己已经写过一页，就接在后面写，不动你写的内容。

**日记写一次就定下来。** 新功能都建立在现成的日记上，不会批量重写旧日记。

## 怎么运作

```
claude.ai（每日同步）─┐
                     ├─> index.py ──> chats.db ──> diary.py ──> ledger.py
手动导入 ─────────────┘   去重、分天     SQLite +     每天一篇     把当天的事
                                       FTS5                    对到主题上
                                          │
                                          ├─> 网页界面（localhost:5055）
                                          └─> to_notion.py ─> Notion
```

- **一天怎么算**：按 UTC+8，凌晨 4 点才算结束，熬夜聊的归到当天。
- **窗口身份**：用 claude.ai 的对话 uuid。没有稳定 id 的来源，退回用第一条消息算的指纹。
- **每篇日记**都会参考上一篇日记和主题账本，所以能接着昨天往下写。
- **把当天的事对到已有主题**，是单独一次很小的模型调用。塞进日记的 prompt 里，主题会越滚越大。「第几天」「隔多久」都由代码来算，模型只做判断。

## 支持导入

- claude.ai：每日自动同步，或者在浏览器控制台运行自带的 `export_claude.js`，导出 JSON / Markdown
- Claude 无痕模式的单窗口 transcript
- Gemini 的 `sess_*.md` 导出
- Google Takeout 里 Gemini Apps 的 `MyActivity.json`

同一份数据导两次也没关系。消息会去重，只有内容真的变了的那几天才会重写日记。

## 安装

需要：Python 3.9+、Google Chrome（claude.ai 同步用）、Gemini API key。

> Ephemeris 目前借用 [Verbatim](https://github.com/Kapozux/_Verbatim_) 的模型调用代码：它要求 Verbatim 的 `getAudio/` 文件夹放在旁边，并从那里 import `reflect._call_model`。默认模型是 `gemini-3.5-flash-lite`。

```bash
pip install flask websocket-client
cp .env.example .env        # 然后填上
python app.py               # http://localhost:5055
```

`.env` 里的字段（用不到的可以不填）：

| 字段 | 用途 |
| --- | --- |
| `CLAUDE_SESSION_KEY`、`CLAUDE_ORG_ID` | 每日 claude.ai 同步（浏览器里的 cookie） |
| `NOTION_TOKEN`、`NOTION_DIARY_DS` | Notion 同步（internal integration secret + 数据库的 data_source_id） |
| `DIARY_NAME` | 日记 prompt 里怎么称呼你 |

## 隐私

所有东西都留在你本机：数据库、日记、导入的文件、Chrome 登录态、`.env`。`.gitignore` 是**白名单**，只有源代码会进仓库。调用模型时，发给你配置的模型服务商的是某一天*你自己*发出的消息。

## 成本

用 `gemini-3.5-flash-lite` 大约每天 1 美分：写日记一次调用，匹配主题一次调用。
