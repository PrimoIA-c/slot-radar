"""Normalisation du champ `mechanic` de slot.report.

Ce champ melange trois natures de donnees : de vrais systemes de gain, mais
aussi des types de jeu (Video Slots, roulette), des features bonus (Hold and
Win, Coin Collect), des mecaniques proprietaires (xWays, DoubleMax) et parfois
des themes (Sci Fi, Mythologies). Environ 200 valeurs distinctes au total.

Ce module ramene ce qui est ramenable vers la taxonomie officielle, et renvoie
None pour tout le reste. La valeur brute reste conservee dans le classeur :
aucune information n'est perdue, et toute correspondance reste auditable.

Regle de conduite : ne jamais deviner. Une valeur qui ne correspond pas
clairement a un systeme de gain vaut mieux vide que mal classee.
"""

from __future__ import annotations

import re

# Taxonomie officielle, telle qu'affichee par le filtre de slot.report.
FIXED_LINE = "FixedLine"
WAYS_TO_WIN = "WaysToWin"
MEGAWAYS = "Megaways"
CLUSTER_PAYS = "ClusterPays"
PAY_ANYWHERE = "PayAnywhere"
INFINITY_REELS = "InfinityReels"
GIGABLOX = "Gigablox"
SPLITZ = "Splitz"
BOTH_WAYS = "BothWays"


def _key(value: str) -> str:
    """Forme canonique : minuscules, sans ponctuation, espaces normalises."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


# --- Correspondances explicites -------------------------------------------
# Valeurs dont le rattachement ne fait aucun doute.

EXACT: dict[str, str] = {}


def _register(target: str, *values: str) -> None:
    for value in values:
        EXACT[_key(value)] = target


_register(
    FIXED_LINE,
    "paylines", "payline", "Lines", "Standard", "Standard Reels", "reel",
    "reels", "Fixed paylines", "Payline-based", "Classic", "Pennyslot",
    "5-reel with multiple paylines", "5x3 reel layout with multiple paylines",
    "Standard reel-based with bonus rounds and retriggerable free spins",
)
_register(
    WAYS_TO_WIN,
    "Ways", "Ways to Win", "trueways", "everyway", "hyperways", "ExtraWays",
    "Push Ways", "Ways (variable)", "27 ways to win", "1ways",
)
_register(MEGAWAYS, "megaways", "Megaquads")
_register(
    CLUSTER_PAYS,
    "cluster", "Cluster Pays", "cluster-pays", "Clusters", "Megaclusters",
    "Cluster-based wins", "cluster pays with cascading wins",
)
_register(
    PAY_ANYWHERE,
    "Pays Anywhere", "Pay Anywhere", "pay-anywhere", "scatter-pays",
    "Scatter Pays", "scatter",
)
_register(INFINITY_REELS, "Infinity Reels", "infinireels", "Spinfinity")
_register(GIGABLOX, "Gigablox")
_register(SPLITZ, "Splitz")
_register(BOTH_WAYS, "Both-Way", "Both Ways", "both way")

# --- Exclusions ------------------------------------------------------------
# Valeurs contenant un mot-cle piegeur mais qui ne sont pas des systemes de
# gain : sans cette liste, la detection par mot-cle les classerait a tort.

EXCLUDED = {
    _key(v)
    for v in (
        # Mecaniques proprietaires bati sur un systeme, pas un systeme
        "xways", "DoubleMax", "MultiMAX", "DuoMax", "Megadozer", "Megapays",
        "Megapots", "power-reels", "Push Originals", "Push Actions",
        "OnlyWins", "RushingWilds", "LightningLines", "WildEnergy", "xpays",
        "HexaPays", "TopHit", "TapCards", "collectr", "Book of", "Triple Reaction",
        # Features bonus
        "Expanding Reels", "Cascading Reels", "Expanding Grid", "Coin Collect",
        "Cash Collect", "Feature Drop", "sticky wilds", "link-and-win",
        "Hold and Ride", "Hold and Respin", "Stash and Spin", "Win Exchange",
        "Wild Fight", "Reel Hot Games", "Retrigger mechanics in Free Spins",
        # Types de jeu
        "Video Slots", "Video Slot",
    )
}

# Types de jeu et mecaniques cascade : ecartes sur decision explicite.
EXCLUDED |= {
    _key(v)
    for v in (
        "cascading", "avalanche", "tumble", "Cascade", "Cascading / Avalanche",
        "Cascading / Expanding", "tumble/cascade",
    )
}

# --- Detection par mot-cle -------------------------------------------------
# Ordre significatif : "megaways" doit passer avant "ways", "cluster" avant
# le reste. Applique seulement si aucune correspondance exacte n'a repondu.

KEYWORDS: list[tuple[str, str]] = [
    ("megaways", MEGAWAYS),
    ("gigablox", GIGABLOX),
    ("splitz", SPLITZ),
    ("infinity reels", INFINITY_REELS),
    ("infinireels", INFINITY_REELS),
    ("cluster", CLUSTER_PAYS),
    ("both way", BOTH_WAYS),
    ("pay anywhere", PAY_ANYWHERE),
    ("pays anywhere", PAY_ANYWHERE),
    ("scatter pay", PAY_ANYWHERE),
    ("ways", WAYS_TO_WIN),
    ("payline", FIXED_LINE),
    ("paylines", FIXED_LINE),
    ("playlines", FIXED_LINE),
    ("unique lines", FIXED_LINE),
    ("winning lines", FIXED_LINE),
]


def gain_system(raw) -> str | None:
    """Ramene une valeur brute vers la taxonomie, ou None si non classable."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    key = _key(text)
    if not key or key in EXCLUDED:
        return None

    if key in EXACT:
        return EXACT[key]

    # Un nombre seul designe un nombre de lignes : "10", "20", "243".
    if re.fullmatch(r"\d+", key):
        return FIXED_LINE

    # Une liste de nombres designe des configurations de lignes : "5, 10, 20".
    if re.fullmatch(r"\d+(?: \d+)+", key):
        return FIXED_LINE

    for needle, target in KEYWORDS:
        if needle in key:
            return target

    return None


# --- Extraction du nombre de lignes / ways ---------------------------------

# "243 Ways", "15 Paylines", "1 000 000 Ways", "7,776 Ways",
# et les tournures ou un mot s'intercale : "14 winning lines", "24 unique lines".
_COUNT = re.compile(
    r"(\d[\d\s,\u00a0]*\d|\d)\s*(?:\w+\s+){0,2}(?:ways|paylines?|lines)\b",
    re.IGNORECASE,
)
# Plages et alternatives : trop ambigu pour en tirer une valeur unique.
_RANGE = re.compile(r"\d[\d\s,.]*\s*(?:-|–|to|up to)\s*\d", re.IGNORECASE)
_LIST = re.compile(r"\d\s*,\s+\d")


def line_count(raw) -> int | None:
    """Extrait un nombre de lignes ou de ways depuis le libelle brut.

    Renvoie None sur les plages ("243-43 923 Ways") et les alternatives
    ("5, 10, 20") : une valeur unique y serait arbitraire.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or _RANGE.search(text) or _LIST.search(text):
        return None
    # "up to N" designe un maximum, pas une valeur fixe.
    if "up to" in text.lower():
        return None

    if re.fullmatch(r"\d+", text):
        return int(text)

    match = _COUNT.search(text)
    if not match:
        return None

    digits = re.sub(r"[^\d]", "", match.group(1))
    if not digits:
        return None
    value = int(digits)
    # Garde-fou : au-dela, on est sur un artefact de parsing.
    return value if 1 <= value <= 10_000_000 else None
