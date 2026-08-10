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

from . import config, sources, taxonomy

log = logging.getLogger(__name__)

SCHEMA_VERSION = 3


def empty_state() -> dict:
    return {"version": SCHEMA_VERSION, "last_run": None, "entries": {}}


def load(today: date | None = None) -> dict:
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
    _migrate(state)
    _migrate_v3(state, today or date.today())
    log.info("state.json charge : %s entrees connues", len(state["entries"]))
    return state


def _migrate(state: dict) -> None:
    """Met le state au schema courant.

    v1 -> v2 : le champ `mechanic` stockait le libelle brut de l'API. Il
    contient desormais la valeur normalisee, le brut passant dans
    `mechanic_raw`. L'enrichissement ordinaire ne comble que les champs vides
    et ne corrigerait donc jamais ces valeurs : la reprise doit etre explicite.
    """
    if state.get("version", 1) >= 2:
        return

    converted = 0
    extracted = 0
    for entry in state["entries"].values():
        raw = entry.get("mechanic_raw") or entry.get("mechanic")
        entry["mechanic_raw"] = raw
        normalized = taxonomy.gain_system(raw)
        if normalized != entry.get("mechanic"):
            entry["mechanic"] = normalized
            converted += 1
        if entry.get("paylines") is None:
            count = taxonomy.line_count(raw)
            if count is not None:
                entry["paylines"] = count
                extracted += 1

    state["version"] = 2
    log.info(
        "Migration v2 : %s gain system normalise(s), %s lignes/ways extraites",
        converted,
        extracted,
    )


def _migrate_v3(state: dict, today: date) -> None:
    """v2 -> v3 : distingue l'annonce de la sortie effective.

    L'endpoint des nouveautes reference les jeux des leur annonce, souvent
    des semaines avant leur mise en ligne. Un seul drapeau `notified` ne
    permettait donc d'annoncer une slot qu'une fois, a l'annonce, et jamais
    le jour de sa sortie reelle.

    `released_notified` comble ce trou. Les entrees deja passees sont
    considerees comme annoncees pour ne pas rejouer tout l'historique ; celles
    datees dans le futur restent en attente de leur sortie.
    """
    if state.get("version", 1) >= 3:
        return

    waiting = 0
    iso_today = today.isoformat()
    for entry in state["entries"].values():
        release = entry.get("release_date")
        already_out = release is None or release <= iso_today
        entry["released_notified"] = already_out
        if not already_out:
            waiting += 1

    state["version"] = 3
    log.info(
        "Migration v3 : %s sortie(s) a venir en attente de leur date", waiting
    )


def save(state: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True)
    config.STATE_FILE.write_text(payload + "\n", encoding="utf-8")
    log.info("state.json ecrit : %s entrees", len(state["entries"]))


def merge(
    state: dict,
    incoming: list[dict],
    today: date,
    notified: bool,
    estimate_dates: bool = True,
) -> list[dict]:
    """Insere les entrees inconnues dans le state.

    Le critere de nouveaute est l'absence de la cle dans le state, jamais la
    date de sortie : la couverture du champ release_date est partielle et une
    slot peut entrer dans la base plusieurs jours apres son lancement. Filtrer
    sur une fenetre de dates ferait passer ces entrees a la trappe.

    `notified` a True lors du bootstrap : on enregistre le catalogue existant
    sans declencher une notification de plusieurs milliers de lignes.

    `estimate_dates` a False lors du bootstrap. En veille quotidienne, une
    entree sans date publiee vient d'etre detectee : la date du jour est une
    approximation raisonnable, marquee comme telle. Au bootstrap au contraire,
    on importe un catalogue historique : dater du jour un jeu sorti en 2015
    serait une invention pure. Dans ce cas la date reste vide.

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
        if release_date:
            stored_date, source = release_date, "provider"
        elif estimate_dates:
            stored_date, source = iso_today, "detection"
        else:
            stored_date, source = None, "inconnue"

        state["entries"][key] = {
            "name": entry["name"],
            "provider": entry["provider"],
            "provider_slug": entry["provider_slug"],
            "release_date": stored_date,
            "date_source": source,
            "first_seen": iso_today,
            "notified": notified,
            # Une entree datee dans le futur reste a annoncer le jour venu.
            "released_notified": notified
            or (stored_date is None or stored_date <= iso_today),
            **{field: entry.get(field) for field in sources.ATTRIBUTES},
        }
        added.append({"key": key, **state["entries"][key]})

    if added:
        log.info("%s nouvelles entrees", len(added))
    return added


def _refresh_release_date(stored: dict, incoming: dict) -> None:
    """Complete une date estimee ou absente si l'API finit par la publier."""
    if stored.get("date_source") in {"detection", "inconnue"} and incoming.get(
        "release_date"
    ):
        stored["release_date"] = incoming["release_date"]
        stored["date_source"] = "provider"
    _refresh_attributes(stored, incoming)


def _refresh_attributes(stored: dict, incoming: dict) -> int:
    """Renseigne les caracteristiques encore absentes.

    Une valeur deja connue n'est jamais remplacee : on ne fait que combler
    les trous, au fur et a mesure que slot.report enrichit sa base.
    """
    filled = 0
    for field in sources.ATTRIBUTES:
        if stored.get(field) is None and incoming.get(field) is not None:
            stored[field] = incoming[field]
            filled += 1
    return filled


def pending_released(state: dict, today: date) -> list[dict]:
    """Slots effectivement sorties et pas encore annoncees comme telles."""
    iso_today = today.isoformat()
    return [
        {"key": key, **value}
        for key, value in state["entries"].items()
        if not value.get("released_notified")
        and (value.get("release_date") is None or value["release_date"] <= iso_today)
    ]


def pending_upcoming(state: dict, today: date) -> list[dict]:
    """Sorties annoncees pour plus tard, pas encore signalees."""
    iso_today = today.isoformat()
    return [
        {"key": key, **value}
        for key, value in state["entries"].items()
        if not value.get("notified")
        and value.get("release_date")
        and value["release_date"] > iso_today
    ]


def mark_notified(state: dict, entries: list[dict], released: bool = False) -> None:
    """Marque les entrees annoncees.

    Le marquage n'a lieu qu'apres un envoi reussi : un echec laisse tout en
    attente et le run suivant reprend l'arriere.
    """
    for entry in entries:
        stored = state["entries"].get(entry["key"])
        if stored is None:
            continue
        stored["notified"] = True
        if released:
            stored["released_notified"] = True


def refresh_dates(state: dict, catalogue: list[dict]) -> int:
    """Complete les dates devenues disponibles chez le provider.

    Le flux quotidien ne voit que les 30 dernieres sorties : une date publiee
    apres coup sur un jeu plus ancien ne serait jamais recuperee. Ce passage
    hebdomadaire sur le catalogue complet corrige ca.

    Seules les entrees sans date fiable sont touchees. Une date deja fournie
    par le provider n'est jamais ecrasee.

    Renvoie le nombre de dates completees.
    """
    filled = 0
    attrs = 0
    for entry in catalogue:
        stored = state["entries"].get(entry["key"])
        if stored is None:
            continue
        attrs += _refresh_attributes(stored, entry)
        if stored.get("date_source") == "provider":
            continue
        if not entry.get("release_date"):
            continue
        stored["release_date"] = entry["release_date"]
        stored["date_source"] = "provider"
        filled += 1

    if filled or attrs:
        log.info(
            "Catalogue : %s date(s) et %s caracteristique(s) completees",
            filled,
            attrs,
        )
    return filled


def all_entries(state: dict) -> list[dict]:
    return [{"key": key, **value} for key, value in state["entries"].items()]
