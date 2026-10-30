# Raccourcis de la chaîne. Chaque cible appelle le même point d'entrée Python.
PY      ?= python3
EXPORT  := PYTHONPATH=src

.PHONY: help fixtures mock extraire agreger schema importer pipeline api purge purge-sim test demo clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

fixtures:   ## Génère les jeux de données de test
	$(PY) mock/generate_fixtures.py

mock:       ## Démarre les sources simulées (port 8001)
	$(PY) mock/mock_sources.py

extraire:   ## Extrait les six sources vers data/raw
	$(EXPORT) $(PY) -m velopolis.cli extraire

agreger:    ## Contrôles qualité, homogénéisation, consolidation
	$(EXPORT) $(PY) -m velopolis.cli agreger

schema:     ## Applique le DDL sur la base cible
	$(EXPORT) $(PY) -m velopolis.cli creer-schema

importer:   ## Charge les données consolidées en base
	$(EXPORT) $(PY) -m velopolis.cli importer

pipeline:   ## Enchaîne extraction, agrégation, schéma et import
	$(EXPORT) $(PY) -m velopolis.cli pipeline

api:        ## Démarre l'API REST (port 8000)
	$(EXPORT) $(PY) -m velopolis.cli api

purge-sim:  ## Simule la purge RGPD sans rien supprimer
	$(EXPORT) $(PY) -m velopolis.cli purger --simulation

purge:      ## Applique les durées de conservation
	$(EXPORT) $(PY) -m velopolis.cli purger

test:       ## Tests unitaires
	$(EXPORT) $(PY) -m pytest

demo:       ## Déroulé complet commenté (voir docs/06-demonstration.md)
	bash scripts/demonstration.sh

clean:      ## Supprime les données produites
	rm -rf data/raw data/curated data/quarantine
