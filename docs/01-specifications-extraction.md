# 1. Spécifications techniques de l'extraction

## 1.1 Périmètre

La DSI demande un socle technique, en amont de tout usage métier : collecter
les données de la flotte, les rendre cohérentes, les stocker et les mettre à
disposition. Les indicateurs métier ne relèvent pas de ce lot.

Six sources sont couvertes, qui représentent les cinq familles d'accès
exigées par le sujet.

| № | Source | Famille | Protocole / format | Volumétrie observée | Fréquence |
|---|---|---|---|---|---|
| 1 | Boîtiers IoT embarqués | Service web | REST, JSON, Bearer, paginé | 57 984 relevés sur 5 jours de test (≈ 17 M/jour en production) | 15 s par véhicule |
| 2 | API météo publique | Service web | REST, JSON, sans authentification | 471 relevés | horaire |
| 3 | Prestataire de maintenance | Fichier | CSV `;`, UTF-8 ou latin-1 | 409 lignes sur 3 mois | mensuelle |
| 4 | Site du concurrent | Scraping | HTML public | 6 offres | hebdomadaire |
| 5 | Base transactionnelle | Base de données | PostgreSQL 16, réplica lecture | 3 040 locations, 400 abonnés | continue |
| 6 | Entrepôt Hive | Big data | Table externe partitionnée par jour | 14 170 points GPS | continue |

## 1.2 Outils retenus et justification

| Besoin | Outil | Pourquoi celui-là |
|---|---|---|
| Langage | Python 3.11+ | Maîtrisé par l'équipe, couvre les six sources sans changer de technologie |
| Appels HTTP | `requests` | Standard de fait, gestion fine des en-têtes et des délais d'attente |
| Analyse HTML | `BeautifulSoup` | Tolérant au HTML mal formé, ce qui est la règle sur un site public |
| Base cible | PostgreSQL 16 | Déjà exploité par la DSI ; `COPY`, index partiels et partitionnement natifs |
| Pilote base | `psycopg` 3 | Support de `COPY FROM STDIN`, curseurs serveur, typage natif |
| API | Flask + PyJWT | Empreinte minimale pour un service de lecture ; JWT sans serveur d'autorisation tiers |
| Entrepôt | `PyHive` (HiveServer2) | Accès HiveQL standard ; repli sur lecture directe des partitions si le cluster est indisponible |

Aucun ordonnanceur n'est introduit dans ce lot : la chaîne s'exécute par une
commande unique (`velopolis.cli pipeline`), appelable depuis l'ordonnanceur
existant de la DSI ou depuis `cron`.

## 1.3 Exigences de programmation

**Séparation des couches.** Extraction, transformation et chargement sont trois
paquets distincts. L'extraction ne transforme rien : elle dépose les données
telles qu'elles arrivent. Cette règle rend la zone brute rejouable, ce qui
permet de corriger une règle de nettoyage et de la rejouer sans redemander les
données à la source.

**Configuration hors du code.** Toutes les URL, identifiants et durées de
conservation viennent de l'environnement (`config.py`). Le dépôt ne contient
que `.env.example` ; passer des sources de test aux sources réelles ne demande
aucune modification de code.

**Idempotence.** Une même fenêtre rejouée deux fois produit le même résultat :
l'import vide puis recharge les tables dans une transaction unique, et la
déduplication repose sur des clés naturelles (`id_vehicule` + `horodatage`,
`id_intervention`, `id_location`).

**Traitement en flux.** La télémétrie est lue et écrite ligne à ligne, jamais
chargée entièrement en mémoire : à 17 millions de messages par jour, un
`read()` complet est exclu. L'extraction de la base utilise un curseur avec
`fetchmany` par lots de 5 000 lignes.

**Journalisation.** Chaque étape écrit une ligne dans `journal_ingestion`
(source, volumes lus, conservés, rejetés, durée, statut). Sans ce journal, une
extraction qui ramène la moitié des lignes passe inaperçue.

**Tests.** Les fonctions de conversion et de contrôle sont pures et couvertes
par 99 tests unitaires, dont une série vérifiant qu'une injection SQL dans les
paramètres de l'API reste sans effet.

**Versionnement.** Dépôt Git, un commit par lot fonctionnel, `.env` exclu.

## 1.4 Contraintes de disponibilité

Les sources externes tombent, ralentissent ou limitent le débit. La politique
est définie une seule fois, dans `extract/http.py`, et s'applique aux trois
sources HTTP.

- **Reprise** : 4 tentatives, attente exponentielle 1 s, 2 s, 4 s.
- **Rejeu sélectif** : seuls 429, 408, 425 et 5xx sont rejoués. Un 400 ou un 404
  signale une requête fautive ; la répéter ne ferait que consommer du quota.
- **Respect des quotas** : l'en-tête `Retry-After` prime sur l'attente calculée.
- **Délai d'attente** : 20 secondes par appel, pour qu'aucune extraction ne
  puisse bloquer l'ordonnanceur.
- **Dégradation contrôlée** : l'échec d'une source n'interrompt pas les cinq
  autres. Le rapport d'exécution porte le statut `echec` pour la source
  concernée, et la chaîne continue.
- **Extraction incrémentale** : l'API IoT est interrogée avec un paramètre
  `since`, ce qui limite le volume transféré et le temps de reprise après
  incident.

Comportement vérifié : pendant la démonstration, l'API de test renvoie un
HTTP 429 toutes les 25 requêtes ; les relances apparaissent dans le journal et
l'extraction aboutit malgré tout.

## 1.5 Contraintes d'accessibilité

**Accès aux sources.** L'extraction de la base transactionnelle se fait sur un
**réplica en lecture**, avec un compte limité au `SELECT` sur trois tables. Une
requête analytique mal calibrée ne doit pas pouvoir dégrader le service rendu
aux abonnés.

**Scraping.** Avant toute collecte, `robots.txt` est lu et interprété : si le
chemin visé est interdit, la collecte est abandonnée et la source est signalée
en échec. Le `Crawl-delay` annoncé est respecté, et le `User-Agent` identifie
Vélopolis avec une adresse de contact. La collecte se limite à des données
publiques et non personnelles.

**Mise à disposition.** Deux canaux, décrits au document 05 : une API REST
authentifiée et paginée, et un accès SQL direct en lecture seule pour les
analystes. Les réponses de l'API sont en JSON, avec des messages d'erreur
explicites en français ; le contrat est publié au format OpenAPI 3.0, lisible
par un humain comme par un générateur de client.

**Minimisation à la source.** Les colonnes `nom`, `prenom`, `email` et
`telephone` ne sont jamais sélectionnées. La date de naissance est lue, convertie
en tranche d'âge, puis écartée avant l'écriture sur disque. Ce qui n'est pas
extrait ne peut pas fuiter.
