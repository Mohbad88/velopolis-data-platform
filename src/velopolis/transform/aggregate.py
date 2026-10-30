"""Agrégation : des six extractions brutes à un jeu de données consolidé unique.

Le traitement se fait en deux temps.

1. **Normalisation par source, en flux.** Chaque ligne est lue, contrôlée,
   convertie (dates en UTC, décimales en point, températures en Celsius,
   statuts ramenés à un vocabulaire fermé), puis écrite dans ``data/curated``.
   Les lignes corrompues partent en quarantaine avec leur motif. Le traitement
   est fait ligne à ligne, et non en chargeant tout en mémoire, parce que la
   télémétrie réelle représente 17 millions de messages par jour.

2. **Consolidation.** Les tables normalisées sont jointes en un seul jeu de
   données à la maille *véhicule × heure* : l'état de la flotte, enrichi de la
   météo de la zone, des locations de l'heure et des interventions du jour.
   C'est ce jeu unique qui alimente la base et l'API.
"""

from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..config import config
from .quality import (
    Quarantaine,
    dans_bornes,
    en_celsius,
    normaliser_statut,
    parser_decimal,
    parser_entier,
    parser_horodatage,
    position_plausible,
)

logger = logging.getLogger(__name__)


@dataclass
class RapportAgregation:
    table: str
    lues: int = 0
    conservees: int = 0
    rejetees: int = 0
    motifs: str = ""
    fichier: Path | None = None
    # Lignes réparées plutôt que rejetées : date reformatée, unité convertie,
    # casse normalisée. C'est le cœur de l'homogénéisation demandée.
    corrections: Counter = field(default_factory=Counter)

    def resume_corrections(self) -> str:
        if not self.corrections:
            return "aucune correction"
        return ", ".join(f"{k} ×{v}" for k, v in sorted(self.corrections.items()))

    def en_dict(self) -> dict:
        return {
            "table": self.table,
            "lues": self.lues,
            "conservees": self.conservees,
            "rejetees": self.rejetees,
            "corrigees": sum(self.corrections.values()),
            "motifs": self.motifs,
            "corrections": self.resume_corrections(),
            "fichier": str(self.fichier) if self.fichier else None,
        }


def _ecrire(cible: Path, colonnes: list[str], lignes: list[dict]) -> Path:
    cible.parent.mkdir(parents=True, exist_ok=True)
    with cible.open("w", encoding="utf-8", newline="") as fh:
        graveur = csv.DictWriter(fh, fieldnames=colonnes, extrasaction="ignore")
        graveur.writeheader()
        graveur.writerows(lignes)
    return cible


def zone_geographique(latitude: float | None, longitude: float | None) -> str | None:
    """Rattache une position à l'une des quatre zones météo de la métropole."""
    if latitude is None or longitude is None:
        return None
    if latitude > 48.13:
        return "nord"
    if latitude < 48.10:
        return "sud"
    if longitude < -1.70:
        return "ouest"
    return "centre"


# --------------------------------------------------------------------------
# Référentiels
# --------------------------------------------------------------------------
def charger_referentiels() -> tuple[dict[str, dict], dict[str, dict]]:
    base = Path(config.referentiel_repertoire)
    vehicules = {
        v["id_vehicule"]: v
        for v in json.loads((base / "referentiel_vehicules.json").read_text(encoding="utf-8"))
    }
    stations = {
        s["id_station"]: s
        for s in json.loads((base / "referentiel_stations.json").read_text(encoding="utf-8"))
    }
    return vehicules, stations


def normaliser_referentiels() -> list[RapportAgregation]:
    vehicules, stations = charger_referentiels()
    r1 = RapportAgregation("dim_vehicule", len(vehicules), len(vehicules))
    r1.fichier = _ecrire(
        config.repertoire_consolide / "dim_vehicule.csv",
        ["id_vehicule", "type", "modele", "date_mise_service"],
        list(vehicules.values()),
    )
    r2 = RapportAgregation("dim_station", len(stations), len(stations))
    r2.fichier = _ecrire(
        config.repertoire_consolide / "dim_station.csv",
        ["id_station", "nom", "quartier", "capacite", "latitude", "longitude"],
        list(stations.values()),
    )
    return [r1, r2]


