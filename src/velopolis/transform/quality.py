"""Contrôles qualité et homogénéisation des formats.

Principe retenu : **une ligne douteuse n'est jamais supprimée en silence**.
Elle part en quarantaine avec son contenu d'origine et le motif du rejet, ce
qui permet de remonter au prestataire ou à l'équipe terrain plutôt que de
constater un trou dans les chiffres trois semaines plus tard.

Les fonctions de ce module sont pures et sans effet de bord : elles sont
couvertes par les tests unitaires de ``tests/test_quality.py``.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
FORMATS_DATE = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y",
)


def parser_horodatage(valeur: Any) -> datetime | None:
    """Accepte les formats ISO 8601 et français, rend un datetime en UTC.

    Toute date est ramenée à UTC : les sources mélangent dates naïves et
    dates horodatées, et comparer les unes aux autres sans fuseau commun
    produit des décalages d'une heure en période d'heure d'été.
    """
    if valeur in (None, ""):
        return None
    if isinstance(valeur, datetime):
        return valeur if valeur.tzinfo else valeur.replace(tzinfo=timezone.utc)

    texte = str(valeur).strip()
    if not texte:
        return None
    normalise = texte.replace("Z", "+0000").replace("z", "+0000")
    # "+01:00" -> "+0100" pour %z
    normalise = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", normalise)

    for forme in FORMATS_DATE:
        try:
            dt = datetime.strptime(normalise, forme)
        except ValueError:
            continue
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


# --------------------------------------------------------------------------
# Nombres
# --------------------------------------------------------------------------
NON_NUMERIQUE = re.compile(r"[^0-9,.\-]")


def parser_decimal(valeur: Any) -> float | None:
    """Lit « 32,50 », « 0,18 €/min », « 19.90 », « 1 234,5 » ; rejette « n/a », « — »."""
    if valeur is None or isinstance(valeur, bool):
        return None
    if isinstance(valeur, (int, float)):
        return float(valeur)

    texte = NON_NUMERIQUE.sub("", str(valeur).replace(" ", "").replace("\xa0", ""))
    if not texte or texte in {"-", ".", ","}:
        return None
    if "," in texte and "." in texte:          # « 1.234,56 » : le point sépare les milliers
        texte = texte.replace(".", "").replace(",", ".")
    else:
        texte = texte.replace(",", ".")
    try:
        return float(texte)
    except ValueError:
        return None


def parser_entier(valeur: Any) -> int | None:
    """Lit « 45 », « 45 min », « 45,0 » ; rejette le reste."""
    nombre = parser_decimal(valeur)
    return None if nombre is None else int(round(nombre))


# --------------------------------------------------------------------------
# Unités
# --------------------------------------------------------------------------
def en_celsius(valeur: Any, unite: str | None) -> float | None:
    """Ramène une température en degrés Celsius, quelle que soit l'unité source."""
    temperature = parser_decimal(valeur)
    if temperature is None:
        return None
    code = (unite or "C").strip().upper()[:1]
    if code == "F":
        return round((temperature - 32) * 5 / 9, 2)
    if code == "K":
        return round(temperature - 273.15, 2)
    return round(temperature, 2)


# --------------------------------------------------------------------------
# Bornes et référentiels
# --------------------------------------------------------------------------
# Emprise de la métropole, élargie d'une marge : au-delà, la position est fausse.
BBOX = {"lat_min": 47.95, "lat_max": 48.25, "lon_min": -1.95, "lon_max": -1.45}


def position_plausible(latitude: Any, longitude: Any) -> bool:
    lat, lon = parser_decimal(latitude), parser_decimal(longitude)
    if lat is None or lon is None:
        return False
    return (
        BBOX["lat_min"] <= lat <= BBOX["lat_max"]
        and BBOX["lon_min"] <= lon <= BBOX["lon_max"]
    )


def dans_bornes(valeur: Any, minimum: float, maximum: float) -> bool:
    nombre = parser_decimal(valeur)
    return nombre is not None and minimum <= nombre <= maximum


def normaliser_statut(valeur: Any) -> str | None:
    """Ramène les statuts à un vocabulaire fermé, insensible à la casse."""
    connus = {
        "available": "disponible",
        "disponible": "disponible",
        "in_use": "en_location",
        "en_location": "en_location",
        "out_of_service": "hors_service",
        "hors_service": "hors_service",
        "charging": "en_charge",
        "en_charge": "en_charge",
    }
    if valeur in (None, ""):
        return None
    return connus.get(str(valeur).strip().lower())


# --------------------------------------------------------------------------
# Quarantaine
# --------------------------------------------------------------------------
@dataclass
class Quarantaine:
    """Collecte les lignes écartées et le motif, puis les écrit sur disque."""

    source: str
    repertoire: Path
    lignes: list[dict] = field(default_factory=list)
    motifs: dict[str, int] = field(default_factory=dict)

    def rejeter(self, ligne: Any, motif: str) -> None:
        self.motifs[motif] = self.motifs.get(motif, 0) + 1
        self.lignes.append(
            {
                "source": self.source,
                "motif": motif,
                "detecte_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "contenu": json.dumps(ligne, ensure_ascii=False, default=str),
            }
        )

    def __len__(self) -> int:
        return len(self.lignes)

    def ecrire(self) -> Path | None:
        if not self.lignes:
            return None
        self.repertoire.mkdir(parents=True, exist_ok=True)
        cible = self.repertoire / f"{self.source}.csv"
        with cible.open("w", encoding="utf-8", newline="") as fh:
            graveur = csv.DictWriter(
                fh, fieldnames=["source", "motif", "detecte_le", "contenu"]
            )
            graveur.writeheader()
            graveur.writerows(self.lignes)
        return cible

    def resume(self) -> str:
        if not self.motifs:
            return "aucun rejet"
        return ", ".join(f"{motif} ×{n}" for motif, n in sorted(self.motifs.items()))
