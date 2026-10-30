"""Source 5 — base de données : réplica en lecture de la base transactionnelle.

Trois choix structurent cette extraction :

1. **Lecture sur un réplica**, jamais sur la base de production : une requête
   analytique mal calibrée ne doit pas dégrader le service aux abonnés.
2. **Minimisation dès la source** : les colonnes directement identifiantes
   (nom, prénom, courriel, téléphone) ne sont pas sélectionnées, et la date de
   naissance est convertie en tranche d'âge puis écartée avant même l'écriture
   dans la zone brute. Aucun identifiant direct n'entre donc dans la chaîne.
3. **Curseur côté serveur** : les résultats sont consommés par lots, pour que
   la mémoire du processus ne dépende pas du volume extrait.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime
from pathlib import Path

from ..config import config
from ..db import connecter, est_postgres, executer
from ..rgpd.pseudonymize import pseudonymiser, tranche_age
from . import RapportExtraction

logger = logging.getLogger(__name__)
SOURCE = "locations"
TAILLE_LOT = 5_000

COLONNES = [
    "id_location",
    "id_abonne_psd",
    "tranche_age",
    "type_abonnement",
    "ville_abonne",
    "id_vehicule",
    "id_station_depart",
    "id_station_arrivee",
    "debut",
    "fin",
    "distance_km",
    "montant_eur",
    "nb_paiements",
]

REQUETE = """
SELECT l.id_location,
       l.id_abonne,
       a.date_naissance,
       a.type_abonnement,
       a.ville,
       l.id_vehicule,
       l.id_station_depart,
       l.id_station_arrivee,
       l.debut,
       l.fin,
       l.distance_km,
       SUM(p.montant_eur)      AS montant_eur,
       COUNT(p.id_paiement)    AS nb_paiements
FROM {p}location  AS l
JOIN {p}abonne    AS a ON a.id_abonne = l.id_abonne
LEFT JOIN {p}paiement AS p ON p.id_location = l.id_location
WHERE l.debut >= %s
  AND l.debut <  %s
  AND l.fin IS NOT NULL
GROUP BY l.id_location, l.id_abonne, a.date_naissance, a.type_abonnement, a.ville,
         l.id_vehicule, l.id_station_depart, l.id_station_arrivee,
         l.debut, l.fin, l.distance_km
ORDER BY l.debut
"""


def _age(naissance, reference: date) -> int | None:
    if naissance in (None, ""):
        return None
    if isinstance(naissance, str):
        try:
            naissance = date.fromisoformat(naissance[:10])
        except ValueError:
            return None
    if isinstance(naissance, datetime):
        naissance = naissance.date()
    return reference.year - naissance.year - (
        (reference.month, reference.day) < (naissance.month, naissance.day)
    )


def extraire(
    debut: str | None = None,
    fin: str | None = None,
    *,
    repertoire: Path | None = None,
    url: str | None = None,
) -> RapportExtraction:
    rapport = RapportExtraction(SOURCE)
    repertoire = (repertoire or config.repertoire_brut) / SOURCE
    repertoire.mkdir(parents=True, exist_ok=True)
    cible = repertoire / "locations.csv"

    url = url or config.source_db_url
    prefixe = "source." if est_postgres(url) else ""
    requete = REQUETE.format(p=prefixe)
    debut = debut or config.fenetre_debut
    fin = fin or config.fenetre_fin
    aujourdhui = date.today()
    total = 0

    try:
        conn = connecter(url)
        try:
            cur = conn.cursor()
            executer(cur, requete, (debut, fin), url)
            with cible.open("w", encoding="utf-8", newline="") as fh:
                graveur = csv.writer(fh)
                graveur.writerow(COLONNES)
                while lot := cur.fetchmany(TAILLE_LOT):
                    for ligne in lot:
                        (id_loc, id_abonne, naissance, type_abo, ville, id_veh,
                         dep, arr, d, f, dist, montant, nb_pay) = ligne
                        graveur.writerow(
                            [
                                id_loc,
                                pseudonymiser(id_abonne),
                                tranche_age(_age(naissance, aujourdhui)),
                                type_abo,
                                ville,
                                id_veh,
                                dep,
                                arr,
                                d,
                                f,
                                dist,
                                montant,
                                nb_pay,
                            ]
                        )
                        total += 1
                    logger.info("%s : %d locations écrites", SOURCE, total)
        finally:
            conn.close()
    except Exception as exc:
        return rapport.echouer(str(exc))

    return rapport.terminer(
        total, cible, "pseudonymisation appliquée dès l'extraction, aucun identifiant direct lu"
    )
