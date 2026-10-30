"""Tests de l'API : authentification, autorisation, pagination, injection SQL.

Les tests s'exécutent contre une base SQLite temporaire construite dans la
fixture, pour qu'ils ne dépendent ni d'un serveur PostgreSQL ni de données
préalablement chargées.
"""

import os
import sqlite3
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """Crée une base minimale et pointe la configuration dessus."""
    chemin = tmp_path_factory.mktemp("db") / "test.db"
    os.environ["DB_URL"] = f"sqlite:///{chemin}"

    conn = sqlite3.connect(chemin)
    conn.executescript((RACINE / "sql" / "ddl" / "schema_sqlite.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO dim_vehicule VALUES ('VLO-00001','velo','Arcade V2','2024-03-01')")
    conn.execute("INSERT INTO dim_station VALUES ('ST-001','Station 001','Centre',16,48.11,-1.68)")
    conn.execute(
        "INSERT INTO fait_telemetrie (id_vehicule,horodatage,latitude,longitude,batterie_pct,"
        "statut,id_station,zone) VALUES "
        "('VLO-00001','2026-05-12T08:00:00+00:00',48.11,-1.68,80,'disponible','ST-001','centre')"
    )
    conn.execute(
        "INSERT INTO socle_flotte_horaire (id_vehicule,heure,type_vehicule,zone,nb_releves,"
        "statut_dominant,nb_locations,duree_location_min,recette_eur,en_maintenance) VALUES "
        "('VLO-00001','2026-05-12T08:00:00','velo','centre',4,'disponible',1,12.0,3.5,0)"
    )
    conn.execute(
        "INSERT INTO fait_location (id_location,id_abonne_psd,tranche_age,type_abonnement,"
        "ville_abonne,id_vehicule,id_station_depart,id_station_arrivee,debut,fin,duree_min,"
        "distance_km,montant_eur) VALUES "
        "(1,'AB-abc123','25-34','annuel','Rennes','VLO-00001','ST-001','ST-001',"
        "'2026-05-12T08:00:00','2026-05-12T08:12:00',12.0,2.1,3.5)"
    )
    conn.commit()
    conn.close()
    return chemin


@pytest.fixture(scope="module")
def client(base):
    import velopolis.config as config_module

    config_module.config = config_module.Config()  # relit DB_URL
    import velopolis.db as db

    db.config = config_module.config
    from velopolis.api import repository

    repository.lire.__globals__["config"] = config_module.config

    from velopolis.api.app import creer_application

    app = creer_application()
    app.config["TESTING"] = True
    return app.test_client()


def jeton(client, identifiant, secret):
    rep = client.post(
        "/api/v1/token", json={"client_id": identifiant, "client_secret": secret}
    )
    return rep.get_json()["access_token"]


def entete(client, identifiant="analyste", secret="analyste-dev"):
    return {"Authorization": f"Bearer {jeton(client, identifiant, secret)}"}


# --------------------------------------------------------------------------
class TestService:
    def test_sante_sans_authentification(self, client):
        rep = client.get("/api/v1/sante")
        assert rep.status_code == 200
        assert rep.get_json()["statut"] == "disponible"

    def test_contrat_openapi_servi(self, client):
        assert client.get("/api/v1/openapi.yaml").status_code == 200


class TestAuthentification:
    def test_jeton_delivre(self, client):
        rep = client.post(
            "/api/v1/token",
            json={"client_id": "tableau-de-bord", "client_secret": "dashboard-dev"},
        )
        assert rep.status_code == 200
        corps = rep.get_json()
        assert corps["token_type"] == "Bearer" and corps["expires_in"] > 0

    def test_mauvais_secret_refuse(self, client):
        rep = client.post(
            "/api/v1/token", json={"client_id": "analyste", "client_secret": "faux"}
        )
        assert rep.status_code == 401

    def test_client_inconnu_refuse(self, client):
        rep = client.post("/api/v1/token", json={"client_id": "x", "client_secret": "y"})
        assert rep.status_code == 401

    def test_sans_jeton(self, client):
        assert client.get("/api/v1/vehicules").status_code == 401

    def test_jeton_malforme(self, client):
        rep = client.get("/api/v1/vehicules", headers={"Authorization": "Bearer pas-un-jwt"})
        assert rep.status_code == 401
        assert rep.get_json()["erreur"] == "jeton_invalide"


class TestAutorisation:
    def test_portee_suffisante(self, client):
        rep = client.get("/api/v1/vehicules", headers=entete(client, "tableau-de-bord", "dashboard-dev"))
        assert rep.status_code == 200

    def test_portee_insuffisante(self, client):
        """Le tableau de bord ne doit pas atteindre les données personnelles."""
        rep = client.get(
            "/api/v1/locations", headers=entete(client, "tableau-de-bord", "dashboard-dev")
        )
        assert rep.status_code == 403
        assert rep.get_json()["erreur"] == "portee_insuffisante"

    def test_portee_dediee_accordee(self, client):
        rep = client.get("/api/v1/locations", headers=entete(client))
        assert rep.status_code == 200

    def test_portee_demandee_non_accordee(self, client):
        rep = client.post(
            "/api/v1/token",
            json={
                "client_id": "tableau-de-bord",
                "client_secret": "dashboard-dev",
                "scope": "locations:lire",
            },
        )
        assert rep.status_code == 400


class TestDonnees:
    def test_liste_vehicules(self, client):
        corps = client.get("/api/v1/vehicules", headers=entete(client)).get_json()
        assert corps["donnees"][0]["id_vehicule"] == "VLO-00001"
        assert corps["pagination"]["retournees"] == 1

    def test_etat_vehicule(self, client):
        corps = client.get(
            "/api/v1/vehicules/VLO-00001/etat", headers=entete(client)
        ).get_json()
        assert corps["donnees"]["batterie_pct"] == 80

    def test_vehicule_inconnu(self, client):
        rep = client.get("/api/v1/vehicules/VLO-99999/etat", headers=entete(client))
        assert rep.status_code == 404

    def test_filtre_par_zone(self, client):
        corps = client.get(
            "/api/v1/flotte/horaire?zone=centre", headers=entete(client)
        ).get_json()
        assert len(corps["donnees"]) == 1
        vide = client.get(
            "/api/v1/flotte/horaire?zone=nord", headers=entete(client)
        ).get_json()
        assert vide["donnees"] == []

    def test_aucune_donnee_nominative_exposee(self, client):
        """Aucun nom, courriel ou téléphone ne doit sortir de l'API."""
        corps = client.get("/api/v1/locations", headers=entete(client)).get_json()
        ligne = corps["donnees"][0]
        assert ligne["id_abonne_psd"].startswith("AB-")
        assert not {"nom", "prenom", "email", "telephone", "date_naissance"} & set(ligne)


class TestPagination:
    def test_limite_plafonnee(self, client):
        corps = client.get(
            "/api/v1/flotte/horaire?limite=99999", headers=entete(client)
        ).get_json()
        assert corps["pagination"]["limite"] == 1000

    def test_limite_minimale(self, client):
        corps = client.get("/api/v1/vehicules?limite=0", headers=entete(client)).get_json()
        assert corps["pagination"]["limite"] == 1

    def test_decalage_negatif_ramene_a_zero(self, client):
        corps = client.get("/api/v1/vehicules?decalage=-5", headers=entete(client)).get_json()
        assert corps["pagination"]["decalage"] == 0


class TestSecuriteRequetes:
    @pytest.mark.parametrize(
        "charge",
        [
            "centre'; DROP TABLE dim_vehicule; --",
            "' OR '1'='1",
            "centre' UNION SELECT * FROM fait_location --",
        ],
    )
    def test_injection_sql_sans_effet(self, client, charge):
        """Les filtres passent par des paramètres : l'injection ne peut pas aboutir."""
        rep = client.get(f"/api/v1/flotte/horaire?zone={charge}", headers=entete(client))
        assert rep.status_code == 200
        assert rep.get_json()["donnees"] == []
        # la table est toujours là
        assert client.get("/api/v1/vehicules", headers=entete(client)).status_code == 200

    def test_tri_hors_liste_ignore(self, client):
        """Le tri est choisi dans une liste fermée, jamais interpolé tel quel."""
        rep = client.get(
            "/api/v1/flotte/horaire?tri=heure;DROP TABLE dim_vehicule", headers=entete(client)
        )
        assert rep.status_code == 200
