# 6. Démonstration

Script : [`scripts/demonstration.sh`](../scripts/demonstration.sh) — treize
étapes, exécutables d'un seul coup par `make demo`. Les sorties ci-dessous sont
celles d'une exécution réelle sur le jeu de test.

## Préparation

```bash
make fixtures            # génère les jeux de test
make mock &              # sources simulées sur le port 8001
make pipeline            # extraction → agrégation → schéma → import
make api &               # API REST sur le port 8000
```

## 1. La chaîne complète

```
fait_telemetrie        lues=57984  conservées=56969  rejetées=1015  corrigées=332
                       batterie_hors_bornes ×231, doublon ×379,
                       position_absente_ou_aberrante ×405
fait_meteo             lues=471    conservées=471    rejetées=0     corrigées=117
fait_intervention      lues=409    conservées=400    rejetées=9     corrigées=351
                       doublon ×3, ligne_vide ×3, vehicule_inconnu ×3
ref_tarif_concurrent   lues=6      conservées=6      rejetées=0     corrigées=6
fait_location          lues=3000   conservées=3000   rejetées=0
fait_trace_gps         lues=14170  conservées=14170  rejetées=0
socle_flotte_horaire   lues=56969  conservées=14400
Chaîne terminée : 24 étapes journalisées.
```

Durée totale : **10 secondes** pour 76 044 lignes extraites, contrôlées,
consolidées et chargées.

Deux colonnes à commenter. **Rejetées** : les lignes irrécupérables, parties en
quarantaine avec leur motif. **Corrigées** : les lignes réparées plutôt que
jetées — dates françaises converties, températures Fahrenheit ramenées en
Celsius, durées « 45 min » et montants « 32,50 » rendus numériques, statuts
remis en minuscules. C'est le cœur de l'homogénéisation : 806 lignes auraient
été perdues si la chaîne s'était contentée de rejeter ce qu'elle ne savait pas
lire telle quelle.

## 2. Disponibilité du service

```json
{"statut": "disponible",
 "volumes": {"dim_station": 25, "dim_vehicule": 120, "fait_intervention": 400,
             "fait_location": 3000, "fait_meteo": 471, "fait_telemetrie": 56969,
             "fait_trace_gps": 14170, "quarantaine_qualite": 1024,
             "ref_tarif_concurrent": 6, "socle_flotte_horaire": 14400}}
```

## 3 et 4. Les accès non authentifiés sont refusés

```
GET /api/v1/vehicules                sans jeton        → HTTP 401
POST /api/v1/token  avec un mauvais secret             → HTTP 401
```

## 5. Obtention d'un jeton

```json
{"access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9…",
 "token_type": "Bearer", "expires_in": 3600, "scope": "flotte:lire"}
```

## 6. Le jeu consolidé, filtré et paginé

`GET /api/v1/flotte/horaire?zone=centre&debut=2026-05-12T08:00:00&limite=2`

```json
{"donnees": [
   {"id_vehicule": "VLO-00001", "type_vehicule": "velo",
    "heure": "2026-05-12T08:00:00", "zone": "centre", "nb_releves": 4,
    "batterie_min_pct": 47, "batterie_moy_pct": 47.2,
    "statut_dominant": "disponible",
    "latitude_moy": 48.099865, "longitude_moy": -1.686218,
    "temperature_c": 13.5, "precipitation_mm": 0.46, "vent_kmh": 11.6,
    "nb_locations": 0, "duree_location_min": 0.0, "recette_eur": 0.0,
    "en_maintenance": 0}],
 "pagination": {"limite": 2, "decalage": 0, "retournees": 2,
                "page_suivante": true}}
```

Une ligne porte ici les six sources à la fois : la télémétrie IoT (batterie,
statut, position), le référentiel véhicule (type), la météo de la zone, les
locations de l'heure et la maintenance du jour.

## 7. Portée insuffisante

Le client « tableau de bord » n'a pas accès aux données personnelles :

