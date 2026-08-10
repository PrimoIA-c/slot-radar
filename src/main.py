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
        "--rebuild",
        action="store_true",
        help="Reconstruit le classeur a zero (efface les ajouts manuels)",
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
    current = state.load(today)

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
        # estimate_dates=False : sur un import historique, une date manquante
        # reste vide plutot que d'etre remplacee par la date du jour.
        added = state.merge(
            current, incoming, today, notified=True, estimate_dates=False
        )
        if args.no_backfill or not config.BOOTSTRAP_BACKFILL:
            log.info("Backfill Excel desactive : seul le state est initialise")
    else:
        incoming = sources.fetch_recent()
        added = state.merge(current, incoming, today, notified=False)

        # Rafraichissement hebdomadaire : le flux quotidien ne montre que les
        # 30 dernieres sorties. Un passage sur le catalogue complet recupere
        # les dates publiees apres coup et rattrape toute sortie que la fenetre
        # des 30 aurait laissee passer lors d'une semaine chargee.
        if config.REFRESH_WEEKDAY >= 0 and today.weekday() == config.REFRESH_WEEKDAY:
            log.info("Rafraichissement hebdomadaire du catalogue complet")
            try:
                catalogue = sources.fetch_catalogue()
            except sources.SourceError as exc:
                # Non bloquant : la collecte du jour a deja reussi.
                log.warning("Rafraichissement impossible (%s), on continue", exc)
            else:
                state.refresh_dates(current, catalogue)
                rattrapage = state.merge(current, catalogue, today, notified=False)
                if rattrapage:
                    log.info("%s sortie(s) rattrapee(s) hors fenetre", len(rattrapage))
                    added += rattrapage

    # --- Excel -------------------------------------------------------------
    entries = state.all_entries(current)
    if is_bootstrap and (args.no_backfill or not config.BOOTSTRAP_BACKFILL):
        entries = []

    if args.dry_run:
        log.info("[dry-run] Classeur non modifie (%s lignes connues)", len(entries))
    elif args.rebuild:
        log.warning(
            "--rebuild : le classeur est reconstruit a zero. Les colonnes et la "
            "mise en forme ajoutees a la main seront perdues."
        )
        excel.rebuild(entries)
    elif is_bootstrap:
        excel.rebuild(entries)
    else:
        # Ajout pur : rien de ce qui est deja dans le fichier n'est reecrit.
        excel.sync(entries)

    # --- Notification ------------------------------------------------------
    is_weekly = args.notify or today.weekday() == config.NOTIFY_WEEKDAY
    released = state.pending_released(current, today)
    upcoming = state.pending_upcoming(current, today)

    # Les sorties effectives peuvent partir tous les jours ; les annonces
    # attendent le recap hebdomadaire. Les jours sans sortie, les deux listes
    # sont vides et le bot ne dit rien.
    send_released = released if (is_weekly or config.DAILY_RELEASES) else []
    send_upcoming = upcoming if is_weekly else []
    waiting = send_released + send_upcoming
    should_notify = bool(waiting)

    if is_bootstrap:
        log.info(
            "Bootstrap termine : %s slots enregistres. Les nouveautes seront "
            "annoncees a partir du prochain %s.",
            len(added),
            WEEKDAYS_FR[config.NOTIFY_WEEKDAY],
        )
    elif not waiting:
        log.info(
            "Rien a annoncer aujourd'hui. Silence. "
            "(%s sortie(s) et %s annonce(s) en attente du recap)",
            len(released) - len(send_released),
            len(upcoming) - len(send_upcoming),
        )
    elif args.dry_run:
        log.info(
            "[dry-run] %s sortie(s) et %s a venir auraient ete annoncees",
            len(send_released),
            len(send_upcoming),
        )
        print("\n--- Apercu du message ---")
        print(notifier.build_message(send_released, send_upcoming, today, is_weekly))
    elif not notifier.is_configured():
        log.warning(
            "TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID absent : envoi impossible. "
            "Les %s entrees restent en attente pour le prochain run.",
            len(waiting),
        )
    else:
        try:
            notifier.send_message(
                notifier.build_message(send_released, send_upcoming, today, is_weekly)
            )
            # Le classeur n'accompagne que le recap hebdomadaire : l'envoyer a
            # chaque alerte quotidienne serait inutilement lourd.
            if config.SEND_EXCEL and is_weekly:
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
            # Marquage seulement apres succes complet. Les sorties effectives
            # portent les deux drapeaux ; une annonce anticipee ne pose que
            # `notified`, pour que la slot soit resignalee le jour de sa sortie.
            state.mark_notified(current, send_released, released=True)
            state.mark_notified(current, send_upcoming)
            log.info(
                "%s sortie(s) et %s a venir annoncees",
                len(send_released),
                len(send_upcoming),
            )

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
