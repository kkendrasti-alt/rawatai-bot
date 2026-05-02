
"""
function_app.py — RawatAI Day 3-4+ Hackathon Coverage Upgrade

Adds coverage for the CWB core capabilities:
  1) Adapt tone based on mood / stress
  2) Encourage mindful moments throughout the day
  3) Reflect on patterns over time
  4) Summarize insights from past entries
  5) Track caregiver habits: mindful pause and sleep
  6) Proactive hydration reminders every 90 minutes during daytime

Existing capabilities preserved:
  - Cosmos DB persistence via cosmos_client.py
  - Database-driven messages via content_library container
  - /journal with phase-aware prompts + AI reflection
  - /checkin with inline keyboard + Habit Agent
  - /setup with Context Agent schedule parsing
  - /today daily summary
  - /breathe short reset
  - Treatment phase detection
"""

import azure.functions as func
import logging
import os
import json
import time
from datetime import datetime, timezone, timedelta
from collections import Counter
from typing import Any, Optional

import requests

# ── Shared modules (in same folder for now) ───────────────────────
# In production these live in a shared/ subfolder.
from cosmos_client import (
    get_caregiver, upsert_caregiver, update_caregiver,
    get_patient_context, upsert_patient_context,
    save_checkin, get_today_checkin, get_streak,
    save_journal_entry,
    get_message, get_journal_prompt,
)
from agents import reflection_agent, habit_agent, context_agent_parse
from phase_detector import get_current_phase, get_next_treatment_date, days_until_treatment

# ── App ───────────────────────────────────────────────────────────
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Disable scheduled reminders by default for local demo.
# Timer triggers require AzureWebJobsStorage / Azurite. Keep /mindful available manually.
ENABLE_SCHEDULED_REMINDERS = os.environ.get("ENABLE_SCHEDULED_REMINDERS", "false").lower() == "true"


# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

CHECKIN_STATES = {
    "checkin_energy", "checkin_stress",
    "checkin_sleep", "checkin_mood", "checkin_patient",
    "checkin_pause",
}

SETUP_STATES = {
    "awaiting_setup",
    "awaiting_daily_checkin_time",
    "awaiting_hydration_pref",
    "awaiting_mindful_time",
    "awaiting_night_reflection_time",
}

# Keep phase names consistent across phase_detector.py, content_library,
# agents.py, README, and demo script.
PHASE_LABELS = {
    "normal": "Normal day",
    "before_treatment": "Before treatment",
    "treatment_day": "Treatment day",
    "recovery_window": "Recovery window",
}

HIGH_STRESS_THRESHOLD = 4
LOW_STRESS_THRESHOLD = 2
LOW_ENERGY_VALUES = {"low", "very low"}
ANXIOUS_MOODS = {"anxious", "worried", "panic", "panicked", "overwhelmed", "takut", "cemas"}
TIRED_MOODS = {"tired", "exhausted", "drained", "capek", "lelah"}
HOPEFUL_MOODS = {"hopeful", "calm", "okay", "grateful", "tenang", "baik"}


# ═══════════════════════════════════════════════════════════════════
# TELEGRAM HELPERS
# ═══════════════════════════════════════════════════════════════════

def _token() -> str:
    return os.environ["TELEGRAM_BOT_TOKEN"]


def send_message(chat_id: int, text: str) -> None:
    """Send plain text message to Telegram."""
    url = f"https://api.telegram.org/bot{_token()}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as e:
        logging.error(f"send_message error: {e}")


def send_with_keyboard(chat_id: int, text: str, buttons: list[list[str]]) -> None:
    """Send a message with inline keyboard buttons."""
    keyboard = {
        "inline_keyboard": [
            [{"text": b, "callback_data": b} for b in row]
            for row in buttons
        ]
    }
    url = f"https://api.telegram.org/bot{_token()}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "reply_markup": keyboard,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as e:
        logging.error(f"send_with_keyboard error: {e}")


def answer_callback(callback_query_id: str) -> None:
    """Acknowledge a button press to remove Telegram's loading indicator."""
    url = f"https://api.telegram.org/bot{_token()}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_query_id}, timeout=5)
    except Exception as e:
        logging.error(f"answer_callback error: {e}")


# ═══════════════════════════════════════════════════════════════════
# SAFE CONTENT LIBRARY ACCESS
# ═══════════════════════════════════════════════════════════════════

def msg(key: str, fallback: str, lang: str = "en") -> str:
    """
    Safe wrapper around get_message().
    Some earlier versions of cosmos_client.get_message accepted only key;
    newer versions may accept key + language.
    """
    try:
        return get_message(key, lang) or fallback
    except TypeError:
        try:
            return get_message(key) or fallback
        except Exception:
            return fallback
    except Exception as e:
        logging.warning(f"get_message failed for key={key}: {e}")
        return fallback


def journal_prompt(phase: str, lang: str = "en") -> str:
    """Safe wrapper around get_journal_prompt()."""
    fallback_prompts = {
        "before_treatment": "Treatment is coming soon. What feels heaviest today?",
        "treatment_day": "Today may ask a lot of you. What do you need to say out loud?",
        "recovery_window": "The days after treatment can feel uncertain. What are you carrying today?",
        "normal": "How are you showing up for yourself today?",
    }
    try:
        return get_journal_prompt(phase, lang) or fallback_prompts.get(phase, fallback_prompts["normal"])
    except Exception as e:
        logging.warning(f"get_journal_prompt failed for phase={phase}: {e}")
        return fallback_prompts.get(phase, fallback_prompts["normal"])


# ═══════════════════════════════════════════════════════════════════
# CONVERSATION STATE (in-memory for hackathon demo)
# ═══════════════════════════════════════════════════════════════════
# NOTE:
# This is acceptable for a short demo, but for production move this into Cosmos DB.
# Azure Functions can restart and lose memory.

user_state: dict[str, Optional[str]] = {}
checkin_buffer: dict[str, dict[str, Any]] = {}

# Accumulates setup answers before final reminder preferences are complete
setup_buffer: dict[str, dict[str, Any]] = {}


def handle_test_reminders(chat_id: str, phase: str) -> None:
    """
    Manual demo command to prove proactive reminder content without enabling TimerTrigger.
    """
    send_message(int(chat_id), "Previewing your proactive reminders:")
    send_message(int(chat_id), build_daily_checkin_push_message(chat_id, phase))
    send_message(int(chat_id), build_hydration_push_message(chat_id, phase))
    send_message(int(chat_id), build_mindful_push_message(chat_id, phase))
    send_message(int(chat_id), build_night_reflection_push_message(chat_id, phase))



# ═══════════════════════════════════════════════════════════════════
# OPTIONAL COSMOS READERS FOR INSIGHTS
# ═══════════════════════════════════════════════════════════════════

def _optional_cosmos_function(function_name: str):
    """
    Allows this function_app.py to work even if cosmos_client.py has not yet
    implemented get_recent_journal_entries/get_recent_checkins/get_active_caregivers.
    """
    try:
        import cosmos_client as cc
        fn = getattr(cc, function_name, None)
        return fn if callable(fn) else None
    except Exception as e:
        logging.warning(f"Could not inspect cosmos_client.{function_name}: {e}")
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Cosmos _ts is epoch seconds.
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


