"""Authentification et autorisation de l'API.

Schéma retenu : **OAuth 2.0, flux « client credentials »**, jeton JWT signé en
HS256 et de durée courte. Un jeton porte la liste des portées (*scopes*)
accordées au client ; chaque point de terminaison déclare la portée qu'il
exige. L'authentification répond donc à « qui appelle ? », l'autorisation à
« a-t-il le droit de voir ces données ? » — les deux questions sont traitées
séparément.

Les secrets des clients ne sont jamais stockés en clair : seul un condensat
PBKDF2-HMAC-SHA256 est conservé. En production, ce registre est remplacé par
l'annuaire de la DSI ; la fonction ``verifier_client`` est le seul point à
réécrire.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import g, jsonify, request

from ..config import config

ALGORITHME = "HS256"
ITERATIONS = 240_000

# Portées disponibles, de la plus restreinte à la plus large.
PORTEES = {
    "flotte:lire": "Référentiels, état horaire de la flotte, météo, tarifs concurrents",
    "locations:lire": "Locations pseudonymisées (données personnelles indirectes)",
    "exploitation:lire": "Journal d'ingestion et quarantaine qualité",
}


def _condensat(secret: str, sel: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", secret.encode("utf-8"), bytes.fromhex(sel), ITERATIONS
    ).hex()


def enregistrer_secret(secret: str) -> str:
    """Produit la valeur « sel$condensat » à stocker pour un client."""
    sel = os.urandom(16).hex()
    return f"{sel}${_condensat(secret, sel)}"


# Registre de démonstration. Les secrets sont ceux de .env.example, à
# remplacer avant toute mise en service.
CLIENTS: dict[str, dict] = {
    "tableau-de-bord": {
        "secret": enregistrer_secret(os.environ.get("CLIENT_DASHBOARD_SECRET", "dashboard-dev")),
        "portees": ["flotte:lire"],
        "libelle": "Tableau de bord de la Direction des opérations",
    },
    "analyste": {
        "secret": enregistrer_secret(os.environ.get("CLIENT_ANALYSTE_SECRET", "analyste-dev")),
        "portees": ["flotte:lire", "locations:lire"],
        "libelle": "Poste analyste (données pseudonymisées)",
    },
    "supervision": {
        "secret": enregistrer_secret(os.environ.get("CLIENT_SUPERVISION_SECRET", "supervision-dev")),
        "portees": ["flotte:lire", "exploitation:lire"],
        "libelle": "Supervision de la chaîne d'ingestion",
    },
}


def verifier_client(identifiant: str, secret: str) -> dict | None:
    client = CLIENTS.get(identifiant)
    if client is None:
        # Comparaison factice : le temps de réponse ne doit pas révéler
        # si l'identifiant existe.
        _condensat(secret, os.urandom(16).hex())
        return None
    sel, _, attendu = client["secret"].partition("$")
    if not hmac.compare_digest(_condensat(secret, sel), attendu):
        return None
    return client


def creer_jeton(identifiant: str, portees: list[str]) -> tuple[str, int]:
    duree = timedelta(minutes=config.api_token_duree_min)
    maintenant = datetime.now(timezone.utc)
    charge = {
        "sub": identifiant,
        "scope": " ".join(portees),
        "iat": maintenant,
        "exp": maintenant + duree,
        "iss": "velopolis-socle",
    }
    return jwt.encode(charge, config.api_secret, algorithm=ALGORITHME), int(duree.total_seconds())


def lire_jeton(jeton: str) -> dict:
    return jwt.decode(jeton, config.api_secret, algorithms=[ALGORITHME], issuer="velopolis-socle")


def exige(portee: str):
    """Décorateur : refuse l'appel si le jeton ne porte pas la portée demandée."""

    def decorateur(vue):
        @wraps(vue)
        def enveloppe(*args, **kwargs):
            entete = request.headers.get("Authorization", "")
            if not entete.startswith("Bearer "):
                return (
                    jsonify({"erreur": "jeton_absent",
                             "message": "En-tête Authorization: Bearer <jeton> attendu."}),
                    401,
                    {"WWW-Authenticate": 'Bearer realm="velopolis"'},
                )
            try:
                charge = lire_jeton(entete[7:].strip())
            except jwt.ExpiredSignatureError:
                return jsonify({"erreur": "jeton_expire",
                                "message": "Jeton expiré, demandez-en un nouveau."}), 401
            except jwt.InvalidTokenError:
                return jsonify({"erreur": "jeton_invalide"}), 401

            portees = charge.get("scope", "").split()
            if portee not in portees:
                return (
                    jsonify({"erreur": "portee_insuffisante",
                             "message": f"La portée « {portee} » est requise.",
                             "portees_du_jeton": portees}),
                    403,
                )
            g.client = charge.get("sub")
            g.portees = portees
            return vue(*args, **kwargs)

        return enveloppe

    return decorateur
