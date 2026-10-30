# 4. Conformité RGPD

## 4.1 Fiche de registre des traitements

À verser au registre tenu par le DPO de Vélopolis, en complément des fiches
existantes « base abonnés » et « géolocalisation ».

| Rubrique | Contenu |
|---|---|
| **Nom du traitement** | Socle de données technique de la flotte |
| **Responsable de traitement** | Vélopolis SAS |
| **Finalités** | Supervision technique de la flotte ; suivi des immobilisations et de la maintenance ; production du reporting contractuel dû à la métropole ; statistiques d'usage agrégées |
| **Base légale** | Intérêt légitime (art. 6.1.f) pour la supervision technique ; obligation contractuelle (art. 6.1.b) pour le reporting de la délégation de service public. À faire confirmer par le DPO |
| **Personnes concernées** | Abonnés du service (indirectement, par leurs locations) |
| **Catégories de données** | Identifiant abonné **pseudonymisé** ; tranche d'âge ; type d'abonnement ; ville ; horodatages de location ; stations de départ et d'arrivée ; position des véhicules ; état technique |
| **Données exclues** | Nom, prénom, courriel, téléphone, date de naissance exacte, coordonnées bancaires, moyen de paiement. **Ces colonnes ne sont jamais sélectionnées par la requête d'extraction** |
| **Destinataires** | Direction des opérations et maintenance (portée `flotte:lire`) ; analystes (portée `locations:lire`, données pseudonymisées) ; supervision (portée `exploitation:lire`) ; métropole (agrégats anonymes uniquement) |
| **Transferts hors UE** | Aucun. Hébergement sur l'infrastructure de la DSI, en France |
| **Durées de conservation** | Télémétrie et traces GPS : 30 jours · Locations pseudonymisées : 13 mois glissants · Quarantaine qualité : 30 jours · Agrégats anonymes : durée de la DSP |
| **Mesures de sécurité** | Pseudonymisation par HMAC-SHA-256 à clé secrète ; chiffrement en transit (TLS) et au repos ; authentification OAuth 2.0 et autorisation par portées ; trois rôles PostgreSQL au moindre privilège ; journalisation des accès aux données personnelles ; purge automatisée et tracée |
| **Analyse d'impact (AIPD)** | Requise : suivi systématique à grande échelle de données de localisation. À conduire avec le DPO avant la mise en production |

## 4.2 Pseudonymisation : pourquoi HMAC et pas un simple condensat

Un `SHA-256(id_abonne)` ne protège rien ici. Le domaine ne compte que
45 000 valeurs entières : un attaquant qui obtient la base calcule les
45 000 condensats en quelques secondes et reconstitue la table de
correspondance. La donnée serait pseudonymisée sur le papier, réidentifiable en
pratique.

Le pseudonyme est donc un **HMAC-SHA-256 avec une clé secrète** conservée hors
de la base, dans le coffre-fort de la DSI :

```
pseudonyme = "AB-" + HMAC_SHA256(clé, id_abonne)[:16]
```

Trois propriétés en découlent.

- **Non réversible sans la clé.** Qui obtient une copie de la base n'a pas la clé.
- **Déterministe.** Le même abonné donne toujours le même pseudonyme, ce qui
  préserve les jointures analytiques entre exécutions.
- **Réversible avec la clé.** L'entreprise peut encore honorer une demande
  d'effacement, en recalculant le pseudonyme de l'abonné concerné
  (`rgpd/purge.py::effacer_abonne`).

Un test vérifie l'absence de collision sur 45 000 pseudonymes, et un autre que
deux clés différentes produisent des pseudonymes différents.

## 4.3 Minimisation : trois niveaux

**À l'extraction.** Les colonnes identifiantes ne sont pas lues. La
pseudonymisation et la conversion de l'âge en tranche sont appliquées **avant**
l'écriture dans la zone brute : aucun identifiant direct n'entre dans la chaîne,
même temporairement, même sur disque.

**Au stockage.** Les traces GPS de trajet ne sont pas conservées au-delà de
30 jours. Au-delà, seule la maille station de départ / station d'arrivée
subsiste, qui suffit aux usages exprimés. La fonction `arrondir_position`
permet, si besoin, de dégrader la précision à trois décimales (environ 110 m) :
la maille reste utile pour analyser un flux entre quartiers, mais ne permet plus
de remonter à une adresse.

**À la restitution.** L'API n'expose aucun identifiant direct. Les locations
sont derrière une portée dédiée (`locations:lire`), et chaque accès est
journalisé côté serveur. Les traces GPS brutes ne sont accessibles par aucune
portée, et aucun rôle PostgreSQL autre que l'ETL n'a le `SELECT` sur
`fait_trace_gps` : c'est la donnée la plus ré-identifiante du socle.

## 4.4 Procédure de tri et de suppression

| Donnée | Durée | Justification |
|---|---|---|
| `fait_telemetrie` | 30 jours | La position d'un véhicule en location est une donnée personnelle indirecte. 30 jours couvrent le suivi d'exploitation et les litiges courants |
| `fait_trace_gps` | 30 jours | Donnée la plus précise, donc la plus sensible |
| `quarantaine_qualite` | 30 jours | Contient des extraits de lignes sources : même durée que la donnée dont elle est issue |
| `fait_location` | 13 mois | Permet la comparaison à l'année précédente ; au-delà, seules les agrégations anonymes sont conservées |

**Mise en œuvre.** `python -m velopolis.cli purger`, à planifier chaque semaine.
Le mode `--simulation` compte les lignes concernées sans rien supprimer : il
sert au contrôle mensuel et permet de vérifier la procédure sans risque.

**Journalisation.** Chaque purge écrit dans `journal_ingestion` le nombre de
lignes supprimées par table. Sans cette trace, l'entreprise ne peut pas
démontrer que la procédure tourne réellement — ce qui est précisément ce que
demande le principe de responsabilité (*accountability*, art. 5.2).

**Droit à l'effacement.** `rgpd/purge.py::effacer_abonne(id_abonne)` recalcule
le pseudonyme à partir de la clé, supprime les lignes correspondantes et
journalise l'opération.

**Évolution recommandée.** Avec le partitionnement mensuel de
`fait_telemetrie`, la purge devient un `DROP PARTITION` : instantané, sans
blocage de la table ni `VACUUM` ultérieur.

## 4.5 Points à faire trancher par le DPO

1. **Base légale du reporting à la métropole** : obligation contractuelle ou
   mission d'intérêt public, selon la rédaction du contrat de DSP.
2. **Durée de 13 mois pour les locations** : à confronter aux besoins du
   contrôle de gestion, qui pourrait demander davantage. Au-delà, il faudra
   démontrer que les données sont réellement anonymes, et non seulement
   pseudonymisées.
3. **Information des personnes** : la mention d'information destinée aux
   abonnés doit couvrir cette finalité de supervision technique.
4. **Sous-traitance** : le prestataire de maintenance transmet des fichiers
   contenant des identifiants de véhicules ; vérifier que le contrat le qualifie
   correctement au sens de l'article 28.
