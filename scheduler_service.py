"""Scheduler and response orchestration helpers for RawatAI."""

import logging
import os

from agents import habit_agent, reflection_agent
from cosmos_client import (
    get_journal_prompt,
    get_patient_context,
    get_today_checkin,
    list_caregivers,
    save_nudge_event,
)
from phase_detector import get_current_phase


def build_aligned_response(payload: dict) -> tuple[int, dict | str]:
    """Shared tone engine used by both HTTP and scheduled flows."""
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


def run_scheduler_batch() -> int:
    """Generate and queue proactive nudges for eligible caregivers."""
    caregivers = list_caregivers(limit=int(os.environ.get("SCHEDULER_BATCH_SIZE", "200")))
    if not caregivers:
        logging.info("scheduler nudge skipped: no caregivers found")
        return 0

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
        status, result = build_aligned_response(payload)
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

    return sent