```json
{"erreur": "portee_insuffisante",
 "message": "La portée « locations:lire » est requise.",
 "portees_du_jeton": ["flotte:lire"]}
```

## 8. Avec la bonne portée, l'abonné reste pseudonymisé

```json
{"avertissement": "Identifiants abonnés pseudonymisés : ne pas tenter de réidentifier.",
 "donnees": [{"id_location": 271, "id_abonne_psd": "AB-3fb59eb06cb1fa14",
              "tranche_age": "45-54", "type_abonnement": "annuel",
              "id_vehicule": "VLO-00035", "id_station_depart": "ST-024",
              "id_station_arrivee": "ST-020",
              "debut": "2026-05-11T00:01:00+00:00",
              "fin": "2026-05-11T01:00:00+00:00",
              "duree_min": 59.0, "distance_km": 12.42, "montant_eur": 11.62}]}
```

Ni nom, ni courriel, ni téléphone, ni date de naissance : ces colonnes n'ont
jamais été extraites. L'accès est journalisé côté serveur.

## 9 et 10. État d'un véhicule, et véhicule inconnu

```json
{"donnees": {"id_vehicule": "VLO-00007", "horodatage": "2026-05-15T23:45:00+00:00",
             "latitude": 48.126697, "longitude": -1.65036, "batterie_pct": 77,
             "statut": "disponible", "id_station": "ST-001", "zone": "centre"}}
```

```
GET /api/v1/vehicules/VLO-99999/etat   → HTTP 404
```

## 11. Tentative d'injection SQL

```
GET /api/v1/flotte/horaire?zone=centre'; DROP TABLE dim_vehicule; --
→ HTTP 200, donnees: []
dim_vehicule toujours interrogeable : True
```

La charge est traitée comme une valeur de filtre, pas comme du SQL : aucune
zone ne porte ce nom, la réponse est vide, la table est intacte.

## 12. Qualité des données

`GET /api/v1/exploitation/qualite`

| Source | Motif | Nombre |
|---|---|---|
| fait_telemetrie | position_absente_ou_aberrante | 405 |
| fait_telemetrie | doublon | 379 |
| fait_telemetrie | batterie_hors_bornes | 231 |
| fait_intervention | doublon | 3 |
| fait_intervention | ligne_vide | 3 |
| fait_intervention | vehicule_inconnu | 3 |

Soit 1,75 % des lignes écartées, toutes consultables dans
`quarantaine_qualite` avec leur contenu d'origine : le prestataire peut être
saisi avec des exemples précis plutôt qu'un reproche général.

## 13. Purge RGPD en simulation

```
fait_telemetrie          56969 ligne(s) antérieures au 2026-09-30 (simulation)
fait_trace_gps           14170 ligne(s) antérieures au 2026-09-30 (simulation)
quarantaine_qualite          0 ligne(s) antérieures au 2026-09-30 (simulation)
fait_location                0 ligne(s) antérieures au 2025-09-30 (simulation)
```

Le mode simulation ne supprime rien : il sert au contrôle mensuel de la
procédure. Sans l'option, les lignes sont supprimées et l'opération est
inscrite dans `journal_ingestion`.

## 14. Tests unitaires

```bash
$ make test
99 passed
```

Répartition : 56 tests sur les conversions et contrôles qualité, 20 sur la
pseudonymisation et la minimisation, 23 sur l'API (authentification,
autorisation, pagination, injection SQL).

## Reprise sur erreur, observée pendant la démonstration

L'API de test renvoie un HTTP 429 toutes les 25 requêtes. Le journal
d'extraction montre la politique de reprise à l'œuvre :

```
iot_telemetrie : page 3, 500 relevés (cumul 1500)
WARNING  http://…/telemetry (HTTP 429) — nouvelle tentative dans 1 s
iot_telemetrie : page 4, 500 relevés (cumul 2000)
```

Et lorsque la source est totalement injoignable, la chaîne ne s'arrête pas :
les sources concernées passent en statut `echec`, les autres aboutissent, et le
journal conserve la trace de l'incident.
