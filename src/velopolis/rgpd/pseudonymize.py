"""Pseudonymisation des identifiants d'abonné et réduction des données.

Un simple condensat SHA-256 de l'identifiant ne protégerait rien : le domaine
ne compte que 45 000 valeurs entières, qu'un attaquant énumère en quelques
secondes pour reconstituer la table de correspondance. Le pseudonyme est donc
calculé par **HMAC-SHA-256 avec une clé secrète** conservée hors de la base,
dans le coffre-fort de la DSI. Sans cette clé, le pseudonyme n'est pas
réversible ; avec elle, l'entreprise peut encore honorer une demande
d'effacement en recalculant le pseudonyme d'un abonné donné.

La fonction est déterministe : le même identifiant donne toujours le même
pseudonyme, ce qui préserve les jointures analytiques entre exécutions.
"""

from __future__ import annotations

import hashlib
import hmac

from ..config import config

PREFIXE = "AB-"
LONGUEUR = 16

TRANCHES = ((18, 24), (25, 34), (35, 44), (45, 54), (55, 64))


def pseudonymiser(valeur, *, sel: str | None = None) -> str | None:
    """Rend un pseudonyme stable et non réversible sans la clé."""
    if valeur in (None, ""):
        return None
    cle = (sel if sel is not None else config.sel_pseudonymisation).encode("utf-8")
    empreinte = hmac.new(cle, str(valeur).encode("utf-8"), hashlib.sha256).hexdigest()
    return PREFIXE + empreinte[:LONGUEUR]


def tranche_age(age: int | None) -> str | None:
    """Remplace un âge exact par une tranche : donnée utile, personne moins identifiable."""
    if age is None or age < 0 or age > 120:
        return None
    if age < 18:
        return "moins de 18"
    for bas, haut in TRANCHES:
        if bas <= age <= haut:
            return f"{bas}-{haut}"
    return "65 et plus"


def arrondir_position(latitude, longitude, decimales: int = 3) -> tuple[float | None, float | None]:
    """Dégrade volontairement la précision d'une position (3 décimales ≈ 110 m).

    Utilisé pour les traces GPS conservées au-delà de la durée courte : la
    maille reste suffisante pour analyser un flux entre quartiers, mais ne
    permet plus de remonter à une adresse.
    """
    try:
        return round(float(latitude), decimales), round(float(longitude), decimales)
    except (TypeError, ValueError):
        return None, None
