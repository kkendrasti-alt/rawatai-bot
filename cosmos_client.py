"""
shared/cosmos_client.py
Cosmos DB wrapper for RawatAI.
Handles all reads and writes across 5 containers.
"""

import os
import logging
from datetime import datetime, timezone
from azure.cosmos import CosmosClient, PartitionKey, exceptions

# ── Connection ────────────────────────────────────────────────────
_client = None
_db = None


def _get_db():
    """Lazy-init the Cosmos client. Runs once per function instance."""
    global _client, _db
    if _db is None:
        endpoint = os.environ["COSMOS_ENDPOINT"]
        key = os.environ["COSMOS_KEY"]
        db_name = os.environ.get("COSMOS_DB", "rawatai-db")
        _client = CosmosClient(endpoint, key)
        _db = _client.get_database_client(db_name)
    return _db


def _container(name: str):
    return _get_db().get_container_client(name)


# ── Helpers ───────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════
# CAREGIVERS
# ═══════════════════════════════════════════════════════════════════

def get_caregiver(chat_id: str) -> dict | None:
    try:
        return _container("caregivers").read_item(
            item=chat_id, partition_key=chat_id
        )
    except exceptions.CosmosResourceNotFoundError:
        return None
    except Exception as e:
        logging.error(f"get_caregiver error: {e}")
        return None


def upsert_caregiver(chat_id: str, data: dict) -> None:
    try:
        data["id"] = chat_id
        data["chat_id"] = chat_id
        data.setdefault("created_at", now_iso())
        data["updated_at"] = now_iso()
        _container("caregivers").upsert_item(data)
    except Exception as e:
        logging.error(f"upsert_caregiver error: {e}")


def update_caregiver(chat_id: str, updates: dict) -> None:
    """Merge updates into existing caregiver record."""
    existing = get_caregiver(chat_id) or {}
    existing.update(updates)
    upsert_caregiver(chat_id, existing)


# ═══════════════════════════════════════════════════════════════════
# PATIENT CONTEXT (treatment schedule)
# ═══════════════════════════════════════════════════════════════════

def get_patient_context(chat_id: str) -> dict | None:
    try:
        return _container("patient_context").read_item(
            item=chat_id, partition_key=chat_id
        )
    except exceptions.CosmosResourceNotFoundError:
        return None
    except Exception as e:
        logging.error(f"get_patient_context error: {e}")
        return None


def upsert_patient_context(chat_id: str, data: dict) -> None:
    try:
        data["id"] = chat_id
        data["chat_id"] = chat_id
        data["updated_at"] = now_iso()
        _container("patient_context").upsert_item(data)
    except Exception as e:
        logging.error(f"upsert_patient_context error: {e}")


# ═══════════════════════════════════════════════════════════════════
# CHECK-INS
# ═══════════════════════════════════════════════════════════════════

def save_checkin(chat_id: str, checkin: dict) -> None:
    try:
        import uuid
        checkin["id"] = str(uuid.uuid4())
        checkin["chat_id"] = chat_id
        checkin.setdefault("date", today_date())
        checkin["created_at"] = now_iso()
        _container("checkins").create_item(checkin)
    except Exception as e:
        logging.error(f"save_checkin error: {e}")


def get_today_checkin(chat_id: str) -> dict | None:
    try:
        query = (
            "SELECT TOP 1 * FROM c WHERE c.chat_id = @id AND c.date = @date "
            "ORDER BY c.created_at DESC"
        )
        params = [
            {"name": "@id", "value": chat_id},
            {"name": "@date", "value": today_date()},
        ]
        items = list(
            _container("checkins").query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=False,
            )
        )
        return items[0] if items else None
    except Exception as e:
        logging.error(f"get_today_checkin error: {e}")
        return None


def get_streak(chat_id: str, habit: str, days: int = 7) -> int:
    """Count consecutive days the habit was True, working backwards from today."""
    try:
        query = (
            f"SELECT c.date, c.habits FROM c "
            f"WHERE c.chat_id = @id ORDER BY c.date DESC OFFSET 0 LIMIT @days"
        )
        params = [
            {"name": "@id", "value": chat_id},
            {"name": "@days", "value": days},
        ]
        items = list(
            _container("checkins").query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=False,
            )
        )
        streak = 0
        for item in items:
            habits = item.get("habits", {})
            if habits.get(habit):
                streak += 1
            else:
                break
        return streak
    except Exception as e:
        logging.error(f"get_streak error: {e}")
        return 0


# ═══════════════════════════════════════════════════════════════════
# JOURNAL ENTRIES
# ═══════════════════════════════════════════════════════════════════

def save_journal_entry(chat_id: str, entry: dict) -> None:
    try:
        import uuid
        entry["id"] = str(uuid.uuid4())
        entry["chat_id"] = chat_id
        entry.setdefault("date", today_date())
        entry["created_at"] = now_iso()
        _container("journal_entries").create_item(entry)
    except Exception as e:
        logging.error(f"save_journal_entry error: {e}")


def get_recent_journals(chat_id: str, limit: int = 7) -> list:
    try:
        query = (
            "SELECT * FROM c WHERE c.chat_id = @id "
            "ORDER BY c.created_at DESC OFFSET 0 LIMIT @limit"
        )
        params = [
            {"name": "@id", "value": chat_id},
            {"name": "@limit", "value": limit},
        ]
        return list(
            _container("journal_entries").query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=False,
            )
        )
    except Exception as e:
        logging.error(f"get_recent_journals error: {e}")
        return []


# ═══════════════════════════════════════════════════════════════════
# CONTENT LIBRARY (database-driven messages)
# ═══════════════════════════════════════════════════════════════════

def get_message(message_id: str, lang: str = "en") -> str | None:
    """
    Fetch a message from the content library by ID.
    Falls back to 'en' if the requested language is missing.
    Falls back to message_id string if not found at all.
    """
    try:
        item = _container("content_library").read_item(
            item=message_id, partition_key=message_id
        )
        return item.get(lang) or item.get("en") or message_id
    except exceptions.CosmosResourceNotFoundError:
        logging.warning(f"Message not found in content_library: {message_id}")
        return None
    except Exception as e:
        logging.error(f"get_message error: {e}")
        return None


def get_journal_prompt(phase: str = "normal", lang: str = "en") -> str:
    """
    Fetch a random journal prompt for the given phase from content_library.
    Falls back to hardcoded default if not found.
    """
    import random
    try:
        query = (
            "SELECT * FROM c WHERE c.type = 'journal_prompt' "
            "AND c.phase = @phase"
        )
        params = [{"name": "@phase", "value": phase}]
        items = list(
            _container("content_library").query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            )
        )
        if items:
            chosen = random.choice(items)
            return chosen.get(lang) or chosen.get("en") or "What felt hardest today?"
    except Exception as e:
        logging.error(f"get_journal_prompt error: {e}")

    # Hardcoded fallbacks — last resort
    fallbacks = {
        "normal": "How are you showing up for yourself today?",
        "before_treatment": "Treatment is approaching. What's weighing on you?",
        "treatment_day": "Today is a lot. Anything you want to say?",
        "recovery_window": "The hardest days are often after. How is home?",
    }
    return fallbacks.get(phase, "What felt hardest today?")
