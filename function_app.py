import azure.functions as func
import logging
import os
import json
import requests
from openai import AzureOpenAI

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ── Azure OpenAI client ──────────────────────────────────────────
client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_KEY"],
    api_version="2024-02-01"
)
DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT"]

# ── Telegram helper ──────────────────────────────────────────────
def send_message(chat_id: int, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)

# ── Reflection Agent ─────────────────────────────────────────────
REFLECTION_SYSTEM_PROMPT = """You are RawatAI's Reflection Agent.
You reply to cancer caregivers who have just journaled about their day.

Rules:
- Keep replies under 3 sentences.
- Acknowledge what they shared. Do not minimize it.
- NEVER use: "stay strong", "be positive", "everything happens for a reason",
  "God has a plan", "be grateful", "look on the bright side", "at least".
- Never give medical advice.
- If they express crisis (suicide, self-harm, "I can't go on"), respond with
  care and include: "If you're in crisis in Indonesia, you can call 119 ext 8."
- Match the language they used. Indonesian input gets Indonesian reply.
- Do not ask follow-up questions.

Your purpose is to make them feel heard, not fixed."""

JOURNAL_PROMPTS = [
    "What felt hardest today?",
    "How are you showing up for yourself today?",
    "What's one thing you wish someone understood about your day?",
    "What are you carrying right now that feels too heavy to say out loud?",
    "What does your body need that it hasn't gotten today?",
]

def reflection_agent(user_message: str) -> str:
    try:
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            max_tokens=200,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Reflection Agent error: {e}")
        return "I'm here. Take your time."

# ── Conversation state (simple in-memory for now) ────────────────
user_state = {}

# ── Webhook ──────────────────────────────────────────────────────
@app.route(route="telegram_webhook", methods=["POST"])
def telegram_webhook(req: func.HttpRequest) -> func.HttpResponse:
    try:
        update = req.get_json()
        logging.info(f"Update: {json.dumps(update)}")

        if "message" not in update:
            return func.HttpResponse("OK", status_code=200)

        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        # ── Commands ──
        if text == "/start":
            reply = (
                "Hi. I'm RawatAI.\n\n"
                "I'm a companion for cancer caregivers — built to help you "
                "reflect, track your own wellbeing, and feel less alone.\n\n"
                "Karena yang merawat juga perlu dirawat.\n\n"
                "Try: /journal  /help"
            )
            send_message(chat_id, reply)

        elif text == "/help":
            reply = (
                "Commands:\n"
                "/start - welcome\n"
                "/journal - daily reflection\n"
                "/help - this list\n\n"
                "Coming soon: /checkin, /setup, /breathe"
            )
            send_message(chat_id, reply)

        elif text == "/journal":
            import random
            prompt = random.choice(JOURNAL_PROMPTS)
            user_state[chat_id] = "awaiting_journal"
            send_message(chat_id, prompt)

        # ── Journal reply ──
        elif user_state.get(chat_id) == "awaiting_journal":
            user_state[chat_id] = None
            send_message(chat_id, "...")
            reflection = reflection_agent(text)
            send_message(chat_id, reflection)

        else:
            send_message(chat_id, "Try /journal to reflect, or /help for commands.")

        return func.HttpResponse("OK", status_code=200)

    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return func.HttpResponse("OK", status_code=200)