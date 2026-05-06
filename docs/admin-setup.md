# Deploiement interne

Aucun portail admin et aucune autorisation cloud ne sont requis pour utiliser ProjectFlow.

## A preparer

- Un dossier racine projets accessible sur le poste.
- Un dossier de reference contenant les fichiers modele a copier.
- Un fichier Excel de repertoire chantier accessible localement, idealement dans un dossier
  synchronise sur les postes.
- Outlook classique installe et configure uniquement si la creation de dossiers Outlook doit
  etre activee.

## Distribution

- Windows : distribuer `ProjectFlowAutomator.exe`.
- macOS : distribuer le `.dmg` produit par la release signee.

Au premier lancement, chaque utilisateur choisit ses chemins locaux dans l'assistant. Cette
configuration est sauvegardee dans le dossier utilisateur ProjectFlow.

## Outlook local

La creation Outlook est optionnelle et desactivee par defaut. L'utilisateur peut l'activer dans
`Parametres` -> `Outlook`, lancer `Detecter`, choisir le compte ou fichier de donnees, puis
tester l'acces.

Le nouvel Outlook Windows sans automation locale n'est pas supporte pour cette fonction.
