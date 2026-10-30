-- =====================================================================
-- Accès direct à la base : trois rôles, trois niveaux de droits.
-- Principe du moindre privilège : aucun compte applicatif n'est propriétaire
-- des tables, et aucun compte humain n'écrit dans le socle.
-- À exécuter en tant que propriétaire de la base.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Rôles « groupe » : ils portent les droits, jamais les mots de passe.
-- ---------------------------------------------------------------------
DROP ROLE IF EXISTS velopolis_etl;
DROP ROLE IF EXISTS velopolis_api;
DROP ROLE IF EXISTS velopolis_analyste;

CREATE ROLE velopolis_etl      NOLOGIN;  -- la chaîne d'ingestion : écrit
CREATE ROLE velopolis_api      NOLOGIN;  -- le service REST : lit le strict nécessaire
CREATE ROLE velopolis_analyste NOLOGIN;  -- les analystes : lecture seule, sans trace GPS

-- ---------------------------------------------------------------------
-- Droits
-- ---------------------------------------------------------------------
GRANT CONNECT ON DATABASE velopolis_socle TO velopolis_etl, velopolis_api, velopolis_analyste;
GRANT USAGE   ON SCHEMA public            TO velopolis_etl, velopolis_api, velopolis_analyste;

-- L'ETL écrit partout, mais ne supprime jamais une table.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO velopolis_etl;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO velopolis_etl;

-- L'API ne lit que ce qu'elle expose : ni traces GPS, ni quarantaine.
GRANT SELECT ON dim_vehicule, dim_station, socle_flotte_horaire,
                fait_telemetrie, fait_intervention, fait_location,
                fait_meteo, ref_tarif_concurrent
      TO velopolis_api;

-- Les analystes travaillent sur le jeu consolidé et les référentiels.
GRANT SELECT ON dim_vehicule, dim_station, socle_flotte_horaire,
                fait_intervention, fait_meteo, ref_tarif_concurrent,
                journal_ingestion
      TO velopolis_analyste;

-- Les traces GPS brutes ne sont accessibles à personne en dehors de l'ETL :
-- c'est la donnée la plus ré-identifiante du socle.
REVOKE ALL ON fait_trace_gps FROM velopolis_api, velopolis_analyste;

-- Tables créées plus tard : mêmes règles, appliquées d'office.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO velopolis_etl;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO velopolis_analyste;

-- ---------------------------------------------------------------------
-- Comptes de connexion : le mot de passe est injecté par le coffre-fort
-- de la DSI, jamais écrit ici (:mdp_etl est une variable psql).
-- ---------------------------------------------------------------------
-- CREATE USER etl_velopolis      LOGIN PASSWORD :'mdp_etl'      IN ROLE velopolis_etl;
-- CREATE USER api_velopolis      LOGIN PASSWORD :'mdp_api'      IN ROLE velopolis_api;
-- CREATE USER analyste_velopolis LOGIN PASSWORD :'mdp_analyste' IN ROLE velopolis_analyste;

-- Connexions chiffrées obligatoires, à poser dans pg_hba.conf :
--   hostssl  velopolis_socle  all  10.0.0.0/8  scram-sha-256
