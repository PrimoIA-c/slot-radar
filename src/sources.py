"""Client de l'API slot.report.

Deux endpoints sont utilises :
  - /new.json    : les 30 sorties les plus recentes (usage quotidien)
  - /slots.json  : le catalogue complet (usage unique, au bootstrap)

Les reponses sont normalisees vers un dictionnaire stable, independant
d'eventuels changements de schema cote API.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """Erreur irrecuperable lors de la recuperation des donnees."""


def _headers() -> dict[str, str]:
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
    }
    if config.API_KEY:
        # L'API delivre une cle liee au domaine. Le nom exact de l'en-tete est
        # a confirmer lors de la generation de la cle : on envoie les deux
        # variantes courantes, un en-tete inconnu est ignore sans dommage.
        headers["X-API-Key"] = config.API_KEY
        headers["Authorization"] = f"Bearer {config.API_KEY}"
    return headers


def _get(path: str, retries: int = 3) -> Any:
    url = f"{config.API_BASE}/{path.lstrip('/')}"
    params = {"key": config.API_KEY} if config.API_KEY else None
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url,
                headers=_headers(),
                params=params,
                timeout=config.HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_error = exc
            log.warning("Tentative %s/%s echouee (%s)", attempt, retries, exc)
            time.sleep(2 * attempt)
            continue

        if response.status_code == 403:
            raise SourceError(
                "403 refuse par slot.report. L'API exige un lien dofollow actif "
                "vers slot.report, verifie par domaine, ou une cle liee au "
                "domaine. Voir la section 'Acces API' du README."
            )
        if response.status_code == 429:
            wait = int(response.headers.get("Retry-After", 30))
            log.warning("429 recu, pause de %ss", wait)
            time.sleep(wait)
            continue
        if response.status_code >= 500:
            last_error = SourceError(f"HTTP {response.status_code}")
            log.warning("Erreur serveur %s, nouvel essai", response.status_code)
            time.sleep(2 * attempt)
            continue

        response.raise_for_status()
        return response.json()

    raise SourceError(f"Echec de {url} apres {retries} tentatives : {last_error}")


def _results(payload: Any) -> list[dict]:
    """Extrait la liste de resultats, quel que soit l'emballage renvoye."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for field in ("results", "data", "slots", "items"):
            value = payload.get(field)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise SourceError(f"Format de reponse inattendu : {type(payload).__name__}")


_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    return _SLUG_CLEAN.sub("-", value.lower()).strip("-")


_GRID = re.compile(r"^\s*(\d+)\s*[xX*]\s*(\d+)\s*$")


def _grid_parts(value) -> tuple[int | None, int | None]:
    """Decoupe une grille "5x3" en (reels, rows)."""
    if not isinstance(value, str):
        return None, None
    match = _GRID.match(value)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _number(value):
    """Renvoie un nombre exploitable, ou None si la valeur est inutilisable."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _text(value) -> str | None:
    """Convertit en texte exploitable, quel que soit le type recu.

    L'API n'est pas homogene : un meme champ peut arriver en chaine, en
    entier, en liste ou a null selon l'endpoint et l'anciennete de la fiche.
    On absorbe tout plutot que de planter sur un cas particulier.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple, set)):
        parts = [t for t in (_text(item) for item in value) if t]
        return ", ".join(dict.fromkeys(parts)) or None
    if isinstance(value, str):
        return value.strip() or None
    return str(value).strip() or None


def _label(value) -> str | None:
    """Normalise un libelle : "very-high" devient "Very high"."""
    text = _text(value)
    if not text:
        return None
    cleaned = text.replace("_", " ").replace("-", " ")
    return cleaned[0].upper() + cleaned[1:]


def normalize(raw: dict) -> dict | None:
    """Convertit une entree brute de l'API vers le format interne.

    Renvoie None si l'entree est inexploitable (pas de nom ou pas de provider).
    """
    name = _text(raw.get("name")) or ""
    provider = _text(raw.get("provider")) or ""
    if not name or not provider:
        return None

    slug = _text(raw.get("slug")) or _slugify(name)
    provider_slug = _text(raw.get("provider_slug")) or _slugify(provider)

    if provider_slug.lower() in config.EXCLUDED_PROVIDERS:
        return None

    release_date = _text(raw.get("release_date"))
    release_date = release_date[:10] if release_date else None

    reels, rows = _grid_parts(raw.get("grid"))

    return {
        # Cle de dedoublonnage : stricte, jamais de rapprochement flou.
        # "Sweet Bonanza" et "Sweet Bonanza 1000" sont deux jeux distincts.
        "key": f"{provider_slug}::{slug}",
        "slug": slug,
        "name": name,
        "provider": provider,
        "provider_slug": provider_slug,
        "release_date": release_date,
        # --- Caracteristiques ---------------------------------------------
        "rtp": _number(raw.get("rtp")),
        "volatility": _label(raw.get("volatility")),
        "max_win": _number(raw.get("max_win")),
        "mechanic": _text(raw.get("mechanic")),
        "reels": reels,
        "rows": rows,
        "paylines": _number(raw.get("paylines")),
    }


# Champs de caracteristiques, utilises par le state pour l'enrichissement.
ATTRIBUTES = ("rtp", "volatility", "max_win", "mechanic", "reels", "rows", "paylines")


def _normalize_all(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        entry = normalize(row)
        if entry is None or entry["key"] in seen:
            continue
        seen.add(entry["key"])
        out.append(entry)
    return out


def fetch_recent() -> list[dict]:
    """Les sorties les plus recentes. Appel quotidien."""
    rows = _results(_get("new.json"))
    entries = _normalize_all(rows)
    log.info("new.json : %s entrees exploitables", len(entries))
    return entries


def fetch_catalogue() -> list[dict]:
    """Le catalogue complet. Appele uniquement au bootstrap."""
    rows = _results(_get("slots.json"))
    entries = _normalize_all(rows)
    log.info("slots.json : %s entrees exploitables", len(entries))
    return entries
