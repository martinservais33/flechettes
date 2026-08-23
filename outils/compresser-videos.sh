#!/bin/bash
# Compresse les vidéos d'animation avant de les commiter.
#
# Traite les deux dossiers :
#   ui/static/animations/          -> animations d'événements (manifest.json)
#   ui/static/animations/players/  -> vidéos de victoire personnelles
#
# Ces fichiers partent dans git : c'est ce qui les fait arriver sur le Pi.
# Et git n'oublie jamais — un gros fichier commité une seule fois alourdit
# le dépôt pour toujours, même supprimé ensuite. D'où ce passage obligé.
#
# Seule la définition est réduite : les vidéos gardent leur durée entière,
# elles ne sont jamais tronquées.
#
# L'extension est préservée, car manifest.json référence les fichiers par
# leur nom exact — un renommage casserait l'animation en silence. Seul le
# .webm doit changer d'extension (avconvert ne sait pas l'écrire) : le
# script le signale alors explicitement.
#
# Usage :  ./outils/compresser-videos.sh [preset]
# Défaut : Preset960x540. Autres valeurs utiles : Preset1280x720 (plus
#          net, plus lourd), Preset640x480 (plus léger).
#
# S'appuie sur avconvert, livré avec macOS (ffmpeg n'est pas nécessaire).

set -euo pipefail

RACINE="$(cd "$(dirname "$0")/.." && pwd)/ui/static/animations"
PRESET="${1:-Preset960x540}"
SEUIL_MO=2          # en dessous, on ne réencode pas : gain nul, perte réelle

command -v avconvert >/dev/null || {
  echo "avconvert introuvable — cet outil suppose macOS." >&2; exit 1; }

[ -d "$RACINE" ] || { echo "Dossier introuvable : $RACINE" >&2; exit 1; }

shopt -s nullglob nocaseglob
fichiers=("$RACINE"/*.mp4 "$RACINE"/*.mov "$RACINE"/*.m4v "$RACINE"/*.webm
          "$RACINE"/players/*.mp4 "$RACINE"/players/*.mov
          "$RACINE"/players/*.m4v "$RACINE"/players/*.webm)
shopt -u nocaseglob

[ ${#fichiers[@]} -gt 0 ] || { echo "Aucune vidéo à compresser."; exit 0; }

total_avant=0
total_apres=0
renommes=()

for src in "${fichiers[@]}"; do
  nom="$(basename "$src")"
  ext="${nom##*.}"
  # étiquette lisible : players/x.mp4 ou x.mp4
  case "$src" in *"/players/"*) label="players/$nom";; *) label="$nom";; esac

  avant=$(stat -f%z "$src")
  total_avant=$((total_avant + avant))

  if [ "$avant" -lt $((SEUIL_MO * 1048576)) ]; then
    printf "  %-30s %7.1f Mo  (déjà léger, ignoré)\n" "$label" \
           "$(bc -l <<< "$avant/1048576")"
    total_apres=$((total_apres + avant))
    continue
  fi

  # avconvert écrit mp4/mov/m4v ; pour tout le reste on bascule en .mp4.
  case "$(echo "$ext" | tr 'A-Z' 'a-z')" in
    mp4|mov|m4v) dest="$src" ;;
    *)           dest="${src%.*}.mp4" ;;
  esac

  tmp="$(mktemp -t flechettes).${dest##*.}"
  if ! avconvert -s "$src" -p "$PRESET" -o "$tmp" --replace >/dev/null 2>&1; then
    echo "  $label : échec de la conversion, fichier laissé intact" >&2
    total_apres=$((total_apres + avant))
    rm -f "$tmp"
    continue
  fi

  # On ne remplace qu'en cas de gain réel, et jamais avant que la
  # conversion ait réussi : une coupure ne peut pas détruire l'original.
  apres=$(stat -f%z "$tmp")
  if [ "$apres" -lt "$avant" ]; then
    [ "$src" != "$dest" ] && { rm -f "$src"; renommes+=("$label -> $(basename "$dest")"); }
    mv "$tmp" "$dest"
    printf "  %-30s %7.1f Mo -> %6.1f Mo\n" "$label" \
           "$(bc -l <<< "$avant/1048576")" "$(bc -l <<< "$apres/1048576")"
    total_apres=$((total_apres + apres))
  else
    rm -f "$tmp"
    printf "  %-30s %7.1f Mo  (pas de gain, ignoré)\n" "$label" \
           "$(bc -l <<< "$avant/1048576")"
    total_apres=$((total_apres + avant))
  fi
done

printf "\nTotal : %.1f Mo -> %.1f Mo\n" \
       "$(bc -l <<< "$total_avant/1048576")" "$(bc -l <<< "$total_apres/1048576")"

if [ ${#renommes[@]} -gt 0 ]; then
  echo
  echo "ATTENTION — fichiers renommés, mettez à jour manifest.json :"
  printf "    %s\n" "${renommes[@]}"
fi

# Filet de sécurité : un nom mal orthographié dans manifest.json ne
# provoque aucune erreur visible, l'animation ne se déclenche simplement
# jamais. Autant le détecter ici.
python3 - "$RACINE" <<'PY'
import json, os, sys
racine = sys.argv[1]
chemin = os.path.join(racine, "manifest.json")
try:
    with open(chemin) as f:
        manifeste = json.load(f)
except Exception as e:
    print(f"\nmanifest.json illisible : {e}")
    sys.exit(0)

manquants = []
for evenement, valeur in manifeste.items():
    fichiers = valeur if isinstance(valeur, list) else valeur.get("files", [])
    for nom in fichiers:
        if not os.path.exists(os.path.join(racine, nom)):
            manquants.append(f"{evenement} -> {nom}")

if manquants:
    print("\nATTENTION — fichiers absents référencés par manifest.json :")
    for ligne in manquants:
        print(f"    {ligne}")
    print("  (ces animations ne se déclencheront jamais, sans message d'erreur)")
PY