# --------------------------------------------------------------------------
# 1. Télémétrie IoT
# --------------------------------------------------------------------------
COLONNES_TELEMETRIE = [
    "id_vehicule",
    "horodatage",
    "latitude",
    "longitude",
    "batterie_pct",
    "statut",
    "id_station",
    "zone",
]


def normaliser_telemetrie(source: Path | None = None) -> RapportAgregation:
    source = source or config.repertoire_brut / "iot_telemetrie" / "telemetrie.jsonl"
    rapport = RapportAgregation("fait_telemetrie")
    quarantaine = Quarantaine("fait_telemetrie", config.repertoire_quarantaine)
    vehicules, stations = charger_referentiels()
    vues: set[tuple[str, str]] = set()
    retenues: list[dict] = []

    with source.open("r", encoding="utf-8") as fh:
        for brut in fh:
            brut = brut.strip()
            if not brut:
                continue
            rapport.lues += 1
            try:
                ligne = json.loads(brut)
            except json.JSONDecodeError:
                quarantaine.rejeter(brut, "json_illisible")
                continue

            id_vehicule = (ligne.get("vehicle_id") or "").strip()
            if id_vehicule not in vehicules:
                quarantaine.rejeter(ligne, "vehicule_inconnu")
                continue

            brut_ts = str(ligne.get("ts") or "")
            horodatage = parser_horodatage(brut_ts)
            if horodatage is None:
                quarantaine.rejeter(ligne, "horodatage_illisible")
                continue
            if "/" in brut_ts:                      # date au format français
                rapport.corrections["date_francaise_convertie"] += 1

            cle = (id_vehicule, horodatage.isoformat())
            if cle in vues:
                quarantaine.rejeter(ligne, "doublon")
                continue

            if not position_plausible(ligne.get("lat"), ligne.get("lon")):
                quarantaine.rejeter(ligne, "position_absente_ou_aberrante")
                continue

            if not dans_bornes(ligne.get("battery_pct"), 0, 100):
                quarantaine.rejeter(ligne, "batterie_hors_bornes")
                continue

            brut_statut = str(ligne.get("status") or "")
            statut = normaliser_statut(brut_statut)
            if statut is None:
                quarantaine.rejeter(ligne, "statut_inconnu")
                continue
            if brut_statut != brut_statut.lower():  # casse hétérogène
                rapport.corrections["casse_statut_normalisee"] += 1

            id_station = ligne.get("station_id")
            if id_station is not None and id_station not in stations:
                id_station = None

            latitude = parser_decimal(ligne.get("lat"))
            longitude = parser_decimal(ligne.get("lon"))
            vues.add(cle)
            retenues.append(
                {
                    "id_vehicule": id_vehicule,
                    "horodatage": horodatage.isoformat(),
                    "latitude": latitude,
                    "longitude": longitude,
                    "batterie_pct": parser_entier(ligne.get("battery_pct")),
                    "statut": statut,
                    "id_station": id_station,
                    "zone": zone_geographique(latitude, longitude),
                }
            )

    retenues.sort(key=lambda r: (r["id_vehicule"], r["horodatage"]))
    rapport.conservees = len(retenues)
    rapport.rejetees = len(quarantaine)
    rapport.motifs = quarantaine.resume()
    rapport.fichier = _ecrire(
        config.repertoire_consolide / "fait_telemetrie.csv", COLONNES_TELEMETRIE, retenues
    )
    quarantaine.ecrire()
    return rapport


# --------------------------------------------------------------------------
# 2. Météo
# --------------------------------------------------------------------------
COLONNES_METEO = ["zone", "horodatage", "temperature_c", "precipitation_mm", "vent_kmh"]


