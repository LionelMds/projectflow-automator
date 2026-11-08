# ProjectFlow mobile (iOS et Android)

Application Expo (React Native, TypeScript) qui reprend les modules de ProjectFlow sur le
téléphone, dans le même ordre et avec les mêmes termes que le poste : **Création projet**,
**Répertoire chantier**, **Planning**. La **Sortie dossier** reste sur le poste. Style : thème
Industry, comme le poste (`src/projectflow/ui/theme.py`).

Elle lit et écrit les **mêmes données** que le poste, par Microsoft Graph, avec la même
application Microsoft Entra et le même tenant (`src/projectflow/auth/msal_client.py`). Les règles
du poste sont reprises en TypeScript avec leurs propres tests unitaires (portés depuis `tests/unit/`).

| Module | Source | Code du poste repris |
|---|---|---|
| Création projet | classeur du répertoire, dossier « Racine projets », Microsoft Planner | `core/project_service.py`, `core/fiche_service.py`, `core/numero.py`, `graph/planner.py` |
| Répertoire chantier | classeur partagé, un onglet par année, colonnes A:E | `core/repertoire_service.py`, `graph/excel.py` |
| Planning | dossier OneDrive « ProjectFlow Planning », un fichier `<type>__<id>.json` par élément | `graph/drive_planning_store.py`, `core/planning/` (calendrier, saisie rapide, commandes, fusion des conflits) |

## Ce que fait l'app

