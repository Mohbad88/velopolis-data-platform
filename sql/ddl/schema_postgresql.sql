-- =====================================================================
-- Vélopolis — socle de données technique de la flotte
-- Modèle physique de données (MPD), cible PostgreSQL 16.
-- Issu du modèle conceptuel MERISE décrit dans docs/02-modelisation-merise.md
--
-- Conventions
--   dim_*   : entités de référence (peu de lignes, mises à jour rares)
--   fait_*  : événements datés (volumineux, insertion seule)
--   ref_*   : référentiels externes datés
--   Horodatages en timestamptz, toujours stockés en UTC.
--   Aucune colonne directement identifiante : l'abonné n'apparaît que sous
--   forme de pseudonyme (cf. docs/04-rgpd-registre-traitements.md).
-- =====================================================================

DROP TABLE IF EXISTS socle_flotte_horaire   CASCADE;
DROP TABLE IF EXISTS fait_trace_gps         CASCADE;
DROP TABLE IF EXISTS fait_location          CASCADE;
DROP TABLE IF EXISTS fait_intervention      CASCADE;
DROP TABLE IF EXISTS fait_meteo             CASCADE;
DROP TABLE IF EXISTS fait_telemetrie        CASCADE;
DROP TABLE IF EXISTS ref_tarif_concurrent   CASCADE;
DROP TABLE IF EXISTS quarantaine_qualite    CASCADE;
DROP TABLE IF EXISTS journal_ingestion      CASCADE;
DROP TABLE IF EXISTS dim_station            CASCADE;
DROP TABLE IF EXISTS dim_vehicule           CASCADE;

-- ---------------------------------------------------------------------
-- Entités de référence
-- ---------------------------------------------------------------------
CREATE TABLE dim_vehicule (
    id_vehicule       varchar(16) PRIMARY KEY,
    type              varchar(16) NOT NULL
                      CHECK (type IN ('velo', 'trottinette')),
    modele            varchar(64),
    date_mise_service date
);
COMMENT ON TABLE dim_vehicule IS 'Référentiel des véhicules de la flotte.';

CREATE TABLE dim_station (
    id_station  varchar(16) PRIMARY KEY,
    nom         varchar(128) NOT NULL,
    quartier    varchar(64),
    capacite    smallint CHECK (capacite > 0),
    latitude    numeric(9,6),
    longitude   numeric(9,6)
);
COMMENT ON TABLE dim_station IS 'Référentiel des 250 stations de la métropole.';

-- ---------------------------------------------------------------------
-- Faits
-- ---------------------------------------------------------------------
CREATE TABLE fait_telemetrie (
    id_telemetrie bigserial PRIMARY KEY,
    id_vehicule   varchar(16) NOT NULL REFERENCES dim_vehicule(id_vehicule),
    horodatage    timestamptz NOT NULL,
    latitude      numeric(9,6) NOT NULL,
    longitude     numeric(9,6) NOT NULL,
    batterie_pct  smallint NOT NULL CHECK (batterie_pct BETWEEN 0 AND 100),
    statut        varchar(16) NOT NULL
                  CHECK (statut IN ('disponible', 'en_location', 'hors_service', 'en_charge')),
    id_station    varchar(16) REFERENCES dim_station(id_station),
    zone          varchar(16),
    CONSTRAINT uq_telemetrie UNIQUE (id_vehicule, horodatage)
);
COMMENT ON TABLE fait_telemetrie IS
    'Relevés des boîtiers embarqués. Donnée personnelle indirecte lorsque le '
    'véhicule est en location : rétention courte, cf. procédure de purge.';
-- En production, cette table est partitionnée par mois :
--   CREATE TABLE fait_telemetrie (...) PARTITION BY RANGE (horodatage);
-- la purge mensuelle devient alors un DROP PARTITION, immédiat et sans VACUUM.

