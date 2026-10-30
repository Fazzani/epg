# Channel Picons

Source de logos TV et radio pour les lecteurs multimédias, dont ViPlayer.
L’index historique référence toutes les images ; le catalogue versionné associe
les médias identifiés à une image et à des métadonnées vérifiées.

[![Logo catalog](https://github.com/Fazzani/epg/actions/workflows/catalog.yml/badge.svg?branch=master)](https://github.com/Fazzani/epg/actions/workflows/catalog.yml)

Branche suivie par le badge : `master`.

## Présentation

| Élément | Rôle |
| --- | --- |
| [index_picons.json](index_picons.json) | Index compatible : `{"name": ["fichier.png", ...]}`. |
| [catalog.v1.json](catalog.v1.json) | Identités et métadonnées des médias qualifiés. |
| [catalog.client.v1.json](catalog.client.v1.json) | Catalogue client pour les applications, avec PNG en 128 et 256 pixels maximum. |
| [catalog.release.v1.json](catalog.release.v1.json) | SHA-256 du catalogue client pour l’actualisation distante. |
| [logosClient/](logosClient/) | PNG adressés par leur SHA-256, chargés à la demande. |
| [logosChannels/](logosChannels/) | Images référencées par les deux catalogues. |
| [logo_manifest.json](logo_manifest.json) | Médias identifiés, empreintes des logos et préférences des collisions. |
| [provenance-archive.zip](provenance-archive.zip) | Originaux, sauvegardes et provenance des collections précédentes. |
| [reports/](reports/) | Images validées, collisions et images restant à qualifier. |

Le catalogue v1 contient France 2, RMC Sport 1 à 4 et RMC Sport News, les
80 stations de la grille principale de [Tunisie Radio](https://www.tunisie-radio.com/),
webradios comprises, et les
médias sélectionnés dans les collections Wikimedia. Les pays
des radios restent `null` en attendant leur qualification. Leur présence sur ce
site ne suffit pas à établir un pays.

Les logos RMC Sport 2 à 4 et RMC Sport News correspondent aux variantes de 2018 ;
celui de RMC Sport 1 correspond à la variante de 2026.

## Démarrage

Python 3.12 et les dépendances verrouillées sont utilisés par la CI.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python update_index.py --check
```

Le contrôle décode les images, vérifie leurs empreintes et compare
les catalogues et rapports aux fichiers versionnés. Il ne contacte aucun site.

## Architecture

Le manifeste `logo_manifest.json` contient les médias et les empreintes de leurs
logos. Le générateur lit le manifeste et les images, puis produit les catalogues
et les rapports JSON.
Il ne modifie aucune image. `build_client_catalog.py` génère séparément les
variantes destinées aux applications, depuis les seuls médias
du catalogue v1. Les anciennes données d'import et leurs outils sont archivés.

Les 43 collisions historiques ont une préférence explicite dans
`preferred_images` : la variante PNG existante a été retenue. Toutes les variantes
restent dans l’index historique. Une nouvelle collision sans préférence bloque
la génération ; une préférence devenue invalide la bloque également.

## Référence

### Contrat historique

`index_picons.json` contient uniquement une propriété `name`, dont la valeur est
une liste triée de noms de fichiers de `logosChannels/`. La casse et les caractères
Unicode sont ceux des fichiers réels. Les extensions acceptées sont PNG, JPG,
JPEG, GIF, WebP et JFIF, sans distinction de casse. Documents, scripts, fichiers
temporaires et sauvegardes ne sont pas indexés.

Les radios importées portent le préfixe `radio-tn-` et figurent dans cette même
liste. Le préfixe identifie la collection d’import, pas le pays du média.

### Catalogue v1

```json
{
  "schema_version": 1,
  "entries": [
    {
      "id": "tv.fr.france-2",
      "name": "France 2",
      "type": "tv",
      "country": "FR",
      "aliases": ["France2", "France 2 HD", "FR: France 2"],
      "logo": "logosChannels/france2.png"
    }
  ]
}
```

`id` est une identité attribuée une fois et conservée lors des changements de nom,
de pays ou d’image. Sa forme est `tv.<espace>.<identité>` ou
`radio.<espace>.<identité>` ; l’espace `tn` des radios importées ne constitue pas
une métadonnée géographique. `type` vaut `tv` ou `radio`. `country` contient un
code pays à deux lettres majuscules ou `null` lorsqu’il n’est pas établi.
Les alias restent vides lorsqu’aucun autre nom n’est confirmé.
Les alias déjà établis sont conservés dans le manifeste.

`logo` est un chemin exact relatif à la racine du dépôt. Pour construire une URL,
utiliser la même révision Git pour le catalogue et ses images et encoder chaque
segment du chemin une seule fois. Exemple Python :

```python
from urllib.parse import quote, urljoin

base_url = "https://raw.githubusercontent.com/Fazzani/epg/master/"
logo_url = urljoin(base_url, quote(entry["logo"], safe="/"))
```

Pour une révision immuable, remplacer `master` par le SHA du commit. Une application
peut conserver `id` et résoudre son image depuis un catalogue plus récent. Elle
doit alors renouveler son cache si le chemin ou la somme de l’image change.

Dans ViPlayer, cet identifiant représente une association
optionnelle au logo. Il ne remplace pas les identifiants des chaînes, des stations
Radio Browser ou des favoris existants. Une correspondance ambiguë entre noms,
pays ou types doit rester sans résultat.

### Catalogue client et images à la demande

`catalog.client.v1.json` conserve `id`, `name`, `type`, `country` et `aliases`
du catalogue v1. Chaque entrée remplace `logo` par `variants`, avec deux clés
`"128"` et `"256"`. Une variante contient :

```json
{
  "path": "logosClient/<sha256>.png",
  "sha256": "<sha256 des octets PNG>",
  "width": 128,
  "height": 72,
  "bytes": 4567
}
```

Ces valeurs illustrent le format ; les dimensions et le poids réels figurent
dans le fichier généré. Le côté le plus long ne dépasse pas la taille choisie.
Les proportions et la transparence sont conservées, sans agrandissement ni
ajout de marges. Les deux tailles peuvent partager le même fichier lorsque la
source est petite ; les images identiques sont également dédupliquées.

Une application peut charger le catalogue à distance et le mettre en cache.
`catalog.release.v1.json` contient son SHA-256 : vérifier le catalogue avant de
remplacer un instantané valide. ViPlayer utilise cette méthode
sur `master`, conserve le dernier instantané valide hors ligne et actualise son
cache après six heures. Les anciens PNG adressés par contenu restent disponibles.

Une application qui souhaite épingler une version peut aussi utiliser les URL Raw
GitHub avec le SHA du commit contenant le catalogue **et** ses PNG :
`https://raw.githubusercontent.com/Fazzani/epg/<commit>/<path>`.
Le manifeste conserve des chemins relatifs et n’intègre pas le SHA de son propre
commit. Utiliser le SHA-256 de la variante comme clé de cache permet de réutiliser
une image inchangée entre deux versions du catalogue. Vérifier ce SHA et `bytes`
sur les octets téléchargés avant de les conserver.

Les attributions, licences, originaux et sauvegardes restent dans
`provenance-archive.zip`. L'archive contient également le guide Wikimedia et les
anciens outils d'import ; elle n'est pas utilisée pour générer le catalogue.
Son intégrité est vérifiée en CI avec `sha256sum -c provenance-archive.sha256`.

### Rapports

- `reports/images.json` : format réellement décodé, dimensions, nombre de trames,
  transparence effective et SHA-256 des images.
- `reports/collisions.json` : variantes et fichier préféré pour chaque collision.
- `reports/unidentified.json` : chemins sans association à un média du catalogue.

Une extension différente du format réel est signalée par `extension_mismatch`.
Les quatre fichiers historiques JPG contenant du PNG restent accessibles sous
leur nom actuel. Un fichier illisible bloque la génération avec son chemin.

## Exploitation

### Récupérer un logo Wikimedia

Depuis une page de fichier Commons ou Wikipédia en français :

```sh
python fetch_wikimedia_logo.py "https://commons.wikimedia.org/wiki/File:DAZN_1_2024.svg" logosChannels/dazn-exemple.png
python update_catalog.py
python update_catalog.py --check
```

Le téléchargement est explicite et distinct de la génération hors réseau. Le
script produit un PNG optimisé de 1 024 pixels maximum, en conservant proportions
et transparence, sans agrandir un raster. Les SVG et les très grands rasters sont
rendus par Wikimedia. Il refuse les animations et tout chemin de sortie existant.
Il n'enregistre ni original ni métadonnées. Une image est ajoutée à l'index ;
une identité doit être qualifiée séparément dans `logo_manifest.json` avant
d'apparaître dans le catalogue client.

### Générer et vérifier

Pour retirer le suffixe final `hd` des noms d'images de `logosChannels/`,
lancer `python rename_hd_logos.py`. Le script conserve l'extension et, si le
nom sans suffixe existe déjà avec cette extension, supprime la variante `hd`.
Il met à jour le manifeste et régénère le catalogue hors réseau.
Les chemins historiques supprimés ne sont plus présents dans l'index.

La commande complète, également exécutée par la CI, est :

```sh
python update_catalog.py
python update_catalog.py --check
```

Elle vérifie le manifeste et les images, puis actualise les index, rapports,
catalogue client et PNG. `--check` vérifie toutes ces étapes sans écrire.
Les commandes ci-dessous permettent de travailler sur une étape isolée.

```sh
python update_index.py
python update_index.py --check
python update_index.py --output-dir dist
python update_index.py --output-dir dist --check
python build_client_catalog.py
python build_client_catalog.py --check
python build_client_catalog.py --output-dir dist
python build_client_catalog.py --output-dir dist --check
```

Sans option, la génération actualise les catalogues et les rapports à
la racine du dépôt. `--output-dir` place ces sorties dans un autre dossier ;
`--check` compare sans écrire. Les fichiers existants dans ce dossier portant
les mêmes noms sont remplacés lors d’une génération.

Après une actualisation du manifeste ou des images, générer d’abord le catalogue v1 avec
`update_index.py`, puis les sorties client avec `build_client_catalog.py`. Cette
seconde commande ne modifie ni les index existants ni les images sources. Elle
écrit les deux manifestes client et leurs PNG ; `--check` compare chaque fichier
attendu, y compris les octets des images, sans écrire. Les anciennes images
adressées par contenu ne sont pas supprimées automatiquement.

### Validation GitHub Actions

Le workflow `Logo catalog` s’exécute sur pull request, push vers `master` et
lancement manuel. Il vérifie le formatage, les tests et les sorties versionnées,
puis génère et publie un artefact contenant les catalogues, rapports et PNG de
`logosClient/`. Le job
d’une pull request vérifie le commit de sa branche source.

La validation ne contacte ni les sources radio ni Wikimedia et n’utilise aucun secret Gist. Elle
dispose uniquement de la permission `contents: read` et ne publie aucun commit.
Les scripts `trim_picons.sh` sont obsolètes : ils délèguent désormais à une
génération dans `dist/`, sans traitement des images ni opération Git.

## Contribution

Pour ajouter ou modifier un média, renseigner son identité, ses métadonnées et
le SHA-256 de son image dans `logo_manifest.json`, puis régénérer les sorties.
Conserver les identifiants existants et les preuves de provenance.

```sh
python -m black --workers 1 update_catalog.py update_index.py build_client_catalog.py rename_hd_logos.py fetch_wikimedia_logo.py tests/
python -m unittest discover -s tests -v
python update_index.py --check
python build_client_catalog.py --check
git diff --check
```

Consigner les changements dans le [changelog](CHANGELOG.md) et ouvrir une pull
request avec ses validations. Le catalogue versionné peut s’enrichir sans
supprimer les images non encore qualifiées de l’index historique.

