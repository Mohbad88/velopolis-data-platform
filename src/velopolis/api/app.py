"""Service REST de mise à disposition du socle de données.

Conventions appliquées à tous les points de terminaison :

* versionnement dans le chemin (``/api/v1/…``), pour pouvoir faire évoluer le
  contrat sans casser les clients existants ;
* pagination systématique (``limite``/``decalage``), plafonnée à 1 000 lignes ;
* réponses JSON uniformes ``{"donnees": [...], "pagination": {...}}`` ;
* erreurs décrites par un code stable et un message en clair ;
* contrat publié au format OpenAPI 3.0 (``openapi.yaml``), servi par
  ``/api/v1/openapi.yaml``.

Lancement : ``python -m velopolis.cli api`` ou ``flask --app velopolis.api.app run``
"""

from __future__ import annotations

import logging

from flask import Flask, g, jsonify, request, send_file

from ..config import config
from . import repository as depot
from .auth import PORTEES, creer_jeton, exige, verifier_client

logger = logging.getLogger(__name__)


def creer_application() -> Flask:
    app = Flask(__name__)
    app.config["JSON_SORT_KEYS"] = False

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------
    def page(donnees: list[dict], extra: dict | None = None):
        limite, decalage = depot.borner(
            request.args.get("limite", type=int), request.args.get("decalage", type=int)
        )
        corps = {
            "donnees": donnees,
            "pagination": {
                "limite": limite,
                "decalage": decalage,
                "retournees": len(donnees),
                "page_suivante": len(donnees) == limite,
            },
        }
        if extra:
            corps.update(extra)
        return jsonify(corps)

    @app.errorhandler(404)
    def _introuvable(_):
        return jsonify({"erreur": "ressource_introuvable"}), 404

    @app.errorhandler(500)
    def _erreur_serveur(exc):  # pragma: no cover
        logger.exception("Erreur non rattrapée : %s", exc)
        return jsonify({"erreur": "erreur_interne"}), 500

    # ------------------------------------------------------------------
    # Service et contrat
    # ------------------------------------------------------------------
    @app.get("/api/v1/sante")
    def sante():
        """Sonde de disponibilité : interroge réellement la base."""
        try:
            compte = depot.compter()
        except Exception as exc:
            return jsonify({"statut": "indisponible", "detail": str(exc)}), 503
        return jsonify({"statut": "disponible", "volumes": compte})

    @app.get("/api/v1/openapi.yaml")
    def contrat():
        return send_file(config.racine / "openapi.yaml", mimetype="application/yaml")

    # ------------------------------------------------------------------
    # Jeton d'accès (OAuth 2.0, client credentials)
    # ------------------------------------------------------------------
    @app.post("/api/v1/token")
    def token():
        charge = request.get_json(silent=True) or request.form
        identifiant = (charge.get("client_id") or "").strip()
        secret = charge.get("client_secret") or ""
        portees_demandees = (charge.get("scope") or "").split()

        client = verifier_client(identifiant, secret)
        if client is None:
            logger.warning("Échec d'authentification pour le client %r", identifiant)
            return jsonify({"error": "invalid_client"}), 401

        accordees = client["portees"]
        if portees_demandees:
            refusees = [p for p in portees_demandees if p not in accordees]
            if refusees:
                return jsonify({"error": "invalid_scope", "portees_refusees": refusees}), 400
            accordees = portees_demandees

        jeton, duree = creer_jeton(identifiant, accordees)
        return jsonify(
            {
                "access_token": jeton,
                "token_type": "Bearer",
                "expires_in": duree,
                "scope": " ".join(accordees),
            }
        )

    @app.get("/api/v1/portees")
    def portees():
        return jsonify({"portees": PORTEES})

    # ------------------------------------------------------------------
    # Référentiels
    # ------------------------------------------------------------------
    @app.get("/api/v1/vehicules")
    @exige("flotte:lire")
    def vehicules():
        return page(
            depot.vehicules(
                request.args.get("limite", type=int), request.args.get("decalage", type=int)
            )
        )

    @app.get("/api/v1/vehicules/<id_vehicule>/etat")
    @exige("flotte:lire")
    def etat_vehicule(id_vehicule: str):
        etat = depot.etat_vehicule(id_vehicule)
        if etat is None:
            return jsonify({"erreur": "vehicule_inconnu", "id_vehicule": id_vehicule}), 404
        return jsonify({"donnees": etat})

    @app.get("/api/v1/stations")
    @exige("flotte:lire")
    def stations():
        return page(
            depot.stations(
                request.args.get("limite", type=int), request.args.get("decalage", type=int)
            )
        )

    # ------------------------------------------------------------------
    # Jeu de données consolidé
    # ------------------------------------------------------------------
    @app.get("/api/v1/flotte/horaire")
    @exige("flotte:lire")
    def flotte_horaire():
        return page(
            depot.flotte_horaire(
                debut=request.args.get("debut"),
                fin=request.args.get("fin"),
                zone=request.args.get("zone"),
                id_vehicule=request.args.get("id_vehicule"),
                tri=request.args.get("tri", "heure"),
                limite=request.args.get("limite", type=int),
                decalage=request.args.get("decalage", type=int),
            )
        )

    @app.get("/api/v1/interventions")
    @exige("flotte:lire")
    def interventions():
        return page(
            depot.interventions(
                id_vehicule=request.args.get("id_vehicule"),
                depuis=request.args.get("depuis"),
                limite=request.args.get("limite", type=int),
                decalage=request.args.get("decalage", type=int),
            )
        )

    @app.get("/api/v1/tarifs-concurrent")
    @exige("flotte:lire")
    def tarifs():
        return page(
            depot.tarifs_concurrent(
                request.args.get("limite", type=int), request.args.get("decalage", type=int)
            )
        )

    # ------------------------------------------------------------------
    # Données personnelles indirectes : portée dédiée
    # ------------------------------------------------------------------
    @app.get("/api/v1/locations")
    @exige("locations:lire")
    def locations():
        donnees = depot.locations(
            debut=request.args.get("debut"),
            fin=request.args.get("fin"),
            limite=request.args.get("limite", type=int),
            decalage=request.args.get("decalage", type=int),
        )
        logger.info("Accès aux locations par le client %s (%d lignes)", g.client, len(donnees))
        return page(
            donnees,
            {"avertissement": "Identifiants abonnés pseudonymisés : ne pas tenter de réidentifier."},
        )

    # ------------------------------------------------------------------
    # Exploitation
    # ------------------------------------------------------------------
    @app.get("/api/v1/exploitation/journal")
    @exige("exploitation:lire")
    def journal():
        return page(
            depot.journal(
                request.args.get("limite", type=int), request.args.get("decalage", type=int)
            )
        )

    @app.get("/api/v1/exploitation/qualite")
    @exige("exploitation:lire")
    def qualite():
        return jsonify({"donnees": depot.qualite()})

    return app


application = creer_application()

if __name__ == "__main__":  # pragma: no cover
    application.run(host="127.0.0.1", port=8000)