CREATE TABLE fait_meteo (
    id_meteo         bigserial PRIMARY KEY,
    zone             varchar(16) NOT NULL,
    horodatage       timestamptz NOT NULL,
    temperature_c    numeric(5,2) CHECK (temperature_c BETWEEN -40 AND 55),
    precipitation_mm numeric(6,2) CHECK (precipitation_mm >= 0),
    vent_kmh         numeric(5,1) CHECK (vent_kmh >= 0),
    CONSTRAINT uq_meteo UNIQUE (zone, horodatage)
);
COMMENT ON TABLE fait_meteo IS 'Relevés météo horaires par zone, toujours en degrés Celsius.';

CREATE TABLE fait_intervention (
    id_intervention   varchar(16) PRIMARY KEY,
    id_vehicule       varchar(16) NOT NULL REFERENCES dim_vehicule(id_vehicule),
    date_intervention date NOT NULL,
    type_panne        varchar(64),
    piece_changee     varchar(64),
    duree_min         integer CHECK (duree_min > 0),
    cout_eur          numeric(8,2) CHECK (cout_eur >= 0),
    prestataire       varchar(64),
    fichier_origine   varchar(128)
);
COMMENT ON COLUMN fait_intervention.fichier_origine IS
    'Fichier CSV d''où provient la ligne : indispensable pour tracer une anomalie '
    'jusqu''à l''export du prestataire.';

CREATE TABLE fait_location (
    id_location        integer PRIMARY KEY,
    id_abonne_psd      varchar(32) NOT NULL,
    tranche_age        varchar(16),
    type_abonnement    varchar(16),
    ville_abonne       varchar(64),
    id_vehicule        varchar(16) NOT NULL REFERENCES dim_vehicule(id_vehicule),
    id_station_depart  varchar(16) REFERENCES dim_station(id_station),
    id_station_arrivee varchar(16) REFERENCES dim_station(id_station),
    debut              timestamptz NOT NULL,
    fin                timestamptz NOT NULL,
    duree_min          numeric(7,1) CHECK (duree_min > 0),
    distance_km        numeric(6,2),
    montant_eur        numeric(8,2) CHECK (montant_eur >= 0),
    CONSTRAINT ck_location_ordre CHECK (fin > debut)
);
COMMENT ON COLUMN fait_location.id_abonne_psd IS
    'Pseudonyme HMAC-SHA256 de l''identifiant abonné. La table de correspondance '
    'reste dans la base transactionnelle ; la clé est détenue par la DSI.';

CREATE TABLE fait_trace_gps (
    id_trace     bigserial PRIMARY KEY,
    id_vehicule  varchar(16) NOT NULL REFERENCES dim_vehicule(id_vehicule),
    id_trajet    varchar(32),
    horodatage   timestamptz NOT NULL,
    latitude     numeric(9,6) NOT NULL,
    longitude    numeric(9,6) NOT NULL,
    vitesse_kmh  numeric(5,1) CHECK (vitesse_kmh BETWEEN 0 AND 60),
    CONSTRAINT uq_trace UNIQUE (id_vehicule, horodatage)
);
COMMENT ON TABLE fait_trace_gps IS
    'Traces haute fréquence issues de l''entrepôt Hive. Donnée la plus sensible '
    'du socle : rétention 30 jours, puis agrégation à la maille quartier.';

CREATE TABLE ref_tarif_concurrent (
    id_tarif               bigserial PRIMARY KEY,
    date_collecte          timestamptz NOT NULL,
    operateur              varchar(64) NOT NULL,
    offre                  varchar(64) NOT NULL,
    type_vehicule          varchar(32),
    prix_deblocage_eur     numeric(6,2),
    prix_minute_eur        numeric(6,3),
    abonnement_mensuel_eur numeric(8,2),
    zones                  varchar(128),
    url_source             varchar(256)
);
COMMENT ON TABLE ref_tarif_concurrent IS
    'Grille tarifaire publique relevée par scraping. Aucune donnée personnelle.';

