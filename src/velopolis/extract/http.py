"""Client HTTP partagé : reprise sur erreur, respect des quotas, journalisation.

Les deux API (boîtiers IoT, météo) et le scraping passent par ce module, pour
que la politique de disponibilité soit définie à un seul endroit :

* 4 tentatives maximum, attente exponentielle 1 s, 2 s, 4 s ;
* rejeu uniquement sur 429, 5xx et erreurs réseau — jamais sur un 4xx métier,
  qui signale une requête fautive et qu'il serait inutile de répéter ;
* respect de l'en-tête ``Retry-After`` quand le serveur l'envoie ;
* délai d'attente (timeout) explicite, pour qu'aucun appel ne puisse bloquer
  l'ordonnanceur indéfiniment.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

CODES_REJOUABLES = {408, 425, 429, 500, 502, 503, 504}
TENTATIVES_MAX = 4
TIMEOUT_S = 20


class ErreurExtraction(RuntimeError):
    """Échec définitif d'un appel après épuisement des tentatives."""


def obtenir(
    url: str,
    *,
    params: dict | None = None,
    entetes: dict | None = None,
    timeout: int = TIMEOUT_S,
    tentatives: int = TENTATIVES_MAX,
) -> requests.Response:
    attente = 1.0
    derniere = ""
    for essai in range(1, tentatives + 1):
        try:
            rep = requests.get(url, params=params, headers=entetes, timeout=timeout)
        except requests.RequestException as exc:
            derniere = f"erreur réseau : {exc}"
        else:
            if rep.status_code < 400:
                return rep
            derniere = f"HTTP {rep.status_code}"
            if rep.status_code not in CODES_REJOUABLES:
                raise ErreurExtraction(f"{url} → {derniere} (non rejouable)")
            entete_attente = rep.headers.get("Retry-After")
            if entete_attente and entete_attente.isdigit():
                attente = max(attente, float(entete_attente))

        if essai < tentatives:
            logger.warning("%s (%s) — nouvelle tentative dans %.0f s", url, derniere, attente)
            time.sleep(attente)
            attente *= 2

    raise ErreurExtraction(f"{url} → échec après {tentatives} tentatives ({derniere})")
