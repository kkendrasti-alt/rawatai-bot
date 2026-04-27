import azure.functions as func
import logging
import os
import json
import requests

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

def send_message(chat_id: int, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)

@app.route(route="telegram_webhook", methods=["POST"])
def telegram_webhook(req: func.HttpRequest) -> func.HttpResponse:
    try:
        update = req.get_json()
        logging.info(f"Update received: {json.dumps(update)}")

        if "message" not in update:
            return func.HttpResponse("OK", status_code=200)

        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        if text == "/start":
            reply = (
                "Hi. I'm RawatAI.\n\n"
                "I'm a companion for cancer caregivers — built to help you "
                "reflect, track your own wellbeing, and feel less alone.\n\n"
                "Karena yang merawat juga perlu dirawat.\n\n"
                "Try: /help"
            )
            send_message(chat_id, reply)

        elif text == "/help":
            reply = (
                "Commands available now:\n"
                "/start - welcome\n"
                "/help - this list\n\n"
                "Coming soon: /journal, /checkin, /setup, /breathe"
            )
            send_message(chat_id, reply)

        else:
            send_message(chat_id, "I'm still learning. Try /start or /help.")

        return func.HttpResponse("OK", status_code=200)

    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return func.HttpResponse("OK", status_code=200)