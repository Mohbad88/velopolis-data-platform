"""Source 3 — fichier de données : CSV mensuels du prestataire de maintenance.

Les exports du prestataire ne sont pas homogènes d'un mois à l'autre :
l'encodage peut être UTF-8 ou latin-1 (export Excel), les dates ISO ou
françaises, les décimales en point ou en virgule. L'extraction se contente de
détecter l'encodage et de concaténer les fichiers sans rien interpréter ; la
normalisation relève de l'agrégation.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from ..config import config
from . import RapportExtraction

logger = logging.getLogger(__name__)
SOURCE = "maintenance"
ENCODAGES = ("utf-8", "cp1252", "latin-1")


def detecter_encodage(chemin: Path) -> str:
    """Retient le premier encodage qui décode le fichier entier sans erreur."""
    octets = chemin.read_bytes()
    for encodage in ENCODAGES:
        try:
            octets.decode(encodage)
        except UnicodeDecodeError:
            continue
        return encodage
    return "latin-1"  # latin-1 ne peut pas échouer : filet de sécurité


def extraire(*, source: Path | None = None, repertoire: Path | None = None) -> RapportExtraction:
    rapport = RapportExtraction(SOURCE)
    source = source or config.maintenance_repertoire
    repertoire = (repertoire or config.repertoire_brut) / SOURCE
    repertoire.mkdir(parents=True, exist_ok=True)
    cible = repertoire / "interventions.csv"

    fichiers = sorted(Path(source).glob("interventions_*.csv"))
    if not fichiers:
        return rapport.echouer(f"aucun fichier dans {source}")

    total, entete_ecrite, details = 0, False, []
    try:
        with cible.open("w", encoding="utf-8", newline="") as sortie:
            graveur = csv.writer(sortie, delimiter=";")
            for fichier in fichiers:
                encodage = detecter_encodage(fichier)
                details.append(f"{fichier.name} ({encodage})")
                with fichier.open("r", encoding=encodage, newline="") as fh:
                    lecteur = csv.reader(fh, delimiter=";")
                    entete = next(lecteur, None)
                    if entete is None:
                        continue
                    if not entete_ecrite:
                        graveur.writerow([*entete, "fichier_origine"])
                        entete_ecrite = True
                    for ligne in lecteur:
                        graveur.writerow([*ligne, fichier.name])
                        total += 1
                logger.info("%s : %s lu en %s", SOURCE, fichier.name, encodage)
    except Exception as exc:
        return rapport.echouer(str(exc))

    return rapport.terminer(total, cible, ", ".join(details))
