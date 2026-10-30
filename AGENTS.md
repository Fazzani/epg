# Instructions pour les agents

## Périmètre et références

- Préférer une petite modification cohérente suivant les conventions existantes.
- Avant toute modification, lire [README.md](README.md), le
  [guide Wikimedia](sources/wikimedia/README.md) et les manifestes concernés :
  `logo_manifest.json`, `sources/radios/manifest.json` et
  `sources/wikimedia/manifest.json`.
- Examiner les scripts, leurs appelants et les tests existants avant de changer
  une règle. Préserver les modifications locales sans rapport avec la demande.

## Source de vérité et génération

- Les manifestes sources, les originaux et les règles Python sont les entrées.
  Modifier ces entrées ou les règles, puis régénérer les sorties avec
  `python update_catalog.py`.
- Cette commande orchestre le rapprochement radio, puis `update_index.py`, puis
  `build_client_catalog.py`. La génération et son contrôle restent hors réseau.
- Ne pas éditer à la main `index_picons.json`, les fichiers `catalog*.json`,
  `credits.client.v1.json`, `logosClient/`, `reports/`,
  `sources/wikimedia/selections.json` ou `sources/wikimedia/upserts.json`.
  Le manifeste de publication `catalog.release.v1.json` est également généré ;
  conserver ses SHA-256 du catalogue client et des crédits cohérents avec leurs octets.
- Ne pas utiliser de LLM pour décider des rapprochements ni ajouter une sélection
  manuelle pour contourner les seuils, marges, exclusions ou contrôles du pipeline.
  Une ambiguïté doit conduire à une abstention. Corriger la source ou la règle
  déterministe, avec un cas de régression, puis relancer la génération.
- Les imports réseau (`import_radios.py`, `import_wikimedia.py`) et les remplacements
  Wikimedia sont des opérations distinctes ; consulter les guides avant de les lancer.
- Préserver les originaux, leurs empreintes, les sauvegardes historiques, les
  attributions, les licences, les identités stables et la provenance. Ne pas déduire
  un pays ou une identité du préfixe d'une station ou d'un simple nom de fichier.
  Les catégories pays Commons explicites sont traitées selon les règles du script,
  pas comme une preuve géographique pour toutes les stations d'une collection.

## Vérifications

Utiliser Python 3.12 et les dépendances verrouillées de `requirements.txt`.
Commandes de référence, depuis la racine ; voir aussi `.github/workflows/catalog.yml` :

```sh
python update_catalog.py
python update_catalog.py --check
python -m unittest discover -s tests -v
python -m black --check --workers 1 update_catalog.py update_index.py build_client_catalog.py import_radios.py import_wikimedia.py upsert_wikimedia.py match_radio_logos.py tests/
git diff --check
```

- Toute modification de logique doit conserver un contrôle exécutable pertinent.
- Ne pas réparer les sorties à la main pour faire passer `--check`. Inspecter le
  diff généré et s'assurer qu'une nouvelle génération ne change plus les sorties.
- Maintenir la CI alignée sur le point d'entrée complet : formatage, tests,
  `--check`, régénération sans dérive et construction des artefacts.
- Mettre à jour le README ou le guide concerné si le contrat, les commandes ou le
  comportement changent. Rapporter séparément les vérifications exécutées et leurs limites.

## Limites d'action

- Ne pas corriger d'autres fichiers ou problèmes sans rapport avec la demande.
- Aucun commit, push, déploiement ou publication externe sans instruction explicite.
- Ne jamais affaiblir la validation, exposer de secret ou supprimer une provenance
  pour débloquer un contrôle.
