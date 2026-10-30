"""Génère les jeux de données de test simulant les sources fournies par la DSI.

Les sources réelles (API IoT, site concurrent, CSV du prestataire, base
PostgreSQL, entrepôt Hive) sont remises par l'équipe pédagogique. Ce script
reconstitue localement des jeux équivalents, volontairement imparfaits :
doublons, valeurs hors bornes, encodages et formats de date hétérogènes,
unités mélangées. Ils servent à démontrer les contrôles qualité de la chaîne.

Usage : python mock/generate_fixtures.py
"""

from __future__ import annotations

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "mock" / "data"
WAREHOUSE = ROOT / "mock" / "warehouse"

SEED = 20260501
DEBUT = datetime(2026, 5, 11, tzinfo=timezone.utc)
JOURS = 5
NB_VEHICULES = 120
NB_STATIONS = 25
NB_ABONNES = 400

STATUTS = ["available", "in_use", "out_of_service", "charging"]
ZONES = ["centre", "nord", "sud", "ouest"]
TYPES_PANNE = [
    "frein",
    "batterie",
    "pneu crevé",
    "vandalisme",
    "éclairage",
    "chaîne",
]
PIECES = ["plaquette", "batterie 36V", "chambre à air", "phare avant", "chaîne", ""]


def vehicules() -> list[dict]:
    rnd = random.Random(SEED)
    out = []
    for i in range(1, NB_VEHICULES + 1):
        out.append(
            {
                "id_vehicule": f"VLO-{i:05d}",
                "type": "velo" if i % 4 else "trottinette",
                "modele": rnd.choice(["Arcade V2", "Arcade V3", "Scoot S1"]),
                "date_mise_service": (
                    datetime(2024, 1, 1) + timedelta(days=rnd.randint(0, 700))
                ).date().isoformat(),
            }
        )
    return out


def stations() -> list[dict]:
    rnd = random.Random(SEED + 1)
    quartiers = ["Centre", "Villejean", "Beaulieu", "Sud-Gare", "Maurepas"]
    return [
        {
            "id_station": f"ST-{i:03d}",
            "nom": f"Station {i:03d}",
            "quartier": rnd.choice(quartiers),
            "capacite": rnd.choice([12, 16, 20, 24]),
            "latitude": round(48.09 + rnd.random() * 0.06, 6),
            "longitude": round(-1.72 + rnd.random() * 0.09, 6),
        }
        for i in range(1, NB_STATIONS + 1)
    ]