def normaliser_meteo(source: Path | None = None) -> RapportAgregation:
    source = source or config.repertoire_brut / "meteo" / "meteo.jsonl"
    rapport = RapportAgregation("fait_meteo")
    quarantaine = Quarantaine("fait_meteo", config.repertoire_quarantaine)
    vues: set[tuple[str, str]] = set()
    retenues: list[dict] = []

    with source.open("r", encoding="utf-8") as fh:
        for brut in fh:
            brut = brut.strip()
            if not brut:
                continue
            rapport.lues += 1
            ligne = json.loads(brut)
            horodatage = parser_horodatage(ligne.get("hour"))
            zone = (ligne.get("zone") or "").strip().lower()
            if horodatage is None or not zone:
                quarantaine.rejeter(ligne, "cle_incomplete")
                continue
            cle = (zone, horodatage.isoformat())
            if cle in vues:
                quarantaine.rejeter(ligne, "doublon")
                continue
            # L'unité varie selon la station émettrice : tout est ramené en Celsius.
            if str(ligne.get("temp_unit") or "C").upper().startswith("F"):
                rapport.corrections["fahrenheit_converti_en_celsius"] += 1
            temperature = en_celsius(ligne.get("temp"), ligne.get("temp_unit"))
            if temperature is None or not -40 <= temperature <= 55:
                quarantaine.rejeter(ligne, "temperature_invalide")
                continue
            vues.add(cle)
            retenues.append(
                {
                    "zone": zone,
                    "horodatage": horodatage.isoformat(),
                    "temperature_c": temperature,
                    "precipitation_mm": parser_decimal(ligne.get("precip_mm")) or 0.0,
                    "vent_kmh": parser_decimal(ligne.get("wind_kmh")),
                }
            )

    retenues.sort(key=lambda r: (r["zone"], r["horodatage"]))
    rapport.conservees = len(retenues)
    rapport.rejetees = len(quarantaine)
    rapport.motifs = quarantaine.resume()
    rapport.fichier = _ecrire(
        config.repertoire_consolide / "fait_meteo.csv", COLONNES_METEO, retenues
    )
    quarantaine.ecrire()
    return rapport


# --------------------------------------------------------------------------
# 3. Interventions de maintenance
# --------------------------------------------------------------------------
COLONNES_INTERVENTION = [
    "id_intervention",
    "id_vehicule",
    "date_intervention",
    "type_panne",
    "piece_changee",
    "duree_min",
    "cout_eur",
    "prestataire",
    "fichier_origine",
]


def normaliser_interventions(source: Path | None = None) -> RapportAgregation:
    source = source or config.repertoire_brut / "maintenance" / "interventions.csv"
    rapport = RapportAgregation("fait_intervention")
    quarantaine = Quarantaine("fait_intervention", config.repertoire_quarantaine)
    vehicules, _ = charger_referentiels()
    vues: set[str] = set()
    retenues: list[dict] = []

    with source.open("r", encoding="utf-8", newline="") as fh:
        for ligne in csv.DictReader(fh, delimiter=";"):
            rapport.lues += 1
            identifiant = (ligne.get("id_intervention") or "").strip()
            if not identifiant:
                quarantaine.rejeter(ligne, "ligne_vide")
                continue
            if identifiant in vues:
                quarantaine.rejeter(ligne, "doublon")
                continue

            id_vehicule = (ligne.get("id_vehicule") or "").strip()
            if id_vehicule not in vehicules:
                quarantaine.rejeter(ligne, "vehicule_inconnu")
                continue

            # Les dates arrivent en ISO (mars, mai) ou en format français (avril).
            brut_date = str(ligne.get("date_intervention") or "")
            date_intervention = parser_horodatage(brut_date)
            if date_intervention is None:
                quarantaine.rejeter(ligne, "date_invalide")
                continue
            if "/" in brut_date:
                rapport.corrections["date_francaise_convertie"] += 1

            # « 45 min » ou « 45 » selon le mois ; « 32,50 » ou « 32.50 » pour le coût.
            brut_duree = str(ligne.get("duree") or "")
            brut_cout = str(ligne.get("cout") or "")
            if "min" in brut_duree:
                rapport.corrections["unite_duree_retiree"] += 1
            if "," in brut_cout:
                rapport.corrections["virgule_decimale_convertie"] += 1
            duree = parser_entier(brut_duree)
            cout = parser_decimal(brut_cout)
            if duree is None or not 0 < duree <= 24 * 60:
                quarantaine.rejeter(ligne, "duree_invalide")
                continue
            if cout is None or cout < 0:
                quarantaine.rejeter(ligne, "cout_illisible")
                continue

            vues.add(identifiant)
            retenues.append(
                {
                    "id_intervention": identifiant,
                    "id_vehicule": id_vehicule,
                    "date_intervention": date_intervention.date().isoformat(),
                    "type_panne": (ligne.get("type_panne") or "").strip().lower() or None,
                    "piece_changee": (ligne.get("piece_changee") or "").strip() or None,
                    "duree_min": duree,
                    "cout_eur": round(cout, 2),
                    "prestataire": (ligne.get("prestataire") or "").strip() or None,
                    "fichier_origine": ligne.get("fichier_origine"),
                }
            )

    retenues.sort(key=lambda r: r["id_intervention"])
    rapport.conservees = len(retenues)
    rapport.rejetees = len(quarantaine)
    rapport.motifs = quarantaine.resume()
    rapport.fichier = _ecrire(
        config.repertoire_consolide / "fait_intervention.csv", COLONNES_INTERVENTION, retenues
    )
    quarantaine.ecrire()
    return rapport


