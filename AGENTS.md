# Instructions pour les agents

## Périmètre et références

- Préférer une petite modification cohérente suivant les conventions existantes.
- Avant toute modification, lire [README.md](README.md) et
  `logo_manifest.json` ; consulter la provenance archivée si nécessaire.
- Examiner les scripts, leurs appelants et les tests existants avant de changer
  une règle. Préserver les modifications locales sans rapport avec la demande.

## Source de vérité et génération

- Le manifeste, les logos et les règles Python sont les entrées.
  Modifier ces entrées ou les règles, puis régénérer les sorties avec
  `python update_catalog.py`.
- Cette commande orchestre `update_index.py`, puis `build_client_catalog.py`.
  La génération et son contrôle restent hors réseau.
- Pour récupérer un fichier Commons ou Wikipédia en français, utiliser
  `fetch_wikimedia_logo.py` selon le README ; cet import réseau est distinct de
  la génération et ne qualifie pas automatiquement une chaîne.
- Ne pas éditer à la main `index_picons.json`, les fichiers `catalog*.json`,
  `logosClient/` ou `reports/`.
  Le manifeste de publication `catalog.release.v1.json` est également généré ;
  conserver son SHA-256 du catalogue client cohérent avec ses octets.
- Ne pas utiliser de LLM pour décider des rapprochements. Une ambiguïté doit
  conduire à une abstention ; conserver les preuves pour toute nouvelle association.
- Préserver les originaux, leurs empreintes, les sauvegardes historiques, les
  attributions, les licences, les identités stables et la provenance. Ne pas déduire
  un pays ou une identité du préfixe d'une station ou d'un simple nom de fichier.
  Les anciennes données et sauvegardes sont conservées dans l'archive de provenance.

## Vérifications

Utiliser Python 3.12 et les dépendances verrouillées de `requirements.txt`.
Commandes de référence, depuis la racine ; voir aussi `.github/workflows/catalog.yml` :

```sh
python update_catalog.py
python update_catalog.py --check
python -m unittest discover -s tests -v
python -m black --check --workers 1 update_catalog.py update_index.py build_client_catalog.py rename_hd_logos.py fetch_wikimedia_logo.py tests/
sha256sum -c provenance-archive.sha256
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
