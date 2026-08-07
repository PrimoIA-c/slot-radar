"""Nettoyage ponctuel : retire les dates inventees lors du bootstrap.

Le bootstrap initial remplacait une date de sortie absente par la date du jour.
Resultat : plusieurs centaines de slots historiques portent la date de l'import
au lieu de leur vraie date de sortie, ce qui est faux et fausse toute analyse.

Ce script remet ces dates a vide et marque leur source comme "inconnue".
Les dates fournies par le provider ne sont jamais touchees.

A lancer une seule fois :
    python -m tools.nettoyer_dates            # apercu, n'ecrit rien
    python -m tools.nettoyer_dates --appliquer
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "data" / "state.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Nettoie les dates inventees")
    parser.add_argument(
        "--appliquer",
        action="store_true",
        help="Ecrit les modifications (sans cette option, simple apercu)",
    )
    args = parser.parse_args()

    if not STATE_FILE.exists():
        print(f"Introuvable : {STATE_FILE}")
        return 1

    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    entries = state.get("entries", {})

    # La date du bootstrap est le plus ancien first_seen enregistre.
    first_seens = [e.get("first_seen") for e in entries.values() if e.get("first_seen")]
    if not first_seens:
        print("Aucune entree exploitable.")
        return 1
    bootstrap_day = min(first_seens)

    # Cible : entrees datees par detection le jour du bootstrap. Les detections
    # posterieures sont de vraies approximations et sont conservees.
    targets = [
        key
        for key, entry in entries.items()
        if entry.get("date_source") == "detection"
        and entry.get("first_seen") == bootstrap_day
    ]

    print(f"Fichier        : {STATE_FILE}")
    print(f"Jour du bootstrap : {bootstrap_day}")
    print(f"Entrees totales   : {len(entries)}")
    print()
    sources = Counter(e.get("date_source", "?") for e in entries.values())
    for name, count in sources.most_common():
        print(f"  {name:<12} {count}")
    print()
    print(f"A nettoyer     : {len(targets)}")

    if not targets:
        print("\nRien a faire.")
        return 0

    print("\nApercu (5 premieres) :")
    for key in targets[:5]:
        entry = entries[key]
        print(
            f"  {entry.get('provider', '?'):<22} {entry.get('name', '?'):<34} "
            f"{entry.get('release_date')} -> (vide)"
        )

    if not args.appliquer:
        print("\nApercu uniquement. Relance avec --appliquer pour ecrire.")
        return 0

    backup = STATE_FILE.with_suffix(".json.bak")
    shutil.copy2(STATE_FILE, backup)
    print(f"\nSauvegarde : {backup}")

    for key in targets:
        entries[key]["release_date"] = None
        entries[key]["date_source"] = "inconnue"

    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{len(targets)} date(s) remises a vide.")
    print("\nRegenere ensuite le classeur :")
    print("    python -m src.main --rebuild --force")
    return 0


if __name__ == "__main__":
    sys.exit(main())
