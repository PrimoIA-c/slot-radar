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


def normalize(raw: dict) -> dict | None:
    """Convertit une entree brute de l'API vers le format interne.

    Renvoie None si l'entree est inexploitable (pas de nom ou pas de provider).
    """
    name = (raw.get("name") or "").strip()
    provider = (raw.get("provider") or "").strip()
    if not name or not provider:
        return None

    slug = (raw.get("slug") or "").strip() or _slugify(name)
    provider_slug = (raw.get("provider_slug") or "").strip() or _slugify(provider)

    if provider_slug.lower() in config.EXCLUDED_PROVIDERS:
        return None

    release_date = raw.get("release_date")
    if isinstance(release_date, str):
        release_date = release_date.strip()[:10] or None
    else:
        release_date = None

    return {
        # Cle de dedoublonnage : stricte, jamais de rapprochement flou.
        # "Sweet Bonanza" et "Sweet Bonanza 1000" sont deux jeux distincts.
        "key": f"{provider_slug}::{slug}",
        "slug": slug,
        "name": name,
        "provider": provider,
        "provider_slug": provider_slug,
        "release_date": release_date,
    }


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
