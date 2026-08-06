"""Configuration centrale du projet, lue depuis les variables d'environnement."""

from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

# --- Chemins ---------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "state.json"
EXCEL_FILE = DATA_DIR / "sorties_slots.xlsx"

# --- Fuseau horaire --------------------------------------------------------
# Tout le raisonnement calendaire (jour de run, jour de notification) se fait
# en heure de Paris. GitHub Actions tourne en UTC : on ne s'y fie jamais.

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Paris"))

# --- Source de donnees -----------------------------------------------------

API_BASE = os.getenv("SLOT_REPORT_API_BASE", "https://slot.report/api/v1")
API_KEY = os.getenv("SLOT_REPORT_API_KEY", "").strip() or None
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "slot-radar/1.0 (weekly release digest; contact via GitHub)",
)

# --- Telegram --------------------------------------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or None
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip() or None

# --- Comportement ----------------------------------------------------------

# 0 = lundi ... 6 = dimanche. Jour d'envoi du recap sur Telegram.
NOTIFY_WEEKDAY = int(os.getenv("NOTIFY_WEEKDAY", "0"))

# Envoyer le fichier Excel en piece jointe avec le recap.
SEND_EXCEL = os.getenv("SEND_EXCEL", "true").lower() in {"1", "true", "yes"}

# Lors du bootstrap : remplir l'Excel avec tout l'historique disponible.
BOOTSTRAP_BACKFILL = os.getenv("BOOTSTRAP_BACKFILL", "true").lower() in {
    "1",
    "true",
    "yes",
}

# Providers a ignorer completement (slugs, separes par des virgules).
EXCLUDED_PROVIDERS = {
    s.strip().lower()
    for s in os.getenv("EXCLUDED_PROVIDERS", "").split(",")
    if s.strip()
}