# --------------------------------------------------------------------------
# 4. Tarifs du concurrent
# --------------------------------------------------------------------------
COLONNES_TARIF = [
    "date_collecte",
    "operateur",
    "offre",
    "type_vehicule",
    "prix_deblocage_eur",
    "prix_minute_eur",
    "abonnement_mensuel_eur",
    "zones",
    "url_source",
]


def normaliser_tarifs(source: Path | None = None) -> RapportAgregation:
    source = source or config.repertoire_brut / "tarifs_concurrent" / "tarifs.csv"
    rapport = RapportAgregation("ref_tarif_concurrent")
    quarantaine = Quarantaine("ref_tarif_concurrent", config.repertoire_quarantaine)
    retenues: list[dict] = []

    with source.open("r", encoding="utf-8", newline="") as fh:
        for ligne in csv.DictReader(fh):
            rapport.lues += 1
            collecte = parser_horodatage(ligne.get("date_collecte"))
            if collecte is None or not (ligne.get("offre") or "").strip():
                quarantaine.rejeter(ligne, "ligne_incomplete")
                continue
            rapport.corrections["montant_avec_devise_converti"] += 1
            prix_minute = parser_decimal(ligne.get("prix_minute"))
            if prix_minute is None:
                quarantaine.rejeter(ligne, "prix_minute_illisible")
                continue
            retenues.append(
                {
                    "date_collecte": collecte.isoformat(),
                    "operateur": ligne.get("operateur"),
                    "offre": ligne.get("offre"),
                    "type_vehicule": (ligne.get("type_vehicule") or "").strip().lower(),
                    "prix_deblocage_eur": parser_decimal(ligne.get("prix_deblocage")) or 0.0,
                    "prix_minute_eur": prix_minute,
                    "abonnement_mensuel_eur": parser_decimal(ligne.get("abonnement_mensuel")),
                    "zones": ligne.get("zones"),
                    "url_source": ligne.get("url_source"),
                }
            )

    rapport.conservees = len(retenues)
    rapport.rejetees = len(quarantaine)
    rapport.motifs = quarantaine.resume()
    rapport.fichier = _ecrire(
        config.repertoire_consolide / "ref_tarif_concurrent.csv", COLONNES_TARIF, retenues
    )
    quarantaine.ecrire()
    return rapport


# --------------------------------------------------------------------------
# 5. Locations
# --------------------------------------------------------------------------
COLONNES_LOCATION = [
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
    "duree_min",
    "distance_km",
    "montant_eur",
]


