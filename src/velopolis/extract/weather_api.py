"""Source 2 — service web : API météo publique (relevés horaires par zone).

Volume faible : un seul appel par zone suffit. La source mélange degrés
Celsius et Fahrenheit selon la station émettrice ; l'unité est conservée ici
telle qu'elle arrive et n'est homogénéisée qu'à l'étape d'agrégation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import config
from . import RapportExtraction
from .http import obtenir

logger = logging.getLogger(__name__)
SOURCE = "meteo"
ZONES = ("centre", "nord", "sud", "ouest")


def extraire(*, repertoire: Path | None = None, zones: tuple[str, ...] = ZONES) -> RapportExtraction:
    rapport = RapportExtraction(SOURCE)
    repertoire = (repertoire or config.repertoire_brut) / SOURCE
    repertoire.mkdir(parents=True, exist_ok=True)
    cible = repertoire / "meteo.jsonl"
    total = 0

    try:
        with cible.open("w", encoding="utf-8") as fh:
            for zone in zones:
                charge = obtenir(config.meteo_url, params={"zone": zone}).json()
                for ligne in charge.get("items", []):
                    fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")
                    total += 1
                logger.info("%s : zone %s, %d relevés", SOURCE, zone, charge.get("count", 0))
    except Exception as exc:
        return rapport.echouer(str(exc))

    return rapport.terminer(total, cible, f"{len(zones)} zones")
