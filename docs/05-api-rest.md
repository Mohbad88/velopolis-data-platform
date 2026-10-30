# 5. API REST et accès direct à la base

Contrat complet : [`openapi.yaml`](../openapi.yaml), également servi par
`GET /api/v1/openapi.yaml`.

## 5.1 Authentification

**OAuth 2.0, flux *client credentials***, jeton JWT signé en HS256, valable
une heure. Ce flux convient parce que les consommateurs sont des applications
(tableau de bord, poste analyste, supervision), pas des utilisateurs finaux
naviguant dans un navigateur.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/token \
     -H "Content-Type: application/json" \
     -d '{"client_id":"tableau-de-bord","client_secret":"…"}'
```

```json
{"access_token":"eyJhbGciOi…","token_type":"Bearer","expires_in":3600,
 "scope":"flotte:lire"}
```

Le jeton est ensuite présenté dans `Authorization: Bearer <jeton>`.

Les secrets des clients ne sont jamais stockés en clair : seul un condensat
PBKDF2-HMAC-SHA256 (240 000 itérations, sel aléatoire par client) est conservé.
Lorsqu'un identifiant inconnu est présenté, un calcul de condensat factice est
tout de même effectué, pour que le temps de réponse ne révèle pas l'existence
du client.

## 5.2 Autorisation par portées

L'authentification répond à « qui appelle ? », l'autorisation à « a-t-il le
droit de voir ces données ? ». Les deux sont traitées séparément.

| Portée | Donne accès à | Clients concernés |
|---|---|---|
| `flotte:lire` | Référentiels, état horaire, interventions, météo, tarifs concurrents | tableau-de-bord, analyste, supervision |
| `locations:lire` | Locations pseudonymisées (**données personnelles indirectes**) | analyste |
| `exploitation:lire` | Journal d'ingestion, synthèse de la quarantaine | supervision |

Un client ne peut jamais obtenir une portée qui ne lui est pas accordée : la
demander renvoie `400 invalid_scope`. Un appel hors portée renvoie `403` avec la
portée exigée et celles que porte le jeton, pour que le développeur du client
comprenne immédiatement ce qui manque.

## 5.3 Points de terminaison

| Méthode | Chemin | Portée | Objet |
|---|---|---|---|
| GET | `/api/v1/sante` | — | Disponibilité et volumétrie de chaque table |
| POST | `/api/v1/token` | — | Obtention d'un jeton |
| GET | `/api/v1/portees` | — | Portées disponibles et ce qu'elles donnent |
| GET | `/api/v1/openapi.yaml` | — | Contrat de l'API |
| GET | `/api/v1/vehicules` | `flotte:lire` | Référentiel des véhicules |
| GET | `/api/v1/vehicules/{id}/etat` | `flotte:lire` | Dernier état connu |
| GET | `/api/v1/stations` | `flotte:lire` | Référentiel des stations |
| GET | `/api/v1/flotte/horaire` | `flotte:lire` | **Jeu consolidé** (véhicule × heure) |
| GET | `/api/v1/interventions` | `flotte:lire` | Interventions de maintenance |
| GET | `/api/v1/tarifs-concurrent` | `flotte:lire` | Grille tarifaire relevée |
| GET | `/api/v1/locations` | `locations:lire` | Locations pseudonymisées |
| GET | `/api/v1/exploitation/journal` | `exploitation:lire` | Journal des exécutions |
| GET | `/api/v1/exploitation/qualite` | `exploitation:lire` | Rejets par motif |

## 5.4 Conventions

**Versionnement dans le chemin** (`/api/v1/…`) : le contrat peut évoluer sans
casser les clients existants.

**Pagination systématique.** `limite` (1 à 1000, défaut 100) et `decalage`.
Sans plafond, un seul appel pourrait demander 57 000 lignes et saturer la base.
Chaque réponse porte un bloc `pagination` avec `page_suivante`.

```json
{"donnees": [...],
 "pagination": {"limite": 100, "decalage": 0, "retournees": 100,
                "page_suivante": true}}
```

**Erreurs lisibles.** Un code stable (`jeton_expire`, `portee_insuffisante`,
`vehicule_inconnu`) et un message en français.

**Sonde de disponibilité.** `/api/v1/sante` interroge réellement la base et
renvoie `503` si elle est injoignable : une sonde qui répond toujours 200 ne
sert à rien.

## 5.5 Sécurité des requêtes

Aucune valeur venant du client n'est concaténée dans une chaîne SQL. Les seules
portions dynamiques sont des noms de colonnes de tri, choisis dans une liste
fermée. Trois tests injectent des charges classiques
(`'; DROP TABLE dim_vehicule; --`, `' OR '1'='1`, `UNION SELECT`) et vérifient
que la réponse est vide et la base intacte.

En production, l'API est publiée derrière un terminaison TLS ; `pg_hba.conf`
impose `hostssl` et `scram-sha-256` sur les connexions à la base.

## 5.6 Accès direct à la base

Certains usages analytiques se prêtent mal à une API : exploration ad hoc,
connexion d'un outil de visualisation. Trois rôles PostgreSQL sont définis dans
[`sql/ddl/roles_et_acces.sql`](../sql/ddl/roles_et_acces.sql), selon le principe
du moindre privilège.

| Rôle | Droits | Pour qui |
|---|---|---|
| `velopolis_etl` | `SELECT`, `INSERT`, `UPDATE`, `DELETE` sur toutes les tables ; `USAGE` sur les séquences | La chaîne d'ingestion |
| `velopolis_api` | `SELECT` sur 8 tables ; **aucun droit sur `fait_trace_gps` ni sur la quarantaine** | Le service REST |
| `velopolis_analyste` | `SELECT` sur le jeu consolidé, les référentiels, la météo, les tarifs et le journal | Les analystes |

Trois précisions.

- Les rôles sont `NOLOGIN` : ils portent les droits, les comptes de connexion
  les reçoivent par héritage. Un départ se traite en supprimant un compte, sans
  toucher aux droits.
- Aucun compte applicatif n'est propriétaire des tables : même compromis, il ne
  peut pas faire de `DROP`.
- `ALTER DEFAULT PRIVILEGES` applique d'office les mêmes règles aux tables
  créées ultérieurement — c'est l'oubli classique lors d'une évolution de schéma.

## 5.7 Exemples d'appels

```bash
# Jeu consolidé, zone centre, à partir du 12 mai 8 h
curl -H "Authorization: Bearer $JETON" \
  "http://127.0.0.1:8000/api/v1/flotte/horaire?zone=centre&debut=2026-05-12T08:00:00&limite=50"

# Dernier état d'un véhicule
curl -H "Authorization: Bearer $JETON" \
  "http://127.0.0.1:8000/api/v1/vehicules/VLO-00007/etat"

# Interventions d'un véhicule depuis le 1er mars
curl -H "Authorization: Bearer $JETON" \
  "http://127.0.0.1:8000/api/v1/interventions?id_vehicule=VLO-00007&depuis=2026-03-01"

# Qualité des données (portée exploitation:lire)
curl -H "Authorization: Bearer $JETON_SUPERVISION" \
  "http://127.0.0.1:8000/api/v1/exploitation/qualite"
```

Une collection Postman s'obtient par import direct du fichier `openapi.yaml`
(*Import → OpenAPI 3.0*), ce qui évite de maintenir une collection en double.
