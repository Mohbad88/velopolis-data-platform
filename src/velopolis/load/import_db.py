"""Import des données consolidées dans la base du projet.

Deux points méritent d'être soulignés.

**Chargement en masse.** Sur PostgreSQL, l'import passe par ``COPY … FROM
STDIN``, qui écrit en flux côté serveur. Un ``INSERT`` par ligne sur
58 000 relevés prendrait plusieurs minutes et autant d'allers-retours réseau ;
``COPY`` les charge en une passe, pour un ordre de grandeur de différence.

**Tout ou rien.** L'ensemble de l'import se déroule dans une seule
transaction : si une table échoue, aucune n'est modifiée. Une base à moitié
chargée est pire qu'une base non chargée, car plus rien n'indique au lecteur
que les chiffres sont incomplets.

L'ordre de chargement respecte les clés étrangères : référentiels d'abord,
faits ensuite.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import config
from ..db import connecter, est_postgres, executer, executer_lot, executer_script

logger = logging.getLogger(__name__)

TAILLE_LOT = 5_000


@dataclass(frozen=True)
class Table:
    nom: str
    fichier: str
    colonnes: tuple[str, ...]


# Ordre significatif : les référentiels avant les faits qui les référencent.
TABLES: tuple[Table, ...] = (
    Table("dim_vehicule", "dim_vehicule.csv",
          ("id_vehicule", "type", "modele", "date_mise_service")),
    Table("dim_station", "dim_station.csv",
          ("id_station", "nom", "quartier", "capacite", "latitude", "longitude")),
    Table("fait_telemetrie", "fait_telemetrie.csv",
          ("id_vehicule", "horodatage", "latitude", "longitude", "batterie_pct",
           "statut", "id_station", "zone")),
    Table("fait_meteo", "fait_meteo.csv",
          ("zone", "horodatage", "temperature_c", "precipitation_mm", "vent_kmh")),
    Table("fait_intervention", "fait_intervention.csv",
          ("id_intervention", "id_vehicule", "date_intervention", "type_panne",
           "piece_changee", "duree_min", "cout_eur", "prestataire", "fichier_origine")),
    Table("fait_location", "fait_location.csv",
          ("id_location", "id_abonne_psd", "tranche_age", "type_abonnement", "ville_abonne",
           "id_vehicule", "id_station_depart", "id_station_arrivee", "debut", "fin",
           "duree_min", "distance_km", "montant_eur")),
    Table("fait_trace_gps", "fait_trace_gps.csv",
          ("id_vehicule", "id_trajet", "horodatage", "latitude", "longitude", "vitesse_kmh")),
    Table("ref_tarif_concurrent", "ref_tarif_concurrent.csv",
          ("date_collecte", "operateur", "offre", "type_vehicule", "prix_deblocage_eur",
           "prix_minute_eur", "abonnement_mensuel_eur", "zones", "url_source")),
    Table("socle_flotte_horaire", "socle_flotte_horaire.csv",
          ("id_vehicule", "heure", "type_vehicule", "zone", "nb_releves", "batterie_min_pct",
           "batterie_moy_pct", "statut_dominant", "latitude_moy", "longitude_moy",
           "temperature_c", "precipitation_mm", "vent_kmh", "nb_locations",
           "duree_location_min", "recette_eur", "en_maintenance")),
)


def creer_schema(url: str | None = None) -> Path:
    """Applique le DDL correspondant au moteur visé."""
    url = url or config.db_url
    fichier = "schema_postgresql.sql" if est_postgres(url) else "schema_sqlite.sql"
    chemin = config.racine / "sql" / "ddl" / fichier
    executer_script(chemin, url)
    logger.info("Schéma appliqué : %s", chemin.name)
    return chemin


def _lignes(chemin: Path, colonnes: tuple[str, ...]):
    """Rend les lignes du CSV dans l'ordre des colonnes cibles, NULL si vide."""
    with chemin.open("r", encoding="utf-8", newline="") as fh:
        for ligne in csv.DictReader(fh):
            yield [(ligne.get(c) if ligne.get(c) not in ("", None) else None) for c in colonnes]


def _copier_postgres(conn, table: Table, chemin: Path) -> int:
    """Chargement en masse via COPY FROM STDIN."""
    total = 0
    colonnes = ", ".join(table.colonnes)
    requete = f"COPY {table.nom} ({colonnes}) FROM STDIN WITH (FORMAT csv)"
    with conn.cursor() as cur, cur.copy(requete) as flux:
        tampon = io.StringIO()
        graveur = csv.writer(tampon)
        for ligne in _lignes(chemin, table.colonnes):
            graveur.writerow(["" if v is None else v for v in ligne])
            total += 1
            if total % TAILLE_LOT == 0:
                flux.write(tampon.getvalue())
                tampon.seek(0)
                tampon.truncate(0)
        flux.write(tampon.getvalue())
    return total


