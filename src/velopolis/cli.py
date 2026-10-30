"""Point d'entrée unique de la chaîne.

    python -m velopolis.cli extraire     # les six sources vers data/raw
    python -m velopolis.cli agreger      # nettoyage, homogénéisation, socle unique
    python -m velopolis.cli creer-schema # applique le DDL
    python -m velopolis.cli importer     # charge la base
    python -m velopolis.cli pipeline     # tout l'enchaînement
    python -m velopolis.cli purger       # purge RGPD
    python -m velopolis.cli api          # démarre l'API REST

Chaque étape journalise son compte rendu dans ``journal_ingestion`` : une
exécution laisse une trace, qu'elle réussisse ou non.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .config import config

logger = logging.getLogger("velopolis")


def configurer_journalisation(verbeux: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbeux else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)-28s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# --------------------------------------------------------------------------
def extraire() -> list[dict]:
    from .extract import (
        competitor_scraping,
        hive_gps,
        iot_api,
        maintenance_csv,
        postgres_source,
        weather_api,
    )

    config.preparer_repertoires()
    rapports = [
        iot_api.extraire(),
        weather_api.extraire(),
        maintenance_csv.extraire(),
        competitor_scraping.extraire(),
        postgres_source.extraire(),
        hive_gps.extraire(),
    ]
    echecs = [r for r in rapports if r.statut == "echec"]
    for r in rapports:
        logger.info(
            "%-18s %-7s %8d lignes  %6.2f s  %s",
            r.source, r.statut, r.lignes, r.duree_s, r.detail,
        )
    if echecs:
        logger.error("%d source(s) en échec : %s", len(echecs), [r.source for r in echecs])
    return [{**r.en_dict(), "etape": "extraction"} for r in rapports]


def agreger() -> list[dict]:
    from .transform import aggregate

    return [{**r.en_dict(), "etape": "agregation", "statut": "succes"} for r in aggregate.executer()]


def creer_schema() -> None:
    from .load import import_db

    import_db.creer_schema()


def importer() -> list[dict]:
    from .load import import_db

    resultats = import_db.importer()
    import_db.importer_quarantaine()
    return [{**r, "etape": "chargement"} for r in resultats]


def purger(simulation: bool = False) -> dict:
    from .rgpd import purge

    return purge.executer(simulation=simulation)


def demarrer_api(hote: str = "127.0.0.1", port: int = 8000) -> None:
    from .api.app import creer_application

    logger.info("API disponible sur http://%s:%d/api/v1/sante", hote, port)
    creer_application().run(host=hote, port=port)


def pipeline() -> list[dict]:
    from .load import import_db

    journal = extraire()
    journal += agreger()
    creer_schema()
    journal += importer()
    import_db.journaliser(journal)
    logger.info("Chaîne terminée : %d étapes journalisées.", len(journal))
    return journal


# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(prog="velopolis", description=__doc__)
    analyseur.add_argument("-v", "--verbeux", action="store_true")
    sous = analyseur.add_subparsers(dest="commande", required=True)

    for nom, aide in (
        ("extraire", "extrait les six sources vers data/raw"),
        ("agreger", "nettoie, homogénéise et consolide"),
        ("creer-schema", "applique le DDL sur la base cible"),
        ("importer", "charge les données consolidées"),
        ("pipeline", "enchaîne extraction, agrégation, schéma et import"),
    ):
        sous.add_parser(nom, help=aide)

    p_purge = sous.add_parser("purger", help="applique les durées de conservation (RGPD)")
    p_purge.add_argument("--simulation", action="store_true",
                         help="compte les lignes concernées sans rien supprimer")

    p_api = sous.add_parser("api", help="démarre l'API REST")
    p_api.add_argument("--hote", default="127.0.0.1")
    p_api.add_argument("--port", type=int, default=8000)

    args = analyseur.parse_args(argv)
    configurer_journalisation(args.verbeux)

    if args.commande == "extraire":
        print(json.dumps(extraire(), indent=2, ensure_ascii=False))
    elif args.commande == "agreger":
        print(json.dumps(agreger(), indent=2, ensure_ascii=False))
    elif args.commande == "creer-schema":
        creer_schema()
    elif args.commande == "importer":
        print(json.dumps(importer(), indent=2, ensure_ascii=False))
    elif args.commande == "pipeline":
        pipeline()
    elif args.commande == "purger":
        print(json.dumps(purger(args.simulation), indent=2, ensure_ascii=False))
    elif args.commande == "api":
        demarrer_api(args.hote, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
