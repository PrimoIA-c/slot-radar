"""Persistance et dedoublonnage.

state.json est la source de verite du projet. L'Excel et les messages Telegram
en sont deduits : on peut supprimer l'Excel a tout moment, il sera regenere a
l'identique au prochain run.

Structure :
{
  "version": 1,
  "last_run": "2026-08-06",
  "entries": {
    "pragmatic-play::sweet-bonanza": {
      "name": "Sweet Bonanza",
      "provider": "Pragmatic Play",
      "provider_slug": "pragmatic-play",
      "release_date": "2026-07-12",
      "date_source": "provider",   # ou "detection" si l'API ne donne pas de date
      "first_seen": "2026-08-06",
      "notified": true
    }
  }
}
"""

from __future__ import annotations

import json
import logging
from datetime import date

from . import config

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def empty_state() -> dict:
    return {"version": SCHEMA_VERSION, "last_run": None, "entries": {}}


def load() -> dict:
    if not config.STATE_FILE.exists():
        log.info("Aucun state.json : premier demarrage")
        return empty_state()

    try:
        state = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"state.json est corrompu ({exc}). Restaure-le depuis l'historique git "
            f"plutot que de le supprimer : le supprimer relancerait un bootstrap "
            f"et marquerait tout le catalogue comme deja notifie."
        ) from exc

    state.setdefault("version", SCHEMA_VERSION)
    state.setdefault("last_run", None)
    state.setdefault("entries", {})
    log.info("state.json charge : %s entrees connues", len(state["entries"]))
    return state


def save(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
    config.STATE_FILE.write_text(payload + "\n", encoding="utf-8")
    log.info("state.json ecrit : %s entrees", len(state["entries"]))


def merge(state: dict, incoming: list[dict], today: date, notified: bool) -> list[dict]:
    """Insere les entrees inconnues dans le state.

    Le critere de nouveaute est l'absence de la cle dans le state, jamais la
    date de sortie : la couverture du champ release_date est partielle et une
    slot peut entrer dans la base plusieurs jours apres son lancement. Filtrer
    sur une fenetre de dates ferait passer ces entrees a la trappe.

    `notified` a True lors du bootstrap : on enregistre le catalogue existant
    sans declencher une notification de plusieurs milliers de lignes.

    Renvoie la liste des entrees reellement ajoutees.
    """
    added: list[dict] = []
    iso_today = today.isoformat()

    for entry in incoming:
        key = entry["key"]
        if key in state["entries"]:
            _refresh_release_date(state["entries"][key], entry)
            continue

        release_date = entry["release_date"]
        state["entries"][key] = {
            "name": entry["name"],
            "provider": entry["provider"],
            "provider_slug": entry["provider_slug"],
            "release_date": release_date or iso_today,
            "date_source": "provider" if release_date else "detection",
            "first_seen": iso_today,
            "notified": notified,
        }
        added.append({"key": key, **state["entries"][key]})

    if added:
        log.info("%s nouvelles entrees", len(added))
    return added


def _refresh_release_date(stored: dict, incoming: dict) -> None:
    """Complete une date estimee si l'API finit par publier la vraie date."""
    if stored.get("date_source") == "detection" and incoming.get("release_date"):
        stored["release_date"] = incoming["release_date"]
        stored["date_source"] = "provider"


def pending(state: dict) -> list[dict]:
    """Entrees pas encore annoncees sur Telegram.

    Un echec d'envoi n'est donc jamais definitif : le run suivant reprendra
    l'arriere en plus de ses propres nouveautes.
    """
    return [
        {"key": key, **value}
        for key, value in state["entries"].items()
        if not value.get("notified")
    ]


def mark_notified(state: dict, entries: list[dict]) -> None:
    for entry in entries:
        stored = state["entries"].get(entry["key"])
        if stored is not None:
            stored["notified"] = True


def all_entries(state: dict) -> list[dict]:
    return [{"key": key, **value} for key, value in state["entries"].items()]
