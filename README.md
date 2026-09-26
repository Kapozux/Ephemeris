# Ephemeris

**English** | [中文](README.zh-CN.md)

Built by **[Kapozux](https://github.com/Kapozux)**

**Turn your AI chat history into a daily diary.** Ephemeris takes every conversation you've had with Claude and Gemini, regroups it by day instead of by chat window, writes one diary entry per day, and keeps a running ledger of what you've been working on.

*ἐφημερίς* is Greek for "daily record". Astronomers later used the word for tables tracking where the planets are each day. This app does the same for you.

## Why

A chat app organizes history by **window**: one conversation can run for months, and a single day is scattered across a dozen windows. That makes it hard to answer two simple questions:

- *What was I actually doing on March 3rd?*
- *How long have I been working on this, and where did I leave it?*

Ephemeris flips the axis. It rebuilds every day from all the windows you touched that day, then links the days together.

## What you get

- **A diary entry for every day.** It's written in the first person from your own messages: what you worked on, what you finished, and what's still open.
- **A theme ledger.** Each ongoing thread (a project, an application, an assignment) is tracked across days. Entries show "day 23 of X" or "back to X after 41 days". A Themes panel shows every thread on one timeline.
- **A per-window view.** Pick one long conversation and see a one-line summary for each day it was active, plus the overall arc. When you keep chatting in that window, only the new or changed days get summarized; old summaries stay as they are.
- **Your handwritten diary, too.** Pages you wrote yourself in Notion are pulled in read-only and shown above the AI entry for that day. They extend the calendar back years before your chat history starts. Anything Ephemeris appended to those pages is stripped out first.
- **Two mood signals, kept apart.** *How the day went* comes from what you say yourself: a check-in, or your handwritten diary. *How stuck you were* (0–4) comes from your chats with the AI. An experiment showed chats capture the struggle, not the day: the good moments usually happen outside the chat, so the two are never merged into one number.
- **A full-screen check-in page.** Pick a word, add a line if you want, and record. You can log several moments a day or backfill earlier days. Each check-in gets a short reply that knows what you've been working on lately. A month view shows the mood mix, a star per day on the calendar, a mood constellation, and a one-line summary.
- **A topic river.** Every theme is sorted into a handful of categories, and a streamgraph shows how your attention moved between them week by week.
- **Full-text search** over everything you've ever sent.
- **Daily auto-sync from claude.ai at 08:30.** New conversations come in, missing diaries get written, and nothing needs clicking.
- **Optional Notion sync.** Diaries go into a Notion database. If you already wrote a page for that day, Ephemeris appends below it and never touches your text.

Entries are **written once and kept**. New features build on the existing diaries, and old entries are never regenerated in bulk.

## How it works

```
claude.ai (daily sync) ─┐
                        ├─> index.py ──> chats.db ──> diary.py ──> ledger.py
manual imports ─────────┘   dedupe,      SQLite +     one entry    match today's
                            split by day FTS5         per day      threads to themes
                                              │
                                              ├─> Flask UI  (localhost:5055)
                                              └─> to_notion.py ─> Notion
```

- **Days** are in UTC+8 and end at 4 a.m., so late-night sessions count toward the day they started.
- **Chat windows** are identified by the claude.ai conversation uuid. Sources without a stable id fall back to a fingerprint of the first message.
- **Each diary entry** also sees the previous entry and the theme ledger, so it can pick up where yesterday left off.
- **What the model reads** is every message you typed yourself, in full. Pasted material (articles, code, subtitles, AI replies) is cut down to its first and last few hundred characters, so a long paste can't crowd out your own words.
- **Two voices.** Diaries and reviews are a mirror: what you did and how you said you felt, with no advice. The check-in reply is a companion: warm, concrete, sometimes one small suggestion.
- **Matching a day's work to existing themes** is a separate, small model call. Putting it inside the diary prompt made themes drift. The code does all the counting ("day N", "N days since"); the model only makes judgments.

## Supported imports

- claude.ai: automatic daily sync, or the bundled `export_claude.js` (run it in the browser console) for JSON / Markdown exports
- Claude incognito single-window transcripts
- Gemini `sess_*.md` exports
- Google Takeout: `MyActivity.json` for Gemini Apps

Importing the same data twice is safe. Messages are de-duplicated, and only days whose content actually changed get a new diary entry.

## Setup

Requirements: Python 3.9+, Google Chrome (for the claude.ai sync), and a Gemini API key.

> Ephemeris currently borrows its model-calling code from [Verbatim](https://github.com/Kapozux/_Verbatim_): it expects Verbatim's `getAudio/` folder next to it and imports `reflect._call_model` from there. The default model is `gemini-3.5-flash-lite`.

```bash
pip install flask websocket-client
cp .env.example .env        # then fill it in
python app.py               # http://localhost:5055
```

`.env` keys (all optional except what you use):

| Key | For |
| --- | --- |
| `CLAUDE_SESSION_KEY`, `CLAUDE_ORG_ID` | daily claude.ai sync (your browser cookie) |
| `NOTION_TOKEN`, `NOTION_DIARY_DS` | Notion sync (internal integration secret + the database's data_source_id) |
| `DIARY_NAME` | what the diary prompts call you |

## Privacy

Everything stays on your machine: the database, diaries, check-ins, handwritten pages, imports, the Chrome profile, and `.env`. `.gitignore` is a **whitelist**, so only source code is ever tracked. Model calls send one day of *your own* messages to the model provider you configured.

## Cost

About one US cent per day with `gemini-3.5-flash-lite`: one call for the diary, one for theme matching.
