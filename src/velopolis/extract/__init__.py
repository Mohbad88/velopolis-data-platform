"""Couche d'extraction : une source = un module, un contrat commun.

Chaque extracteur expose une fonction ``extraire(...) -> RapportExtraction``
qui dépose les données telles quelles dans ``data/raw/<source>/`` et rend
compte de ce qu'elle a lu. Aucune transformation n'est faite ici : la zone
brute doit rester rejouable à l'identique.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RapportExtraction:
    source: str
    fichier: Path | None = None
    lignes: int = 0
    duree_s: float = 0.0
    statut: str = "succes"
    detail: str = ""
    debut: float = field(default_factory=time.monotonic, repr=False)

    def terminer(self, lignes: int, fichier: Path | None = None, detail: str = "") -> "RapportExtraction":
        self.lignes = lignes
        self.fichier = fichier
        self.detail = detail
        self.duree_s = round(time.monotonic() - self.debut, 2)
        return self

    def echouer(self, detail: str) -> "RapportExtraction":
        self.statut = "echec"
        self.detail = detail
        self.duree_s = round(time.monotonic() - self.debut, 2)
        return self

    def en_dict(self) -> dict:
        d = asdict(self)
        d.pop("debut", None)
        d["fichier"] = str(self.fichier) if self.fichier else None
        return d
