"""
shared/agents.py
All three RawatAI agents: Reflection, Habit, Context.
Each agent is a function that takes input and returns a string.
"""

import os
import json
import logging
from openai import AzureOpenAI

# ── Shared client ─────────────────────────────────────────────────
_openai_client = None


def _get_client() -> AzureOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_KEY"],
            api_version="2024-02-01",
        )
    return _openai_client


DEPLOYMENT = None


def _get_deployment() -> str:
    global DEPLOYMENT
    if DEPLOYMENT is None:
        DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT"]
    return DEPLOYMENT


def _call(system: str, user: str, max_tokens: int = 200,
          temperature: float = 0.7) -> str:
    try:
        response = _get_client().chat.completions.create(
            model=_get_deployment(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"OpenAI call error: {e}")
        raise


# ═══════════════════════════════════════════════════════════════════
# REFLECTION AGENT
# ═══════════════════════════════════════════════════════════════════

def reflection_agent(
    user_message: str,
    phase: str = "normal",
    fallback: str = "I'm here. Take your time.",
) -> str:
    """
    Responds to a caregiver's journal entry with empathy.
    Phase adjusts tone and follow-up behavior.
    """
    phase_instructions = {
        "normal": "You may include one gentle follow-up question if it feels natural.",
        "before_treatment": (
            "Treatment day is 1-3 days away. The caregiver may be anxious. "
            "Be gentle. No follow-up questions."
        ),
        "treatment_day": (
            "Today is treatment day. The caregiver is under maximum load. "
            "Keep response to 1-2 sentences. No questions. Just presence."
        ),
        "recovery_window": (
            "Treatment was 1-5 days ago. Recovery is physically and emotionally hard. "
            "Acknowledge the weight. No questions. No habits mentioned."
        ),
    }

    system = f"""You are RawatAI's Reflection Agent.
You reply to cancer caregivers who have just journaled about their day.

Current treatment phase: {phase}
Phase context: {phase_instructions.get(phase, phase_instructions['normal'])}

Rules:
- Keep replies under 3 sentences.
- Acknowledge what they shared. Do not minimize it.
- NEVER use: "stay strong", "be positive", "everything happens for a reason",
  "God has a plan", "be grateful", "look on the bright side", "at least",
  "silver lining", "it could be worse".
- Never give medical advice. If they ask, defer to their care team.
- If they express crisis (suicide, self-harm, "I can't go on", "I want to die"),
  respond with care and include exactly:
  "If you're in crisis in Indonesia, you can call 119 ext 8."
- Match the language they used. Indonesian input gets Indonesian reply.
  Mixed input: default to the language used most.

Your purpose is to make them feel heard, not fixed."""

    try:
        return _call(system, user_message, max_tokens=200, temperature=0.7)
    except Exception:
        return fallback


# ═══════════════════════════════════════════════════════════════════
# HABIT AGENT
# ═══════════════════════════════════════════════════════════════════

def habit_agent(
    checkin: dict,
    streaks: dict,
    phase: str = "normal",
    fallback: str = "Noted. You showed up today.",
) -> str:
    """
    Responds to a completed check-in with acknowledgment.
    Incorporates streaks and phase-aware softening.
    """
    phase_note = ""
    if phase in ("treatment_day", "recovery_window"):
        phase_note = (
            "This is a hard week. Mention that habits are optional today — "
            "explicitly say something like 'Skip today if you need to. "
            "That is a choice, not a miss.'"
        )

    streak_summary = ", ".join(
        f"{k}: {v} day streak" for k, v in streaks.items() if v > 0
    ) or "no active streaks yet"

    checkin_summary = (
        f"Energy: {checkin.get('energy', 'unknown')}, "
        f"Stress: {checkin.get('stress', 'unknown')}/5, "
        f"Sleep: {'yes' if checkin.get('sleep_ok') else 'no'}, "
        f"Mood: {checkin.get('mood', 'unknown')}"
    )

    system = f"""You are RawatAI's Habit Agent.
You respond to cancer caregivers after they complete a daily check-in.

Current check-in: {checkin_summary}
Habit streaks: {streak_summary}
Treatment phase: {phase}
{phase_note}

Rules:
- Maximum 2 sentences.
- Celebrate streaks OBSERVATIONALLY, never congratulatory.
  Say "3 days of water — noticed." not "Amazing! Great job!"
- NEVER use: "should", "must", "need to", "failed", "only", "but you",
  "you missed", "you forgot".
- Missed habits get neutral response ("tomorrow is a new page"), never corrective.
- Match the language used in their check-in responses if available.
  Otherwise default to English.

Your purpose is to make self-care feel possible, not like another duty."""

    user = f"Caregiver just completed check-in: {checkin_summary}. Streaks: {streak_summary}."

    try:
        return _call(system, user, max_tokens=120, temperature=0.6)
    except Exception:
        return fallback


# ═══════════════════════════════════════════════════════════════════
# CONTEXT AGENT — Job 1: Parse schedule
# ═══════════════════════════════════════════════════════════════════

def context_agent_parse(user_message: str) -> dict:
    """
    Parses a natural-language treatment schedule into structured JSON.

    Returns dict with keys:
      patient_alias, treatment_type, next_treatment_date,
      cycle_days, confidence, clarifying_question
    """
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    system = f"""You are RawatAI's Context Agent. You never reply to the caregiver directly.
You only return structured JSON.

Today's date: {today}

Job: Parse a natural-language treatment schedule.

Return ONLY valid JSON with these exact keys:
{{
  "patient_alias": "how caregiver refers to patient (e.g. my son, mother, husband)",
  "treatment_type": "chemotherapy | radiation | surgery | immunotherapy | other",
  "next_treatment_date": "ISO 8601 date string YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS",
  "cycle_days": integer or null if one-time,
  "confidence": "high | medium | low",
  "clarifying_question": null or "ONE question string if confidence is low"
}}

Rules:
- If the start date is in the past and cycle_days is known, compute the next upcoming date.
- If no time given, use T09:00:00.
- If confidence is low, set clarifying_question to exactly ONE question. Never a list.
- Never include prose, preamble, or explanation. JSON only.
- If you cannot parse anything meaningful, return confidence: "low" with a clarifying question."""

    try:
        raw = _call(system, user_message, max_tokens=300, temperature=0.1)

        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)

        # Validate required keys
        required = ["patient_alias", "treatment_type", "next_treatment_date",
                    "cycle_days", "confidence", "clarifying_question"]
        for key in required:
            result.setdefault(key, None)

        return result

    except json.JSONDecodeError as e:
        logging.error(f"context_agent_parse JSON error: {e} | raw: {raw}")
        return {
            "patient_alias": None,
            "treatment_type": None,
            "next_treatment_date": None,
            "cycle_days": None,
            "confidence": "low",
            "clarifying_question": "Could you tell me when the next treatment is scheduled?",
        }
    except Exception as e:
        logging.error(f"context_agent_parse error: {e}")
        return {
            "patient_alias": None,
            "treatment_type": None,
            "next_treatment_date": None,
            "cycle_days": None,
            "confidence": "low",
            "clarifying_question": "Could you tell me more about the treatment schedule?",
        }
