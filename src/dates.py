"""Normalisation des dates de sortie publiees par slot.report.

L'API ne publie pas toujours en ISO. Releve sur le state du 16/09/2026,
1781 entrees sur 6350 ne sont pas au format AAAA-MM-JJ :

    886  "20-04-2021"
    619  "13.07.2023"
    250  "2019"
     26  "2019-02"

Tant que ces valeurs circulent telles quelles, toute comparaison de dates
ment. `"31.10.2024" <= "2026-09-16"` est faux : la slot n'entre jamais dans
`pending_released()`, reste a `released_notified: false` pour toujours, et
n'est donc jamais annoncee le jour de sa sortie. Le meme texte fait echouer
`datetime.strptime` dans excel.py, d'ou la cellule de date vide.

Le jour vient en premier dans les formes completes. Ce n'est pas une
supposition : sur les 1505 dates completes du state, 882 ont un premier
nombre superieur a 12, et aucune n'a un second nombre superieur a 12. Une
lecture mois-jour serait impossible sur 882 d'entre elles et n'est appuyee
par aucune.

Une date partielle n'est jamais completee. "2019" ne devient pas le
1er janvier 2019 : la fonction renvoie None et l'appelant conserve la valeur
publiee pour l'affichage. Une date inventee finirait recopiee dans le
dashboard, puis dans un test vendu a un provider.

Note : app/store.py porte une fonction equivalente, volontairement laissee en
place. L'atelier tourne tous les jours et ne depend pas du paquet du bot ;
les faire dependre l'un de l'autre pour trente lignes couterait plus que la
duplication. Une fois le state migre, celle de l'atelier ne voit plus que de
l'ISO et se contente de le laisser passer.
"""

from __future__ import annotations

import re
from datetime import date

# L'ISO est acceptee en prefixe : certaines fiches portent une heure derriere.
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# Jour-mois-annee, separe par un point, un tiret ou une barre.
_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})")

# Annee-mois-jour avec des separateurs inhabituels : "2019.7.1".
_YMD_LOOSE = re.compile(r"^(\d{4})[./](\d{1,2})[./](\d{1,2})")


def _build(year, month, day) -> str | None:
    """Construit la date, ou None si elle n'existe pas.

    Passer par datetime elimine ce qu'une expression reguliere laisse entrer :
    un mois 13, un 31 fevrier, un jour 32. Mieux vaut une date absente qu'une
    date fausse propagee jusqu'au dashboard.
    """
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def to_iso(value) -> str | None:
    """Ramene une date en AAAA-MM-JJ, ou None si elle est inexploitable."""
    if not value:
        return None
    text = str(value).strip()

    match = _ISO.match(text)
    if match:
        return _build(*match.groups())

    match = _DMY.match(text)
    if match:
        day, month, year = match.groups()
        return _build(year, month, day)

    match = _YMD_LOOSE.match(text)
    if match:
        return _build(*match.groups())

    return None


def split(value) -> tuple[str | None, str | None]:
    """Renvoie (date ISO, valeur publiee a conserver).

    La valeur publiee n'est conservee que lorsqu'elle n'a pas pu etre
    convertie : c'est alors la seule information qui reste, et l'atelier
    l'affiche. Une date convertie sans ambiguite n'a rien a signaler ;
    la garder ferait afficher un avertissement "verifie-la" sur 1505 fiches
    sans qu'il y ait quoi que ce soit a verifier.
    """
    if not value:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    iso = to_iso(text)
    return iso, (text if iso is None else None)
