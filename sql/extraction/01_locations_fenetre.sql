-- =====================================================================
-- Extraction des locations terminées sur une fenêtre glissante.
-- Source : base transactionnelle (réplica en lecture).
-- Utilisée par src/velopolis/extract/postgres_source.py
--
-- Paramètres : $1 = début de fenêtre, $2 = fin de fenêtre (exclue).
-- =====================================================================
--
-- SÉLECTION
--   Les colonnes nom, prenom, email et telephone ne sont pas lues : elles ne
--   servent à aucun usage analytique, et ce qui n'est pas extrait ne peut pas
--   fuiter. date_naissance est lue uniquement pour en déduire une tranche
--   d'âge côté application, puis écartée.
--
-- FILTRAGE
--   l.debut >= $1 AND l.debut < $2 : borne inférieure incluse, supérieure
--   exclue, pour qu'une exécution quotidienne ne reprenne ni n'oublie une
--   ligne à la frontière.
--   l.fin IS NOT NULL : une location en cours n'a ni durée ni montant
--   définitifs ; elle sera reprise à l'exécution suivante.
--
-- JOINTURES
--   JOIN abonne : jointure interne, car une location sans abonné est une
--   incohérence référentielle qui doit être vue, pas masquée.
--   LEFT JOIN paiement : jointure externe, car un paiement peut être différé
--   ou échoué ; la location existe quand même et doit remonter avec un
--   montant nul plutôt que de disparaître.
--
-- OPTIMISATIONS
--   - index idx_location_debut (debut) : rend le filtre de fenêtre sélectif
--     et évite le parcours séquentiel de la table.
--   - agrégation des paiements côté serveur (SUM/COUNT) : une seule ligne par
--     location circule sur le réseau au lieu d'une ligne par paiement.
--   - ORDER BY l.debut : l'ordre stable rend le fichier produit comparable
--     d'une exécution à l'autre, ce qui simplifie les contrôles de recette.
-- =====================================================================

SELECT l.id_location,
       l.id_abonne,                       -- pseudonymisé par l'application
       a.date_naissance,                  -- converti en tranche d'âge, puis écarté
       a.type_abonnement,
       a.ville,
       l.id_vehicule,
       l.id_station_depart,
       l.id_station_arrivee,
       l.debut,
       l.fin,
       l.distance_km,
       SUM(p.montant_eur)   AS montant_eur,
       COUNT(p.id_paiement) AS nb_paiements
FROM source.location  AS l
JOIN source.abonne    AS a ON a.id_abonne  = l.id_abonne
LEFT JOIN source.paiement AS p ON p.id_location = l.id_location
WHERE l.debut >= $1
  AND l.debut <  $2
  AND l.fin IS NOT NULL
GROUP BY l.id_location, l.id_abonne, a.date_naissance, a.type_abonnement, a.ville,
         l.id_vehicule, l.id_station_depart, l.id_station_arrivee,
         l.debut, l.fin, l.distance_km
ORDER BY l.debut;