# --------------------------------------------------------------------------
# Source 1 — télémétrie des boîtiers IoT (servie par l'API REST simulée)
# --------------------------------------------------------------------------
def telemetrie(vehs: list[dict], stats: list[dict]) -> list[dict]:
    """Un relevé toutes les 15 minutes par véhicule, avec anomalies injectées."""
    rnd = random.Random(SEED + 2)
    lignes: list[dict] = []
    for v in vehs:
        lat = 48.09 + rnd.random() * 0.06
        lon = -1.72 + rnd.random() * 0.09
        batterie = rnd.randint(40, 100)
        for pas in range(JOURS * 24 * 4):
            ts = DEBUT + timedelta(minutes=15 * pas)
            lat += (rnd.random() - 0.5) * 0.002
            lon += (rnd.random() - 0.5) * 0.002
            batterie = max(3, batterie - rnd.choice([0, 0, 0, 1]))
            if batterie < 15 and rnd.random() < 0.3:
                batterie = rnd.randint(85, 100)  # passage en atelier
            ligne = {
                "vehicle_id": v["id_vehicule"],
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "battery_pct": batterie,
                "status": rnd.choices(STATUTS, weights=[60, 25, 8, 7])[0],
                "station_id": rnd.choice(stats)["id_station"] if rnd.random() < 0.6 else None,
            }
            lignes.append(ligne)

    # --- anomalies volontaires ---------------------------------------------
    n = len(lignes)
    for idx in rnd.sample(range(n), k=n // 200):          # coordonnées absentes
        lignes[idx]["lat"] = None
        lignes[idx]["lon"] = None
    for idx in rnd.sample(range(n), k=n // 250):          # batterie hors bornes
        lignes[idx]["battery_pct"] = rnd.choice([-1, 127, 255])
    for idx in rnd.sample(range(n), k=n // 300):          # date au format français
        brut = lignes[idx]["ts"].replace("Z", "")
        dt = datetime.fromisoformat(brut)
        lignes[idx]["ts"] = dt.strftime("%d/%m/%Y %H:%M:%S")
    for idx in rnd.sample(range(n), k=n // 400):          # statut en casse variable
        lignes[idx]["status"] = lignes[idx]["status"].upper()
    for idx in rnd.sample(range(n), k=n // 500):          # position aberrante
        lignes[idx]["lat"] = round(rnd.uniform(-60, 60), 6)
        lignes[idx]["lon"] = round(rnd.uniform(-120, 120), 6)
    doublons = [dict(lignes[i]) for i in rnd.sample(range(n), k=n // 150)]
    lignes.extend(doublons)                               # doublons stricts
    rnd.shuffle(lignes)
    return lignes


# --------------------------------------------------------------------------
# Source 2 — API météo publique
# --------------------------------------------------------------------------
def meteo() -> list[dict]:
    rnd = random.Random(SEED + 3)
    out = []
    for zone in ZONES:
        for h in range(JOURS * 24):
            ts = DEBUT + timedelta(hours=h)
            temp = round(9 + 8 * rnd.random(), 1)
            ligne = {
                "zone": zone,
                "hour": ts.isoformat().replace("+00:00", "Z"),
                "temp": temp,
                # la zone "ouest" est servie par une station en degrés Fahrenheit
                "temp_unit": "F" if zone == "ouest" else "C",
                "precip_mm": round(max(0.0, rnd.gauss(0.3, 0.8)), 2),
                "wind_kmh": round(abs(rnd.gauss(14, 7)), 1),
            }
            if ligne["temp_unit"] == "F":
                ligne["temp"] = round(temp * 9 / 5 + 32, 1)
            if rnd.random() < 0.02:          # relevés manquants
                continue
            out.append(ligne)
    return out


# --------------------------------------------------------------------------
# Source 3 — fichiers CSV mensuels du prestataire de maintenance
# --------------------------------------------------------------------------
ENTETE_CSV = [
    "id_intervention",
    "id_vehicule",
    "date_intervention",
    "type_panne",
    "piece_changee",
    "duree",
    "cout",
    "prestataire",
]


def interventions(vehs: list[dict]) -> dict[str, list[list[str]]]:
    """Trois mois de CSV, chacun avec ses propres travers de saisie."""
    rnd = random.Random(SEED + 4)
    mois = {"2026-03": [], "2026-04": [], "2026-05": []}
    compteur = 1
    for cle, lignes in mois.items():
        annee, m = (int(x) for x in cle.split("-"))
        for _ in range(rnd.randint(110, 160)):
            jour = rnd.randint(1, 28)
            date = datetime(annee, m, jour)
            fr = cle == "2026-04"  # avril : dates françaises et virgule décimale
            lignes.append(
                [
                    f"INT-{compteur:05d}",
                    rnd.choice(vehs)["id_vehicule"],
                    date.strftime("%d/%m/%Y") if fr else date.date().isoformat(),
                    rnd.choice(TYPES_PANNE),
                    rnd.choice(PIECES),
                    f"{rnd.randint(10, 180)} min" if fr else str(rnd.randint(10, 180)),
                    (f"{rnd.randint(5, 120)},{rnd.randint(0, 99):02d}" if fr
                     else f"{rnd.randint(5, 120)}.{rnd.randint(0, 99):02d}"),
                    "AtelierPlus",
                ]
            )
            compteur += 1
        # anomalies : lignes vides, doublons, véhicule inconnu, coût illisible
        lignes.append(["", "", "", "", "", "", "", ""])
        lignes.append(list(lignes[0]))
        lignes.append(
            [f"INT-{compteur:05d}", "VLO-99999", "2026-02-30", "frein", "", "30", "n/a", "AtelierPlus"]
        )
        compteur += 1
        rnd.shuffle(lignes)
    return mois


# --------------------------------------------------------------------------
# Source 4 — base transactionnelle PostgreSQL (abonnés, locations, paiements)
# --------------------------------------------------------------------------
def sql_source(vehs: list[dict], stats: list[dict]) -> str:
    rnd = random.Random(SEED + 5)
    prenoms = ["Camille", "Yanis", "Awa", "Thomas", "Léa", "Mehdi", "Jade", "Hugo"]
    noms = ["Martin", "Diallo", "Bernard", "Nguyen", "Petit", "Garcia", "Roux"]
    villes = ["Rennes", "Cesson-Sévigné", "Bruz", "Saint-Grégoire"]
    out = [
        "-- Base transactionnelle de test (source fournie par la DSI).",
        "-- Contient des données à caractère personnel : elle n'est jamais copiée",
        "-- telle quelle dans le socle analytique (cf. docs/04-rgpd...).",
        "DROP SCHEMA IF EXISTS source CASCADE;",
        "CREATE SCHEMA source;",
        """CREATE TABLE source.abonne (
    id_abonne      integer PRIMARY KEY,
    nom            text NOT NULL,
    prenom         text NOT NULL,
    email          text NOT NULL,
    telephone      text,
    date_naissance date,
    ville          text,
    type_abonnement text NOT NULL,
    date_adhesion  date NOT NULL
);""",
        """CREATE TABLE source.location (
    id_location       integer PRIMARY KEY,
    id_abonne         integer NOT NULL REFERENCES source.abonne(id_abonne),
    id_vehicule       text NOT NULL,
    id_station_depart text,
    id_station_arrivee text,
    debut             timestamptz NOT NULL,
    fin               timestamptz,
    distance_km       numeric(6,2)
);""",
        """CREATE TABLE source.paiement (
    id_paiement  integer PRIMARY KEY,
    id_location  integer NOT NULL REFERENCES source.location(id_location),
    montant_eur  numeric(8,2) NOT NULL,
    moyen        text NOT NULL,
    date_paiement timestamptz NOT NULL
);""",
    ]

    for i in range(1, NB_ABONNES + 1):
        p, nm = rnd.choice(prenoms), rnd.choice(noms)
        naiss = datetime(1960, 1, 1) + timedelta(days=rnd.randint(0, 17000))
        out.append(
            "INSERT INTO source.abonne VALUES "
            f"({i}, '{nm}', '{p}', '{p.lower()}.{nm.lower()}{i}@example.org', "
            f"'06{rnd.randint(10000000, 99999999)}', DATE '{naiss.date()}', "
            f"'{rnd.choice(villes)}', '{rnd.choice(['mensuel', 'annuel', 'occasionnel'])}', "
            f"DATE '{(datetime(2023, 1, 1) + timedelta(days=rnd.randint(0, 1100))).date()}');"
        )

    id_loc, id_pay = 1, 1
    for _ in range(3000):
        debut = DEBUT + timedelta(minutes=rnd.randint(0, JOURS * 24 * 60))
        duree = rnd.randint(3, 75)
        fin = debut + timedelta(minutes=duree)
        dist = round(duree * rnd.uniform(0.10, 0.28), 2)
        a = rnd.randint(1, NB_ABONNES)
        out.append(
            "INSERT INTO source.location VALUES "
            f"({id_loc}, {a}, '{rnd.choice(vehs)['id_vehicule']}', "
            f"'{rnd.choice(stats)['id_station']}', '{rnd.choice(stats)['id_station']}', "
            f"TIMESTAMPTZ '{debut.isoformat()}', TIMESTAMPTZ '{fin.isoformat()}', {dist});"
        )
        montant = round(1.0 + duree * 0.18, 2)
        out.append(
            "INSERT INTO source.paiement VALUES "
            f"({id_pay}, {id_loc}, {montant}, '{rnd.choice(['CB', 'SEPA', 'wallet'])}', "
            f"TIMESTAMPTZ '{fin.isoformat()}');"
        )
        id_loc += 1
        id_pay += 1

    # locations ouvertes (fin NULL) : cas limite à filtrer à l'extraction
    for _ in range(40):
        debut = DEBUT + timedelta(minutes=rnd.randint(0, JOURS * 24 * 60))
        out.append(
            "INSERT INTO source.location VALUES "
            f"({id_loc}, {rnd.randint(1, NB_ABONNES)}, '{rnd.choice(vehs)['id_vehicule']}', "
            f"'{rnd.choice(stats)['id_station']}', NULL, "
            f"TIMESTAMPTZ '{debut.isoformat()}', NULL, NULL);"
        )
        id_loc += 1

    out.append("CREATE INDEX idx_location_debut ON source.location(debut);")
    out.append("CREATE INDEX idx_location_abonne ON source.location(id_abonne);")
    out.append("CREATE INDEX idx_paiement_location ON source.paiement(id_location);")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# Source 5 — entrepôt big data : table externe Hive partitionnée par jour
# --------------------------------------------------------------------------
def traces_gps(vehs: list[dict]) -> dict[str, list[list]]:
    """Points GPS haute fréquence, stockés comme une table externe Hive."""
    rnd = random.Random(SEED + 6)
    partitions: dict[str, list[list]] = {}
    for j in range(JOURS):
        jour = (DEBUT + timedelta(days=j)).date().isoformat()
        lignes = []
        for v in rnd.sample(vehs, k=70):
            trip = f"TRIP-{rnd.randint(100000, 999999)}"
            lat = 48.09 + rnd.random() * 0.06
            lon = -1.72 + rnd.random() * 0.09
            base = DEBUT + timedelta(days=j, minutes=rnd.randint(0, 1200))
            for k in range(rnd.randint(20, 60)):
                lat += (rnd.random() - 0.5) * 0.001
                lon += (rnd.random() - 0.5) * 0.001
                lignes.append(
                    [
                        v["id_vehicule"],
                        (base + timedelta(seconds=15 * k)).isoformat().replace("+00:00", "Z"),
                        round(lat, 6),
                        round(lon, 6),
                        round(abs(rnd.gauss(14, 5)), 1),
                        trip,
                    ]
                )
        partitions[jour] = lignes
    return partitions


def vers_sqlite(sql_postgres: str) -> str:
    """Transpose le jeu source en SQLite, pour le mode démonstration."""
    import re

    sql = sql_postgres
    sql = re.sub(r"DROP SCHEMA IF EXISTS source CASCADE;\n?", "", sql)
    sql = re.sub(r"CREATE SCHEMA source;\n?", "", sql)
    sql = sql.replace("source.", "")
    sql = sql.replace("timestamptz", "TEXT").replace("date ", "TEXT ")
    sql = re.sub(r"numeric\(\d+,\d+\)", "REAL", sql)
    sql = sql.replace("text ", "TEXT ").replace("integer ", "INTEGER ")
    sql = re.sub(r"\bDATE '([^']*)'", r"'\1'", sql)
    sql = re.sub(r"\bTIMESTAMPTZ '([^']*)'", r"'\1'", sql)
    entete = (
        "-- Jeu source transposé en SQLite (mode démonstration).\n"
        "DROP TABLE IF EXISTS paiement;\n"
        "DROP TABLE IF EXISTS location;\n"
        "DROP TABLE IF EXISTS abonne;\n"
    )
    return entete + sql


# --------------------------------------------------------------------------
def main() -> None:
    vehs, stats = vehicules(), stations()
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "maintenance").mkdir(parents=True, exist_ok=True)

    (DATA / "referentiel_vehicules.json").write_text(
        json.dumps(vehs, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (DATA / "referentiel_stations.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (DATA / "telemetrie.json").write_text(
        json.dumps(telemetrie(vehs, stats), ensure_ascii=False), encoding="utf-8"
    )
    (DATA / "meteo.json").write_text(
        json.dumps(meteo(), ensure_ascii=False), encoding="utf-8"
    )

    for cle, lignes in interventions(vehs).items():
        # avril est volontairement encodé en latin-1 (export Excel du prestataire)
        encodage = "latin-1" if cle == "2026-04" else "utf-8"
        chemin = DATA / "maintenance" / f"interventions_{cle}.csv"
        with chemin.open("w", encoding=encodage, newline="", errors="replace") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(ENTETE_CSV)
            w.writerows(lignes)

    for jour, lignes in traces_gps(vehs).items():
        rep = WAREHOUSE / "gps_traces" / f"dt={jour}"
        rep.mkdir(parents=True, exist_ok=True)
        with (rep / "part-00000.csv").open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh).writerows(lignes)

    fixtures = ROOT / "sql" / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    postgres = sql_source(vehs, stats)
    (fixtures / "seed_source_postgres.sql").write_text(postgres, encoding="utf-8")
    (fixtures / "seed_source_sqlite.sql").write_text(vers_sqlite(postgres), encoding="utf-8")
    print("Jeux de test générés dans mock/data, mock/warehouse et sql/fixtures.")


if __name__ == "__main__":
    main()
