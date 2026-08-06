"""Point d'entree du radar.

Deroulement d'un run :
  1. Garde anti-doublon : un seul run effectif par jour (heure de Paris)
  2. Bootstrap si premier demarrage, sinon collecte des sorties recentes
  3. Dedoublonnage et mise a jour du state
  4. Regeneration du classeur Excel
  5. Notification Telegram, uniquement le jour configure

Usage :
    python -m src.main
    python -m src.main --notify        # force l'envoi hors du jour prevu
    python -m src.main --dry-run       # n'ecrit rien, n'envoie rien
    python -m src.main --force         # ignore la garde quotidienne
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from . import config, excel, notifier, sources, state

log = logging.getLogger("slot-radar")

WEEKDAYS_FR = [
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
]


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Radar de sorties de slots")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Envoie le recap meme si ce n'est pas le jour configure",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore la garde 'deja execute aujourd'hui'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule le run sans ecrire ni envoyer quoi que ce soit",
    )
    parser.add_argument(
        "--no-backfill",
        action="store_true",
        help="Au bootstrap, n'importe pas l'historique dans l'Excel",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    now = datetime.now(config.TZ)
    today = now.date()
    log.info(
        "Run du %s %s (%s)",
        WEEKDAYS_FR[today.weekday()],
        today.strftime("%d/%m/%Y"),
        now.strftime("%H:%M %Z"),
    )

    exit_code = 0
    current = state.load()

    # Garde quotidienne. Le cron tourne a deux heures UTC pour couvrir le
    # changement d'heure : sans cette garde, le second creneau relancerait un
    # run inutile. Elle resiste aussi aux retards de planification de GitHub,
    # contrairement a un simple controle de l'heure locale.
    if current["last_run"] == today.isoformat() and not args.force:
        log.info("Deja execute aujourd'hui, rien a faire. (--force pour passer outre)")
        return 0

    is_bootstrap = not current["entries"]

    # --- Collecte ----------------------------------------------------------
    if is_bootstrap:
        log.info("Premier demarrage : import du catalogue complet")
        incoming = sources.fetch_catalogue()
        if not incoming:
            log.error("Catalogue vide, bootstrap annule pour ne pas figer un state vide")
            return 1
        # Tout le catalogue existant est marque comme deja notifie : le bot ne
        # doit pas annoncer 6 000 slots historiques a son premier lundi.
        added = state.merge(current, incoming, today, notified=True)
        if args.no_backfill or not config.BOOTSTRAP_BACKFILL:
            log.info("Backfill Excel desactive : seul le state est initialise")
    else:
        incoming = sources.fetch_recent()
        added = state.merge(current, incoming, today, notified=False)

    # --- Excel -------------------------------------------------------------
    entries = state.all_entries(current)
    if is_bootstrap and (args.no_backfill or not config.BOOTSTRAP_BACKFILL):
        entries = []

    if args.dry_run:
        log.info("[dry-run] Excel non ecrit (%s lignes auraient ete generees)", len(entries))
    else:
        excel.build(entries)

    # --- Notification ------------------------------------------------------
    should_notify = args.notify or today.weekday() == config.NOTIFY_WEEKDAY
    waiting = state.pending(current)

    if is_bootstrap:
        log.info(
            "Bootstrap termine : %s slots enregistres. Les nouveautes seront "
            "annoncees a partir du prochain %s.",
            len(added),
            WEEKDAYS_FR[config.NOTIFY_WEEKDAY],
        )
    elif not should_notify:
        log.info(
            "Pas le jour d'envoi (%s attendu). %s entree(s) en attente.",
            WEEKDAYS_FR[config.NOTIFY_WEEKDAY],
            len(waiting),
        )
    elif not waiting:
        log.info("Jour d'envoi, mais aucune nouveaute a annoncer. Silence.")
    elif args.dry_run:
        log.info("[dry-run] %s entree(s) auraient ete annoncees", len(waiting))
        print("\n--- Apercu du message ---")
        print(notifier.build_message(waiting, today))
    elif not notifier.is_configured():
        log.warning(
            "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID absent : envoi impossible. "
            "Les %s entrees restent en attente pour le prochain run.",
            len(waiting),
        )
    else:
        try:
            notifier.send_message(notifier.build_message(waiting, today))
            if config.SEND_EXCEL:
                notifier.send_document(
                    config.EXCEL_FILE,
                    caption=f"Historique complet — {len(entries)} sorties",
                )
        except notifier.NotifierError as exc:
            # Echec non fatal : on laisse les entrees en attente et on persiste
            # quand meme le state, sinon la collecte du jour serait perdue.
            log.error("Envoi echoue (%s). %s entree(s) reportees.", exc, len(waiting))
            exit_code = 1
        else:
            # Marquage seulement apres succes complet.
            state.mark_notified(current, waiting)
            log.info("%s entree(s) annoncees et marquees", len(waiting))

    # --- Persistance -------------------------------------------------------
    current["last_run"] = today.isoformat()
    if args.dry_run:
        log.info("[dry-run] state.json non ecrit")
    else:
        state.save(current)

    log.info("Termine : %s ajout(s) ce run", len(added))
    return exit_code


def main() -> int:
    _setup_logging()
    args = _parse_args()
    try:
        return run(args)
    except sources.SourceError as exc:
        log.error("Source indisponible : %s", exc)
        return 1
    except notifier.NotifierError as exc:
        log.error("Notification echouee : %s", exc)
        return 1
    except Exception:
        log.exception("Erreur inattendue")
        return 1


if __name__ == "__main__":
    sys.exit(main())