-- ---------------------------------------------------------------------
-- Jeu de données consolidé (maille véhicule × heure)
-- ---------------------------------------------------------------------
CREATE TABLE socle_flotte_horaire (
    id_vehicule        varchar(16) NOT NULL REFERENCES dim_vehicule(id_vehicule),
    heure              timestamptz NOT NULL,
    type_vehicule      varchar(16),
    zone               varchar(16),
    nb_releves         smallint NOT NULL,
    batterie_min_pct   smallint,
    batterie_moy_pct   numeric(5,1),
    statut_dominant    varchar(16),
    latitude_moy       numeric(9,6),
    longitude_moy      numeric(9,6),
    temperature_c      numeric(5,2),
    precipitation_mm   numeric(6,2),
    vent_kmh           numeric(5,1),
    nb_locations       smallint NOT NULL DEFAULT 0,
    duree_location_min numeric(7,1) NOT NULL DEFAULT 0,
    recette_eur        numeric(8,2) NOT NULL DEFAULT 0,
    en_maintenance     smallint NOT NULL DEFAULT 0,
    PRIMARY KEY (id_vehicule, heure)
);
COMMENT ON TABLE socle_flotte_horaire IS
    'Jeu de données unique exposé par l''API : état de chaque véhicule heure par '
    'heure, enrichi de la météo, des locations et de la maintenance.';

-- ---------------------------------------------------------------------
-- Exploitation : traçabilité des chargements et des rejets
-- ---------------------------------------------------------------------
CREATE TABLE journal_ingestion (
    id_execution   bigserial PRIMARY KEY,
    source         varchar(64) NOT NULL,
    etape          varchar(32) NOT NULL,
    execute_le     timestamptz NOT NULL DEFAULT now(),
    lignes_lues    integer NOT NULL DEFAULT 0,
    lignes_gardees integer NOT NULL DEFAULT 0,
    lignes_rejetees integer NOT NULL DEFAULT 0,
    duree_s        numeric(8,2),
    statut         varchar(16) NOT NULL CHECK (statut IN ('succes', 'echec')),
    detail         text
);
COMMENT ON TABLE journal_ingestion IS
    'Une ligne par source et par exécution : permet de prouver qu''un chargement '
    'a eu lieu, et d''alerter si le volume s''écarte de la veille.';

CREATE TABLE quarantaine_qualite (
    id_rejet    bigserial PRIMARY KEY,
    source      varchar(64) NOT NULL,
    motif       varchar(64) NOT NULL,
    detecte_le  timestamptz NOT NULL,
    contenu     text NOT NULL
);
COMMENT ON TABLE quarantaine_qualite IS
    'Lignes écartées, conservées avec leur motif pour remonter à la source de '
    'l''anomalie. Purgée en même temps que les données auxquelles elle se rapporte.';

-- ---------------------------------------------------------------------
-- Index : chaque index répond à un accès identifié, aucun « au cas où »
-- ---------------------------------------------------------------------
-- Tableau de bord temps réel : dernier état par véhicule.
CREATE INDEX idx_telemetrie_vehicule_date ON fait_telemetrie (id_vehicule, horodatage DESC);
-- Purge RGPD et requêtes par fenêtre de temps.
CREATE INDEX idx_telemetrie_date          ON fait_telemetrie (horodatage);
-- Taux de remplissage par station.
CREATE INDEX idx_telemetrie_station       ON fait_telemetrie (id_station, horodatage)
    WHERE id_station IS NOT NULL;
CREATE INDEX idx_location_debut           ON fait_location (debut);
CREATE INDEX idx_location_vehicule        ON fait_location (id_vehicule, debut);
CREATE INDEX idx_intervention_vehicule    ON fait_intervention (id_vehicule, date_intervention);
CREATE INDEX idx_trace_date               ON fait_trace_gps (horodatage);
CREATE INDEX idx_socle_heure              ON socle_flotte_horaire (heure);
CREATE INDEX idx_socle_zone_heure         ON socle_flotte_horaire (zone, heure);
