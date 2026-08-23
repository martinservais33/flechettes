# Vidéos de victoire personnelles

Un fichier par joueur, nommé d'après lui. C'est tout : aucune ligne à
ajouter dans `manifest.json`, aucun réglage dans l'application.

```
players/martin.mp4
players/Jean-Marc.mp4
players/coco.webm
```

Quand un joueur gagne une partie, sa vidéo est jouée en plein écran.
S'il n'en a pas, l'animation générique `victoire` du manifeste prend le
relais (et s'il n'y en a pas non plus, rien ne s'affiche — comme avant).

## Comment le nom est reconnu

Le rapprochement se fait sur le nom **normalisé** : minuscules, sans
accents, sans espaces ni ponctuation. Le joueur `Jean-Marc` et le joueur
`jean marc` retrouvent donc tous les deux `Jean-Marc.mp4`.

Conséquence : deux joueurs dont les noms ne diffèrent que par la casse ou
les accents partagent la même vidéo.

## Formats

`.mp4`, `.webm`, `.mov`, `.m4v` (et `.gif`, traité comme une image).

Le `.mp4` en H.264 est le plus sûr : c'est le seul lu partout, y compris
par le Chromium du Raspberry Pi.

## Durée et poids — à ne pas négliger

La vidéo est jouée **en entier**, quelle que soit sa longueur, et la
partie reste en pause pendant ce temps. À vous de juger : une vidéo vue
trente fois pendant les vacances a intérêt à être courte.

Ces fichiers sont versionnés dans git : c'est ce qui les fait arriver sur
le Pi lors d'un `git pull`. Or **git n'oublie jamais** — un fichier de
50 Mo commité une seule fois alourdit le dépôt pour toujours, même
supprimé par la suite. Une vidéo de téléphone brute fait exactement cette
taille.

D'où le passage obligé, à lancer depuis `flechette/` avant de commiter :

```sh
./outils/compresser-videos.sh                    # ramène en 960x540
./outils/compresser-videos.sh Preset1280x720     # plus net, plus lourd
./outils/compresser-videos.sh Preset640x480      # plus léger
```

**Seule la définition est réduite : les vidéos gardent leur durée
entière, elles ne sont jamais tronquées.**

Il ne touche pas aux fichiers déjà légers, ne remplace un fichier qu'en
cas de gain réel, et peut être relancé sans rien dégrader. Il s'appuie
sur `avconvert`, livré avec macOS — pas besoin d'installer ffmpeg.

Ordre de grandeur constaté : une source 1080p de 25 s pesant 64 Mo
redescend à 14,6 Mo, sans perdre une seconde. Et de vraies images
filmées compressent bien mieux que le cas de test.

## Le son

La vidéo est lancée avec le son. Si le navigateur refuse la lecture
automatique non muette, elle repart en muet : rien ne casse, mais il n'y
a plus de son.

Sur le Pi, pour garantir le son, lancer Chromium avec :

```
--autoplay-policy=no-user-gesture-required
```

## Supprimer un joueur

La vidéo reste sur le disque. C'est voulu : supprimer un joueur ne
supprime ni ses parties ni son Elo. S'il est recréé plus tard, il
retrouve sa vidéo. Pour l'effacer vraiment, supprimer le fichier à la
main.
