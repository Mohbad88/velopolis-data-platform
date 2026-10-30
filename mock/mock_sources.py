"""Services simulant les sources externes fournies par la DSI.

Un seul processus Flask expose, sur le port 8001 :

* ``/iot/api/v1/telemetry``  — API REST des boîtiers embarqués (Bearer + pagination)
* ``/meteo/api/v1/weather``  — API météo publique (sans authentification)
* ``/concurrent/tarifs``     — page HTML de la grille tarifaire d'un concurrent
* ``/concurrent/robots.txt`` — directives d'indexation, vérifiées avant scraping

L'API IoT renvoie volontairement un HTTP 429 toutes les 25 requêtes afin que la
politique de reprise du client (``extract/http.py``) soit réellement exercée
pendant la démonstration.

Usage : python mock/mock_sources.py  (ou : make mock)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, Response, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "mock" / "data"
JETON_ATTENDU = os.environ.get("IOT_API_TOKEN", "jeton-de-test-iot")

app = Flask(__name__)
_telemetrie = json.loads((DATA / "telemetrie.json").read_text(encoding="utf-8"))
_meteo = json.loads((DATA / "meteo.json").read_text(encoding="utf-8"))
_compteur = {"n": 0}


# --------------------------------------------------------------------------
# API REST des boîtiers IoT
# --------------------------------------------------------------------------
@app.get("/iot/api/v1/telemetry")
def telemetry():
    entete = request.headers.get("Authorization", "")
    if entete != f"Bearer {JETON_ATTENDU}":
        return jsonify({"error": "unauthorized"}), 401

    _compteur["n"] += 1
    if _compteur["n"] % 25 == 0:  # quota simulé
        return jsonify({"error": "rate limited"}), 429, {"Retry-After": "1"}

    page = max(1, request.args.get("page", type=int, default=1))
    taille = min(2000, request.args.get("page_size", type=int, default=500))
    depuis = request.args.get("since")

    lignes = _telemetrie
    if depuis:
        # Le filtre serveur ne s'applique qu'aux horodatages ISO : les relevés
        # dont la date est mal formée sont renvoyés tels quels, comme le ferait
        # une API qui ne valide pas ses propres données. C'est au client de les
        # détecter et de les mettre en quarantaine.
        lignes = [
            r
            for r in lignes
            if not str(r.get("ts", ""))[:4].isdigit() or str(r.get("ts", "")) >= depuis
        ]

    debut = (page - 1) * taille
    return jsonify(
        {
            "page": page,
            "page_size": taille,
            "total": len(lignes),
            "items": lignes[debut : debut + taille],
        }
    )


# --------------------------------------------------------------------------
# API météo publique
# --------------------------------------------------------------------------
@app.get("/meteo/api/v1/weather")
def weather():
    zone = request.args.get("zone")
    lignes = [r for r in _meteo if zone is None or r["zone"] == zone]
    return jsonify({"count": len(lignes), "items": lignes})


# --------------------------------------------------------------------------
# Site public du concurrent
# --------------------------------------------------------------------------
TARIFS = [
    ("Pass Liberté", "Vélo électrique", "1,00 €", "0,18 €/min", "—", "Centre, Nord, Sud"),
    ("Pass Liberté", "Trottinette", "1,00 €", "0,25 €/min", "—", "Centre, Nord"),
    ("Abonnement Mensuel", "Vélo électrique", "0,00 €", "0,10 €/min", "19,90 €", "Toutes zones"),
    ("Abonnement Mensuel", "Trottinette", "0,00 €", "0,16 €/min", "19,90 €", "Toutes zones"),
    ("Abonnement Annuel", "Vélo électrique", "0,00 €", "0,08 €/min", "179,00 €", "Toutes zones"),
    ("Pass Étudiant", "Vélo électrique", "0,50 €", "0,12 €/min", "9,90 €", "Centre, Ouest"),
]


@app.get("/concurrent/tarifs")
def tarifs():
    lignes = "\n".join(
        "      <tr>" + "".join(f"<td>{c}</td>" for c in t) + "</tr>" for t in TARIFS
    )
    html = f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><title>MobiCity — Nos tarifs</title></head>
<body>
  <h1>Nos tarifs</h1>
  <p class="maj">Grille tarifaire en vigueur au 1er mai 2026.</p>
  <table id="grille-tarifaire">
    <thead>
      <tr><th>Offre</th><th>Véhicule</th><th>Déblocage</th><th>Prix à la minute</th>
          <th>Abonnement</th><th>Zones couvertes</th></tr>
    </thead>
    <tbody>
{lignes}
    </tbody>
  </table>
</body></html>"""
    return Response(html, mimetype="text/html; charset=utf-8")


@app.get("/concurrent/robots.txt")
def robots():
    return Response(
        "User-agent: *\nDisallow: /compte/\nDisallow: /api/\nAllow: /\nCrawl-delay: 2\n",
        mimetype="text/plain",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("MOCK_PORT", 8001)), debug=False)
