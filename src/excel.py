"""Generation du classeur Excel.

Le classeur est integralement reconstruit a chaque run depuis state.json.
C'est volontaire : pas d'ecriture incrementale, donc pas de derive possible
entre le state et le fichier, et un classeur supprime par erreur se regenere
tout seul au run suivant.

Structure : un onglet "Toutes sorties" en tete, puis un onglet par provider.
Colonnes : Date de sortie | Provider | Slot
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from . import config

log = logging.getLogger(__name__)

HEADERS = ["Date de sortie", "Provider", "Slot"]
GLOBAL_SHEET = "Toutes sorties"
DATE_FORMAT = "DD/MM/YYYY"

HEADER_FILL = PatternFill("solid", fgColor="1F2933")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=11)
COLUMN_WIDTHS = [16, 26, 44]

# Caracteres interdits par Excel dans un nom d'onglet.
_ILLEGAL_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")


def _sheet_name(provider: str, taken: set[str]) -> str:
    """Nom d'onglet valide : 31 caracteres max, sans caractere interdit, unique."""
    cleaned = _ILLEGAL_SHEET_CHARS.sub("-", provider).strip() or "Inconnu"
    candidate = cleaned[:31]

    if candidate.lower() not in taken:
        taken.add(candidate.lower())
        return candidate

    # Collision apres troncature : on suffixe en gardant la limite de 31.
    for index in range(2, 100):
        suffix = f" ({index})"
        candidate = f"{cleaned[: 31 - len(suffix)]}{suffix}"
        if candidate.lower() not in taken:
            taken.add(candidate.lower())
            return candidate

    raise ValueError(f"Impossible de nommer un onglet pour '{provider}'")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _sort_key(entry: dict) -> tuple:
    """Tri par date decroissante, puis provider et nom pour un ordre stable."""
    parsed = _parse_date(entry.get("release_date"))
    return (
        -(parsed.toordinal() if parsed else 0),
        entry.get("provider", ""),
        entry.get("name", ""),
    )


def _fill_sheet(sheet: Worksheet, entries: list[dict]) -> None:
    sheet.append(HEADERS)
    for column in range(1, len(HEADERS) + 1):
        cell = sheet.cell(row=1, column=column)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")

    for entry in sorted(entries, key=_sort_key):
        parsed = _parse_date(entry.get("release_date"))
        sheet.append([parsed, entry.get("provider", ""), entry.get("name", "")])
        if parsed is not None:
            sheet.cell(row=sheet.max_row, column=1).number_format = DATE_FORMAT

    for index, width in enumerate(COLUMN_WIDTHS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    sheet.freeze_panes = "A2"
    sheet.row_dimensions[1].height = 20
    if sheet.max_row > 1:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{sheet.max_row}"


def build(entries: list[dict]) -> None:
    """Reconstruit le classeur complet a partir des entrees du state."""
    workbook = Workbook()
    workbook.remove(workbook.active)

    global_sheet = workbook.create_sheet(GLOBAL_SHEET)
    _fill_sheet(global_sheet, entries)

    by_provider: dict[str, list[dict]] = {}
    for entry in entries:
        by_provider.setdefault(entry.get("provider", "Inconnu"), []).append(entry)

    taken = {GLOBAL_SHEET.lower()}
    for provider in sorted(by_provider, key=str.casefold):
        sheet = workbook.create_sheet(_sheet_name(provider, taken))
        _fill_sheet(sheet, by_provider[provider])

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    workbook.save(config.EXCEL_FILE)
    log.info(
        "Excel ecrit : %s lignes, %s onglets provider",
        len(entries),
        len(by_provider),
    )
