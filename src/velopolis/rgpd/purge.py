"""Procédure de tri et de suppression (article 5.1.e du RGPD).

Trois durées de conservation, validées avec le DPO :

======================  ========  ====================================================
Donnée                  Durée     Justification
======================  ========  ====================================================
Télémétrie, traces GPS  30 jours  Position d'un véhicule en location = donnée
                                  personnelle indirecte. 30 jours suffisent au suivi
                                  d'exploitation et aux litiges courants.
Locations pseudonymisées 13 mois  Permet la comparaison à l'année précédente ; au-delà,
                                  seules les agrégations anonymes sont conservées.
Quarantaine qualité     30 jours  Contient des extraits de lignes sources : même
                                  durée que la donnée dont elle est issue.
======================  ========  ====================================================

La purge est **journalisée** : chaque exécution écrit dans
``journal_ingestion`` le nombre de lignes supprimées par table. Sans cette
trace, l'entreprise ne peut pas démontrer que la procédure tourne réellement,
ce qui est précisément ce que demande le principe de responsabilité
(*accountability*).

Le mode ``--simulation`` compte les lignes concernées sans rien supprimer :
il sert au contrôle mensuel.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from ..config import config
from ..db import connecter
from ..db import executer as exec_sql

logger = logging.getLogger(__name__)


def _limites() -> dict[str, str]:
    maintenant = datetime.now(timezone.utc)
    limite_courte = (maintenant - timedelta(days=config.retention_telemetrie_jours)).isoformat()
    jours_locations = int(config.retention_location_mois * 30.44)
    limite_longue = (maintenant - timedelta(days=jours_locations)).isoformat()
    return {"courte": limite_courte, "longue": limite_longue}


REGLES = (
    ("fait_telemetrie", "horodatage", "courte"),
    ("fait_trace_gps", "horodatage", "courte"),
    ("quarantaine_qualite", "detecte_le", "courte"),
    ("fait_location", "debut", "longue"),
)


def executer(simulation: bool = False, url: str | None = None) -> dict:
    url = url or config.db_url
    limites = _limites()
    resultat: dict = {
        "execute_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "simulation" if simulation else "suppression",
        "limites": limites,
        "tables": {},
    }

    conn = connecter(url)
    try:
        cur = conn.cursor()
        for table, colonne, duree in REGLES:
            limite = limites[duree]
            exec_sql(cur, f"SELECT COUNT(*) FROM {table} WHERE {colonne} < %s", (limite,), url)
            concernees = cur.fetchone()[0]
            if not simulation and concernees:
                exec_sql(cur, f"DELETE FROM {table} WHERE {colonne} < %s", (limite,), url)
            resultat["tables"][table] = {
                "limite": limite,
                "lignes_concernees": concernees,
                "supprimees": 0 if simulation else concernees,
            }
            logger.info(
                "%-22s %7d ligne(s) antérieures au %s%s",
                table, concernees, limite[:10], " (simulation)" if simulation else " supprimées",
            )

        if not simulation:
            executer(
                cur,
                "INSERT INTO journal_ingestion "
                "(source, etape, lignes_lues, lignes_gardees, lignes_rejetees, statut, detail) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    "rgpd",
                    "purge",
                    sum(t["lignes_concernees"] for t in resultat["tables"].values()),
                    0,
                    sum(t["supprimees"] for t in resultat["tables"].values()),
                    "succes",
                    "; ".join(
                        f"{t}: {d['supprimees']}" for t, d in resultat["tables"].items()
                    ),
                ),
                url,
            )
            conn.commit()
    finally:
        conn.close()

    return resultat


def effacer_abonne(id_abonne: int, url: str | None = None) -> dict:
    """Droit à l'effacement : supprime les lignes rattachées à un abonné.

    L'identifiant fourni est l'identifiant *source* ; il est repseudonymisé
    avec la clé de l'entreprise pour retrouver les lignes correspondantes.
    C'est tout l'intérêt d'un pseudonyme déterministe : il reste réversible
    pour qui détient la clé, et seulement pour lui.
    """
    from .pseudonymize import pseudonymiser

    url = url or config.db_url
    pseudonyme = pseudonymiser(id_abonne)
    conn = connecter(url)
    try:
        cur = conn.cursor()
        exec_sql(cur, "SELECT COUNT(*) FROM fait_location WHERE id_abonne_psd = %s",
                 (pseudonyme,), url)
        nombre = cur.fetchone()[0]
        exec_sql(cur, "DELETE FROM fait_location WHERE id_abonne_psd = %s", (pseudonyme,), url)
        executer(
            cur,
            "INSERT INTO journal_ingestion (source, etape, lignes_rejetees, statut, detail) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("rgpd", "effacement", nombre, "succes",
             f"demande d'effacement traitée pour le pseudonyme {pseudonyme}"),
            url,
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Effacement : %d location(s) supprimée(s) pour %s", nombre, pseudonyme)
    return {"pseudonyme": pseudonyme, "lignes_supprimees": nombre, "traite_le": date.today().isoformat()}
