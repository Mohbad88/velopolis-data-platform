"""Configuration centralisée, lue dans l'environnement (principe 12-factor).

Aucun secret n'est écrit dans le code ni versionné : le dépôt ne contient que
``.env.example``. Les identifiants réels sont fournis par variables
d'environnement ou par le gestionnaire de secrets de la DSI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]


def _charger_env(chemin: Path = RACINE / ".env") -> None:
    """Charge un fichier .env sans dépendance externe (ne surcharge jamais l'existant)."""
    if not chemin.is_file():
        return
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        os.environ.setdefault(cle.strip(), valeur.strip().strip('"').strip("'"))


_charger_env()


@dataclass(frozen=True)
class Config:
    # --- répertoires de travail -------------------------------------------
    racine: Path = RACINE
    repertoire_brut: Path = field(default_factory=lambda: RACINE / "data" / "raw")
    repertoire_consolide: Path = field(default_factory=lambda: RACINE / "data" / "curated")
    repertoire_quarantaine: Path = field(default_factory=lambda: RACINE / "data" / "quarantine")

    # --- source 1 : API REST des boîtiers IoT ------------------------------
    iot_url: str = os.environ.get("IOT_API_URL", "http://127.0.0.1:8001/iot/api/v1/telemetry")
    iot_token: str = os.environ.get("IOT_API_TOKEN", "jeton-de-test-iot")
    iot_taille_page: int = int(os.environ.get("IOT_PAGE_SIZE", "500"))

    # --- source 2 : API météo publique -------------------------------------
    meteo_url: str = os.environ.get("METEO_API_URL", "http://127.0.0.1:8001/meteo/api/v1/weather")

    # --- source 3 : fichiers CSV du prestataire ----------------------------
    maintenance_repertoire: Path = field(
        default_factory=lambda: Path(
            os.environ.get("MAINTENANCE_DIR", str(RACINE / "mock" / "data" / "maintenance"))
        )
    )

    # --- référentiels véhicules et stations (export JSON du SI) ------------
    referentiel_repertoire: Path = field(
        default_factory=lambda: Path(
            os.environ.get("REFERENTIEL_DIR", str(RACINE / "mock" / "data"))
        )
    )

    # --- source 4 : site public du concurrent ------------------------------
    concurrent_url: str = os.environ.get(
        "CONCURRENT_URL", "http://127.0.0.1:8001/concurrent/tarifs"
    )
    concurrent_robots_url: str = os.environ.get(
        "CONCURRENT_ROBOTS_URL", "http://127.0.0.1:8001/concurrent/robots.txt"
    )
    concurrent_user_agent: str = os.environ.get(
        "CONCURRENT_USER_AGENT", "VelopolisDataBot/1.0 (+contact: dsi@velopolis.fr)"
    )
    concurrent_delai_s: float = float(os.environ.get("CONCURRENT_DELAI_S", "2"))

    # --- source 5 : base transactionnelle (lecture seule, réplica) ---------
    source_db_url: str = os.environ.get(
        "SOURCE_DB_URL", "postgresql://velopolis@/velopolis_source?host=/tmp"
    )

    # --- source 6 : entrepôt big data (Hive) -------------------------------
    hive_host: str = os.environ.get("HIVE_HOST", "")
    hive_port: int = int(os.environ.get("HIVE_PORT", "10000"))
    hive_base: str = os.environ.get("HIVE_DATABASE", "velopolis")
    hive_table: str = os.environ.get("HIVE_TABLE", "gps_traces")
    hive_entrepot: Path = field(
        default_factory=lambda: Path(
            os.environ.get("HIVE_WAREHOUSE_DIR", str(RACINE / "mock" / "warehouse"))
        )
    )

    # --- base cible du projet ----------------------------------------------
    db_url: str = os.environ.get("DB_URL", "postgresql://velopolis@/velopolis_socle?host=/tmp")

    # --- API REST de mise à disposition ------------------------------------
    api_secret: str = os.environ.get("API_SECRET", "secret-de-developpement-a-remplacer")
    api_token_duree_min: int = int(os.environ.get("API_TOKEN_DUREE_MIN", "60"))

    # --- RGPD ---------------------------------------------------------------
    sel_pseudonymisation: str = os.environ.get("RGPD_SALT", "sel-de-developpement")
    retention_telemetrie_jours: int = int(os.environ.get("RETENTION_TELEMETRIE_JOURS", "30"))
    retention_location_mois: int = int(os.environ.get("RETENTION_LOCATION_MOIS", "13"))

    # --- fenêtre d'extraction ----------------------------------------------
    fenetre_debut: str = os.environ.get("FENETRE_DEBUT", "2026-05-11T00:00:00Z")
    fenetre_fin: str = os.environ.get("FENETRE_FIN", "2026-05-16T00:00:00Z")

    def preparer_repertoires(self) -> None:
        for rep in (self.repertoire_brut, self.repertoire_consolide, self.repertoire_quarantaine):
            rep.mkdir(parents=True, exist_ok=True)


config = Config()
