"""Source 1 — service web : API REST des boîtiers IoT embarqués.

Extraction paginée et incrémentale : seuls les relevés postérieurs à
``since`` sont demandés, ce qui évite de relire l'historique à chaque
exécution. La réponse est déposée telle quelle (JSON Lines) dans la zone
brute : aucun nettoyage n'est fait ici.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import config
from . import RapportExtraction
from .http import obtenir

logger = logging.getLogger(__name__)
SOURCE = "iot_telemetrie"


def extraire(
    depuis: str | None = None,
    *,
    repertoire: Path | None = None,
    pages_max: int = 500,
) -> RapportExtraction:
    rapport = RapportExtraction(SOURCE)
    repertoire = (repertoire or config.repertoire_brut) / SOURCE
    repertoire.mkdir(parents=True, exist_ok=True)
    cible = repertoire / "telemetrie.jsonl"

    depuis = depuis or config.fenetre_debut
    entetes = {"Authorization": f"Bearer {config.iot_token}", "Accept": "application/json"}
    total = 0

    try:
        with cible.open("w", encoding="utf-8") as fh:
            for page in range(1, pages_max + 1):
                reponse = obtenir(
                    config.iot_url,
                    params={"since": depuis, "page": page, "page_size": config.iot_taille_page},
                    entetes=entetes,
                )
                charge = reponse.json()
                lignes = charge.get("items", [])
                for ligne in lignes:
                    fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")
                total += len(lignes)
                logger.info("%s : page %d, %d relevés (cumul %d)", SOURCE, page, len(lignes), total)
                if len(lignes) < config.iot_taille_page:
                    break
    except Exception as exc:
        return rapport.echouer(str(exc))

    return rapport.terminer(total, cible, f"extraction incrémentale depuis {depuis}")
