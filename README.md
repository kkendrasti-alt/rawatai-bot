# RawatAI

RawatAI is a Telegram-based AI companion for cancer caregivers.

It helps caregivers save treatment context, check in on their condition, journal what they are carrying, receive burden-aware support, view daily and weekly reflections, take short mindful/breathing pauses, and receive proactive reminders.

> **Karena yang merawat juga perlu dirawat.**

---

## Current Code Snapshot

| File | Role |
|---|---|
| `function_app.py` | Azure Functions entry point, Telegram webhook, command routing, reminders, daily/weekly UX |
| `agents.py` | Reflection, Habit, and Context agents powered by Microsoft Foundry / Azure OpenAI |
| `cosmos_client.py` | Cosmos DB wrapper for caregiver profiles, treatment context, check-ins, journal entries, content library, and nudges |
| `scheduler_service.py` | Shared scheduler / response orchestration helper for proactive nudges |

---

## What Changed Recently

### 1. Function App / deployment stability

`function_app.py` keeps the required Azure Functions Python v2 top-level app object:

```python
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
```

This prevents the earlier issue where Azure could not find the Function App entry point.

### 2. Telegram routes

The app exposes:

- `GET /api/health`
- `POST /api/telegram_webhook`

The webhook handles text messages and Telegram inline keyboard callback presses.

### 3. Supported commands

Detected in the latest `function_app.py`:

- `/start`
- `/help`
- `/reset`
- `/setup`
- `/checkin`
- `/journal`
- `/today`
- `/weekly`
- `/insights`
- `/mindful`
- `/breathe`
- `/testreminders`
- `/runscheduler`

### 4. Consistent next-step guidance

Responses now use a consistent `next_actions_footer()` pattern so the user always knows what to do next.

Examples of next options include:

- `/checkin`
- `/journal`
- `/mindful`
- `/breathe`
- `/help`

### 5. Improved `/mindful`

`/mindful` now adapts lightly based on today’s check-in:

- high stress / anxious mood → grounding pause
- low energy / tired mood → low-energy pause
- otherwise → 60-second mindful pause

It also explains that the exercise is self-guided and no timed follow-up will be sent.

### 6. Added `/breathe`

`/breathe` provides a guided breathing reset. It is intentionally self-guided and non-blocking so the webhook does not wait or trigger Telegram retries.

### 7. Improved `/journal`

Journaling now includes:

- phase-aware prompts,
- AI reflection via `reflection_agent()`,
- a local fallback if Foundry/OpenAI fails,
- a closing menu to guide the user to `/checkin`, `/mindful`, `/breathe`, or `/help`.

The fallback now reflects the current journal text instead of using unrelated old themes.

### 8. Improved `/checkin`

The check-in flow collects:

- energy,
- stress,
- sleep,
- mood,
- loved-one condition,
- mindful pause.

The Habit Agent now detects caregiver burden level so high-strain check-ins receive emotional support first, not generic habit/streak feedback.

### 9. Improved `/today`

`/today` is now a caregiver daily snapshot. It can include:

- treatment context,
- today’s check-in,
- last 24 hours journal insight,
- detected themes,
- one practical next step,
- clear next actions.

### 10. Improved `/weekly`

`/weekly` is now positioned as a 7-day caregiver reflection. It can summarize:

- journal patterns,
- check-in load,
- treatment context,
- what the caregiver still did,
- one gentle focus for next week.

### 11. Proactive scheduler

Scheduler behavior is enabled by default:

```bash
ENABLE_SCHEDULED_REMINDERS=true
```

The scheduler can send:

- daily check-in prompts,
- mindful pause prompts,
- night reflection prompts,
- hydration reminders.

Manual test commands are also available:

- `/testreminders`
- `/runscheduler`

### 12. Microsoft Foundry / Azure OpenAI integration

`agents.py` now supports both:

1. **Microsoft Foundry Target URI / OpenAI-compatible Responses API**

```text
https://<resource>.services.ai.azure.com/api/projects/<project>/openai/v1/
```

2. **Classic Azure OpenAI endpoint**

```text
https://<resource>.openai.azure.com/
```

The code can trim `/responses` if the full Foundry target URI is pasted accidentally.

### 13. Agent layer

| Agent | Function | Purpose |
|---|---|---|
| Reflection Agent | `reflection_agent()` | Journal response and emotional reflection |
| Habit Agent | `habit_agent()` | Burden-aware check-in response |
| Context Agent | `context_agent_parse()` | Treatment schedule parsing |

### 14. Deterministic schedule parsing

The Context Agent uses deterministic parsing first for demo reliability.

Example:

```text
My son has chemo every 21 days starting May 3 at 9 AM
```

If deterministic parsing fails, it falls back to LLM JSON parsing.

### 15. Cosmos DB wrapper

`cosmos_client.py` centralizes access for:

- caregivers,
- patient context,
- check-ins,
- journal entries,
- content library,
- nudge events.

### 16. Scheduler service

`scheduler_service.py` provides response orchestration and proactive nudge generation using the same agent layer.

---

## Architecture

```text
Telegram
   ↓
Azure Function App
   ├── /api/telegram_webhook
   ├── /api/health
   └── timer trigger / scheduler
   ↓
RawatAI Application Layer
   ├── setup flow
   ├── check-in flow
   ├── journal flow
   ├── today / weekly summaries
   ├── mindful / breathe flows
   └── reminder orchestration
   ↓
Agent Layer
   ├── Context Agent
   ├── Habit Agent
   └── Reflection Agent
   ↓
Microsoft Foundry / Azure OpenAI
   └── gpt-4.1-mini deployment
   ↓
Cosmos DB
   ├── caregivers
   ├── patient_context
   ├── checkins
   ├── journal_entries
   ├── content_library
   └── nudge_events
```

