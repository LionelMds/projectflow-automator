# Deploiement interne

ProjectFlow reste local pour les dossiers projet, les fiches et Outlook. Le repertoire chantier
OneDrive et l'integration Planner utilisent une connexion Microsoft afin d'ecrire directement dans
les services cloud concernes, sans passer par une copie locale synchronisee.

## A preparer

- Un dossier racine projets accessible sur le poste.
- Un dossier de reference contenant les fichiers modele a copier.
- Un fichier Excel de repertoire chantier partage via OneDrive ou SharePoint et accessible aux
  utilisateurs concernes.
- Outlook classique installe et configure uniquement si la creation de dossiers Outlook doit
  etre activee.
- Un plan Microsoft Planner accessible aux utilisateurs si la creation de taches doit etre activee.
- Une App Registration Microsoft publique avec le redirect URI desktop `http://localhost`.
- Le Client ID public est embarque dans ProjectFlow. Le secret GitHub
  `PROJECTFLOW_MICROSOFT_CLIENT_ID` reste disponible uniquement pour remplacer cette valeur lors
  d'une release.

## Permissions Microsoft

L'application desktop n'utilise pas de secret client. Le Client ID est embarque dans ProjectFlow.

Permissions deleguees minimales :

- `Files.ReadWrite.All`
- `Tasks.ReadWrite`
- `User.Read`

ProjectFlow ne demande pas explicitement les scopes reserves (`offline_access`, `profile`,
`openid`) : MSAL/Microsoft les gere automatiquement quand ils sont necessaires.

Si le tenant bloque le consentement utilisateur, un administrateur Microsoft 365 doit accorder le
consentement une seule fois pour l'application. Les utilisateurs n'ont ensuite aucune action Azure
a faire.

## Distribution

- Windows : distribuer `ProjectFlowAutomatorSetup.exe`. L'installateur est par utilisateur et
  ne demande pas de droits administrateur.
- macOS : distribuer le `.dmg` produit par la release signee.

Les fichiers `.sha256` publies avec la release servent a la verification automatique des mises
a jour. Ne les supprimez pas de la GitHub Release.

Au premier lancement, chaque utilisateur choisit ses chemins locaux dans l'assistant. Si le
repertoire chantier pointe vers OneDrive, ProjectFlow ouvrira le navigateur Microsoft a la
premiere utilisation du repertoire, puis reutilisera le cache token local.

Si Planner est active, le meme compte Microsoft est utilise pour lister les plans accessibles,
lister les buckets du plan choisi, creer ou mettre a jour la tache projet, et l'assigner a
l'utilisateur connecte.

## Mises a jour

Les mises a jour in-app utilisent les artefacts GitHub Releases :

- Windows telecharge `ProjectFlowAutomatorSetup.exe`, verifie son SHA256, puis lance
  l'installateur.
- macOS telecharge le DMG, verifie son SHA256, puis ouvre l'image disque pour installation.

Pour publier une mise a jour, creer un tag `vX.Y.Z`. Le workflow produit les installateurs,
leurs checksums et la release GitHub.

## Outlook local

La creation Outlook est optionnelle et desactivee par defaut. L'utilisateur peut l'activer dans
`Parametres` -> `Outlook`, lancer `Detecter`, choisir le compte ou fichier de donnees, puis
tester l'acces.

Le nouvel Outlook Windows sans automation locale n'est pas supporte pour cette fonction.

## Microsoft Planner

La creation Planner est optionnelle et desactivee par defaut. L'utilisateur peut l'activer dans
`Parametres` -> `Microsoft Planner`, lancer `Detecter`, choisir le plan, charger les colonnes,
puis tester l'acces.

ProjectFlow cree une seule tache par numero de projet principal. Si une tache dont le titre
commence deja par le numero existe dans le plan, ProjectFlow la met a jour au lieu d'en creer une
nouvelle : titre, colonne cible et assignation a l'utilisateur connecte.