def normaliser_locations(source: Path | None = None) -> RapportAgregation:
    source = source or config.repertoire_brut / "locations" / "locations.csv"
    rapport = RapportAgregation("fait_location")
    quarantaine = Quarantaine("fait_location", config.repertoire_quarantaine)
    vehicules, stations = charger_referentiels()
    vues: set[str] = set()
    retenues: list[dict] = []

    with source.open("r", encoding="utf-8", newline="") as fh:
        for ligne in csv.DictReader(fh):
            rapport.lues += 1
            identifiant = (ligne.get("id_location") or "").strip()
            if not identifiant or identifiant in vues:
                quarantaine.rejeter(ligne, "identifiant_absent_ou_doublon")
                continue
            debut = parser_horodatage(ligne.get("debut"))
            fin = parser_horodatage(ligne.get("fin"))
            if debut is None or fin is None:
                quarantaine.rejeter(ligne, "horodatage_incomplet")
                continue
            duree = (fin - debut).total_seconds() / 60
            if not 0 < duree <= 24 * 60:
                quarantaine.rejeter(ligne, "duree_incoherente")
                continue
            if (ligne.get("id_vehicule") or "") not in vehicules:
                quarantaine.rejeter(ligne, "vehicule_inconnu")
                continue

            vues.add(identifiant)
            retenues.append(
                {
                    "id_location": identifiant,
                    "id_abonne_psd": ligne.get("id_abonne_psd"),
                    "tranche_age": ligne.get("tranche_age") or None,
                    "type_abonnement": ligne.get("type_abonnement"),
                    "ville_abonne": ligne.get("ville_abonne"),
                    "id_vehicule": ligne.get("id_vehicule"),
                    "id_station_depart": (ligne.get("id_station_depart") or None)
                    if ligne.get("id_station_depart") in stations
                    else None,
                    "id_station_arrivee": (ligne.get("id_station_arrivee") or None)
                    if ligne.get("id_station_arrivee") in stations
                    else None,
                    "debut": debut.isoformat(),
                    "fin": fin.isoformat(),
                    "duree_min": round(duree, 1),
                    "distance_km": parser_decimal(ligne.get("distance_km")),
                    "montant_eur": parser_decimal(ligne.get("montant_eur")) or 0.0,
                }
            )

    retenues.sort(key=lambda r: r["debut"])
    rapport.conservees = len(retenues)
    rapport.rejetees = len(quarantaine)
    rapport.motifs = quarantaine.resume()
    rapport.fichier = _ecrire(
        config.repertoire_consolide / "fait_location.csv", COLONNES_LOCATION, retenues
    )
    quarantaine.ecrire()
    return rapport


# --------------------------------------------------------------------------
# 6. Traces GPS (big data)
# --------------------------------------------------------------------------
COLONNES_TRACE = ["id_vehicule", "id_trajet", "horodatage", "latitude", "longitude", "vitesse_kmh"]


def normaliser_traces(source: Path | None = None) -> RapportAgregation:
    source = source or config.repertoire_brut / "traces_gps" / "traces_gps.csv"
    rapport = RapportAgregation("fait_trace_gps")
    quarantaine = Quarantaine("fait_trace_gps", config.repertoire_quarantaine)
    vehicules, _ = charger_referentiels()
    vues: set[tuple[str, str]] = set()
    retenues: list[dict] = []

    with source.open("r", encoding="utf-8", newline="") as fh:
        for ligne in csv.DictReader(fh):
            rapport.lues += 1
            id_vehicule = (ligne.get("id_vehicule") or "").strip()
            horodatage = parser_horodatage(ligne.get("horodatage"))
            if id_vehicule not in vehicules or horodatage is None:
                quarantaine.rejeter(ligne, "cle_invalide")
                continue
            cle = (id_vehicule, horodatage.isoformat())
            if cle in vues:
                quarantaine.rejeter(ligne, "doublon")
                continue
            if not position_plausible(ligne.get("latitude"), ligne.get("longitude")):
                quarantaine.rejeter(ligne, "position_aberrante")
                continue
            vitesse = parser_decimal(ligne.get("vitesse_kmh"))
            if vitesse is None or not 0 <= vitesse <= 60:
                quarantaine.rejeter(ligne, "vitesse_invalide")
                continue
            vues.add(cle)
            retenues.append(
                {
                    "id_vehicule": id_vehicule,
                    "id_trajet": ligne.get("id_trajet"),
                    "horodatage": horodatage.isoformat(),
                    "latitude": parser_decimal(ligne.get("latitude")),
                    "longitude": parser_decimal(ligne.get("longitude")),
                    "vitesse_kmh": vitesse,
                }
            )

    rapport.conservees = len(retenues)
    rapport.rejetees = len(quarantaine)
    rapport.motifs = quarantaine.resume()
    rapport.fichier = _ecrire(
        config.repertoire_consolide / "fait_trace_gps.csv", COLONNES_TRACE, retenues
    )
    quarantaine.ecrire()
    return rapport


# --------------------------------------------------------------------------
# Consolidation : le jeu de données unique
# --------------------------------------------------------------------------
COLONNES_SOCLE = [
    "id_vehicule",
    "type_vehicule",
    "heure",
    "zone",
    "nb_releves",
    "batterie_min_pct",
    "batterie_moy_pct",
    "statut_dominant",
    "latitude_moy",
    "longitude_moy",
    "temperature_c",
    "precipitation_mm",
    "vent_kmh",
    "nb_locations",
    "duree_location_min",
    "recette_eur",
    "en_maintenance",
]


