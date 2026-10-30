---
title: Changelog
description: Changements du catalogue de logos TV et radio et de ses outils de publication.
---

## 2026-10-30 : catalogue client et PNG pour les applications

### Fonctionnel

- Catalogue client séparé pour les 143 médias qualifiés, avec variantes PNG de
  128 et 256 pixels maximum et crédits en texte avec liens vers les sources.
- Images téléchargeables à la demande depuis une révision Git immuable ;
  empreinte et poids disponibles pour valider le téléchargement et son cache.

### Technique

- Génération locale sans réseau, sans agrandissement, avec proportions et
  transparence conservées. Les 276 PNG distincts sont adressés par leur SHA-256 ;
  les petites sources et les images identiques partagent leurs fichiers.
- Index historique, catalogue v1 et images sources conservés. Les sorties client
  disposent de leur propre commande de génération et de contrôle octet par octet.
- Artefact CI enrichi avec le manifeste client, les crédits et les PNG.

### Tests validés

- `python -m unittest discover -s tests -v` : 61 tests réussis, dont sept nouveaux
  contrôles des variantes, du cache par contenu, des crédits, des fichiers
  manquants ou corrompus et de la génération sans modification des sources.
- Black sur les nouveaux fichiers Python et `git diff --check` réussis.

## 2026-10-30 : import Wikimedia et upsert des logos

### Fonctionnel

- Ajout de 241 fichiers de logos provenant des six catégories Commons demandées
  et de leurs sous-catégories : Émirats arabes unis, Égypte, Arabie saoudite,
  Tunisie et DAZN. Les originaux, dont 60 SVG, et leurs crédits sont conservés.
- Qualification de 62 médias supplémentaires ; mise à jour de 73 chemins
  historiques vérifiés avec sauvegarde des images précédentes. Quatorze
  associations ambiguës restent inchangées.
- README principal complété et guide Wikimedia avec aperçus, sources, licences
  et procédure d’actualisation.

### Technique

- Import paginé et dédupliqué par identifiant de page Commons, contrôle de la
  révision SHA-1 et des empreintes locales, SVG conservés et rendus PNG fournis
  par Wikimedia. Les PNG ont un côté maximal de 1 024 pixels.
- Les quatre fichiers hors sujet sont exclus explicitement de l’inventaire.
  Les images retirées des catégories et les exclusions déjà connues restent
  archivées ; retirer une sélection conserve sa provenance et ses fichiers.
- Validation des sélections avant publication, contrôle des anciennes images
  sauvegardées et ajout du rapport Wikimedia à la génération et aux artefacts CI.

### Tests validés

- `python -m unittest discover -s tests -v` : 54 tests réussis, couvrant les
  mises à jour, les erreurs avant publication, la rétention et le retrait des
  sélections, en plus des contrôles d’index et des radios.
- Import complet de 241 fichiers avec empreintes source conformes à Commons ;
  actualisation depuis le cache local sans réseau et sans différence d’octets.
- Black sur les fichiers Python modifiés et contrôle des liens locaux des README.

## 2026-10-30 : catalogue versionné et radios

### Fonctionnel

- Ajout des 80 stations de la grille Tunisie Radio, y compris les webradios, aux
  deux index. Les originaux et leur provenance sont conservés.
- Publication d’un catalogue v1 avec identifiants stables, métadonnées vérifiées
  et pays inconnu explicite. France 2 constitue le premier média TV qualifié.

### Technique

- Conservation du contrat historique `{"name": [...]}` avec chemins exacts,
  filtrage des images et génération déterministe. Les documents, sauvegardes et
  autres fichiers parasites disparaissent de l’index.
- Préférences explicites pour les 44 collisions historiques ; toutes les images
  restent disponibles. Rapports de décodage, formats, dimensions, transparence,
  empreintes et images restant à identifier.
- Import séparé de la validation, PNG sans déformation ni agrandissement et
  vérification locale des originaux par empreinte SHA-256.
- Remplacement de Travis et de la publication Gist par GitHub Actions, avec
  dépendances verrouillées, permissions en lecture seule et artefacts de build.
- Les deux scripts `trim_picons.sh` deviennent des points d’entrée obsolètes
  vers une génération dans `dist/`. Ils ne modifient plus les images et
  n’effectuent plus d’opérations Git.

### Tests validés

- Tests de régression du contrat historique, chemins, collisions, métadonnées,
  images invalides et génération sans modification des sources.
- Tests d’import : chargement différé, déduplication, webradios, erreurs HTTP,
  transparence, proportions, identifiants persistants et absence de publication
  après un échec de téléchargement ou de décodage.
- Vérification des 9 644 images indexées et des 80 originaux importés.
- Deux conversions successives des radios produisent les mêmes fichiers.
