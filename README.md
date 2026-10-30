# Vélopolis — socle de données technique de la flotte

Chaîne de collecte, d'agrégation, de stockage et de mise à disposition du socle
de données de la flotte Vélopolis (3 000 véhicules, 250 stations, 45 000 abonnés).

Livrable de l'épreuve **E4 — bloc 2** du titre *Expert en infrastructures de
données massives* (RNCP 37638).

![Architecture](docs/img/archi.png)

## Ce que fait la chaîne

| Étape | Module | Résultat |
|---|---|---|
| Extraction de 6 sources | `velopolis.extract` | `data/raw/` — copie brute, rejouable |
| Contrôles qualité et homogénéisation | `velopolis.transform` | `data/curated/` + `data/quarantine/` |
| Modélisation et création de la base | `sql/ddl/` | 11 tables, 3 rôles, 9 index |
| Import | `velopolis.load` | PostgreSQL chargé par `COPY FROM STDIN` |
| Mise à disposition | `velopolis.api` | API REST OAuth 2.0 + accès SQL direct |
| Conformité | `velopolis.rgpd` | pseudonymisation, purge, droit à l'effacement |

Les six sources couvrent les cinq familles exigées par le sujet : **service web**
(API IoT et API météo), **fichier** (CSV du prestataire), **scraping** (site du
concurrent), **base de données** (PostgreSQL transactionnel) et **système big
data** (table Hive partitionnée).

## Démarrage rapide (mode démonstration, sans infrastructure)

Le mode démonstration utilise SQLite et des sources simulées : la chaîne
complète se déroule sur un poste nu, sans PostgreSQL ni cluster Hive. Il sert
aux tests et à la soutenance ; la cible de production reste PostgreSQL.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # mode démonstration par défaut
make fixtures                   # génère les jeux de test
make mock &                     # démarre les sources simulées (port 8001)
make pipeline                   # extraction → agrégation → schéma → import
make api                        # API REST sur http://127.0.0.1:8000
```

Vérification :

```bash
curl -s http://127.0.0.1:8000/api/v1/sante | python -m json.tool
```

## Démarrage sur PostgreSQL

```bash
docker compose up -d                       # PostgreSQL 16 sur le port 5432
export DB_URL="postgresql://velopolis:velopolis@localhost:5432/velopolis_socle"
export SOURCE_DB_URL="postgresql://velopolis:velopolis@localhost:5432/velopolis_source"

psql "$SOURCE_DB_URL" -f sql/fixtures/seed_source_postgres.sql   # base transactionnelle de test
make pipeline
psql "$DB_URL" -f sql/ddl/roles_et_acces.sql                     # rôles et droits
```

Pour brancher les sources réelles, il suffit de renseigner les variables
correspondantes dans `.env` (`IOT_API_URL`, `CONCURRENT_URL`, `HIVE_HOST`,
`MAINTENANCE_DIR`…) : aucun code n'est à modifier.

## Commandes

```bash
make fixtures     # régénère les jeux de test
make mock         # démarre les sources simulées
make extraire     # extraction des 6 sources
make agreger      # contrôles qualité, homogénéisation, consolidation
make schema       # applique le DDL
make importer     # charge la base
make pipeline     # enchaîne les quatre étapes
make api          # démarre l'API REST
make purge-sim    # simule la purge RGPD (ne supprime rien)
make purge        # applique les durées de conservation
make test         # tests unitaires
make demo         # fixtures + pipeline + appels d'API commentés
```

Chaque commande `make` appelle `python -m velopolis.cli <commande>` ;
`python -m velopolis.cli --help` détaille les options.

## Organisation du dépôt

```
src/velopolis/
  config.py            configuration par variables d'environnement
  db.py                connexion unique PostgreSQL / SQLite
  cli.py               point d'entrée en ligne de commande
  extract/             une source = un module + client HTTP commun
  transform/           quality.py (règles) · aggregate.py (consolidation)
  load/                création du schéma et import en masse
  rgpd/                pseudonymisation, purge, droit à l'effacement
  api/                 app.py (routes) · auth.py (OAuth2/JWT) · repository.py (SQL)
sql/
  ddl/                 schéma PostgreSQL (référence), SQLite (démo), rôles
  extraction/          requêtes commentées : choix, filtres, jointures, optimisations
  fixtures/            jeu de données source de test
docs/                  spécifications, MERISE, SQL, RGPD, API, démonstration
mock/                  sources simulées (API IoT, météo, site concurrent, Hive)
tests/                 99 tests : qualité, RGPD, API, injection SQL
openapi.yaml           contrat de l'API
```

## Documentation

| Document | Contenu |
|---|---|
| [docs/01-specifications-extraction.md](docs/01-specifications-extraction.md) | Sources, outils, exigences de programmation, accessibilité et disponibilité |
| [docs/02-modelisation-merise.md](docs/02-modelisation-merise.md) | MCD, règles de passage, MPD, choix d'index |
| [docs/03-requetes-sql.md](docs/03-requetes-sql.md) | Requêtes d'extraction commentées et plans d'exécution mesurés |
| [docs/04-rgpd-registre-traitements.md](docs/04-rgpd-registre-traitements.md) | Registre, pseudonymisation, durées de conservation, procédure de purge |
| [docs/05-api-rest.md](docs/05-api-rest.md) | Points de terminaison, portées, exemples d'appels |
| [docs/06-demonstration.md](docs/06-demonstration.md) | Déroulé de la démonstration, avec sorties réelles |

## Dépendances

Python 3.11 ou plus récent. Les versions exactes sont dans `requirements.txt` :
`requests`, `beautifulsoup4` (extraction), `Flask`, `PyJWT` (API),
`psycopg[binary]` (PostgreSQL), `PyHive` (Hive, optionnel), `pytest` (tests).

## Sécurité et conformité

- Aucun secret dans le dépôt : `.env` est ignoré par Git, seul `.env.example` est versionné.
- Aucun identifiant direct d'abonné n'entre dans la chaîne : la pseudonymisation
  est appliquée dès la requête d'extraction.
- Toutes les requêtes SQL de l'API sont paramétrées ; une suite de tests vérifie
  que l'injection est sans effet.
- Les durées de conservation sont appliquées par un traitement planifié et journalisé.
