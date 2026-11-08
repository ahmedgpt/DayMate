# DayMate — AI daily diary & reminder assistant

Chat with a Telegram bot to log your day, set reminders and get summaries. An LLM agent calls tools on a
Python **MCP server** (LLM: **Groq**, free tier); an **n8n** workflow delivers due reminders and daily summaries.

```
Telegram ─► bot (long polling) ─► ChatController ─► Agent (LLM tool loop)
                                                      │  MCP over streamable HTTP
                                                      │  headers: Bearer token + X-DayMate-User-Id
                                                      ▼
n8n ── REST /api/* (Bearer) ──────────────► MCP server + REST  ─► services ─► SQLite
```

## Quick start (after setup)

1. Start Docker Desktop and wait for "Engine running".
2. Double-click `start_daymate.bat` (or run `D:\DayMate\start_daymate.bat`): starts n8n, the server and the Telegram bot.
3. To stop: close the two DayMate windows and run `stop_daymate.bat` (stops n8n too). Data is kept.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then fill in the values below
```

| Variable | Needed for | Where to get it |
|---|---|---|
| `MCP_AUTH_TOKEN` | server, agent, n8n | any long random string |
| `GROQ_API_KEY` | agent (free tier, no billing needed) | console.groq.com/keys |
| `LLM_MODEL` | agent | default `openai/gpt-oss-120b`; must support tool calling and be available to your account |
| `TELEGRAM_BOT_TOKEN` | bot | @BotFather on Telegram |
| `DEFAULT_LOCATION` | optional weather default | e.g. `Lahore` |

Open-Meteo needs no key.

## Run

```powershell
.\.venv\Scripts\python -m app.main server   # terminal 1: MCP server + REST API on :8000
.\.venv\Scripts\python -m app.main chat     # terminal 2: talk to the agent without Telegram
.\.venv\Scripts\python -m app.main bot      # terminal 2 (instead): Telegram bot
```

On first message the bot asks for your city/timezone and saves it (`set_timezone`); reminders and "today" depend on it.

Inspect the tools with the MCP inspector: `npx @modelcontextprotocol/inspector` → Streamable HTTP →
`http://127.0.0.1:8000/mcp`, header `Authorization: Bearer <MCP_AUTH_TOKEN>` and `X-DayMate-User-Id: <id>`.

## MCP tools

`add_diary_entry`, `get_diary_entries` (date range + keyword `search`), `update_diary_entry`, `create_reminder`,
`list_reminders`, `delete_reminder` (sets `status=cancelled`), `get_daily_summary`, `get_weekly_summary`,
`get_weather` (Open-Meteo: current / 15-day forecast / historical), `set_timezone`.
No tool takes a `user_id`; identity comes from the `X-DayMate-User-Id` header set by our agent code.
Every response is `{"success": true, ...}` or `{"success": false, "error": code, "message": text}`.

## n8n (local, Docker)

```powershell
docker compose up -d          # n8n at http://localhost:5678 (first visit: create the owner account)
docker compose logs -f n8n    # logs
docker compose down           # stop (workflows/credentials persist in the n8n_data volume)
```
[docker-compose.yml](docker-compose.yml) sets `DAYMATE_URL=http://host.docker.internal:8000`, so the DayMate server must be running on the host.
Workflow files are mounted read-only at `/workflows` in the container. The CLI import used during setup:
`docker exec daymate-n8n n8n import:workflow --input=/workflows/<file>.json` (credentials still need creating in the UI or via `import:credentials`).
Workflows are imported **inactive**: activate them in the UI only when Telegram is reachable from the container, otherwise failed sends use up
a reminder's retry attempts. Check with `docker exec daymate-n8n wget -qO- https://api.telegram.org/`.

## n8n (generic notes)

1. Run n8n with `DAYMATE_URL` in its environment (e.g. `http://host.docker.internal:8000` if n8n is in Docker; start
   the DayMate server with `MCP_HOST=0.0.0.0` in that case and keep it off the public internet or put HTTPS in front).
2. Create credentials: **Header Auth** named `DayMate API` (`Authorization` = `Bearer <MCP_AUTH_TOKEN>`) and (daily/weekly summaries only) **Groq** named `Groq account`. No Telegram credential is needed.
3. Import `n8n/reminder_delivery.json`, `n8n/daily_summary.json` and `n8n/weekly_report.json`, check the credentials, activate.

