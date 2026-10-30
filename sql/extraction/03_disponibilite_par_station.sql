-- =====================================================================
-- Taux de remplissage des stations, par créneau horaire.
-- Source : socle, fait_telemetrie × dim_station.
-- Paramètres : $1 = début, $2 = fin (exclue).
-- =====================================================================
--
-- FILTRAGE
--   Le filtre sur horodatage est posé AVANT l'agrégation (clause WHERE), et
--   non après (HAVING) : il réduit le volume agrégé au lieu d'agréger puis de
--   jeter. Le filtre sur id_station IS NOT NULL permet en outre d'utiliser
--   l'index partiel idx_telemetrie_station.
--
-- AGRÉGATION
--   FILTER (WHERE …) remplace les SUM(CASE WHEN … THEN 1 ELSE 0 END)
--   habituels : même résultat, intention plus lisible, et une seule passe.
--
-- PRÉCISION
--   capacite est converti en numeric avant la division : en division entière,
--   un taux de 0,8 deviendrait 0.
-- =====================================================================

SELECT s.id_station,
       s.nom,
       s.quartier,
       s.capacite,
       date_trunc('hour', t.horodatage)                              AS creneau,
       COUNT(*)                                                      AS nb_releves,
       COUNT(*) FILTER (WHERE t.statut = 'disponible')               AS nb_disponibles,
       COUNT(*) FILTER (WHERE t.statut = 'hors_service')             AS nb_hors_service,
       ROUND(
           COUNT(*) FILTER (WHERE t.statut = 'disponible')::numeric
           / NULLIF(s.capacite, 0) * 100, 1)                         AS taux_remplissage_pct
FROM fait_telemetrie AS t
JOIN dim_station     AS s ON s.id_station = t.id_station
WHERE t.horodatage >= $1
  AND t.horodatage <  $2
  AND t.id_station IS NOT NULL
GROUP BY s.id_station, s.nom, s.quartier, s.capacite, date_trunc('hour', t.horodatage)
HAVING COUNT(*) >= 2          -- un créneau avec un seul relevé n'est pas représentatif
ORDER BY creneau, s.id_station;
