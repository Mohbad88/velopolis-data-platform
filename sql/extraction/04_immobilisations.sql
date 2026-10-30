-- =====================================================================
-- Périodes d'immobilisation, reconstituées à partir des relevés IoT.
-- Source : socle, fait_telemetrie (+ fait_intervention pour le coût).
-- =====================================================================
--
-- La source ne contient pas de « période » : seulement des relevés isolés
-- portant un statut. Il faut donc reconstruire les plages continues pendant
-- lesquelles un véhicule est resté hors service.
--
-- Technique : la différence entre deux numérotations
-- (ROW_NUMBER sur l'ensemble des relevés d'un véhicule, moins ROW_NUMBER sur
-- les seuls relevés « hors_service ») reste constante tant que la série est
-- ininterrompue. Cette constante sert de clé de groupe. C'est le motif dit
-- des « îlots et lacunes » (gaps and islands), qui évite une boucle
-- procédurale ou une jointure récursive.
-- =====================================================================

WITH releves AS (
    SELECT id_vehicule,
           horodatage,
           statut,
           ROW_NUMBER() OVER (PARTITION BY id_vehicule ORDER BY horodatage) AS rang_global,
           ROW_NUMBER() OVER (PARTITION BY id_vehicule, statut ORDER BY horodatage) AS rang_statut
    FROM fait_telemetrie
),
plages AS (
    SELECT id_vehicule,
           MIN(horodatage) AS debut_immobilisation,
           MAX(horodatage) AS fin_immobilisation,
           COUNT(*)        AS nb_releves
    FROM releves
    WHERE statut = 'hors_service'
    GROUP BY id_vehicule, rang_global - rang_statut
)
SELECT p.id_vehicule,
       v.type,
       p.debut_immobilisation,
       p.fin_immobilisation,
       ROUND(EXTRACT(EPOCH FROM (p.fin_immobilisation - p.debut_immobilisation)) / 3600.0, 2)
           AS duree_heures,
       p.nb_releves,
       COUNT(i.id_intervention)         AS nb_interventions,
       COALESCE(SUM(i.cout_eur), 0)     AS cout_total_eur
FROM plages           AS p
JOIN dim_vehicule     AS v ON v.id_vehicule = p.id_vehicule
LEFT JOIN fait_intervention AS i
       ON i.id_vehicule = p.id_vehicule
      AND i.date_intervention BETWEEN p.debut_immobilisation::date
                                  AND p.fin_immobilisation::date
GROUP BY p.id_vehicule, v.type, p.debut_immobilisation, p.fin_immobilisation, p.nb_releves
ORDER BY duree_heures DESC;