Every workflow has a **Test now (manual)** trigger next to its schedule: click *Execute workflow* to run it immediately.
For the summaries the test path uses the *current* hour/weekday in Asia/Karachi (the container's `GENERIC_TIMEZONE`), so it finds
Telegram users in that timezone who have entries. Each workflow ends in an explicit branch (`Delivered` / `Sent`,
`Delivery failed (will retry)` / `Send failed`, or `Nothing due` / `Nobody due`) so an execution never looks unfinished.
Digests include only users with a Telegram chat; terminal (`chat` mode) users are never summarised or messaged.
CLI equivalent (while n8n is running): `docker exec -e N8N_RUNNERS_BROKER_PORT=5691 -e N8N_PORT=5699 daymate-n8n n8n execute --id=dm-weekly-report`.

**Reminder delivery** (every minute) - hybrid design, one scheduler:

```
n8n (Docker)  every minute ──► POST /api/reminders/claim-due      lease due reminders (n8n is the only scheduler)
              per reminder  ──► POST /api/reminders/{id}/deliver   host server sends Telegram, THEN records the result
```
n8n never talks to Telegram and holds no Telegram credential (useful when Telegram is blocked from Docker or you don't want
the bot token in n8n). The DayMate server on the host sends the message with its own `TELEGRAM_BOT_TOKEN`:
- **Marked `sent` only after Telegram confirms.** On failure the lease is released, `attempt_count`/`last_error` are recorded, the
  endpoint answers 502 (n8n's error branch), and the reminder is claimed again until `REMINDER_MAX_ATTEMPTS`, then `failed`.
- **No duplicates:** `deliver` requires an active lease; a per-reminder lock plus a `status = 'pending'` check means a repeated or
  concurrent call returns `skipped` without sending. Cancelled/sent reminders are never sent. Delivery is still *at-least-once* in
  one narrow case: Telegram accepted the message but the database write that follows failed.
- Connection failures are retried (3 tries) inside one attempt; a read timeout is not retried because Telegram may already have the message.
- There are no `/sent` or `/failed` endpoints: nothing can mark a reminder sent without delivering it.

**Daily summary** (hourly): returns users whose local hour is `hour=23` (edit the URL) and who logged something today; an
AI Agent node writes the summary from those entries only; it is sent through `POST /api/telegram/send` (the server accepts only chat ids of registered users).

**Weekly report** (hourly trigger): calls `/api/digests/weekly?weekday=6&hour=20` (Sunday 20:00 user-local; `0`=Monday..`6`=Sunday,
edit the URL to change). The window is the rolling 7 days ending that day, computed by the same `SummaryService.weekly` code that backs
the `get_weekly_summary` MCP tool. n8n calls it over REST (not MCP) for consistency with the other workflows. Counts and totals
come from the API; the AI node only writes prose. Users with no entries that week get nothing.

> The workflow JSON files were written against n8n's documented node formats but **have not been run against a live n8n
> instance**. The AI Agent / Groq Chat Model node versions and model dropdown in particular may need re-selecting after import.

## Checking Groq with your account

```powershell
.\.venv\Scripts\python scripts/check_groq.py            # uses LLM_MODEL from .env
.\.venv\Scripts\python scripts/check_groq.py <model-id> # try another model
```
Lists the models your key can see, then runs the real agent against the real MCP tools on a throwaway database:
diary add/get/update, reminder create/list/cancel (including a vague-time request that must trigger a question), and daily/weekly
summaries, printing PASS/FAIL per check. If your model is missing or tool calls misbehave, pick another tool-calling model from the list.

## Tests

```powershell
.\.venv\Scripts\python -m pytest tests -q
```
Unit tests cover services, timezone/DST handling, ownership isolation, lease/retry logic and the weather client (mocked HTTP).
Integration tests start the real server and check tool discovery, per-user isolation over MCP, the n8n REST flow, and the agent loop
with a scripted LLM. Real-network check done once manually: Open-Meteo current, forecast and historical endpoints.
`GroqLLM` is unit-tested against a faked client (message/tool translation, retry, error mapping). **Not covered by automated tests:** a real Groq conversation (use `scripts/check_groq.py`), the live Telegram API, and live n8n.

## Decisions & deviations from the spec

- `mcp>=1.30,<2`: v2 renamed FastMCP → MCPServer and changed APIs. Revisit when v2 is stable for your needs.
- n8n uses REST endpoints on the same server rather than n8n's MCP Client node (version support varies); same database/services.
- Atomic claim via `lease_until_utc` instead of an extra `sending` status; statuses remain pending/sent/cancelled/failed.
- Summary tools return grounded data + code-computed counts; the LLM writes the prose. The `daily_summaries` table is not created (not needed yet).
- Weeks are a rolling 7 days ending today unless both dates are given. Default weekly report: Sunday 20:00 user-local (configurable in the workflow URL).
- Hosting: local development only (confirmed); no cloud deployment.
- Scope confirmed: DayMate is the final CP1 project; no separate Weather Agent.
- One diary entry per message by default; `duration_minutes` is only set when the user states it.
- Short chat history (last 8 turns) is kept in memory for follow-up questions only; it is not the diary and is lost on restart.
- LLM is **Groq** (official `groq` SDK, free tier) behind a small interface (`app/agent/llm.py`), replacing the earlier Anthropic default at the user's request. The agent keeps one internal message shape; `GroqLLM` translates it to Groq's OpenAI-style chat/tool format. Null/`"null"` optional arguments from smaller models are dropped, and a malformed tool call (`tool_use_failed`) is retried once. n8n summaries use n8n's Groq Chat Model node. No paid APIs are used. Free-tier rate limits apply; a rate-limit error makes the bot say it can't reach the model and do nothing.
- Unsupported Telegram message types get a polite text-only reply; group chats are ignored.