---

## Environment Variables

### Required

```bash
TELEGRAM_BOT_TOKEN="your-telegram-bot-token"

COSMOS_ENDPOINT="https://your-cosmos-account.documents.azure.com:443/"
COSMOS_KEY="your-cosmos-key"
COSMOS_DB="rawatai-db"

AZURE_OPENAI_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>/openai/v1/"
AZURE_OPENAI_KEY="your-foundry-or-azure-openai-key"
AZURE_OPENAI_DEPLOYMENT="gpt-4.1-mini"
```

### Optional

```bash
AZURE_OPENAI_API_KEY="alternative key variable name"
AZURE_OPENAI_API_VERSION="2024-12-01-preview"
ENABLE_SCHEDULED_REMINDERS="true"
SCHEDULER_BATCH_SIZE="200"
```

---

## Local Development

### 1. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Create `local.settings.json`

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "TELEGRAM_BOT_TOKEN": "your-token",
    "COSMOS_ENDPOINT": "your-cosmos-endpoint",
    "COSMOS_KEY": "your-cosmos-key",
    "COSMOS_DB": "rawatai-db",
    "AZURE_OPENAI_ENDPOINT": "https://<resource>.services.ai.azure.com/api/projects/<project>/openai/v1/",
    "AZURE_OPENAI_KEY": "your-key",
    "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini",
    "ENABLE_SCHEDULED_REMINDERS": "true"
  }
}
```

### 3. Compile check

```bash
python3 -m py_compile function_app.py agents.py cosmos_client.py phase_detector.py scheduler_service.py
```

### 4. Run locally

```bash
func start
```

Expected:

```text
health: [GET] http://localhost:7071/api/health
telegram_webhook: [POST] http://localhost:7071/api/telegram_webhook
reminder_scheduler: timerTrigger
```

---

## Testing

### Test Foundry / OpenAI config

```bash
python3 - <<'PY'
from agents import debug_openai_config, reflection_agent

print(debug_openai_config())

print(reflection_agent(
    "I feel overwhelmed because my son has chemo soon and I could not sleep.",
    phase="before_treatment",
    fallback="FALLBACK_USED"
))
PY
```

Expected:

```text
mode: foundry_responses
```

And the response should not be `FALLBACK_USED`.

### Test health endpoint

```bash
curl -i http://localhost:7071/api/health
```

### Test Telegram webhook locally

```bash
curl -i -X POST "http://localhost:7071/api/telegram_webhook" \
  -H "Content-Type: application/json" \
  -d '{"message":{"chat":{"id":123456},"from":{"first_name":"Test"},"text":"/start"}}'
```

### Demo test flow

```text
/start
/setup
My son has chemo every 21 days starting May 3 at 9 AM
/checkin
/journal
/today
/weekly
/mindful
/breathe
/testreminders
```

---

## Deployment

```bash
func azure functionapp publish rawatai-bot-7842 --python --build remote --verbose
```

Restart:

```bash
az functionapp restart \
  --name rawatai-bot-7842 \
  --resource-group rawatai-rg
```

Confirm settings:

```bash
az functionapp config appsettings list \
  --name rawatai-bot-7842 \
  --resource-group rawatai-rg \
  --query "[?name=='TELEGRAM_BOT_TOKEN' || name=='AZURE_OPENAI_ENDPOINT' || name=='AZURE_OPENAI_DEPLOYMENT' || name=='COSMOS_ENDPOINT' || name=='ENABLE_SCHEDULED_REMINDERS'].name" \
  -o table
```

Set Telegram webhook:

```bash
HOST="rawatai-bot-7842-gwb5aqfaatf8cxhf.australiaeast-01.azurewebsites.net"
WEBHOOK_URL="https://${HOST}/api/telegram_webhook"

curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  --data-urlencode "url=${WEBHOOK_URL}"
```

---

## Safety and Guardrails

RawatAI is caregiver support, not clinical decision support.

Current guardrail behavior:

- avoids diagnosis,
- avoids treatment-change advice,
- redirects symptom/treatment questions to the care team,
- avoids toxic positivity,
- detects crisis/self-harm language in the Reflection Agent prompt,
- uses lightweight patient aliases like “my son” instead of requiring full identity,
- keeps next steps small and practical.

---

## Known Limitations

- In-memory `user_state`, `checkin_buffer`, and `setup_buffer` are not durable across cold starts or scale-out.
- Telegram callback state may reset if the Azure Function instance restarts.
- Scheduler delivery depends on correct Cosmos caregiver records and Azure app settings.
- Current scheduler uses fixed WIB handling.
- Some direct Cosmos queries still live in `function_app.py`; future cleanup can move them into `cosmos_client.py`.
- `scheduler_service.py` can generate/queue nudges, but the Telegram send path still lives primarily in `function_app.py`.

---

## Suggested Next Improvements

1. Move conversation state to Cosmos DB or Redis.
2. Move remaining direct Cosmos queries from `function_app.py` into `cosmos_client.py`.
3. Add `/settings` to view reminder times.
4. Add `/privacy` to explain what data is stored.
5. Add evaluation cases for:
   - medical advice refusal,
   - crisis response,
   - caregiver burden detection,
   - journaling specificity,
   - treatment schedule parsing.
6. Add timezone handling beyond fixed WIB.
7. Add richer weekly analysis across journal and check-in patterns.