- **Création projet** : Année / Numéro / Sous-projet, « Suivant disponible », Désignation,
  Client (Société, Contact, Localisation, Géré par · C6, Initiales · C9), Microsoft Planner
  (bucket, personnes, échéance). « Charger », « Créer », « Mettre à jour », « Réinitialiser »
  (vide tout sauf l'année), avec le journal des étapes comme sur le poste. Créer remplit la ligne
  du répertoire (refusé si B:E est déjà rempli,
  sauf « Mettre à jour »), copie le dossier de référence en `<racine>/<année>/<AAAA-NNNN>` s'il
  n'existe pas, renomme et remplit la fiche (C3, D3–D6, C6, C9, B9), crée ou met à jour la tâche
  Planner. Un sous-projet reprend la fiche du parent et s'insère sous son groupe dans le répertoire.
  Les fichiers CAO (SolidWorks / AutoCAD), le dossier Outlook et l'impression restent sur le poste.
- **Répertoire chantier** : année, recherche, prochaine ligne disponible, modification d'une
  ligne (A:E seulement, relue avant l'écriture ; refusée si elle a changé entre-temps). Les
  colonnes comptables F:L ne sont jamais lues ni écrites. **Appui long** sur une ligne : Charger
  dans Création, Ouvrir le dossier, Ouvrir la fiche (app Excel, sinon Excel pour le web),
  Nouvelle tâche, Voir dans le planning, Modifier la ligne, Copier le numéro.
- **Explorer** : la racine projets (« Clients ») par année, les quatre dernières et « Autre »
  pour en taper une ; les dossiers de l'année avec désignation et client du répertoire,
  recherche. Toucher ouvre le dossier ; appui long : Ouvrir le dossier, Ouvrir la fiche, Charger
  dans Création, Nouvelle tâche, Voir dans le planning, Voir dans Répertoire, Copier le numéro.
- **Dossier** (« Ouvrir le dossier », partout) : un explorateur dans l'app, comme l'app Fichiers,
  ouvert sur le dossier du projet : sous-dossiers, fichiers avec taille et date, vignettes des
  photos. Un fichier Excel, Word ou PowerPoint s'ouvre dans son app (modifié directement sur
  OneDrive) ; les autres (PDF, photos, plans…) dans l'aperçu du téléphone (Quick Look sur iOS,
  l'app choisie sur Android). Appui long : Partager ou enregistrer dans Fichiers, Ouvrir sur le
  web. iOS et Android ne permettent pas à une app d'ouvrir un dossier OneDrive précis dans leur
  app Fichiers : d'où l'explorateur.
- **Planning** :
  - vues **Jour**, **Semaine** et **Mois** du tableau magnétique (une ligne par personne, par
    équipe ; demi-journées ; samedi et dimanche jamais affichés), jours fériés GE / VD et
    fermetures de l'entreprise grisés avec leur nom, vendredi après-midi non travaillé ;
  - **saisie rapide** avec la grammaire du poste (`2026-121 montage marquise jeu-ven LBI YCH`,
    `/vac LBI lun 5j`…), aperçu en direct, avertissement si une personne est absente ; toucher une
    case vide ouvre la saisie sur cette ligne et cette demi-journée. Raccourcis à toucher (personnes,
    jours, durées, provisoire, urgent) et dossiers du répertoire proposés pendant la frappe ;
  - **appui long** (menus du poste) : sur une tâche ou une carte, statut, Modifier, Renvoyer à
    planifier / Placer, Ouvrir le dossier, Ouvrir la fiche, Voir dans Répertoire, Charger dans
    Création, Supprimer ; sur une case vide, Nouvelle tâche ici et l'absence du jour ; sur une
    absence, la voir ou la supprimer. « Voir dans le planning » ouvre la semaine de la prochaine
    tâche du dossier et met ses tâches en évidence ;
  - **désignation des tâches** comme sur le poste : une tâche sans titre saisi prend la désignation
    de son dossier (sinon le nom du type), et copie client, contact et désignation du répertoire ;
    le répertoire de l'année du dossier est lu au besoin. Un numéro court (`4998`) se complète en
    `2026-4998` quand il est unique, et changer le dossier d'une tâche recopie ses données. Une
    tâche sans copie affiche celles du répertoire ;
  - **fiche d'une tâche** : titre, dossier et statut en tête avec les accès au dossier, puis Quand
    (jour, demi-journée, durée), Qui (personnes, choix par équipe ; personnes requises), Détails
    (type, dossier, notes, urgent). Chaque modification est écrite aussitôt avec « Annuler » ; les
    textes à la sortie du champ. « Renvoyer à planifier », « Placer sur le planning » pour une
    carte, suppression ; absences supprimables ;
  - « **Annuler** » après chaque modification ;
  - lecture **incrémentale** (delta) toutes les 30 s pendant que le planning est affiché ;
  - écriture conditionnelle comme sur le poste : si un autre poste a modifié l'élément entre-temps,
    les champs touchés ici sont réappliqués sur sa version ; un champ modifié des deux côtés garde
    la valeur de l'autre poste et un message le dit.
  - Chaque modification est signée des **initiales** (Paramètres) et du poste « iPhone » /
    « Android » : sans initiales, le planning reste en lecture, comme sur le poste.
- **Mode démo** (Paramètres) : données d'exemple en mémoire, rien n'est lu ni écrit chez
  Microsoft. Tout peut s'essayer sans compte Microsoft.

Restent sur le poste : la **Sortie dossier**, gestion des équipes, des personnes et des réglages partagés du planning,
ordre manuel des cartes « À planifier », duplication et détachement d'une tâche, travail de nuit
saisi autrement que par la saisie rapide, export / impression du planning, calendrier Outlook.

## Connexion Microsoft

La connexion reprend celle du poste : même application Entra (`ced436ff-…`), même tenant et même
adresse de retour `http://localhost` (`auth/browser_sign_in.py`). **Rien n'est à ajouter dans
Entra.** La page Microsoft s'ouvre dans l'app (Paramètres → « Se connecter à Microsoft ») ; sa
réponse à `http://localhost` est interceptée par l'app avant chargement
(`src/components/MicrosoftSignIn.tsx`), puis le code est échangé contre les jetons avec PKCE,
comme MSAL sur le poste. Dans le navigateur (`npm run web`), seule la démo est disponible.

Autorisations demandées : celles du poste (`Files.ReadWrite.All`, `Tasks.ReadWrite`,
`User.ReadBasic.All`, `GroupMember.Read.All`).

Se connecter avec le **compte Microsoft partagé** du répertoire chantier : le planning est dans
son OneDrive. Dans Paramètres, coller les mêmes liens que sur le poste : répertoire, racine
projets, dossier de référence, plan Planner.

## Lancer

```bash
npm install
npm run web        # navigateur, pour essayer l'interface (mode démo)
npm start          # serveur de développement pour un téléphone
```

Sur téléphone, **Expo Go** suffit pour tout essayer, connexion Microsoft comprise : scanner le QR
code du serveur, avec le même compte Expo dans Expo Go et sur le PC (exigé pour un iPhone). Pour
installer l'app elle-même, EAS la construit dans le cloud (compte Expo ; pour un iPhone, compte
Apple Developer). Le projet Expo est `@lionel.mds/projectflow` ; les profils sont dans `eas.json` :

```bash
npx eas-cli@latest build --profile development --platform android
npx eas-cli@latest build --profile development --platform ios
```

Pour distribuer l'app à l'équipe sans les outils de développement : profil `preview`
(distribution interne), puis `production` pour les stores.

## Qualité

```bash
npm run typecheck
npm run lint
npm test
npx expo-doctor
```

Les tests (`src/lib/__tests__`) reprennent ceux du poste : calendrier et jours fériés, travail de
nuit, saisie rapide (toute la grammaire), commandes du planning, rangs « À planifier », fusion
des conflits, numéros de projet, fiche, Planner, répertoire. S'y ajoutent ceux propres au
mobile : lecture delta, écriture avec conflit, vues Jour / Semaine / Mois, connexion Microsoft,
chemins OneDrive (raccourcis vers SharePoint).

## Organisation

- `src/app/` : écrans (Expo Router). `(tabs)/` : les trois modules et l'Explorer ; écrans modaux : `ligne`
  (répertoire), `tache`, `absence`, `saisie` (planning), `parametres`.
- `src/lib/` : logique sans interface — `repertoire/`, `planning/`, `projects/` (création),
  Graph, connexion, démo.
- `src/components/`, `src/theme/` : composants et jetons du thème Industry.

Identifiants d'app : `ch.balzmetal.projectflow` (iOS et Android, dans `app.json`), à changer
avant la première publication si un autre identifiant est voulu.
