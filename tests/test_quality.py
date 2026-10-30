"""Tests des fonctions de contrôle qualité et d'homogénéisation.

Ces fonctions traitent 58 000 lignes par exécution : une erreur de conversion
y est invisible à l'œil nu et fausse tous les indicateurs en aval. C'est la
partie du code qui justifie le plus d'être testée.
"""

from datetime import timezone

import pytest

from velopolis.transform.quality import (
    Quarantaine,
    dans_bornes,
    en_celsius,
    normaliser_statut,
    parser_decimal,
    parser_entier,
    parser_horodatage,
    position_plausible,
)


class TestHorodatage:
    @pytest.mark.parametrize(
        "valeur,attendu",
        [
            ("2026-05-12T08:15:00Z", (2026, 5, 12, 8, 15)),
            ("2026-05-12T08:15:00+00:00", (2026, 5, 12, 8, 15)),
            ("2026-05-12 08:15:00", (2026, 5, 12, 8, 15)),
            ("12/05/2026 08:15:00", (2026, 5, 12, 8, 15)),
            ("12/05/2026", (2026, 5, 12, 0, 0)),
            ("2026-05-12", (2026, 5, 12, 0, 0)),
        ],
    )
    def test_formats_acceptes(self, valeur, attendu):
        dt = parser_horodatage(valeur)
        assert dt is not None
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == attendu

    def test_toujours_en_utc(self):
        """Une date sans fuseau est supposée UTC, jamais locale."""
        assert parser_horodatage("2026-05-12T08:15:00").tzinfo == timezone.utc

    def test_decalage_horaire_converti(self):
        dt = parser_horodatage("2026-05-12T10:15:00+02:00")
        assert dt.utcoffset().total_seconds() == 7200

    @pytest.mark.parametrize("valeur", ["", None, "n/a", "2026-02-30", "hier", "0000"])
    def test_valeurs_rejetees(self, valeur):
        assert parser_horodatage(valeur) is None


class TestNombres:
    @pytest.mark.parametrize(
        "valeur,attendu",
        [
            ("32,50", 32.50),       # virgule décimale (export français)
            ("32.50", 32.50),
            ("0,18 €/min", 0.18),   # montant avec devise et unité
            ("19,90 €", 19.90),
            ("1.234,56", 1234.56),  # point séparateur de milliers
            ("-5,5", -5.5),
            (42, 42.0),
        ],
    )
    def test_decimales(self, valeur, attendu):
        assert parser_decimal(valeur) == pytest.approx(attendu)

    @pytest.mark.parametrize("valeur", ["n/a", "—", "", None, "abc", True])
    def test_decimales_invalides(self, valeur):
        assert parser_decimal(valeur) is None

    @pytest.mark.parametrize("valeur,attendu", [("45 min", 45), ("45", 45), ("45,4", 45)])
    def test_entiers(self, valeur, attendu):
        assert parser_entier(valeur) == attendu


class TestUnites:
    def test_celsius_inchange(self):
        assert en_celsius(14.2, "C") == 14.2

    def test_fahrenheit_converti(self):
        assert en_celsius(57.6, "F") == pytest.approx(14.22, abs=0.01)

    def test_kelvin_converti(self):
        assert en_celsius(287.35, "K") == pytest.approx(14.2, abs=0.01)

    def test_unite_absente_supposee_celsius(self):
        assert en_celsius("14,2", None) == 14.2


class TestBornes:
    def test_position_dans_la_metropole(self):
        assert position_plausible(48.11, -1.68)

    @pytest.mark.parametrize(
        "lat,lon", [(None, None), (0, 0), (48.11, None), (12.5, 77.0), ("", "")]
    )
    def test_position_aberrante(self, lat, lon):
        assert not position_plausible(lat, lon)

    @pytest.mark.parametrize("valeur,ok", [(0, True), (100, True), (-1, False), (127, False)])
    def test_batterie(self, valeur, ok):
        assert dans_bornes(valeur, 0, 100) is ok


class TestStatuts:
    @pytest.mark.parametrize(
        "brut,attendu",
        [
            ("available", "disponible"),
            ("AVAILABLE", "disponible"),
            ("  In_Use ", "en_location"),
            ("out_of_service", "hors_service"),
            ("charging", "en_charge"),
        ],
    )
    def test_vocabulaire_ferme(self, brut, attendu):
        assert normaliser_statut(brut) == attendu

    @pytest.mark.parametrize("brut", ["unknown", "", None, "en panne"])
    def test_statut_inconnu(self, brut):
        assert normaliser_statut(brut) is None


class TestQuarantaine:
    def test_collecte_et_compte(self, tmp_path):
        q = Quarantaine("fait_telemetrie", tmp_path)
        q.rejeter({"a": 1}, "doublon")
        q.rejeter({"a": 2}, "doublon")
        q.rejeter({"a": 3}, "batterie_hors_bornes")
        assert len(q) == 3
        assert q.motifs == {"doublon": 2, "batterie_hors_bornes": 1}
        assert "doublon ×2" in q.resume()

    def test_ecriture_sur_disque(self, tmp_path):
        q = Quarantaine("source", tmp_path)
        q.rejeter({"x": "é"}, "motif")
        cible = q.ecrire()
        assert cible.is_file()
        contenu = cible.read_text(encoding="utf-8")
        assert "motif" in contenu and "é" in contenu

    def test_rien_a_ecrire(self, tmp_path):
        assert Quarantaine("source", tmp_path).ecrire() is None
