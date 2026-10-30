"""Source 6 — système big data : table externe Hive des traces GPS.

La table ``velopolis.gps_traces`` est partitionnée par jour (``dt``). Deux
modes d'accès, choisis selon la configuration :

* ``HIVE_HOST`` renseigné — connexion HiveServer2 via ``pyhive`` et requête
  HiveQL filtrée sur la partition, de sorte que seules les partitions utiles
  sont lues (*partition pruning*) ;
* sinon — lecture directe des fichiers de l'entrepôt sur le système de
  fichiers, en ne descendant que dans les répertoires ``dt=`` compris dans la
  fenêtre demandée. C'est exactement le même élagage de partitions, appliqué
  côté client ; ce mode permet de dérouler la chaîne sans cluster.

Dans les deux cas, la règle est la même : ne jamais balayer la table entière.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, timedelta
from pathlib import Path

from ..config import config
from . import RapportExtraction

logger = logging.getLogger(__name__)
SOURCE = "traces_gps"
COLONNES = ["id_vehicule", "horodatage", "latitude", "longitude", "vitesse_kmh", "id_trajet"]

REQUETE_HIVE = """
SELECT vehicle_id, ts, lat, lon, speed_kmh, trip_id
FROM {base}.{table}
WHERE dt >= '{debut}' AND dt <= '{fin}'
"""


def partitions(debut: str, fin: str) -> list[str]:
    """Liste les clés de partition (``YYYY-MM-DD``) couvertes par la fenêtre."""
    d1 = date.fromisoformat(debut[:10])
    d2 = date.fromisoformat(fin[:10])
    return [(d1 + timedelta(days=i)).isoformat() for i in range((d2 - d1).days + 1)]


def _lire_hiveserver(debut: str, fin: str):  # pragma: no cover (nécessite un cluster)
    from pyhive import hive  # type: ignore

    conn = hive.Connection(
        host=config.hive_host, port=config.hive_port, database=config.hive_base
    )
    cur = conn.cursor()
    cur.execute(
        REQUETE_HIVE.format(
            base=config.hive_base, table=config.hive_table, debut=debut[:10], fin=fin[:10]
        )
    )
    while lot := cur.fetchmany(10_000):
        yield from lot
    conn.close()


def _lire_entrepot(debut: str, fin: str):
    """Lecture des partitions sur disque, sans cluster."""
    racine = Path(config.hive_entrepot) / config.hive_table
    attendues = set(partitions(debut, fin))
    lues, ignorees = 0, 0
    for repertoire in sorted(racine.glob("dt=*")):
        cle = repertoire.name.split("=", 1)[1]
        if cle not in attendues:
            ignorees += 1
            continue
        lues += 1
        for fichier in sorted(repertoire.glob("part-*")):
            with fichier.open("r", encoding="utf-8", newline="") as fh:
                yield from csv.reader(fh)
    logger.info("%s : %d partitions lues, %d élaguées", SOURCE, lues, ignorees)


def extraire(
    debut: str | None = None, fin: str | None = None, *, repertoire: Path | None = None
) -> RapportExtraction:
    rapport = RapportExtraction(SOURCE)
    repertoire = (repertoire or config.repertoire_brut) / SOURCE
    repertoire.mkdir(parents=True, exist_ok=True)
    cible = repertoire / "traces_gps.csv"

    debut = debut or config.fenetre_debut
    fin = fin or config.fenetre_fin
    mode = "HiveServer2" if config.hive_host else "entrepôt sur disque"
    total = 0

    try:
        source = _lire_hiveserver(debut, fin) if config.hive_host else _lire_entrepot(debut, fin)
        with cible.open("w", encoding="utf-8", newline="") as fh:
            graveur = csv.writer(fh)
            graveur.writerow(COLONNES)
            for ligne in source:
                graveur.writerow(list(ligne))
                total += 1
    except Exception as exc:
        return rapport.echouer(str(exc))

    return rapport.terminer(
        total, cible, f"{mode}, partitions {debut[:10]} → {fin[:10]} (élagage appliqué)"
    )
