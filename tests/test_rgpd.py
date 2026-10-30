"""Tests de la pseudonymisation : la garantie la plus sensible du projet."""

import pytest

from velopolis.rgpd.pseudonymize import arrondir_position, pseudonymiser, tranche_age


class TestPseudonymisation:
    def test_deterministe(self):
        """Même entrée, même pseudonyme : sinon les jointures analytiques cassent."""
        assert pseudonymiser(1234, sel="s") == pseudonymiser(1234, sel="s")

    def test_depend_de_la_cle(self):
        """Sans la clé, un attaquant ne peut pas reconstruire la correspondance."""
        assert pseudonymiser(1234, sel="cle-a") != pseudonymiser(1234, sel="cle-b")

    def test_entrees_differentes(self):
        assert pseudonymiser(1, sel="s") != pseudonymiser(2, sel="s")

    def test_format(self):
        p = pseudonymiser(42, sel="s")
        assert p.startswith("AB-") and len(p) == 19

    def test_pas_de_collision_sur_le_parc_abonnes(self):
        """45 000 abonnés : aucun doublon ne doit apparaître sur 16 caractères."""
        pseudonymes = {pseudonymiser(i, sel="s") for i in range(45_000)}
        assert len(pseudonymes) == 45_000

    @pytest.mark.parametrize("valeur", [None, ""])
    def test_valeur_vide(self, valeur):
        assert pseudonymiser(valeur) is None


class TestTrancheAge:
    @pytest.mark.parametrize(
        "age,attendu",
        [
            (15, "moins de 18"),
            (18, "18-24"),
            (24, "18-24"),
            (25, "25-34"),
            (54, "45-54"),
            (64, "55-64"),
            (65, "65 et plus"),
            (95, "65 et plus"),
        ],
    )
    def test_tranches(self, age, attendu):
        assert tranche_age(age) == attendu

    @pytest.mark.parametrize("age", [None, -1, 130])
    def test_ages_invalides(self, age):
        assert tranche_age(age) is None


class TestArrondiPosition:
    def test_precision_degradee(self):
        """Trois décimales ≈ 110 m : la rue n'est plus identifiable."""
        assert arrondir_position(48.111456, -1.680987) == (48.111, -1.681)

    def test_valeurs_invalides(self):
        assert arrondir_position(None, "x") == (None, None)
