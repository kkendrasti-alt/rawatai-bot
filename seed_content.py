"""
seed_content.py
Run this ONCE to seed the Cosmos DB content_library container
with all database-driven messages and journal prompts.

Usage:
  python seed_content.py

Requires COSMOS_ENDPOINT and COSMOS_KEY in environment or .env file.
"""

import os
import sys
from azure.cosmos import CosmosClient, exceptions

# ── Load local env if running outside Azure Functions ─────────────
try:
    import json
    with open("local.settings.json") as f:
        settings = json.load(f)
    for k, v in settings.get("Values", {}).items():
        os.environ.setdefault(k, v)
except FileNotFoundError:
    pass

ENDPOINT = os.environ["COSMOS_ENDPOINT"]
KEY = os.environ["COSMOS_KEY"]
DB_NAME = os.environ.get("COSMOS_DB", "rawatai-db")

client = CosmosClient(ENDPOINT, KEY)
db = client.get_database_client(DB_NAME)
container = db.get_container_client("content_library")


# ═══════════════════════════════════════════════════════════════════
# CONTENT DOCUMENTS
# ═══════════════════════════════════════════════════════════════════

documents = [

    # ── Bot messages ───────────────────────────────────────────────
    {
        "id": "welcome_message",
        "type": "message",
        "phase": None,
        "en": (
            "Hi. I'm RawatAI.\n\n"
            "I'm a companion for cancer caregivers — built to help you "
            "reflect, track your own wellbeing, and feel less alone.\n\n"
            "Karena yang merawat juga perlu dirawat.\n\n"
            "Try: /journal   /checkin   /setup   /help"
        ),
        "id_lang": (
            "Halo. Saya RawatAI.\n\n"
            "Saya adalah pendamping untuk caregiver kanker — dibuat untuk membantu "
            "Anda merefleksikan diri, memantau kesehatan Anda sendiri, dan merasa "
            "tidak sendirian.\n\n"
            "Karena yang merawat juga perlu dirawat.\n\n"
            "Coba: /journal   /checkin   /setup   /bantuan"
        ),
    },
    {
        "id": "help_message",
        "type": "message",
        "phase": None,
        "en": (
            "Commands:\n"
            "/start — welcome\n"
            "/journal — daily reflection\n"
            "/checkin — how are you doing today\n"
            "/setup — enter your loved one's treatment schedule\n"
            "/today — daily summary\n"
            "/breathe — 2-minute breathing reset\n"
            "/help — this list"
        ),
        "id_lang": (
            "Perintah:\n"
            "/start — sambutan\n"
            "/journal — refleksi harian\n"
            "/checkin — bagaimana hari ini\n"
            "/setup — masukkan jadwal pengobatan\n"
            "/today — ringkasan harian\n"
            "/breathe — reset napas 2 menit\n"
            "/bantuan — daftar ini"
        ),
    },
    {
        "id": "unknown_command",
        "type": "message",
        "phase": None,
        "en": "Try /journal to reflect, /checkin to check in, or /help for all commands.",
        "id_lang": "Coba /journal untuk refleksi, /checkin untuk laporan, atau /bantuan untuk semua perintah.",
    },
    {
        "id": "setup_ask",
        "type": "message",
        "phase": None,
        "en": (
            "Tell me your loved one's treatment schedule in plain language.\n\n"
            "Example: My son has chemo every 21 days starting May 3 at 9 AM\n\n"
            "I'll remember the cycle and support you around each treatment day."
        ),
        "id_lang": (
            "Ceritakan jadwal pengobatan orang yang Anda rawat dalam bahasa sehari-hari.\n\n"
            "Contoh: Anak saya kemo setiap 21 hari mulai 3 Mei jam 9 pagi\n\n"
            "Saya akan mengingat siklus ini dan mendukung Anda di sekitar setiap hari pengobatan."
        ),
    },
    {
        "id": "setup_success",
        "type": "message",
        "phase": None,
        "en": "Got it. I'll remember the schedule and keep your days lighter around treatment time.",
        "id_lang": "Baik. Saya akan mengingat jadwal ini dan menjaga hari-hari Anda lebih ringan di sekitar waktu pengobatan.",
    },
    {
        "id": "setup_clarify",
        "type": "message",
        "phase": None,
        "en": "I want to make sure I get this right.",
        "id_lang": "Saya ingin memastikan saya memahami ini dengan benar.",
    },
    {
        "id": "setup_error",
        "type": "message",
        "phase": None,
        "en": "I had trouble understanding that. Try again — for example: My mother has chemo every 14 days starting April 30.",
        "id_lang": "Saya kesulitan memahami itu. Coba lagi — misalnya: Ibu saya kemo setiap 14 hari mulai 30 April.",
    },
    {
        "id": "journal_thinking",
        "type": "message",
        "phase": None,
        "en": "...",
        "id_lang": "...",
    },
    {
        "id": "journal_fallback",
        "type": "message",
        "phase": None,
        "en": "I'm here. Take your time.",
        "id_lang": "Saya di sini. Tidak perlu terburu-buru.",
    },
    {
        "id": "checkin_ask_energy",
        "type": "message",
        "phase": None,
        "en": "How's your energy today?",
        "id_lang": "Bagaimana energi Anda hari ini?",
    },
    {
        "id": "checkin_ask_stress",
        "type": "message",
        "phase": None,
        "en": "Stress level right now? (1 = calm, 5 = overwhelmed)",
        "id_lang": "Tingkat stres sekarang? (1 = tenang, 5 = sangat tertekan)",
    },
    {
        "id": "checkin_ask_sleep",
        "type": "message",
        "phase": None,
        "en": "Did you sleep at least 5 hours last night?",
        "id_lang": "Apakah Anda tidur setidaknya 5 jam tadi malam?",
    },
    {
        "id": "checkin_ask_mood",
        "type": "message",
        "phase": None,
        "en": "How would you describe your mood?",
        "id_lang": "Bagaimana suasana hati Anda?",
    },
    {
        "id": "checkin_ask_patient",
        "type": "message",
        "phase": None,
        "en": "One more — how is your loved one doing today?",
        "id_lang": "Satu lagi — bagaimana kondisi orang yang Anda rawat hari ini?",
    },
    {
        "id": "checkin_complete",
        "type": "message",
        "phase": None,
        "en": "Noted. You showed up today — that counts.",
        "id_lang": "Dicatat. Anda hadir hari ini — itu berarti.",
    },
    {
        "id": "breathe_intro",
        "type": "message",
        "phase": None,
        "en": "Let's take two minutes together.\n\nRead slowly. Follow the rhythm.",
        "id_lang": "Mari luangkan dua menit bersama.\n\nBaca perlahan. Ikuti ritmenya.",
    },
    {
        "id": "breathe_inhale",
        "type": "message",
        "phase": None,
        "en": "Breathe in... (4 counts)",
        "id_lang": "Tarik napas... (4 hitungan)",
    },
    {
        "id": "breathe_hold",
        "type": "message",
        "phase": None,
        "en": "Hold... (4 counts)",
        "id_lang": "Tahan... (4 hitungan)",
    },
    {
        "id": "breathe_exhale",
        "type": "message",
        "phase": None,
        "en": "Breathe out slowly... (6 counts)",
        "id_lang": "Hembuskan perlahan... (6 hitungan)",
    },
    {
        "id": "breathe_close",
        "type": "message",
        "phase": None,
        "en": "You're still here. That's enough.",
        "id_lang": "Anda masih di sini. Itu sudah cukup.",
    },
    {
        "id": "today_no_checkin",
        "type": "message",
        "phase": None,
        "en": "No check-in yet today. Try /checkin when you have 30 seconds.",
        "id_lang": "Belum ada check-in hari ini. Coba /checkin saat Anda punya 30 detik.",
    },
    {
        "id": "today_no_setup",
        "type": "message",
        "phase": None,
        "en": "No treatment schedule set. Try /setup to add one.",
        "id_lang": "Belum ada jadwal pengobatan. Coba /setup untuk menambahkan.",
    },

    # ── Phase-aware messages ────────────────────────────────────────
    {
        "id": "nudge_before_treatment",
        "type": "message",
        "phase": "before_treatment",
        "en": (
            "Treatment day is approaching.\n\n"
            "Let's keep today simple.\n\n"
            "When you're ready: /journal   /breathe"
        ),
        "id_lang": (
            "Hari pengobatan sudah dekat.\n\n"
            "Mari jaga hari ini tetap sederhana.\n\n"
            "Jika siap: /journal   /breathe"
        ),
    },
    {
        "id": "nudge_treatment_day",
        "type": "message",
        "phase": "treatment_day",
        "en": (
            "Today is treatment day.\n\n"
            "You don't have to do anything right now. I'm just here.\n\n"
            "/journal anytime you want to talk."
        ),
        "id_lang": (
            "Hari ini adalah hari pengobatan.\n\n"
            "Anda tidak perlu melakukan apa pun sekarang. Saya hanya ada di sini.\n\n"
            "/journal kapan pun Anda ingin berbicara."
        ),
    },
    {
        "id": "nudge_recovery",
        "type": "message",
        "phase": "recovery_window",
        "en": (
            "The hardest days are often the ones after treatment.\n\n"
            "Be gentle with yourself today. Habits are optional.\n\n"
            "/journal   /breathe"
        ),
        "id_lang": (
            "Hari-hari tersulit seringkali adalah yang setelah pengobatan.\n\n"
            "Berbaik hatilah pada diri sendiri hari ini. Kebiasaan bersifat opsional.\n\n"
            "/journal   /breathe"
        ),
    },

    # ── Journal prompts by phase ────────────────────────────────────
    {
        "id": "jp_normal_1",
        "type": "journal_prompt",
        "phase": "normal",
        "en": "How are you showing up for yourself today?",
        "id_lang": "Bagaimana Anda hadir untuk diri sendiri hari ini?",
    },
    {
        "id": "jp_normal_2",
        "type": "journal_prompt",
        "phase": "normal",
        "en": "What's one thing you wish someone understood about your day?",
        "id_lang": "Apa satu hal yang Anda ingin seseorang pahami tentang hari Anda?",
    },
    {
        "id": "jp_normal_3",
        "type": "journal_prompt",
        "phase": "normal",
        "en": "What are you carrying right now that feels too heavy to say out loud?",
        "id_lang": "Apa yang Anda tanggung sekarang yang terasa terlalu berat untuk diucapkan?",
    },
    {
        "id": "jp_normal_4",
        "type": "journal_prompt",
        "phase": "normal",
        "en": "What does your body need today that it hasn't gotten?",
        "id_lang": "Apa yang dibutuhkan tubuh Anda hari ini yang belum terpenuhi?",
    },
    {
        "id": "jp_normal_5",
        "type": "journal_prompt",
        "phase": "normal",
        "en": "What felt hardest today?",
        "id_lang": "Apa yang paling berat hari ini?",
    },
    {
        "id": "jp_before_1",
        "type": "journal_prompt",
        "phase": "before_treatment",
        "en": "Treatment day is approaching. What's weighing on you right now?",
        "id_lang": "Hari pengobatan sudah dekat. Apa yang memberatkan Anda sekarang?",
    },
    {
        "id": "jp_before_2",
        "type": "journal_prompt",
        "phase": "before_treatment",
        "en": "What's one small thing you want to make sure gets done before treatment day?",
        "id_lang": "Apa satu hal kecil yang ingin Anda pastikan selesai sebelum hari pengobatan?",
    },
    {
        "id": "jp_before_3",
        "type": "journal_prompt",
        "phase": "before_treatment",
        "en": "How are you holding up as tomorrow approaches?",
        "id_lang": "Bagaimana Anda bertahan saat esok hari semakin dekat?",
    },
    {
        "id": "jp_treatment_1",
        "type": "journal_prompt",
        "phase": "treatment_day",
        "en": "Today is a lot. You don't have to answer. If you want to say anything, I'm here.",
        "id_lang": "Hari ini sangat berat. Anda tidak harus menjawab. Jika ingin berkata sesuatu, saya di sini.",
    },
    {
        "id": "jp_treatment_2",
        "type": "journal_prompt",
        "phase": "treatment_day",
        "en": "What's one word for how today feels?",
        "id_lang": "Satu kata untuk menggambarkan perasaan hari ini?",
    },
    {
        "id": "jp_recovery_1",
        "type": "journal_prompt",
        "phase": "recovery_window",
        "en": "The hardest days are often the ones after. How is home right now?",
        "id_lang": "Hari-hari tersulit seringkali setelah pengobatan. Bagaimana kondisi di rumah sekarang?",
    },
    {
        "id": "jp_recovery_2",
        "type": "journal_prompt",
        "phase": "recovery_window",
        "en": "You don't have to track anything today. How are you, really?",
        "id_lang": "Anda tidak perlu melacak apa pun hari ini. Bagaimana sebenarnya kondisi Anda?",
    },
    {
        "id": "jp_recovery_3",
        "type": "journal_prompt",
        "phase": "recovery_window",
        "en": "What did you need today that you didn't get?",
        "id_lang": "Apa yang Anda butuhkan hari ini namun tidak terpenuhi?",
    },
]


# ═══════════════════════════════════════════════════════════════════
# SEED RUNNER
# ═══════════════════════════════════════════════════════════════════

def seed():
    print(f"Seeding {len(documents)} documents to content_library...")
    success = 0
    for doc in documents:
        try:
            container.upsert_item(doc)
            print(f"  ✓ {doc['id']}")
            success += 1
        except Exception as e:
            print(f"  ✗ {doc['id']}: {e}")

    print(f"\nDone: {success}/{len(documents)} seeded successfully.")


if __name__ == "__main__":
    seed()
