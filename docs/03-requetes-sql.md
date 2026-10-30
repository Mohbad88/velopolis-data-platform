# 3. Requêtes SQL d'extraction

Les quatre requêtes sont dans [`sql/extraction/`](../sql/extraction/), chacune
commentée en tête de fichier. Ce document résume les choix et donne les plans
d'exécution mesurés sur PostgreSQL 16, sur le jeu de test
(56 969 relevés, 3 000 locations, 400 interventions).

## 3.1 Requête 1 — locations sur une fenêtre glissante

Fichier : `01_locations_fenetre.sql`

**Sélection.** Les colonnes `nom`, `prenom`, `email` et `telephone` ne sont pas
lues. Elles ne servent à aucun usage analytique, et ce qui n'est pas extrait ne
peut pas fuiter. `date_naissance` est lue uniquement pour en déduire une tranche
d'âge côté application, puis écartée.

**Filtrage.** `debut >= $1 AND debut < $2` : borne basse incluse, borne haute
exclue. Une exécution quotidienne ne reprend ni n'oublie une ligne à la
frontière. `fin IS NOT NULL` écarte les locations en cours, qui n'ont ni durée
ni montant définitifs — 40 lignes dans le jeu de test ; elles seront reprises à
l'exécution suivante.

**Jointures.**

- `JOIN abonne` : jointure **interne**. Une location sans abonné est une
  incohérence référentielle ; elle doit être visible, pas masquée par un
  `LEFT JOIN` qui la ferait passer pour une ligne normale avec des `NULL`.
- `LEFT JOIN paiement` : jointure **externe**. Un paiement peut être différé ou
  avoir échoué. La location existe quand même et doit remonter avec un montant
  nul plutôt que de disparaître du socle.

**Optimisations.** L'agrégation `SUM`/`COUNT` est faite côté serveur : une seule
ligne par location circule sur le réseau, au lieu d'une ligne par paiement.
L'index `idx_location_debut` rend le filtre de fenêtre sélectif. Le `ORDER BY`
stable rend le fichier produit comparable d'une exécution à l'autre, ce qui
simplifie les contrôles de recette.

## 3.2 Requête 2 — dernier état connu de chaque véhicule

Fichier : `02_dernier_etat_flotte.sql`

C'est la requête la plus appelée : elle sert l'API et le futur tableau de bord.
Deux écritures donnent le même résultat, pour des coûts très différents.

**Écriture naïve** — sous-requête corrélée :

```sql
SELECT t.* FROM fait_telemetrie t
WHERE t.horodatage = (SELECT MAX(horodatage) FROM fait_telemetrie t2
                      WHERE t2.id_vehicule = t.id_vehicule);
```

**Écriture retenue** — `DISTINCT ON`, qui parcourt l'index
`(id_vehicule, horodatage DESC)` et s'arrête à la première ligne de chaque
véhicule.

**Mesure** (`EXPLAIN ANALYZE`, PostgreSQL 16, 56 969 lignes) :

| Écriture | Plan | Temps d'exécution |
|---|---|---|
| Sous-requête corrélée | `Seq Scan` + 56 969 exécutions de la sous-requête | **4 310 ms** |
| `DISTINCT ON` | `Index Scan using idx_telemetrie_vehicule_date` | **7,5 ms** |

Soit un facteur **575**. Sur le volume de production (17 M de lignes par jour),
la première écriture est tout simplement inutilisable.

Une variante portable, à base de `ROW_NUMBER() OVER (PARTITION BY …)`, est
donnée en commentaire du fichier pour les moteurs qui ne connaissent pas
`DISTINCT ON`.

## 3.3 Requête 3 — taux de remplissage par station et par créneau

Fichier : `03_disponibilite_par_station.sql`

**Filtrage avant agrégation.** Le filtre temporel est posé en `WHERE`, pas en
`HAVING` : il réduit le volume à agréger au lieu d'agréger puis de jeter. Le
`HAVING COUNT(*) >= 2` qui subsiste porte sur le résultat de l'agrégation
(écarter les créneaux non représentatifs) : c'est son usage légitime.

**Index partiel.** La condition `id_station IS NOT NULL` permet d'utiliser
`idx_telemetrie_station`, qui est précisément défini avec cette clause. Environ
40 % des relevés n'ont pas de station : l'index est d'autant plus petit, donc
plus rapide à parcourir et moins coûteux à maintenir.

**Agrégation conditionnelle.** `COUNT(*) FILTER (WHERE statut = 'disponible')`
remplace le `SUM(CASE WHEN … THEN 1 ELSE 0 END)` habituel : même résultat, en
une seule passe, et l'intention se lit directement.

**Précision.** `capacite` est converti en `numeric` avant la division : en
division entière, un taux de 0,8 deviendrait 0. `NULLIF(capacite, 0)` évite la
division par zéro sur une station de capacité non renseignée.

## 3.4 Requête 4 — reconstitution des périodes d'immobilisation

Fichier : `04_immobilisations.sql`

La source ne contient pas de « période » : seulement des relevés isolés portant
un statut. Il faut reconstruire les plages continues pendant lesquelles un
véhicule est resté hors service.

**Technique des îlots et lacunes** (*gaps and islands*) : la différence entre
deux numérotations — `ROW_NUMBER()` sur tous les relevés d'un véhicule, moins
`ROW_NUMBER()` sur ses seuls relevés « hors service » — reste constante tant
que la série est ininterrompue. Cette constante sert de clé de regroupement.

L'alternative serait une jointure récursive ou une boucle procédurale, toutes
deux plus lentes et beaucoup moins lisibles.

Résultat sur le jeu de test : les périodes d'immobilisation sont restituées avec
leur durée, le nombre de relevés qui les composent, et le coût des interventions
rattachées — exactement l'indicateur que la Direction des opérations attend du
socle.

## 3.5 Requêtes de l'API

Les requêtes servies par l'API sont dans
[`src/velopolis/api/repository.py`](../src/velopolis/api/repository.py).
Deux règles y sont systématiques :

1. **Aucune valeur venant du client n'est concaténée dans une chaîne SQL.** Les
   filtres passent par des marqueurs de paramètres. Trois tests vérifient qu'une
   charge d'injection (`'; DROP TABLE dim_vehicule; --`) renvoie simplement un
   résultat vide et laisse la base intacte.
2. **Les seules portions dynamiques sont des noms de colonnes de tri**, choisis
   dans une liste fermée (`TRIS_AUTORISES`). Une valeur hors liste retombe
   silencieusement sur le tri par défaut.