def _lire_csv(chemin: Path) -> list[dict]:
    with chemin.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def construire_socle() -> RapportAgregation:
    """Assemble les tables normalisées à la maille véhicule × heure."""
    rapport = RapportAgregation("socle_flotte_horaire")
    consolide = config.repertoire_consolide
    vehicules, _ = charger_referentiels()

    meteo = {
        (l["zone"], l["horodatage"][:13]): l for l in _lire_csv(consolide / "fait_meteo.csv")
    }
    maintenance = {
        (l["id_vehicule"], l["date_intervention"])
        for l in _lire_csv(consolide / "fait_intervention.csv")
    }

    locations: dict[tuple[str, str], dict] = {}
    for l in _lire_csv(consolide / "fait_location.csv"):
        cle = (l["id_vehicule"], l["debut"][:13])
        agr = locations.setdefault(cle, {"nb": 0, "duree": 0.0, "recette": 0.0})
        agr["nb"] += 1
        agr["duree"] += float(l["duree_min"] or 0)
        agr["recette"] += float(l["montant_eur"] or 0)

    groupes: dict[tuple[str, str], dict] = {}
    with (consolide / "fait_telemetrie.csv").open("r", encoding="utf-8", newline="") as fh:
        for l in csv.DictReader(fh):
            rapport.lues += 1
            cle = (l["id_vehicule"], l["horodatage"][:13])
            g = groupes.setdefault(
                cle,
                {"batteries": [], "statuts": Counter(), "lat": [], "lon": [], "zones": Counter()},
            )
            if l["batterie_pct"]:
                g["batteries"].append(int(l["batterie_pct"]))
            g["statuts"][l["statut"]] += 1
            if l["latitude"]:
                g["lat"].append(float(l["latitude"]))
            if l["longitude"]:
                g["lon"].append(float(l["longitude"]))
            if l["zone"]:
                g["zones"][l["zone"]] += 1

    lignes: list[dict] = []
    for (id_vehicule, heure), g in sorted(groupes.items()):
        zone = g["zones"].most_common(1)[0][0] if g["zones"] else None
        obs_meteo = meteo.get((zone, heure), {}) if zone else {}
        loc = locations.get((id_vehicule, heure), {})
        lignes.append(
            {
                "id_vehicule": id_vehicule,
                "type_vehicule": vehicules.get(id_vehicule, {}).get("type"),
                "heure": heure + ":00:00",
                "zone": zone,
                "nb_releves": sum(g["statuts"].values()),
                "batterie_min_pct": min(g["batteries"]) if g["batteries"] else None,
                "batterie_moy_pct": round(sum(g["batteries"]) / len(g["batteries"]), 1)
                if g["batteries"]
                else None,
                "statut_dominant": g["statuts"].most_common(1)[0][0],
                "latitude_moy": round(sum(g["lat"]) / len(g["lat"]), 6) if g["lat"] else None,
                "longitude_moy": round(sum(g["lon"]) / len(g["lon"]), 6) if g["lon"] else None,
                "temperature_c": obs_meteo.get("temperature_c"),
                "precipitation_mm": obs_meteo.get("precipitation_mm"),
                "vent_kmh": obs_meteo.get("vent_kmh"),
                "nb_locations": loc.get("nb", 0),
                "duree_location_min": round(loc.get("duree", 0.0), 1),
                "recette_eur": round(loc.get("recette", 0.0), 2),
                "en_maintenance": int((id_vehicule, heure[:10]) in maintenance),
            }
        )

    rapport.conservees = len(lignes)
    rapport.fichier = _ecrire(consolide / "socle_flotte_horaire.csv", COLONNES_SOCLE, lignes)
    rapport.motifs = "jointures météo, locations et maintenance"
    return rapport


# --------------------------------------------------------------------------
def executer() -> list[RapportAgregation]:
    config.preparer_repertoires()
    rapports = [
        *normaliser_referentiels(),
        normaliser_telemetrie(),
        normaliser_meteo(),
        normaliser_interventions(),
        normaliser_tarifs(),
        normaliser_locations(),
        normaliser_traces(),
    ]
    rapports.append(construire_socle())
    for r in rapports:
        logger.info(
            "%-22s lues=%-7d conservées=%-7d rejetées=%-5d corrigées=%-5d %s",
            r.table, r.lues, r.conservees, r.rejetees,
            sum(r.corrections.values()), r.motifs or r.resume_corrections(),
        )
    return rapports
