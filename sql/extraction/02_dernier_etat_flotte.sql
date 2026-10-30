-- =====================================================================
-- Dernier état connu de chaque véhicule.
-- Source : socle, table fait_telemetrie.
-- Sert à l'API (/api/v1/vehicules/{id}/etat) et au tableau de bord temps réel.
-- =====================================================================
--
-- Première écriture, naïve, à ne pas utiliser :
--
--   SELECT t.* FROM fait_telemetrie t
--   WHERE t.horodatage = (SELECT MAX(horodatage) FROM fait_telemetrie t2
--                         WHERE t2.id_vehicule = t.id_vehicule);
--
-- Cette forme relance une sous-requête corrélée par ligne : sur 57 000
-- relevés, PostgreSQL exécute 57 000 agrégations.
--
-- Forme retenue : DISTINCT ON, propre à PostgreSQL, qui parcourt l'index
-- (id_vehicule, horodatage DESC) et s'arrête à la première ligne de chaque
-- véhicule. Le plan tombe sur un Index Scan, sans tri ni agrégation.
-- L'équivalent portable est la fonction de fenêtrage ROW_NUMBER(), donnée
-- plus bas.
-- =====================================================================

SELECT DISTINCT ON (t.id_vehicule)
       t.id_vehicule,
       v.type,
       t.horodatage,
       t.latitude,
       t.longitude,
       t.batterie_pct,
       t.statut,
       t.id_station,
       s.nom      AS nom_station,
       s.quartier
FROM fait_telemetrie AS t
JOIN dim_vehicule    AS v ON v.id_vehicule = t.id_vehicule
LEFT JOIN dim_station AS s ON s.id_station = t.id_station
ORDER BY t.id_vehicule, t.horodatage DESC;

-- ---------------------------------------------------------------------
-- Variante portable (même résultat, utilisable hors PostgreSQL) :
-- ---------------------------------------------------------------------
-- WITH classe AS (
--     SELECT t.*,
--            ROW_NUMBER() OVER (PARTITION BY id_vehicule ORDER BY horodatage DESC) AS rang
--     FROM fait_telemetrie t
-- )
-- SELECT * FROM classe WHERE rang = 1;
