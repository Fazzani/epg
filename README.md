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
| [logosChannels/](logosChannels/) | Images référencées par les deux catalogues. |
| [logo_manifest.json](logo_manifest.json) | Médias historiques identifiés et préférences des collisions. |
| [sources/radios/manifest.json](sources/radios/manifest.json) | Stations importées, provenance et sommes SHA-256. |
| [reports/](reports/) | Images validées, collisions et images restant à qualifier. |

Le catalogue v1 commence par France 2 et les 80 stations de la grille principale
de [Tunisie Radio](https://www.tunisie-radio.com/), webradios comprises. Les pays
des radios restent `null` en attendant leur qualification. Leur présence sur ce
site ne suffit pas à établir un pays.

## Démarrage

Python 3.12 et les dépendances verrouillées sont utilisés par la CI.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python update_index.py --check
```

Le contrôle décode les images, vérifie la provenance locale des radios et compare
les catalogues et rapports aux fichiers versionnés. Il ne contacte aucun site.

## Architecture

Les manifestes sont les sources de métadonnées. Le générateur lit les images et
les manifestes, puis produit les deux catalogues et les trois rapports JSON.
Il ne modifie aucune image. Les téléchargements appartiennent exclusivement à
la commande d’import, lancée séparément.

Les 44 collisions historiques ont une préférence explicite dans
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

Dans une future intégration ViPlayer, cet identifiant représente une association
optionnelle au logo. Il ne remplace pas les identifiants des chaînes, des stations
Radio Browser ou des favoris existants. Une correspondance ambiguë entre noms,
pays ou types doit rester sans résultat.

### Rapports

- `reports/images.json` : format réellement décodé, dimensions, nombre de trames,
  transparence effective et SHA-256 des images et des originaux importés.
- `reports/collisions.json` : variantes et fichier préféré pour chaque collision.
- `reports/unidentified.json` : chemins sans association à un média du catalogue.

Une extension différente du format réel est signalée par `extension_mismatch`.
Les quatre fichiers historiques JPG contenant du PNG restent accessibles sous
leur nom actuel. Un fichier illisible bloque la génération avec son chemin.

## Exploitation

### Générer et vérifier

```sh
python update_index.py
python update_index.py --check
python update_index.py --output-dir dist
python update_index.py --output-dir dist --check
```

Sans option, la génération actualise les deux catalogues et les trois rapports à
la racine du dépôt. `--output-dir` place ces sorties dans un autre dossier ;
`--check` compare sans écrire. Les fichiers existants dans ce dossier portant
les mêmes noms sont remplacés lors d’une génération.

### Actualiser les radios

```sh
python import_radios.py
python update_index.py
python update_index.py --check
```

L’import exige actuellement 80 stations. Un changement de ce nombre nécessite
une revue de la grille, puis une valeur explicite `--expected-count N`.
`--html-file fichier.html` réutilise une page sauvegardée mais télécharge toujours
les images ; `--output-dir dossier` importe une collection dans un autre dossier.
Pour conserver les identifiants existants, actualiser le dossier contenant déjà
le manifeste de cette collection.

L’import lit les images différées, déduplique les pages de stations et préfère
les originaux. Un original inaccessible ou invalide entraîne un repli vers la
vignette de la grille, consigné avec sa résolution et sa raison. Tout échec
restant empêche la publication de l’import. Les téléchargements et le décodage
se terminent avant le remplacement des fichiers publiés.

Les octets originaux sont conservés dans `sources/radios/originals/`. Les images
publiées sont des PNG statiques à la taille de l’original, avec orientation EXIF
appliquée, proportions et transparence préservées. Une animation fournit sa
première trame. Aucun agrandissement ni cadrage carré n’est appliqué.

Chaque station garde son identité, même si elle partage une image avec une autre.
La page source et les URL des images sont consignées dans le manifeste. Les logos
restent les marques de leurs titulaires ; le dépôt ne leur attribue pas de
nouvelle licence.

### Validation GitHub Actions

Le workflow `Logo catalog` s’exécute sur pull request, push vers `master` et
lancement manuel. Il vérifie le formatage, les tests et les sorties versionnées,
puis génère et publie un artefact contenant les catalogues et rapports. Le job
d’une pull request vérifie le commit de sa branche source.

La validation ne télécharge pas les radios et n’utilise aucun secret Gist. Elle
dispose uniquement de la permission `contents: read` et ne publie aucun commit.
Les scripts `trim_picons.sh` sont obsolètes : ils délèguent désormais à une
génération dans `dist/`, sans traitement des images ni opération Git.

## Contribution

Pour qualifier un ancien média, ajouter son identité et ses métadonnées vérifiées
dans `logo_manifest.json`, choisir son image, puis régénérer les sorties. Pour
enrichir une radio importée, modifier son entrée dans le manifeste de radios ;
les identifiants, pays confirmés et alias sont conservés lors des actualisations.

```sh
python -m black --workers 1 update_index.py import_radios.py tests/
python -m unittest discover -s tests -v
python update_index.py --check
git diff --check
```

Consigner les changements dans le [changelog](CHANGELOG.md) et ouvrir une pull
request avec ses validations. Le catalogue versionné peut s’enrichir sans
supprimer les images non encore qualifiées de l’index historique.

