"""Envoi du recap sur Telegram."""

from __future__ import annotations

import html
import logging
from datetime import date, datetime
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

TELEGRAM_MAX_CHARS = 4096
SAFE_CHUNK = 3900  # marge pour ne jamais couper au ras de la limite


class NotifierError(RuntimeError):
    """Echec d'envoi. Les entrees concernees restent non notifiees."""


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/{method}"


def is_configured() -> bool:
    return bool(config.TELEGRAM_TOKEN and config.TELEGRAM_CHAT_ID)


def _post(method: str, **kwargs) -> dict:
    response = requests.post(_api(method), timeout=config.HTTP_TIMEOUT, **kwargs)
    try:
        payload = response.json()
    except ValueError:
        raise NotifierError(f"{method} : reponse illisible (HTTP {response.status_code})")

    if not payload.get("ok"):
        raise NotifierError(
            f"{method} refuse par Telegram : "
            f"{payload.get('description', 'raison inconnue')}"
        )
    return payload


def _fr_date(value: str | None) -> str:
    if not value:
        return "?"
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return value


def _provider_block(entries: list[dict]) -> list[str]:
    """Groupe par provider, providers alphabetiques, dates croissantes."""
    lines: list[str] = []
    by_provider: dict[str, list[dict]] = {}
    for entry in entries:
        by_provider.setdefault(entry.get("provider", "Inconnu"), []).append(entry)

    for provider in sorted(by_provider, key=str.casefold):
        lines.append(f"<b>{html.escape(provider)}</b>")
        rows = sorted(
            by_provider[provider],
            key=lambda item: (item.get("release_date") or "9999", item.get("name", "")),
        )
        for entry in rows:
            source = entry.get("date_source")
            if source == "inconnue" or not entry.get("release_date"):
                label, marker = "date inconnue", ""
            else:
                label = _fr_date(entry.get("release_date"))
                # Une date deduite de la detection n'a pas la fiabilite d'une
                # date publiee par le provider.
                marker = "~" if source == "detection" else ""
            lines.append(f"  {marker}{label} — {html.escape(entry.get('name', '?'))}")
        lines.append("")
    return lines


def build_message(
    released: list[dict], upcoming: list[dict], today: date, weekly: bool = True
) -> str:
    """Compose le message.

    Deux sections possibles : les slots effectivement sorties, et celles
    annoncees pour plus tard. L'alerte quotidienne ne porte que la premiere,
    le recap hebdomadaire porte les deux.
    """
    if weekly:
        title = f"Sorties slots — semaine du {today.strftime('%d/%m/%Y')}"
    else:
        title = f"Sortie du jour — {today.strftime('%d/%m/%Y')}"
    lines = [f"<b>{title}</b>", ""]

    if released:
        plural = "s" if len(released) > 1 else ""
        # En alerte quotidienne le titre porte deja l'information : pas
        # besoin d'un en-tete de section pour une seule liste.
        if weekly:
            lines.append(f"<b>SORTIES ({len(released)})</b>")
            lines.append(f"<i>Disponible{plural} maintenant</i>")
            lines.append("")
        lines += _provider_block(released)

    if upcoming:
        lines.append(f"<b>A VENIR ({len(upcoming)})</b>")
        lines.append("<i>Annoncees, pas encore disponibles</i>")
        lines.append("")
        lines += _provider_block(upcoming)

    if any(
        e.get("date_source") == "detection" for e in list(released) + list(upcoming)
    ):
        lines.append("<i>~ date de detection, non publiee par le provider</i>")

    return "\n".join(lines).strip()


def _chunks(text: str) -> list[str]:
    """Decoupe le message sur des sauts de ligne, jamais en plein milieu."""
    if len(text) <= TELEGRAM_MAX_CHARS:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for line in text.split("\n"):
        # Ligne unique demesuree : on la tronque plutot que de casser l'envoi.
        if len(line) > SAFE_CHUNK:
            line = line[: SAFE_CHUNK - 3] + "..."
        if size + len(line) + 1 > SAFE_CHUNK and current:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1

    if current:
        chunks.append("\n".join(current))
    return chunks


def send_message(text: str) -> None:
    for index, chunk in enumerate(_chunks(text), start=1):
        _post(
            "sendMessage",
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        log.info("Message %s envoye (%s caracteres)", index, len(chunk))


def send_document(path: Path, caption: str = "") -> None:
    if not path.exists():
        log.warning("Fichier absent, envoi ignore : %s", path)
        return

    with path.open("rb") as handle:
        _post(
            "sendDocument",
            data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption[:1024]},
            files={"document": (path.name, handle)},
        )
    log.info("Document envoye : %s", path.name)
