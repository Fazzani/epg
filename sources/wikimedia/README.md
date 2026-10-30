---
title: Logos Wikimedia Commons
description: Importer les collections Wikimedia, choisir leurs logos et conserver les crédits et les images remplacées.
---

# Logos Wikimedia Commons

Cette collection contient **241 fichiers de logos**, leurs originaux et une
version PNG pour les lecteurs multimédias. Les six catégories demandées et leurs
sous-catégories ont fourni 245 fichiers uniques ; quatre éléments sans rapport
avec un logo de chaîne sont exclus explicitement.

## Collections

| Catégorie source | Collection |
| --- | --- |
| [Logos des chaînes des Émirats arabes unis](https://commons.wikimedia.org/wiki/Category:Logos_of_television_channels_in_the_United_Arab_Emirates) | Émirats arabes unis |
| [Logos SVG des chaînes des Émirats arabes unis](https://commons.wikimedia.org/wiki/Category:SVG_logos_of_television_channels_of_the_United_Arab_Emirates) | Émirats arabes unis, SVG |
| [Logos des chaînes en Égypte](https://commons.wikimedia.org/wiki/Category:Logos_of_television_channels_in_Egypt) | Égypte |
| [Logos des chaînes en Arabie saoudite](https://commons.wikimedia.org/wiki/Category:Logos_of_television_channels_in_Saudi_Arabia) | Arabie saoudite |
| [Logos des chaînes en Tunisie](https://commons.wikimedia.org/wiki/Category:Logos_of_television_channels_in_Tunisia) | Tunisie |
| [Logos SVG DAZN](https://commons.wikimedia.org/wiki/Category:SVG_logos_of_DAZN) | DAZN |

Une image présente dans plusieurs catégories n’est importée qu’une fois. Les
anciens logos, variantes de couleur et logos de réseau restent disponibles comme
images ; ils ne créent pas automatiquement de nouvelles chaînes dans le catalogue.

## Utilisation

Les PNG sont référencés dans [l’index historique](../../index_picons.json) sous
le nom `commons-<page_id>.png`. Les médias qualifiés sont accessibles dans le
[catalogue v1](../../catalog.v1.json) avec une identité indépendante de l’image.
La règle de résolution des URL est documentée dans le [README principal](../../README.md).

Les originaux sont conservés dans [originals/](originals/), dont **60 SVG**.
Git les traite comme des fichiers binaires pour conserver exactement leurs
octets, y compris leurs fins de ligne. Leurs empreintes contrôlent cette intégrité.
Les PNG ont un côté maximal de 1 024 pixels. Les images raster ne sont pas
agrandies ; les rendus des SVG proviennent du serveur Wikimedia. Les proportions
et la transparence effective sont contrôlées lors de la validation locale.

## Aperçu de la sélection

| Média | Logo | Crédit et licence Commons |
| --- | --- | --- |
| [Abu Dhabi TV](https://commons.wikimedia.org/wiki/File:Abu_Dhabi_TV_Logo.svg) | <img src="../../logosChannels/abudhabitv.png" alt="Abu Dhabi TV" width="112" height="59"> | Abu Dhabi Media Network; Public domain |
| [CBC](https://commons.wikimedia.org/wiki/File:CBC_TV.png) | <img src="../../logosChannels/cbc.png" alt="CBC" width="112" height="63"> | Capital Broadcasting Center; Public domain |
| [Al Saudiya](https://commons.wikimedia.org/wiki/File:Al-Saudia_TV_Logo_2018.png) | <img src="../../logosChannels/saudi1.png" alt="Al Saudiya" width="112" height="149"> | مريم محمد الغلبان; [CC BY 3.0](https://creativecommons.org/licenses/by/3.0) |
| [Watania 1](https://commons.wikimedia.org/wiki/File:Logo_T%C3%A9l%C3%A9vision_tunisienne_1,_2017.svg) | <img src="../../logosChannels/watania1.png" alt="Watania 1" width="112" height="155"> | Habib M’henni; Public domain |
| [MBC 1](https://commons.wikimedia.org/wiki/File:MBC_1_Logo.svg) | <img src="../../logosChannels/mbc.png" alt="MBC 1" width="112" height="42"> | MBC Group; Public domain |
| [DAZN 1](https://commons.wikimedia.org/wiki/File:DAZN_1_2024.svg) | <img src="../../logosChannels/commons-190633500.png" alt="DAZN 1" width="112" height="54"> | DAZN; Public domain |

## Sélection et upsert

[selections.json](selections.json) déclare les associations retenues entre un
média et une page Commons. `logo` est le chemin canonique du média ;
`legacy_paths` contient ses autres chemins existants dont l’identité a été vérifiée.
Une sélection met à jour ces chemins avec le même PNG. Les anciens octets sont
conservés dans [legacy-originals/](legacy-originals/), avec leur SHA-256 dans
[upserts.json](upserts.json).

L’identifiant `page_id` reste la clé d’upsert du fichier Commons, y compris lorsque
son titre ou sa révision change. L’identifiant `id` reste celui du média. Une
nouvelle révision actualise le PNG sans changer ces identifiants ni les chemins
historiques sélectionnés. Un import identique réutilise les fichiers validés et
ne produit pas de nouvelle sauvegarde.

Retirer une sélection arrête ses remplacements suivants sans supprimer son image
ni sa sauvegarde. Son ancienne provenance reste dans `retired_upserts` de
`upserts.json`.

Les noms historiques ambigus figurent dans `not_updated` et restent inchangés.
Les chaînes DAZN des différents marchés ne sont pas associées automatiquement
aux anciens noms DAZN. Un pays non établi reste `null` ; l’espace `xx` d’un
identifiant n’est pas une métadonnée de pays.

Les sources Commons sont communautaires. La sélection documente le fichier
retenu et ne certifie pas qu’il représente l’habillage actuellement diffusé à
l’antenne. Les anciens logos restent des variantes consultables.

## Actualisation

Depuis la racine du dépôt, avec les dépendances de `requirements.txt` :

```sh
python import_wikimedia.py
python update_index.py
python update_index.py --check
```

L’API est parcourue avec sa pagination. Les téléchargements HTTPS restent limités
aux domaines Wikimedia attendus ; les erreurs de limitation de débit font l’objet
de tentatives bornées. Une erreur de téléchargement, de décodage, de somme ou de
sélection empêche le remplacement des fichiers publiés.

`--metadata-file fichier.json` réutilise un instantané des réponses de l’API.
Les images inchangées sont reprises depuis le cache local vérifié ; toute image
manquante ou nouvelle nécessite encore le réseau. `--output-dir dossier`
importe une collection séparée : conserver ses sélections et son manifeste dans
ce même dossier pour retrouver ses associations lors des actualisations.

Une image importée qui disparaît ensuite des catégories reste conservée et
marquée `retained` dans l’inventaire. Aucun nettoyage automatique ne supprime les
versions précédentes des originaux. Une nouvelle version d’un média ne doit pas
être choisie uniquement parce que son fichier a une date d’envoi plus récente.
Les exclusions déjà inventoriées restent également archivées lorsqu’elles
disparaissent des catégories ; une exclusion inconnue de l’inventaire est rejetée.

## Couverture, exclusions et attribution

- [inventory.json](inventory.json) : fichiers découverts, catégories et exclusions.
- [exclusions.json](exclusions.json) : trois portraits et un logo de série animée,
  avec leur justification et leur page source.
- [manifest.json](manifest.json) : un enregistrement par fichier, avec artiste,
  crédit, licence déclarée, URL source, date de révision et empreintes.
- [Rapport Wikimedia](../../reports/wikimedia.json) : couverture contrôlée,
  fichiers disponibles et sélection retenue pour chaque média.

Les champs `artist`, `credit`, `license_short_name`, `license_url` et
`attribution_required` sont conservés tels qu’ils sont fournis par Commons ;
`extmetadata` conserve les informations détaillées. Une valeur absente reste
explicitement vide ou inconnue. Les crédits peuvent contenir le balisage HTML de
la page source : les traiter comme des données, sans exécuter ce balisage.

La page de description de chaque fichier reste la référence pour ses crédits et
sa licence. Lors d’une redistribution, joindre ces informations à l’image et
signaler les modifications : rendu SVG en PNG, réduction éventuelle et première
trame pour une animation. Les conditions d’un fichier ne remplacent pas celles
des autres fichiers ; les marques restent celles de leurs titulaires.
