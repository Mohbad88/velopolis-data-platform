#!/usr/bin/env bash
# Déroulé de la démonstration (point 9 du sujet).
# Chaque appel est commenté : ce que l'on montre, et ce qu'il faut regarder.
set -u
export PYTHONPATH=src
API=${API:-http://127.0.0.1:8000}
JQ() { python3 -m json.tool; }
titre() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }

titre "1. La chaîne complète : extraction, agrégation, schéma, import"
python3 -m velopolis.cli pipeline 2>&1 | grep -E "lues=|importées|Chaîne"

titre "2. Disponibilité du service (sans authentification)"
curl -s "$API/api/v1/sante" | JQ

titre "3. Un appel sans jeton est refusé"
curl -s -o /dev/null -w "HTTP %{http_code}\n" "$API/api/v1/vehicules"

titre "4. Un mauvais secret est refusé"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST "$API/api/v1/token" \
  -H "Content-Type: application/json" \
  -d '{"client_id":"analyste","client_secret":"faux"}'

titre "5. Obtention d'un jeton (client « tableau-de-bord », portée flotte:lire)"
JETON_DASH=$(curl -s -X POST "$API/api/v1/token" -H "Content-Type: application/json" \
  -d '{"client_id":"tableau-de-bord","client_secret":"dashboard-dev"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
echo "jeton obtenu : ${JETON_DASH:0:40}…"

titre "6. Jeu de données consolidé, filtré par zone et paginé"
curl -s -H "Authorization: Bearer $JETON_DASH" \
  "$API/api/v1/flotte/horaire?zone=centre&debut=2026-05-12T08:00:00&limite=2" | JQ

titre "7. Ce client n'a pas accès aux données personnelles (403 attendu)"
curl -s -H "Authorization: Bearer $JETON_DASH" "$API/api/v1/locations?limite=1" | JQ

titre "8. Avec la portée locations:lire, l'abonné reste pseudonymisé"
JETON_ANA=$(curl -s -X POST "$API/api/v1/token" -H "Content-Type: application/json" \
  -d '{"client_id":"analyste","client_secret":"analyste-dev"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -H "Authorization: Bearer $JETON_ANA" "$API/api/v1/locations?limite=1" | JQ

titre "9. Dernier état connu d'un véhicule"
curl -s -H "Authorization: Bearer $JETON_ANA" "$API/api/v1/vehicules/VLO-00007/etat" | JQ

titre "10. Véhicule inconnu (404 attendu)"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -H "Authorization: Bearer $JETON_ANA" \
  "$API/api/v1/vehicules/VLO-99999/etat"

titre "11. Tentative d'injection SQL : sans effet, la table est intacte"
curl -s -o /dev/null -w "HTTP %{http_code}\n" -H "Authorization: Bearer $JETON_ANA" \
  --get --data-urlencode "zone=centre'; DROP TABLE dim_vehicule; --" \
  "$API/api/v1/flotte/horaire"
curl -s -H "Authorization: Bearer $JETON_ANA" "$API/api/v1/vehicules?limite=1" \
  | python3 -c "import sys,json;print('dim_vehicule toujours interrogeable :',
  len(json.load(sys.stdin)['donnees'])>0)"

titre "12. Qualité des données : lignes écartées, par motif"
JETON_SUP=$(curl -s -X POST "$API/api/v1/token" -H "Content-Type: application/json" \
  -d '{"client_id":"supervision","client_secret":"supervision-dev"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -H "Authorization: Bearer $JETON_SUP" "$API/api/v1/exploitation/qualite" | JQ

titre "13. Purge RGPD en simulation (aucune suppression)"
python3 -m velopolis.cli purger --simulation

printf '\n\033[1;32mDémonstration terminée.\033[0m\n'