def _item_dt(item: dict[str, Any]) -> datetime:
    return (
        _parse_dt(item.get("created_at"))
        or _parse_dt(item.get("timestamp"))
        or _parse_dt(item.get("updated_at"))
        or _parse_dt(item.get("_ts"))
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def _filter_recent(items: list[dict[str, Any]], days: int, limit: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    filtered = []
    for item in items or []:
        dt = _item_dt(item)
        if dt == datetime.min.replace(tzinfo=timezone.utc) or dt >= cutoff:
            filtered.append(item)
    filtered.sort(key=_item_dt, reverse=True)
    return filtered[:limit]


def _direct_cosmos_query(container_env: str, default_container: str, chat_id: str, limit: int = 30) -> list[dict[str, Any]]:
    """
    Optional fallback reader using azure-cosmos directly.

    Expected env vars, supporting common aliases:
      COSMOS_ENDPOINT or COSMOS_DB_ENDPOINT
      COSMOS_KEY or COSMOS_DB_KEY
      COSMOS_DATABASE_NAME or COSMOS_DB_DATABASE or COSMOS_DATABASE
      JOURNAL_CONTAINER_NAME / CHECKIN_CONTAINER_NAME if you use custom names
    """
    try:
        from azure.cosmos import CosmosClient
    except Exception:
        return []

    endpoint = os.environ.get("COSMOS_ENDPOINT") or os.environ.get("COSMOS_DB_ENDPOINT")
    key = os.environ.get("COSMOS_KEY") or os.environ.get("COSMOS_DB_KEY")
    database_name = (
        os.environ.get("COSMOS_DATABASE_NAME")
        or os.environ.get("COSMOS_DB_DATABASE")
        or os.environ.get("COSMOS_DATABASE")
        or "rawatai"
    )
    container_name = os.environ.get(container_env) or default_container

    if not endpoint or not key:
        return []

    try:
        client = CosmosClient(endpoint, credential=key)
        db = client.get_database_client(database_name)
        container = db.get_container_client(container_name)

        query = """
        SELECT TOP @limit * FROM c
        WHERE c.chat_id = @chat_id
           OR c.user_id = @chat_id
           OR c.caregiver_id = @chat_id
           OR c.id = @chat_id
        """
        params = [
            {"name": "@chat_id", "value": str(chat_id)},
            {"name": "@limit", "value": int(limit)},
        ]
        return list(
            container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
    except Exception as e:
        logging.warning(f"Direct Cosmos query failed for {default_container}: {e}")
        return []


def get_recent_journals(chat_id: str, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
    """
    Recent journal entries for pattern reflection.
    Prefer cosmos_client.get_recent_journal_entries if implemented.
    Fallback to direct Cosmos query if env vars are available.
    """
    fn = _optional_cosmos_function("get_recent_journal_entries")
    if fn:
        try:
            return _filter_recent(fn(chat_id, days=days, limit=limit), days, limit)
        except TypeError:
            return _filter_recent(fn(chat_id, limit), days, limit)
        except Exception as e:
            logging.warning(f"get_recent_journal_entries failed: {e}")

    items = _direct_cosmos_query("JOURNAL_CONTAINER_NAME", "journal_entries", chat_id, limit=limit)
    return _filter_recent(items, days, limit)


def get_recent_checkins(chat_id: str, days: int = 7, limit: int = 20) -> list[dict[str, Any]]:
    """
    Recent check-ins for pattern reflection.
    Prefer cosmos_client.get_recent_checkins if implemented.
    Fallback to direct Cosmos query if env vars are available.
    """
    fn = _optional_cosmos_function("get_recent_checkins")
    if fn:
        try:
            return _filter_recent(fn(chat_id, days=days, limit=limit), days, limit)
        except TypeError:
            return _filter_recent(fn(chat_id, limit), days, limit)
        except Exception as e:
            logging.warning(f"get_recent_checkins failed: {e}")

    items = _direct_cosmos_query("CHECKIN_CONTAINER_NAME", "checkins", chat_id, limit=limit)
    return _filter_recent(items, days, limit)


# ═══════════════════════════════════════════════════════════════════
# TONE + MINDFULNESS ENGINE
# ═══════════════════════════════════════════════════════════════════

def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def build_support_profile(checkin: Optional[dict[str, Any]], phase: str) -> dict[str, Any]:
    """
    Converts mood/stress/energy/sleep/phase into a support strategy.
    This is the lightweight "Adapt tone based on mood/stress" layer.
    """
    checkin = checkin or {}

    stress = checkin.get("stress")
    try:
        stress_int = int(stress) if stress is not None else None
    except Exception:
        stress_int = None

    energy = _normalize(checkin.get("energy"))
    mood = _normalize(checkin.get("mood"))
    sleep_ok = checkin.get("sleep_ok")

    high_stress = stress_int is not None and stress_int >= HIGH_STRESS_THRESHOLD
    low_energy = energy in LOW_ENERGY_VALUES
    anxious = mood in ANXIOUS_MOODS or any(token in mood for token in ANXIOUS_MOODS)
    tired = mood in TIRED_MOODS or any(token in mood for token in TIRED_MOODS)
    hopeful = mood in HOPEFUL_MOODS or any(token in mood for token in HOPEFUL_MOODS)
    poor_sleep = sleep_ok is False

    if high_stress or anxious:
        tone = "grounding"
        intensity = "high_support"
        instruction = (
            "Use a very gentle, grounding tone. Validate first. Keep suggestions tiny. "
            "Do not over-cheerlead or ask the caregiver to do too much."
        )
    elif low_energy or tired or poor_sleep:
        tone = "soft_low_energy"
        intensity = "medium_support"
        instruction = (
            "Use a soft, low-pressure tone. Normalize tiredness. Suggest rest-sized actions only."
        )
    elif hopeful or (stress_int is not None and stress_int <= LOW_STRESS_THRESHOLD):
        tone = "encouraging"
        intensity = "light_support"
        instruction = (
            "Use a warm, encouraging tone. Reinforce what is working without sounding generic."
        )
    else:
        tone = "steady"
        intensity = "standard_support"
        instruction = (
            "Use a calm, steady tone. Reflect what the caregiver said and offer one small next step."
        )

    if phase == "before_treatment":
        instruction += " Acknowledge that treatment is approaching."
    elif phase == "treatment_day":
        instruction += " Acknowledge that today may be emotionally demanding."
    elif phase == "recovery_window":
        instruction += " Acknowledge that recovery days can be uncertain."

    return {
        "stress": stress_int,
        "energy": energy,
        "mood": mood,
        "sleep_ok": sleep_ok,
        "phase": phase,
        "tone": tone,
        "intensity": intensity,
        "agent_instruction": instruction,
    }


def tone_context_for_agent(profile: dict[str, Any]) -> str:
    """Small context block injected into agent input without changing agents.py."""
    return (
        "[Caregiver support context]\n"
        f"- stress: {profile.get('stress')}\n"
        f"- energy: {profile.get('energy')}\n"
        f"- mood: {profile.get('mood')}\n"
        f"- sleep_ok: {profile.get('sleep_ok')}\n"
        f"- treatment_phase: {profile.get('phase')}\n"
        f"- tone_instruction: {profile.get('agent_instruction')}\n"
        "[End context]\n\n"
    )


def mindful_moment(profile: dict[str, Any]) -> str:
    """
    Returns a short, context-aware mindful moment.
    This supports 'encourage mindful moments throughout the day'
    even without a scheduler.
    """
    tone = profile.get("tone")
    phase = profile.get("phase")

    if tone == "grounding":
        base = (
            "🌿 Tiny mindful moment:\n"
            "Put both feet on the floor. Name 3 things you can see. "
            "Then take one slower breath than the last."
        )
    elif tone == "soft_low_energy":
        base = (
            "🌿 Tiny mindful moment:\n"
            "Unclench your jaw. Drop your shoulders. "
            "For the next breath, you do not need to fix anything."
        )
    elif tone == "encouraging":
        base = (
            "🌿 Tiny mindful moment:\n"
            "Notice one small thing that supported you today, even if it was imperfect."
        )
    else:
        base = (
            "🌿 Tiny mindful moment:\n"
            "Pause for one breath. Ask yourself: what do I need in the next 10 minutes?"
        )

    if phase == "treatment_day":
        return base + "\n\nToday can be heavy. Keep the next step very small."
    if phase == "before_treatment":
        return base + "\n\nSince treatment is approaching, choose less pressure today."
    if phase == "recovery_window":
        return base + "\n\nRecovery windows can be unpredictable. Gentleness counts."
    return base


def should_offer_mindful_moment(profile: dict[str, Any]) -> bool:
    return (
        profile.get("intensity") in {"high_support", "medium_support"}
        or profile.get("phase") in {"before_treatment", "treatment_day", "recovery_window"}
    )


def time_of_day_mindful_prompt(profile: dict[str, Any]) -> str:
    """
    A lightweight day-part nudge for /today and /mindful.
    This gives 'throughout the day' value without needing background jobs.
    """
    hour = datetime.now().hour

    if 5 <= hour < 11:
        opener = "Morning check-in"
        question = "What is one thing you can make lighter this morning?"
    elif 11 <= hour < 16:
        opener = "Midday pause"
        question = "What has your body been asking for today?"
    elif 16 <= hour < 21:
        opener = "Evening reset"
        question = "What can you release before the day ends?"
    else:
        opener = "Late-night grounding"
        question = "What can wait until tomorrow?"

    return f"🌿 {opener}:\n{question}\n\n{mindful_moment(profile)}"


# ═══════════════════════════════════════════════════════════════════
# PATTERN / INSIGHT ENGINE
# ═══════════════════════════════════════════════════════════════════

THEME_KEYWORDS = {
    "tiredness": ["tired", "exhausted", "drained", "capek", "lelah", "sleep", "tidur"],
    "worry": ["worry", "worried", "anxious", "cemas", "takut", "fear", "afraid"],
    "helplessness": ["helpless", "nothing", "can't do", "tidak bisa", "gak bisa", "apa-apa"],
    "pain": ["pain", "sakit", "kesakitan", "hurt"],
    "treatment": ["chemo", "kemoterapi", "treatment", "hospital", "rumah sakit", "doctor", "dokter"],
    "guilt": ["guilt", "guilty", "bersalah", "kurang", "not enough"],
    "hope": ["hope", "hopeful", "grateful", "syukur", "bersyukur", "tenang"],
}


def _entry_text(item: dict[str, Any]) -> str:
    return str(
        item.get("entry")
        or item.get("journal")
        or item.get("text")
        or item.get("user_text")
        or item.get("message")
        or ""
    )


def detect_themes(journal_entries: list[dict[str, Any]]) -> list[tuple[str, int]]:
    corpus = " ".join(_entry_text(entry).lower() for entry in journal_entries)
    counts = []
    for theme, keywords in THEME_KEYWORDS.items():
        score = sum(corpus.count(keyword.lower()) for keyword in keywords)
        if score > 0:
            counts.append((theme, score))
    counts.sort(key=lambda x: x[1], reverse=True)
    return counts[:3]


def summarize_checkin_patterns(checkins: list[dict[str, Any]]) -> dict[str, Any]:
    if not checkins:
        return {
            "count": 0,
            "avg_stress": None,
            "low_energy_days": 0,
            "poor_sleep_days": 0,
            "common_mood": None,
        }

    stress_values = []
    moods = []
    low_energy_days = 0
    poor_sleep_days = 0

    for item in checkins:
        try:
            if item.get("stress") is not None:
                stress_values.append(int(item.get("stress")))
        except Exception:
            pass

        mood = _normalize(item.get("mood"))
        if mood:
            moods.append(mood)

        if _normalize(item.get("energy")) in LOW_ENERGY_VALUES:
            low_energy_days += 1

        if item.get("sleep_ok") is False:
            poor_sleep_days += 1

    common_mood = Counter(moods).most_common(1)[0][0] if moods else None

    return {
        "count": len(checkins),
        "avg_stress": round(sum(stress_values) / len(stress_values), 1) if stress_values else None,
        "low_energy_days": low_energy_days,
        "poor_sleep_days": poor_sleep_days,
        "common_mood": common_mood,
    }


def build_weekly_insight(chat_id: str, phase: str) -> str:
    """
    Rule-based supportive insight summary.
    Cheaper and safer than using LLM for hackathon demo.
    """
    journals = get_recent_journals(chat_id, days=7, limit=20)
    checkins = get_recent_checkins(chat_id, days=7, limit=20)
    themes = detect_themes(journals)
    checkin_summary = summarize_checkin_patterns(checkins)

    if not journals and not checkins:
        return (
            "I don't have enough entries yet to notice patterns.\n\n"
            "Try /journal or /checkin for a few days, then come back to /weekly.\n\n"
            "For today: one honest check-in is already a good start."
        )

    lines = ["🧭 Your 7-day reflection\n"]

    if journals:
        lines.append(f"I found {len(journals)} journal entr{'y' if len(journals) == 1 else 'ies'} from the last 7 days.")
    if checkins:
        lines.append(f"I also found {len(checkins)} check-in{'s' if len(checkins) != 1 else ''}.")

    if themes:
        theme_names = [theme.replace("_", " ") for theme, _ in themes]
        if len(theme_names) == 1:
            lines.append(f"\nOne theme that showed up: {theme_names[0]}.")
        else:
            lines.append(f"\nThemes that showed up: {', '.join(theme_names)}.")

    avg_stress = checkin_summary.get("avg_stress")
    if avg_stress is not None:
        if avg_stress >= 4:
            lines.append(f"\nYour average stress was {avg_stress}/5 — that is a high-load week.")
        elif avg_stress >= 3:
            lines.append(f"\nYour average stress was {avg_stress}/5 — not extreme, but still worth caring for.")
        else:
            lines.append(f"\nYour average stress was {avg_stress}/5 — there may have been some steadier moments too.")

    poor_sleep_days = checkin_summary.get("poor_sleep_days", 0)
    if poor_sleep_days:
        lines.append(f"You reported less than 5 hours of sleep on {poor_sleep_days} day{'s' if poor_sleep_days != 1 else ''}.")

    low_energy_days = checkin_summary.get("low_energy_days", 0)
    if low_energy_days:
        lines.append(f"Low energy appeared on {low_energy_days} day{'s' if low_energy_days != 1 else ''}.")

    # Treatment-phase insight
    if phase == "before_treatment":
        lines.append("\nPattern note: treatment is approaching, so it makes sense if your mind feels louder than usual.")
    elif phase == "treatment_day":
        lines.append("\nPattern note: today may not be the day for big goals. Presence is enough.")
    elif phase == "recovery_window":
        lines.append("\nPattern note: recovery windows can make emotions uneven. That does not mean you are failing.")

    # Supportive synthesis
    if themes and themes[0][0] in {"worry", "helplessness", "pain"}:
        lines.append(
            "\nSupportive insight:\n"
            "A lot of what you are carrying seems connected to wanting to protect someone you love. "
            "That care is real, but you do not have to carry it without pause."
        )
    elif avg_stress is not None and avg_stress >= 4:
        lines.append(
            "\nSupportive insight:\n"
            "This looks like a week for smaller habits, not stronger discipline. "
            "Water, one pause, and sleep when possible are enough."
        )
    else:
        lines.append(
            "\nSupportive insight:\n"
            "You kept showing up in small ways. RawatAI will keep helping you notice those patterns."
        )

    lines.append(
        "\nOne gentle next step:\n"
        "Choose only one caregiver habit for the next 24 hours: water, pause, or sleep."
    )

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# PHASE CONTEXT LOADER
# ═══════════════════════════════════════════════════════════════════

def load_phase(chat_id: str) -> str:
    """Load patient context and compute current treatment phase."""
    context = get_patient_context(chat_id)
    if not context:
        return "normal"

    next_date = get_next_treatment_date(context)
    phase = get_current_phase(next_date) or "normal"

    # Normalize older phase names if phase_detector.py uses earlier naming.
    aliases = {
        "normal_day": "normal",
        "pre_chemo": "before_treatment",
        "chemo_day": "treatment_day",
        "before_chemo": "before_treatment",
    }
    return aliases.get(phase, phase)



# ═══════════════════════════════════════════════════════════════════
# SETUP + REMINDER PREFERENCES
# ═══════════════════════════════════════════════════════════════════

def parse_hhmm_time(text: str) -> Optional[str]:
    """
    Parse user time input into HH:MM 24-hour format.
    Accepts: 8, 8.00, 8:00, 08:00, 9 PM, 9.00PM, 21:00.
    """
    raw = str(text or "").strip().lower()
    raw = raw.replace(".", ":").replace(" ", "")

    is_pm = raw.endswith("pm")
    is_am = raw.endswith("am")
    raw = raw.replace("am", "").replace("pm", "")

    if ":" in raw:
        parts = raw.split(":")
        if len(parts) != 2:
            return None
        hour_text, minute_text = parts
    else:
        hour_text, minute_text = raw, "00"

    if not hour_text.isdigit() or not minute_text.isdigit():
        return None

    hour = int(hour_text)
    minute = int(minute_text)

    if is_pm and hour < 12:
        hour += 12
    if is_am and hour == 12:
        hour = 0

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None

    return f"{hour:02d}:{minute:02d}"



def hhmm_to_minutes(hhmm: str) -> Optional[int]:
    try:
        hour_text, minute_text = str(hhmm).split(":")
        return int(hour_text) * 60 + int(minute_text)
    except Exception:
        return None


def minutes_to_hhmm(total_minutes: int) -> str:
    total_minutes = total_minutes % (24 * 60)
    hour = total_minutes // 60
    minute = total_minutes % 60
    return f"{hour:02d}:{minute:02d}"


def add_minutes_to_hhmm(hhmm: str, minutes: int) -> str:
    base = hhmm_to_minutes(hhmm)
    if base is None:
        return "09:30"
    return minutes_to_hhmm(base + minutes)


def yes_answer(text: str) -> bool:
    return str(text or "").strip().lower() in {"yes", "y", "ya", "iya", "boleh", "ok", "okay"}


def no_answer(text: str) -> bool:
    return str(text or "").strip().lower() in {"no", "n", "tidak", "nggak", "ga", "gak", "skip"}

def save_reminder_preferences(
    chat_id: str,
    daily_checkin_time: Optional[str] = None,
    mindful_time: Optional[str] = None,
    night_reflection_time: Optional[str] = None,
    hydration_reminders_enabled: Optional[bool] = None,
    hydration_interval_minutes: Optional[int] = None,
    hydration_start_time: Optional[str] = None,
    hydration_end_time: Optional[str] = None,
    timezone_name: str = "Asia/Jakarta",
) -> None:
    """
    Save reminder preferences into caregiver profile.
    Uses update_caregiver(), so cosmos_client.py only needs to support updating
    the caregivers container.
    """
    payload = {
        "timezone": timezone_name,
        "reminders_enabled": True,
        "reminder_preferences_updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if daily_checkin_time:
        payload["daily_checkin_time"] = daily_checkin_time
    if mindful_time:
        payload["mindful_time"] = mindful_time
    if night_reflection_time:
        payload["night_reflection_time"] = night_reflection_time
    if hydration_reminders_enabled is not None:
        payload["hydration_reminders_enabled"] = hydration_reminders_enabled
    if hydration_interval_minutes:
        payload["hydration_interval_minutes"] = hydration_interval_minutes
    if hydration_start_time:
        payload["hydration_start_time"] = hydration_start_time
    if hydration_end_time:
        payload["hydration_end_time"] = hydration_end_time

    try:
        update_caregiver(chat_id, payload)
    except Exception as e:
        logging.warning(f"save_reminder_preferences failed: {e}")


def has_reminder_been_sent_today(caregiver: dict[str, Any], reminder_type: str, local_date: str) -> bool:
    sent_log = caregiver.get("reminder_sent_log") or {}
    return sent_log.get(reminder_type) == local_date


def mark_reminder_sent(chat_id: str, reminder_type: str, local_date: str) -> None:
    """
    Mark a reminder as sent today to avoid duplicate pushes.
    """
    caregiver = get_caregiver(chat_id) or {}
    sent_log = caregiver.get("reminder_sent_log") or {}
    sent_log[reminder_type] = local_date

    try:
        update_caregiver(chat_id, {
            "reminder_sent_log": sent_log,
            "last_reminder_sent_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logging.warning(f"mark_reminder_sent failed: {e}")


def build_daily_checkin_push_message(chat_id: str, phase: str) -> str:
    base = msg(
        "push_daily_checkin",
        "Good morning. Before the day gets full, let’s check in gently.\n\nTry /checkin",
    )

    if phase == "before_treatment":
        return base + "\n\nTreatment is approaching, so we’ll keep today’s check-in extra gentle."
    if phase == "treatment_day":
        return base + "\n\nToday may ask a lot from you. Small answers are enough."
    if phase == "recovery_window":
        return base + "\n\nRecovery windows can be uneven. Let’s notice what you need today."
    return base


def build_mindful_push_message(chat_id: str, phase: str) -> str:
    checkin = get_today_checkin(chat_id)
    profile = build_support_profile(checkin, phase)
    base = msg("push_mindful", "A small pause for you.\n\n")
    return base + time_of_day_mindful_prompt(profile)


def build_night_reflection_push_message(chat_id: str, phase: str) -> str:
    base = msg(
        "push_night_reflection",
        "The day is closing. You don’t need to carry all of it into the night.\n\n"
        "Try /journal or /weekly when you’re ready.",
    )

    if phase == "treatment_day":
        return base + "\n\nFor today: presence was already a lot."
    if phase == "recovery_window":
        return base + "\n\nFor tonight: recovery does not need to be measured perfectly."
    if phase == "before_treatment":
        return base + "\n\nFor tonight: it makes sense if your thoughts feel louder."
    return base

def build_hydration_push_message(chat_id: str, phase: str) -> str:
    base = msg(
        "push_hydration",
        "Small hydration pause 💧\n\n"
        "Take a few sips if you can. No need to finish a bottle — just one small reset.",
    )

    if phase == "treatment_day":
        return base + "\n\nTreatment days can get busy. This is just a gentle body check."
    if phase == "before_treatment":
        return base + "\n\nSince treatment is approaching, small care still counts."
    if phase == "recovery_window":
        return base + "\n\nRecovery windows can be uneven. A few sips is enough."
    return base


def hydration_slot_for_now(caregiver: dict[str, Any], now_hhmm: str) -> Optional[str]:
    """
    Returns the hydration slot HH:MM if now matches the caregiver's 90-minute hydration cadence.
    Example:
      start 09:30, interval 90, end 18:30
      due times: 09:30, 11:00, 12:30, 14:00, 15:30, 17:00, 18:30
    """
    if caregiver.get("hydration_reminders_enabled") is not True:
        return None

    start = caregiver.get("hydration_start_time") or "09:30"
    end = caregiver.get("hydration_end_time") or "18:30"
    interval = int(caregiver.get("hydration_interval_minutes") or 90)

    start_min = hhmm_to_minutes(start)
    end_min = hhmm_to_minutes(end)
    now_min = hhmm_to_minutes(now_hhmm)

    if start_min is None or end_min is None or now_min is None:
        return None

    if now_min < start_min or now_min > end_min:
        return None

    elapsed = now_min - start_min
    if elapsed % interval == 0:
        return now_hhmm

    return None


def has_hydration_slot_been_sent(caregiver: dict[str, Any], local_date: str, slot_hhmm: str) -> bool:
    sent_log = caregiver.get("hydration_sent_log") or {}
    return sent_log.get(local_date) == slot_hhmm


def mark_hydration_slot_sent(chat_id: str, local_date: str, slot_hhmm: str) -> None:
    caregiver = get_caregiver(chat_id) or {}
    sent_log = caregiver.get("hydration_sent_log") or {}
    sent_log[local_date] = slot_hhmm

    try:
        update_caregiver(chat_id, {
            "hydration_sent_log": sent_log,
            "last_hydration_reminder_sent_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logging.warning(f"mark_hydration_slot_sent failed: {e}")



# ═══════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════

def handle_start(chat_id: str, user_info: dict[str, Any]) -> None:
    """Create or update caregiver profile, send welcome."""
    existing = get_caregiver(chat_id)
    if not existing:
        upsert_caregiver(chat_id, {
            "name": user_info.get("first_name", ""),
            "language": "en",
            "vertical": "cancer",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        })
    else:
        try:
            update_caregiver(chat_id, {"last_seen_at": datetime.now(timezone.utc).isoformat()})
        except Exception:
            pass

    welcome = msg(
        "welcome_message",
        "Hi. I'm RawatAI.\n\n"
        "I'm a companion for cancer caregivers.\n\n"
        "Karena yang merawat juga perlu dirawat.\n\n"
        "Try: /journal   /checkin   /setup   /today   /weekly   /mindful   /help",
    )
    send_message(int(chat_id), welcome)


def handle_help(chat_id: str) -> None:
    help_text = msg(
        "help_message",
        "Commands:\n"
        "/start - begin\n"
        "/setup - save treatment schedule + reminder times\n"
        "/journal - guided reflection\n"
        "/checkin - energy/stress/sleep/mood + caregiver habits\n"
        "/today - today's caregiver snapshot\n"
        "/weekly - 7-day pattern insight\n"
        "/mindful - quick mindful moment\n"
        "/breathe - 2-minute grounding reset\n"
        "/reset - clear current conversation state\n"
        "/help - show this menu",
    )
    send_message(int(chat_id), help_text)


def handle_reset(chat_id: str) -> None:
    """Emergency recovery command for demos."""
    user_state.pop(chat_id, None)
    checkin_buffer.pop(chat_id, None)
    setup_buffer.pop(chat_id, None)
    send_message(int(chat_id), "Reset done. Try /journal, /checkin, /mindful, or /help.")


def handle_journal(chat_id: str, phase: str) -> None:
    """Ask a phase-appropriate journal prompt and set state."""
    prompt = journal_prompt(phase, "en")
    user_state[chat_id] = "awaiting_journal"

    # If today's check-in suggests high stress, add a soft grounding preface.
    today = get_today_checkin(chat_id)
    profile = build_support_profile(today, phase)
    if should_offer_mindful_moment(profile):
        prompt = (
            "Before you write, take one slow breath.\n\n"
            f"{prompt}"
        )

    send_message(int(chat_id), prompt)


def handle_journal_reply(chat_id: str, text: str, phase: str) -> None:
    """Process journal reply: save to Cosmos, call Reflection Agent."""
    user_state[chat_id] = None

    thinking = msg("journal_thinking", "I'm reading this gently...")
    send_message(int(chat_id), thinking)

    today = get_today_checkin(chat_id)
    profile = build_support_profile(today, phase)

    fallback = msg("journal_fallback", "I'm here. Take your time.")
    agent_input = tone_context_for_agent(profile) + text

    try:
        reflection = reflection_agent(agent_input, phase=phase, fallback=fallback)
    except Exception as e:
        logging.error(f"reflection_agent error: {e}", exc_info=True)
        reflection = fallback

    if should_offer_mindful_moment(profile):
        reflection = f"{reflection}\n\n{mindful_moment(profile)}"

    send_message(int(chat_id), reflection)

    save_journal_entry(chat_id, {
        "phase_at_entry": phase,
        "language": "en",
        "prompt": journal_prompt(phase, "en"),
        "entry": text,
        "ai_reflection": reflection,
        "support_profile": profile,
        "agent_version": "reflection-v2-tone-aware",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


def handle_checkin_start(chat_id: str) -> None:
    """Start check-in flow — ask energy question with buttons."""
    checkin_buffer[chat_id] = {}
    user_state[chat_id] = "checkin_energy"
    question = msg("checkin_ask_energy", "How's your energy today?")
    send_with_keyboard(int(chat_id), question, [["Low", "Medium", "High"]])


def handle_checkin_step(chat_id: str, answer: str, phase: str) -> None:
    """Process one check-in answer and advance to the next step."""
    state = user_state.get(chat_id)
    buf = checkin_buffer.setdefault(chat_id, {})

    if state == "checkin_energy":
        buf["energy"] = answer.lower()
        send_message(int(chat_id), f"Energy: {answer} ✓")
        user_state[chat_id] = "checkin_stress"
        question = msg("checkin_ask_stress", "Stress level? (1 = calm, 5 = overwhelmed)")
        send_with_keyboard(int(chat_id), question, [["1", "2", "3", "4", "5"]])

    elif state == "checkin_stress":
        try:
            buf["stress"] = int(answer)
        except ValueError:
            buf["stress"] = None
        send_message(int(chat_id), f"Stress: {answer}/5 ✓")
        user_state[chat_id] = "checkin_sleep"
        question = msg("checkin_ask_sleep", "Did you sleep at least 5 hours?")
        send_with_keyboard(int(chat_id), question, [["Yes", "No"]])

    elif state == "checkin_sleep":
        buf["sleep_ok"] = answer.lower() in ("yes", "ya", "y")
        send_message(int(chat_id), f"Sleep: {answer} ✓")
        user_state[chat_id] = "checkin_mood"
        question = msg("checkin_ask_mood", "How would you describe your mood?")
        send_with_keyboard(
            int(chat_id), question,
            [["Tired", "Anxious"], ["Okay", "Hopeful"]]
        )

    elif state == "checkin_mood":
        buf["mood"] = answer.lower()
        send_message(int(chat_id), f"Mood: {answer} ✓")
        context = get_patient_context(chat_id)
        if context and context.get("next_treatment_date"):
            user_state[chat_id] = "checkin_patient"
            question = msg("checkin_ask_patient", "One more — how is your loved one today?")
            send_with_keyboard(
                int(chat_id), question,
                [["Okay", "Uncomfortable"], ["Resting", "In pain"]]
            )
        else:
            user_state[chat_id] = "checkin_pause"
            question = msg("checkin_ask_pause", "Did you take one mindful pause today?")
            send_with_keyboard(int(chat_id), question, [["Yes", "No"]])

    elif state == "checkin_patient":
        buf["patient_status"] = answer.lower()
        send_message(int(chat_id), f"Got it: {answer} ✓")
        user_state[chat_id] = "checkin_pause"
        question = msg("checkin_ask_pause", "Did you take one mindful pause today?")
        send_with_keyboard(int(chat_id), question, [["Yes", "No"]])

    elif state == "checkin_pause":
        buf.setdefault("habits", {})
        buf["habits"]["pause"] = answer.lower() in ("yes", "ya", "y")
        send_message(int(chat_id), f"Pause: {answer} ✓")

        _finish_checkin(chat_id, buf, phase)


def _finish_checkin(chat_id: str, buf: dict[str, Any], phase: str) -> None:
    """Save check-in, compute streaks, call Habit Agent, add mindful nudge if needed."""
    user_state[chat_id] = None

    habits = buf.setdefault("habits", {})
    habits.setdefault("pause", False)
    habits["sleep5h"] = buf.get("sleep_ok", False)
    buf["phase_at_checkin"] = phase
    buf["created_at"] = datetime.now(timezone.utc).isoformat()

    profile = build_support_profile(buf, phase)
    buf["support_profile"] = profile

    save_checkin(chat_id, buf)

    streaks = {
        "pause": get_streak(chat_id, "pause"),
        "sleep": get_streak(chat_id, "sleep5h"),
    }

    fallback = msg("checkin_complete", "Noted. You showed up today.")
    try:
        response = habit_agent(
            {
                **buf,
                "tone_instruction": profile["agent_instruction"],
                "mindfulness_recommendation": mindful_moment(profile),
            },
            streaks,
            phase=phase,
            fallback=fallback,
        )
    except Exception as e:
        logging.error(f"habit_agent error: {e}", exc_info=True)
        response = fallback

    # Explicit missed-habit reframing
    missed_habits = []
    if not habits.get("pause"):
        missed_habits.append("pause")
    if buf.get("sleep_ok") is False:
        missed_habits.append("sleep")

    if missed_habits:
        response += (
            "\n\nGentle habit note: missing a habit is not a failure. "
            "It is useful information about how heavy today felt."
        )

        if "sleep" in missed_habits:
            response += (
                "\nMissing sleep may be a signal that today needs to be lighter."
            )
        if "pause" in missed_habits:
            response += (
                "\nOne mindful pause can be just one slow breath."
            )

    if should_offer_mindful_moment(profile):
        response += f"\n\n{mindful_moment(profile)}"

    send_message(int(chat_id), response)
    checkin_buffer.pop(chat_id, None)


def handle_setup_start(chat_id: str) -> None:
    setup_buffer[chat_id] = {}
    user_state[chat_id] = "awaiting_setup"
    setup = msg(
        "setup_ask",
        "Tell me your loved one's treatment schedule.\n\n"
        "Example: My son has chemo every 21 days starting May 3 at 9 AM",
    )
    send_message(int(chat_id), setup)


def handle_setup_reply(chat_id: str, text: str) -> None:
    """Parse treatment schedule via Context Agent, then ask reminder times."""
    user_state[chat_id] = None

    result = context_agent_parse(text)
    confidence = result.get("confidence", "low")

    if confidence == "low" and result.get("clarifying_question"):
        prefix = msg("setup_clarify", "I want to make sure I get this right.")
        send_message(int(chat_id), f"{prefix}\n\n{result['clarifying_question']}")
        user_state[chat_id] = "awaiting_setup"
        return

    if not result.get("next_treatment_date"):
        error = msg(
            "setup_error",
            "I had trouble with that. Try again — "
            "example: My mother has chemo every 14 days starting April 30.",
        )
        send_message(int(chat_id), error)
        return

    upsert_patient_context(chat_id, {
        "vertical": "cancer",
        "patient_alias": result.get("patient_alias"),
        "treatment_type": result.get("treatment_type"),
        "next_treatment_date": result.get("next_treatment_date"),
        "cycle_days": result.get("cycle_days"),
        "confidence": confidence,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })

    user_state[chat_id] = "awaiting_daily_checkin_time"
    send_message(
        int(chat_id),
        "Got it. I’ll remember the treatment schedule.\n\n"
        "What time do you want your daily caregiver check-in?\n"
        "Example: 08:00 or 8 AM"
    )


def handle_daily_checkin_time_reply(chat_id: str, text: str) -> None:
    daily_time = parse_hhmm_time(text)
    if not daily_time:
        send_message(
            int(chat_id),
            "I couldn’t read that time. Please use something like 08:00, 8 AM, or 8.00."
        )
        user_state[chat_id] = "awaiting_daily_checkin_time"
        return

    setup_buffer.setdefault(chat_id, {})["daily_checkin_time"] = daily_time
    save_reminder_preferences(chat_id, daily_checkin_time=daily_time)

    user_state[chat_id] = "awaiting_hydration_pref"
    send_with_keyboard(
        int(chat_id),
        f"Daily check-in set for {daily_time} WIB.\n\n"
        "Would you like gentle hydration reminders every 90 minutes during the day?",
        [["Yes", "No"]]
    )


def handle_hydration_pref_reply(chat_id: str, text: str) -> None:
    answer = str(text or "").strip()

    if not (yes_answer(answer) or no_answer(answer)):
        user_state[chat_id] = "awaiting_hydration_pref"
        send_with_keyboard(
            int(chat_id),
            "Please choose Yes or No. Would you like gentle hydration reminders every 90 minutes during the day?",
            [["Yes", "No"]]
        )
        return

    daily_time = setup_buffer.get(chat_id, {}).get("daily_checkin_time")
    hydration_start = add_minutes_to_hhmm(daily_time or "08:00", 90)
    hydration_end = "18:30"

    if yes_answer(answer):
        save_reminder_preferences(
            chat_id,
            hydration_reminders_enabled=True,
            hydration_interval_minutes=90,
            hydration_start_time=hydration_start,
            hydration_end_time=hydration_end,
        )
        hydration_line = (
            f"Hydration reminders enabled every 90 minutes from {hydration_start} to {hydration_end} WIB."
        )
    else:
        save_reminder_preferences(chat_id, hydration_reminders_enabled=False)
        hydration_line = "Hydration reminders skipped. You can enable them later by running /setup again."

    user_state[chat_id] = "awaiting_mindful_time"
    send_message(
        int(chat_id),
        f"{hydration_line}\n\n"
        "What time do you want a mindful pause reminder?\n"
        "Example: 13:00 or 1 PM"
    )



def handle_mindful_time_reply(chat_id: str, text: str) -> None:
    mindful_time = parse_hhmm_time(text)
    if not mindful_time:
        send_message(
            int(chat_id),
            "I couldn’t read that time. Please use something like 13:00, 1 PM, or 1.00 PM."
        )
        user_state[chat_id] = "awaiting_mindful_time"
        return

    save_reminder_preferences(chat_id, mindful_time=mindful_time)
    user_state[chat_id] = "awaiting_night_reflection_time"
    send_message(
        int(chat_id),
        f"Mindful pause reminder set for {mindful_time} WIB.\n\n"
        "What time do you want your night reflection reminder?\n"
        "Example: 21:00 or 9 PM"
    )


def handle_night_reflection_time_reply(chat_id: str, text: str) -> None:
    night_time = parse_hhmm_time(text)
    if not night_time:
        send_message(
            int(chat_id),
            "I couldn’t read that time. Please use something like 21:00, 9 PM, or 9.00 PM."
        )
        user_state[chat_id] = "awaiting_night_reflection_time"
        return

    save_reminder_preferences(chat_id, night_reflection_time=night_time)
    user_state[chat_id] = None

    caregiver = get_caregiver(chat_id) or {}
    daily_time = caregiver.get("daily_checkin_time", setup_buffer.get(chat_id, {}).get("daily_checkin_time", "08:00"))
    mindful_time = caregiver.get("mindful_time", "13:00")

    hydration_enabled = caregiver.get("hydration_reminders_enabled")
    hydration_start = caregiver.get("hydration_start_time", "09:30")
    hydration_end = caregiver.get("hydration_end_time", "18:30")
    hydration_interval = caregiver.get("hydration_interval_minutes", 90)

    if hydration_enabled:
        hydration_summary = f"Hydration: every {hydration_interval} minutes from {hydration_start} to {hydration_end} WIB"
    else:
        hydration_summary = "Hydration: off"

    success = msg(
        "setup_success",
        "Setup complete. I’ll support you around treatment days and send gentle reminders.",
    )

    send_message(
        int(chat_id),
        f"{success}\n\n"
        f"Daily check-in: {daily_time} WIB\n"
        f"{hydration_summary}\n"
        f"Mindful pause: {mindful_time} WIB\n"
        f"Night reflection: {night_time} WIB\n\n"
        "You can enter manually anytime with /checkin, /mindful, /journal, or /today."
    )

    setup_buffer.pop(chat_id, None)



def handle_today(chat_id: str, phase: str) -> None:
    """Build and send a daily summary with a mindful moment suggestion."""
    lines = ["📊 Today's snapshot\n"]

    checkin = get_today_checkin(chat_id)
    if checkin:
        lines.append(f"Energy: {str(checkin.get('energy', '—')).title()}")
        lines.append(f"Stress: {checkin.get('stress', '—')}/5")
        lines.append(f"Sleep: {'✓' if checkin.get('sleep_ok') else '✗'}")
        lines.append(f"Mood: {str(checkin.get('mood', '—')).title()}")
    else:
        lines.append(msg("today_no_checkin", "No check-in yet. Try /checkin."))

    pause = get_streak(chat_id, "pause")
    sleep = get_streak(chat_id, "sleep5h")
    lines.append(f"\n⏸ Pause: {pause}d  😴 Sleep: {sleep}d")

    caregiver = get_caregiver(chat_id) or {}
    if caregiver.get("hydration_reminders_enabled"):
        interval = caregiver.get("hydration_interval_minutes", 90)
        start = caregiver.get("hydration_start_time", "09:30")
        end = caregiver.get("hydration_end_time", "18:30")
        lines.append(f"💧 Hydration reminders: every {interval} min from {start} to {end}")

    context = get_patient_context(chat_id)
    if context:
        next_date = get_next_treatment_date(context)
        days = days_until_treatment(next_date)
        if days is not None:
            if days == 0:
                lines.append("\n🏥 Treatment day today")
            elif days > 0:
                lines.append(f"\n🏥 Next treatment in {days} day{'s' if days != 1 else ''}")
            else:
                lines.append(f"\n🏥 Treatment was {abs(days)} day{'s' if abs(days) != 1 else ''} ago")
    else:
        lines.append(f"\n{msg('today_no_setup', 'No treatment schedule — try /setup.')}")

    if phase == "before_treatment":
        lines.append("\n⚠️ Treatment is approaching. Take it easy today.")
    elif phase == "treatment_day":
        lines.append("\n💙 Treatment day. You're doing enough.")
    elif phase == "recovery_window":
        lines.append("\n🌱 Recovery time. Habits are optional today.")

    profile = build_support_profile(checkin, phase)
    lines.append(f"\n{time_of_day_mindful_prompt(profile)}")

    send_message(int(chat_id), "\n".join(lines))


def handle_breathe(chat_id: str) -> None:
    """
    Demo-safe non-blocking breathing sequence.
    Avoid time.sleep() inside webhook because Telegram may retry long requests.
    """
    breathe = msg(
        "breathe_intro",
        "Let's take two minutes together.\n\n"
        "1. Breathe in for 4 counts.\n"
        "2. Hold for 4 counts.\n"
        "3. Breathe out slowly for 6 counts.\n\n"
        "Repeat this five times.\n\n"
        "You're still here. That's enough.",
    )
    send_message(int(chat_id), breathe)


def handle_mindful(chat_id: str, phase: str) -> None:
    """Standalone quick mindful moment command."""
    checkin = get_today_checkin(chat_id)
    profile = build_support_profile(checkin, phase)
    send_message(int(chat_id), time_of_day_mindful_prompt(profile))


def handle_weekly(chat_id: str, phase: str) -> None:
    """Summarize patterns from past journal entries and check-ins."""
    send_message(int(chat_id), msg("weekly_thinking", "Looking gently at the last 7 days..."))
    insight = build_weekly_insight(chat_id, phase)
    send_message(int(chat_id), insight)


# ═══════════════════════════════════════════════════════════════════
# OPTIONAL PROACTIVE REMINDER SCANNER
# ═══════════════════════════════════════════════════════════════════

def _get_active_caregivers_for_reminders() -> list[dict[str, Any]]:
    """
    Read caregivers eligible for proactive reminders.
    Preferred: implement get_active_caregivers() in cosmos_client.py.
    Fallback: direct Cosmos query against caregivers container.
    """
    fn = _optional_cosmos_function("get_active_caregivers")
    if fn:
        try:
            return fn() or []
        except Exception as e:
            logging.warning(f"get_active_caregivers failed: {e}")

    try:
        from azure.cosmos import CosmosClient
    except Exception:
        return []

    endpoint = os.environ.get("COSMOS_ENDPOINT") or os.environ.get("COSMOS_DB_ENDPOINT")
    key = os.environ.get("COSMOS_KEY") or os.environ.get("COSMOS_DB_KEY")
    database_name = (
        os.environ.get("COSMOS_DATABASE_NAME")
        or os.environ.get("COSMOS_DB_DATABASE")
        or os.environ.get("COSMOS_DATABASE")
        or "rawatai-db"
    )
    container_name = os.environ.get("CAREGIVER_CONTAINER_NAME") or "caregivers"

    if not endpoint or not key:
        return []

    try:
        client = CosmosClient(endpoint, credential=key)
        db = client.get_database_client(database_name)
        container = db.get_container_client(container_name)

        query = "SELECT * FROM c WHERE c.reminders_enabled = true"
        return list(container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))
    except Exception as e:
        logging.warning(f"Fallback caregiver reminder query failed: {e}")
        return []


def _is_due_now(target_hhmm: Optional[str], now_hhmm: str) -> bool:
    """
    Simple due check. Timer runs every 5 minutes, so this triggers when HH:MM matches.
    For demo, use exact HH:MM. In production, use a tolerance window.
    """
    return bool(target_hhmm and target_hhmm == now_hhmm)


# Scheduled reminder scanner is optional. Disabled by default locally to avoid
# AzureWebJobsStorage/Azurite errors. Enable by setting:
#   ENABLE_SCHEDULED_REMINDERS=true
if ENABLE_SCHEDULED_REMINDERS:

    @app.timer_trigger(
        schedule="0 */5 * * * *",
        arg_name="timer",
        run_on_startup=False,
        use_monitor=False,
    )
    def scheduled_reminder_scanner(timer: func.TimerRequest) -> None:
        """
        Every 5 minutes, checks caregiver reminder preferences and pushes messages
        when the local WIB time matches:
          - daily_checkin_time
          - mindful_time
          - night_reflection_time
        """
        now_utc = datetime.now(timezone.utc)
        now_wib = now_utc + timedelta(hours=7)
        now_hhmm = now_wib.strftime("%H:%M")
        local_date = now_wib.strftime("%Y-%m-%d")

        caregivers = _get_active_caregivers_for_reminders()
        logging.info(f"Reminder scanner at WIB {now_hhmm}; caregivers={len(caregivers)}")

        for caregiver in caregivers:
            try:
                chat_id = str(caregiver.get("chat_id") or caregiver.get("id"))
                if not chat_id:
                    continue

                phase = load_phase(chat_id)

                if (
                    _is_due_now(caregiver.get("daily_checkin_time"), now_hhmm)
                    and not has_reminder_been_sent_today(caregiver, "daily_checkin", local_date)
                ):
                    send_message(int(chat_id), build_daily_checkin_push_message(chat_id, phase))
                    mark_reminder_sent(chat_id, "daily_checkin", local_date)

                hydration_slot = hydration_slot_for_now(caregiver, now_hhmm)
                if (
                    hydration_slot
                    and not has_hydration_slot_been_sent(caregiver, local_date, hydration_slot)
                ):
                    send_message(int(chat_id), build_hydration_push_message(chat_id, phase))
                    mark_hydration_slot_sent(chat_id, local_date, hydration_slot)

                if (
                    _is_due_now(caregiver.get("mindful_time"), now_hhmm)
                    and not has_reminder_been_sent_today(caregiver, "mindful", local_date)
                ):
                    send_message(int(chat_id), build_mindful_push_message(chat_id, phase))
                    mark_reminder_sent(chat_id, "mindful", local_date)

                if (
                    _is_due_now(caregiver.get("night_reflection_time"), now_hhmm)
                    and not has_reminder_been_sent_today(caregiver, "night_reflection", local_date)
                ):
                    send_message(int(chat_id), build_night_reflection_push_message(chat_id, phase))
                    mark_reminder_sent(chat_id, "night_reflection", local_date)

            except Exception as e:
                logging.warning(f"scheduled reminder send failed: {e}")


# ═══════════════════════════════════════════════════════════════════
# MAIN WEBHOOK
# ═══════════════════════════════════════════════════════════════════

@app.route(route="telegram_webhook", methods=["POST"])
def telegram_webhook(req: func.HttpRequest) -> func.HttpResponse:
    chat_id = None

    try:
        update = req.get_json()
        logging.info(f"Update: {json.dumps(update)[:500]}")

        # ── Handle callback queries (button presses) ──────────────
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_id = str(cq["message"]["chat"]["id"])
            answer = cq["data"]
            callback_id = cq["id"]

            answer_callback(callback_id)

            state = user_state.get(chat_id)
            if state in CHECKIN_STATES:
                phase = load_phase(chat_id)
                handle_checkin_step(chat_id, answer, phase)
            elif state == "awaiting_hydration_pref":
                handle_hydration_pref_reply(chat_id, answer)

            return func.HttpResponse("OK", status_code=200)

        # ── Handle text messages ──────────────────────────────────
        if "message" not in update:
            return func.HttpResponse("OK", status_code=200)

        message = update["message"]
        chat_id = str(message["chat"]["id"])
        user_info = message.get("from", {})
        text = message.get("text", "").strip()

        if not text:
            return func.HttpResponse("OK", status_code=200)

        phase = load_phase(chat_id)
        state = user_state.get(chat_id)

        # ── Commands ──────────────────────────────────────────────
        if text == "/start":
            handle_start(chat_id, user_info)

        elif text == "/help":
            handle_help(chat_id)

        elif text == "/reset":
            handle_reset(chat_id)

        elif text == "/journal":
            handle_journal(chat_id, phase)

        elif text == "/checkin":
            handle_checkin_start(chat_id)

        elif text == "/setup":
            handle_setup_start(chat_id)

        elif text == "/today":
            handle_today(chat_id, phase)

        elif text == "/breathe":
            handle_breathe(chat_id)

        elif text == "/mindful":
            handle_mindful(chat_id, phase)

        elif text in {"/weekly", "/insights"}:
            handle_weekly(chat_id, phase)

        elif text == "/testreminders":
            handle_test_reminders(chat_id, phase)

        # ── Conversation states ───────────────────────────────────
        elif state == "awaiting_journal":
            handle_journal_reply(chat_id, text, phase)

        elif state == "awaiting_setup":
            handle_setup_reply(chat_id, text)

        elif state == "awaiting_daily_checkin_time":
            handle_daily_checkin_time_reply(chat_id, text)

        elif state == "awaiting_hydration_pref":
            handle_hydration_pref_reply(chat_id, text)

        elif state == "awaiting_mindful_time":
            handle_mindful_time_reply(chat_id, text)

        elif state == "awaiting_night_reflection_time":
            handle_night_reflection_time_reply(chat_id, text)

        elif state in CHECKIN_STATES:
            # Typed answer instead of button tap
            handle_checkin_step(chat_id, text, phase)

        else:
            unknown = msg(
                "unknown_command",
                "Try /journal to reflect, /checkin to check in, /mindful for a pause, "
                "or /help for all commands.",
            )
            send_message(int(chat_id), unknown)

        return func.HttpResponse("OK", status_code=200)

    except Exception as e:
        logging.error(f"Webhook error: {e}", exc_info=True)
        try:
            if chat_id:
                send_message(
                    int(chat_id),
                    "Something went wrong. Please try /help or /start.",
                )
        except Exception:
            pass

        # Always return OK so Telegram does not retry endlessly.
        return func.HttpResponse("OK", status_code=200)