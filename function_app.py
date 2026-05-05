"""Azure Functions entrypoints for RawatAI."""

import json
import logging
import os

import azure.functions as func

from agents import habit_agent, reflection_agent
from cosmos_client import (
    get_journal_prompt,
    get_patient_context,
    get_today_checkin,
    list_caregivers,
    save_nudge_event,
)
from phase_detector import get_current_phase

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _build_aligned_response(payload: dict) -> tuple[int, dict | str]:
    """Shared tone engine for both /respond and scheduler nudges."""
    mode = str(payload.get("mode", "journal")).lower()
    phase = get_current_phase(payload.get("next_treatment_date"))

    if mode == "checkin":
        checkin = payload.get("checkin", {})
        streaks = payload.get("streaks", {})
        text = habit_agent(checkin=checkin, streaks=streaks, phase=phase)
    else:
        user_message = str(payload.get("message", "")).strip()
        if not user_message:
            return 400, "Missing 'message'"
        text = reflection_agent(user_message=user_message, phase=phase)

    return 200, {"phase": phase, "response": text}


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("ok", status_code=200)


@app.route(route="respond", methods=["POST"])
def respond(req: func.HttpRequest) -> func.HttpResponse:
    """Generate phase-aware responses using the alignment logic in agents.py."""
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    status, result = _build_aligned_response(payload)
    if status != 200:
        return func.HttpResponse(str(result), status_code=status)

    return func.HttpResponse(json.dumps(result), status_code=200, mimetype="application/json")


@app.timer_trigger(
    schedule="0 */5 * * * *",
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
def scheduler(mytimer: func.TimerRequest) -> None:
    """Scheduler heartbeat and proactive nudge generator (every 5 minutes)."""
    if mytimer.past_due:
        logging.warning("scheduler trigger is running late")

    logging.info("scheduler trigger fired")

    caregivers = list_caregivers(limit=int(os.environ.get("SCHEDULER_BATCH_SIZE", "200")))
    if not caregivers:
        logging.info("scheduler nudge skipped: no caregivers found")
        return

    sent = 0
    for caregiver in caregivers:
        chat_id = str(caregiver.get("chat_id") or caregiver.get("id") or "").strip()
        if not chat_id:
            continue

        context = get_patient_context(chat_id) or {}
        phase = get_current_phase(context.get("next_treatment_date"))
        if phase == "normal":
            continue

        if get_today_checkin(chat_id):
            continue

        lang = caregiver.get("lang", "en")
        prompt = get_journal_prompt(phase=phase, lang=lang)
        payload = {
            "mode": "journal",
            "message": prompt,
            "next_treatment_date": context.get("next_treatment_date"),
        }
        status, result = _build_aligned_response(payload)
        if status != 200:
            logging.warning("scheduler nudge generation failed for %s: %s", chat_id, result)
            continue

        save_nudge_event(
            chat_id,
            {
                "type": "proactive_nudge",
                "phase": result["phase"],
                "message": result["response"],
                "source_prompt": prompt,
            },
        )
        sent += 1

    logging.info("scheduler proactive nudges queued: %s", sent)