def _inserer_sqlite(conn, table: Table, chemin: Path) -> int:
    marqueurs = ", ".join("%s" for _ in table.colonnes)
    colonnes = ", ".join(table.colonnes)
    requete = f"INSERT INTO {table.nom} ({colonnes}) VALUES ({marqueurs})"
    cur = conn.cursor()
    total, lot = 0, []
    for ligne in _lignes(chemin, table.colonnes):
        lot.append(ligne)
        if len(lot) >= TAILLE_LOT:
            executer_lot(cur, requete, lot, config.db_url)
            total += len(lot)
            lot.clear()
    if lot:
        executer_lot(cur, requete, lot, config.db_url)
        total += len(lot)
    return total


def importer(url: str | None = None, *, source: Path | None = None) -> list[dict]:
    """Vide puis recharge toutes les tables du socle, en une transaction."""
    url = url or config.db_url
    source = source or config.repertoire_consolide
    resultats: list[dict] = []

    conn = connecter(url)
    try:
        if not est_postgres(url):
            # SQLite est ouvert en mode auto-commit : sans BEGIN explicite,
            # chaque lot serait validé séparément et la promesse « tout ou
            # rien » ne tiendrait pas (en plus d'un fsync par lot).
            conn.execute("PRAGMA synchronous = OFF")
            conn.execute("BEGIN")
        cur = conn.cursor()
        # Purge dans l'ordre inverse des dépendances.
        for table in reversed(TABLES):
            executer(cur, f"DELETE FROM {table.nom}", url=url)

        for table in TABLES:
            chemin = Path(source) / table.fichier
            if not chemin.is_file():
                logger.warning("%s : fichier absent (%s), table ignorée", table.nom, chemin)
                resultats.append({"table": table.nom, "lignes": 0, "statut": "absent"})
                continue
            if est_postgres(url):
                n = _copier_postgres(conn, table, chemin)
            else:
                n = _inserer_sqlite(conn, table, chemin)
            logger.info("%-22s %7d lignes importées", table.nom, n)
            resultats.append({"table": table.nom, "lignes": n, "statut": "succes"})

        conn.commit()
    except Exception:
        conn.rollback()
        logger.error("Import interrompu : la base est restée dans son état initial.")
        raise
    finally:
        conn.close()

    return resultats


def importer_quarantaine(url: str | None = None, *, source: Path | None = None) -> int:
    """Charge les lignes écartées, pour qu'elles soient consultables en base."""
    url = url or config.db_url
    source = source or config.repertoire_quarantaine
    fichiers = sorted(Path(source).glob("*.csv"))
    if not fichiers:
        return 0

    requete = (
        "INSERT INTO quarantaine_qualite (source, motif, detecte_le, contenu) "
        "VALUES (%s, %s, %s, %s)"
    )
    total = 0
    conn = connecter(url)
    try:
        if not est_postgres(url):
            conn.execute("BEGIN")
        cur = conn.cursor()
        executer(cur, "DELETE FROM quarantaine_qualite", url=url)
        for fichier in fichiers:
            with fichier.open("r", encoding="utf-8", newline="") as fh:
                lot = [
                    [l["source"], l["motif"], l["detecte_le"], l["contenu"]]
                    for l in csv.DictReader(fh)
                ]
            if lot:
                executer_lot(cur, requete, lot, url)
                total += len(lot)
        conn.commit()
    finally:
        conn.close()
    logger.info("quarantaine_qualite    %7d lignes importées", total)
    return total


def journaliser(entrees: list[dict], url: str | None = None) -> int:
    """Enregistre le compte rendu d'exécution dans journal_ingestion."""
    url = url or config.db_url
    requete = (
        "INSERT INTO journal_ingestion "
        "(source, etape, lignes_lues, lignes_gardees, lignes_rejetees, duree_s, statut, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    lignes = [
        [
            e.get("source", e.get("table", "?")),
            e.get("etape", "ingestion"),
            int(e.get("lignes", e.get("lues", 0)) or 0),
            int(e.get("conservees", e.get("lignes", 0)) or 0),
            int(e.get("rejetees", 0) or 0),
            float(e.get("duree_s", 0) or 0),
            e.get("statut", "succes"),
            (e.get("detail") or e.get("motifs") or "")[:2000],
        ]
        for e in entrees
    ]
    conn = connecter(url)
    try:
        cur = conn.cursor()
        executer_lot(cur, requete, lignes, url)
        conn.commit()
    finally:
        conn.close()
    return len(lignes)
