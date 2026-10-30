"""Source 4 — scraping : grille tarifaire publiée par un opérateur concurrent.

Précautions prises avant toute collecte, parce qu'un scraping mal conduit
expose l'entreprise autant juridiquement que techniquement :

* lecture de ``robots.txt`` et abandon si le chemin visé est interdit ;
* user-agent identifiant Vélopolis et une adresse de contact ;
* délai entre deux requêtes conforme au ``Crawl-delay`` annoncé ;
* collecte limitée à des données publiques, non personnelles, et conservation
  de l'horodatage de collecte pour pouvoir dater la grille relevée.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from ..config import config
from . import RapportExtraction
from .http import obtenir

logger = logging.getLogger(__name__)
SOURCE = "tarifs_concurrent"
COLONNES = [
    "date_collecte",
    "operateur",
    "offre",
    "type_vehicule",
    "prix_deblocage",
    "prix_minute",
    "abonnement_mensuel",
    "zones",
    "url_source",
]


def scraping_autorise(url: str, robots_url: str, user_agent: str) -> tuple[bool, float]:
    """Retourne (autorisé, délai à respecter) d'après robots.txt."""
    analyseur = RobotFileParser()
    try:
        contenu = obtenir(robots_url, tentatives=2).text
    except Exception as exc:  # robots.txt absent ou injoignable
        logger.warning("robots.txt illisible (%s) : collecte refusée par précaution", exc)
        return False, 0.0
    analyseur.parse(contenu.splitlines())
    delai = analyseur.crawl_delay(user_agent) or config.concurrent_delai_s
    return analyseur.can_fetch(user_agent, url), float(delai)


def analyser(html: str, url: str, operateur: str = "MobiCity") -> list[dict]:
    """Extrait les lignes de la table tarifaire. Les montants restent bruts."""
    soupe = BeautifulSoup(html, "html.parser")
    table = soupe.find("table", id="grille-tarifaire") or soupe.find("table")
    if table is None:
        raise ValueError("aucune table tarifaire trouvée dans la page")

    collecte = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lignes = []
    corps = table.find("tbody") or table
    for tr in corps.find_all("tr"):
        cellules = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cellules) < 6:
            continue
        lignes.append(
            {
                "date_collecte": collecte,
                "operateur": operateur,
                "offre": cellules[0],
                "type_vehicule": cellules[1],
                "prix_deblocage": cellules[2],
                "prix_minute": cellules[3],
                "abonnement_mensuel": cellules[4],
                "zones": cellules[5],
                "url_source": url,
            }
        )
    return lignes


def extraire(*, repertoire: Path | None = None) -> RapportExtraction:
    rapport = RapportExtraction(SOURCE)
    repertoire = (repertoire or config.repertoire_brut) / SOURCE
    repertoire.mkdir(parents=True, exist_ok=True)
    cible = repertoire / "tarifs.csv"

    url = config.concurrent_url
    agent = config.concurrent_user_agent
    autorise, delai = scraping_autorise(url, config.concurrent_robots_url, agent)
    if not autorise:
        return rapport.echouer(f"robots.txt interdit la collecte de {urlparse(url).path}")
    logger.info("%s : collecte autorisée, délai annoncé %.0f s", SOURCE, delai)

    try:
        html = obtenir(url, entetes={"User-Agent": agent}).text
        lignes = analyser(html, url)
        with cible.open("w", encoding="utf-8", newline="") as fh:
            graveur = csv.DictWriter(fh, fieldnames=COLONNES)
            graveur.writeheader()
            graveur.writerows(lignes)
    except Exception as exc:
        return rapport.echouer(str(exc))

    return rapport.terminer(len(lignes), cible, f"robots.txt respecté, crawl-delay {delai:.0f} s")
