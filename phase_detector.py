"""
shared/phase_detector.py
Detects the current treatment phase based on next treatment date.
No AI call needed — pure date math.
"""

from datetime import datetime, timezone, timedelta
import logging


def get_current_phase(next_treatment_date_iso: str | None) -> str:
    """
    Returns one of:
      'normal'           — more than 3 days from treatment
      'before_treatment' — 1-3 days before treatment
      'treatment_day'    — day of treatment
      'recovery_window'  — 1-5 days after treatment
    """
    if not next_treatment_date_iso:
        return "normal"

    try:
        # Parse the ISO date — handle both date-only and datetime formats
        raw = next_treatment_date_iso.replace("Z", "+00:00")
        if "T" in raw:
            treatment_dt = datetime.fromisoformat(raw)
        else:
            treatment_dt = datetime.fromisoformat(raw + "T00:00:00+00:00")

        # Normalize to UTC date only for comparison
        today = datetime.now(timezone.utc).date()
        treatment_date = treatment_dt.date()

        days_delta = (treatment_date - today).days

        if days_delta == 0:
            return "treatment_day"
        elif 1 <= days_delta <= 3:
            return "before_treatment"
        elif -5 <= days_delta <= -1:
            return "recovery_window"
        else:
            return "normal"

    except Exception as e:
        logging.error(f"get_current_phase error: {e}")
        return "normal"


def get_next_treatment_date(context: dict, from_date: str = None) -> str | None:
    """
    Given a patient_context doc, compute the actual next upcoming treatment date.
    Rolls the cycle forward if the stored next_treatment_date is in the past.
    Returns ISO date string or None.
    """
    if not context:
        return None

    next_date_iso = context.get("next_treatment_date")
    cycle_days = context.get("cycle_days")

    if not next_date_iso:
        return None

    try:
        raw = next_date_iso.replace("Z", "+00:00")
        if "T" in raw:
            next_dt = datetime.fromisoformat(raw)
        else:
            next_dt = datetime.fromisoformat(raw + "T00:00:00+00:00")

        today = datetime.now(timezone.utc)

        # If we have a cycle and the date is in the past, roll forward
        if cycle_days and next_dt.date() < today.date():
            while next_dt.date() < today.date():
                next_dt += timedelta(days=cycle_days)

        return next_dt.isoformat()

    except Exception as e:
        logging.error(f"get_next_treatment_date error: {e}")
        return next_date_iso


def days_until_treatment(next_treatment_date_iso: str | None) -> int | None:
    """Returns number of days until next treatment. Negative = days since."""
    if not next_treatment_date_iso:
        return None
    try:
        raw = next_treatment_date_iso.replace("Z", "+00:00")
        if "T" in raw:
            treatment_dt = datetime.fromisoformat(raw)
        else:
            treatment_dt = datetime.fromisoformat(raw + "T00:00:00+00:00")
        today = datetime.now(timezone.utc).date()
        return (treatment_dt.date() - today).days
    except Exception as e:
        logging.error(f"days_until_treatment error: {e}")
        return None
