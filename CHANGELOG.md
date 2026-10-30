---
title: Changelog
description: Changements du catalogue de logos TV et radio et de ses outils de publication.
---

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
