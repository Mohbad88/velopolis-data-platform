-- =====================================================================
-- Même modèle physique, transposé en SQLite.
-- Ce fichier n'existe que pour le « mode démonstration » : il permet de
-- dérouler la chaîne complète et les tests sans installer PostgreSQL.
-- La cible de production reste sql/ddl/schema_postgresql.sql, qui seul
-- porte les types exacts, les séquences et la partitionnement.
-- =====================================================================

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS socle_flotte_horaire;
DROP TABLE IF EXISTS fait_trace_gps;
DROP TABLE IF EXISTS fait_location;
DROP TABLE IF EXISTS fait_intervention;
DROP TABLE IF EXISTS fait_meteo;
DROP TABLE IF EXISTS fait_telemetrie;
DROP TABLE IF EXISTS ref_tarif_concurrent;
DROP TABLE IF EXISTS quarantaine_qualite;
DROP TABLE IF EXISTS journal_ingestion;
DROP TABLE IF EXISTS dim_station;
DROP TABLE IF EXISTS dim_vehicule;

CREATE TABLE dim_vehicule (
    id_vehicule       TEXT PRIMARY KEY,
    type              TEXT NOT NULL CHECK (type IN ('velo', 'trottinette')),
    modele            TEXT,
    date_mise_service TEXT
);

CREATE TABLE dim_station (
    id_station TEXT PRIMARY KEY,
    nom        TEXT NOT NULL,
    quartier   TEXT,
    capacite   INTEGER CHECK (capacite > 0),
    latitude   REAL,
    longitude  REAL
);

CREATE TABLE fait_telemetrie (
    id_telemetrie INTEGER PRIMARY KEY AUTOINCREMENT,
    id_vehicule   TEXT NOT NULL REFERENCES dim_vehicule(id_vehicule),
    horodatage    TEXT NOT NULL,
    latitude      REAL NOT NULL,
    longitude     REAL NOT NULL,
    batterie_pct  INTEGER NOT NULL CHECK (batterie_pct BETWEEN 0 AND 100),
    statut        TEXT NOT NULL
                  CHECK (statut IN ('disponible', 'en_location', 'hors_service', 'en_charge')),
    id_station    TEXT REFERENCES dim_station(id_station),
    zone          TEXT,
    UNIQUE (id_vehicule, horodatage)
);

CREATE TABLE fait_meteo (
    id_meteo         INTEGER PRIMARY KEY AUTOINCREMENT,
    zone             TEXT NOT NULL,
    horodatage       TEXT NOT NULL,
    temperature_c    REAL CHECK (temperature_c BETWEEN -40 AND 55),
    precipitation_mm REAL CHECK (precipitation_mm >= 0),
    vent_kmh         REAL CHECK (vent_kmh >= 0),
    UNIQUE (zone, horodatage)
);

CREATE TABLE fait_intervention (
    id_intervention   TEXT PRIMARY KEY,
    id_vehicule       TEXT NOT NULL REFERENCES dim_vehicule(id_vehicule),
    date_intervention TEXT NOT NULL,
    type_panne        TEXT,
    piece_changee     TEXT,
    duree_min         INTEGER CHECK (duree_min > 0),
    cout_eur          REAL CHECK (cout_eur >= 0),
    prestataire       TEXT,
    fichier_origine   TEXT
);

CREATE TABLE fait_location (
    id_location        INTEGER PRIMARY KEY,
    id_abonne_psd      TEXT NOT NULL,
    tranche_age        TEXT,
    type_abonnement    TEXT,
    ville_abonne       TEXT,
    id_vehicule        TEXT NOT NULL REFERENCES dim_vehicule(id_vehicule),
    id_station_depart  TEXT REFERENCES dim_station(id_station),
    id_station_arrivee TEXT REFERENCES dim_station(id_station),
    debut              TEXT NOT NULL,
    fin                TEXT NOT NULL,
    duree_min          REAL CHECK (duree_min > 0),
    distance_km        REAL,
    montant_eur        REAL CHECK (montant_eur >= 0),
    CHECK (fin > debut)
);

CREATE TABLE fait_trace_gps (
    id_trace    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_vehicule TEXT NOT NULL REFERENCES dim_vehicule(id_vehicule),
    id_trajet   TEXT,
    horodatage  TEXT NOT NULL,
    latitude    REAL NOT NULL,
    longitude   REAL NOT NULL,
    vitesse_kmh REAL CHECK (vitesse_kmh BETWEEN 0 AND 60),
    UNIQUE (id_vehicule, horodatage)
);

CREATE TABLE ref_tarif_concurrent (
    id_tarif               INTEGER PRIMARY KEY AUTOINCREMENT,
    date_collecte          TEXT NOT NULL,
    operateur              TEXT NOT NULL,
    offre                  TEXT NOT NULL,
    type_vehicule          TEXT,
    prix_deblocage_eur     REAL,
    prix_minute_eur        REAL,
    abonnement_mensuel_eur REAL,
    zones                  TEXT,
    url_source             TEXT
);

CREATE TABLE socle_flotte_horaire (
    id_vehicule        TEXT NOT NULL REFERENCES dim_vehicule(id_vehicule),
    heure              TEXT NOT NULL,
    type_vehicule      TEXT,
    zone               TEXT,
    nb_releves         INTEGER NOT NULL,
    batterie_min_pct   INTEGER,
    batterie_moy_pct   REAL,
    statut_dominant    TEXT,
    latitude_moy       REAL,
    longitude_moy      REAL,
    temperature_c      REAL,
    precipitation_mm   REAL,
    vent_kmh           REAL,
    nb_locations       INTEGER NOT NULL DEFAULT 0,
    duree_location_min REAL NOT NULL DEFAULT 0,
    recette_eur        REAL NOT NULL DEFAULT 0,
    en_maintenance     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id_vehicule, heure)
);

CREATE TABLE journal_ingestion (
    id_execution    INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    etape           TEXT NOT NULL,
    execute_le      TEXT NOT NULL DEFAULT (datetime('now')),
    lignes_lues     INTEGER NOT NULL DEFAULT 0,
    lignes_gardees  INTEGER NOT NULL DEFAULT 0,
    lignes_rejetees INTEGER NOT NULL DEFAULT 0,
    duree_s         REAL,
    statut          TEXT NOT NULL CHECK (statut IN ('succes', 'echec')),
    detail          TEXT
);

CREATE TABLE quarantaine_qualite (
    id_rejet   INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    motif      TEXT NOT NULL,
    detecte_le TEXT NOT NULL,
    contenu    TEXT NOT NULL
);

CREATE INDEX idx_telemetrie_vehicule_date ON fait_telemetrie (id_vehicule, horodatage DESC);
CREATE INDEX idx_telemetrie_date          ON fait_telemetrie (horodatage);
CREATE INDEX idx_telemetrie_station       ON fait_telemetrie (id_station, horodatage);
CREATE INDEX idx_location_debut           ON fait_location (debut);
CREATE INDEX idx_location_vehicule        ON fait_location (id_vehicule, debut);
CREATE INDEX idx_intervention_vehicule    ON fait_intervention (id_vehicule, date_intervention);
CREATE INDEX idx_trace_date               ON fait_trace_gps (horodatage);
CREATE INDEX idx_socle_heure              ON socle_flotte_horaire (heure);
CREATE INDEX idx_socle_zone_heure         ON socle_flotte_horaire (zone, heure);
