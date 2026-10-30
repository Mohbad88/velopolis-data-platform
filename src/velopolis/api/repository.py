"""Accès aux données pour l'API : toutes les requêtes sont ici, paramétrées.

Aucune valeur venant du client n'est concaténée dans une chaîne SQL : les
filtres passent par des marqueurs de paramètres, ce qui écarte l'injection
SQL. Les seules portions construites dynamiquement sont des noms de colonnes
choisis dans une liste fermée (tri), jamais du texte libre.
"""

from __future__ import annotations

from ..db import lire

LIMITE_DEFAUT = 100
LIMITE_MAX = 1_000

TRIS_AUTORISES = {"heure", "id_vehicule", "recette_eur", "nb_locations"}


def borner(limite: int | None, decalage: int | None) -> tuple[int, int]:
    """Impose une pagination : sans plafond, un seul appel peut saturer la base."""
    limite = LIMITE_DEFAUT if limite is None else max(1, min(int(limite), LIMITE_MAX))
    decalage = max(0, int(decalage or 0))
    return limite, decalage


def vehicules(limite=None, decalage=None) -> list[dict]:
    limite, decalage = borner(limite, decalage)
    return lire(
        "SELECT id_vehicule, type, modele, date_mise_service "
        "FROM dim_vehicule ORDER BY id_vehicule LIMIT %s OFFSET %s",
        (limite, decalage),
    )


def stations(limite=None, decalage=None) -> list[dict]:
    limite, decalage = borner(limite, decalage)
    return lire(
        "SELECT id_station, nom, quartier, capacite, latitude, longitude "
        "FROM dim_station ORDER BY id_station LIMIT %s OFFSET %s",
        (limite, decalage),
    )


def etat_vehicule(id_vehicule: str) -> dict | None:
    lignes = lire(
        "SELECT id_vehicule, horodatage, latitude, longitude, batterie_pct, statut, "
        "       id_station, zone "
        "FROM fait_telemetrie WHERE id_vehicule = %s "
        "ORDER BY horodatage DESC LIMIT 1",
        (id_vehicule,),
    )
    return lignes[0] if lignes else None


def flotte_horaire(
    debut=None, fin=None, zone=None, id_vehicule=None,
    tri="heure", limite=None, decalage=None,
) -> list[dict]:
    limite, decalage = borner(limite, decalage)
    colonne = tri if tri in TRIS_AUTORISES else "heure"

    conditions, params = [], []
    if debut:
        conditions.append("heure >= %s")
        params.append(debut)
    if fin:
        conditions.append("heure < %s")
        params.append(fin)
    if zone:
        conditions.append("zone = %s")
        params.append(zone)
    if id_vehicule:
        conditions.append("id_vehicule = %s")
        params.append(id_vehicule)
    filtre = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    return lire(
        f"SELECT id_vehicule, type_vehicule, heure, zone, nb_releves, batterie_min_pct, "
        f"       batterie_moy_pct, statut_dominant, latitude_moy, longitude_moy, "
        f"       temperature_c, precipitation_mm, vent_kmh, nb_locations, "
        f"       duree_location_min, recette_eur, en_maintenance "
        f"FROM socle_flotte_horaire {filtre} "
        f"ORDER BY {colonne}, id_vehicule LIMIT %s OFFSET %s",
        (*params, limite, decalage),
    )


def interventions(id_vehicule=None, depuis=None, limite=None, decalage=None) -> list[dict]:
    limite, decalage = borner(limite, decalage)
    conditions, params = [], []
    if id_vehicule:
        conditions.append("id_vehicule = %s")
        params.append(id_vehicule)
    if depuis:
        conditions.append("date_intervention >= %s")
        params.append(depuis)
    filtre = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return lire(
        f"SELECT id_intervention, id_vehicule, date_intervention, type_panne, "
        f"       piece_changee, duree_min, cout_eur, prestataire "
        f"FROM fait_intervention {filtre} "
        f"ORDER BY date_intervention DESC, id_intervention LIMIT %s OFFSET %s",
        (*params, limite, decalage),
    )


def locations(debut=None, fin=None, limite=None, decalage=None) -> list[dict]:
    """Locations pseudonymisées : portée dédiée, aucun identifiant direct exposé."""
    limite, decalage = borner(limite, decalage)
    conditions, params = [], []
    if debut:
        conditions.append("debut >= %s")
        params.append(debut)
    if fin:
        conditions.append("debut < %s")
        params.append(fin)
    filtre = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return lire(
        f"SELECT id_location, id_abonne_psd, tranche_age, type_abonnement, id_vehicule, "
        f"       id_station_depart, id_station_arrivee, debut, fin, duree_min, "
        f"       distance_km, montant_eur "
        f"FROM fait_location {filtre} ORDER BY debut LIMIT %s OFFSET %s",
        (*params, limite, decalage),
    )


def tarifs_concurrent(limite=None, decalage=None) -> list[dict]:
    limite, decalage = borner(limite, decalage)
    return lire(
        "SELECT date_collecte, operateur, offre, type_vehicule, prix_deblocage_eur, "
        "       prix_minute_eur, abonnement_mensuel_eur, zones "
        "FROM ref_tarif_concurrent ORDER BY date_collecte DESC, offre "
        "LIMIT %s OFFSET %s",
        (limite, decalage),
    )


def journal(limite=None, decalage=None) -> list[dict]:
    limite, decalage = borner(limite, decalage)
    return lire(
        "SELECT id_execution, source, etape, execute_le, lignes_lues, lignes_gardees, "
        "       lignes_rejetees, duree_s, statut, detail "
        "FROM journal_ingestion ORDER BY id_execution DESC LIMIT %s OFFSET %s",
        (limite, decalage),
    )


def qualite() -> list[dict]:
    return lire(
        "SELECT source, motif, COUNT(*) AS nombre "
        "FROM quarantaine_qualite GROUP BY source, motif ORDER BY nombre DESC"
    )


def compter() -> dict:
    tables = (
        "dim_vehicule", "dim_station", "fait_telemetrie", "fait_meteo",
        "fait_intervention", "fait_location", "fait_trace_gps",
        "ref_tarif_concurrent", "socle_flotte_horaire", "quarantaine_qualite",
    )
    return {t: lire(f"SELECT COUNT(*) AS n FROM {t}")[0]["n"] for t in tables}
