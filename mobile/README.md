# ProjectFlow Mobile

Compagnon mobile de **ProjectFlow Automator** pour Balz Métal SA, réalisé d'après la maquette
Claude Design « ProjectFlow Mobile » (système graphique *Industry*).

C'est une application web installable (PWA) : on l'ouvre dans Safari ou Chrome, puis
« Ajouter à l'écran d'accueil ». Elle travaille directement dans Microsoft 365 via Microsoft Graph :

| Onglet | Ce qui est fait réellement |
| --- | --- |
| **Créer** | Dossier `Racine/AAAA/AAAA-NNNN` créé dans OneDrive/SharePoint, dossier de référence copié sans écrasement, fiche Excel renommée et remplie (C3, D3–D6, C6, C9, date en B9), ligne A:E du répertoire chantier écrite (sous-projet inséré après le groupe du parent), dossiers Outlook, tâche Planner, e-mail de notification, modèles CAO. |
| **Sortie** | Inventaire du dossier projet (fiches, PDF de prise de cote, `Photos`, `Plans/Plan d'exécution`), puis copie horodatée sous `Sorties dossier` avec la date d'atelier en E2 de la fiche copiée. Les sources ne sont jamais modifiées. |
| **Répertoire** | Lecture de l'onglet de l'année, recherche, modification de B:E avec contrôle de modification concurrente, chargement, sous-projet, duplication, mise à jour et suppression (corbeille OneDrive, tâches Planner, dossiers Outlook, numéro libéré). |

La logique métier est reprise de l'application de bureau (`src/projectflow/core`, `graph`),
avec les mêmes règles et messages.

### Notification des personnes assignées

Sous-option de **Créer une tâche Planner**. À la création, un e-mail « Nouvelle assignation »
part de la boîte Outlook de l'utilisateur connecté (`/me/sendMail`) vers les membres cochés,
avec les informations générales du projet, un bouton vers le dossier OneDrive et un lien
vers la tâche Planner. Lors d'une mise à jour, seuls les membres **nouvellement** assignés à la
tâche reçoivent l'e-mail. « Aperçu du message » montre le contenu avant l'envoi.

## Démarrer

```bash
cd mobile
npm install
npm run dev        # http://localhost:5173
npm test           # tests de la logique métier sur un OneDrive en mémoire
npm run build      # dist/ à publier
```

**Mode démonstration** : bouton « Essayer la démonstration » (ou `?demo` dans l'adresse).
Toutes les fonctions tournent sur des données fictives en mémoire, rien n'est écrit dans
Microsoft 365.

## Configuration Microsoft (une fois, par un administrateur)

1. **App Registration** (Entra ID). On peut réutiliser celle de ProjectFlow bureau
   (`ced436ff-2be9-4792-8551-02e12351c6c9`) ou en créer une dédiée.
2. **Authentification → Ajouter une plateforme → Application monopage (SPA)** avec l'adresse
   exacte où l'app est publiée, par exemple `https://projectflow.balzmetal.ch/`, plus
   `http://localhost:5173/` pour le développement.
3. **Autorisations déléguées** Microsoft Graph :
   - `User.Read`, `Files.ReadWrite.All` : OneDrive, fiches et répertoire ;
   - `Tasks.ReadWrite`, `User.ReadBasic.All`, `GroupMember.Read.All` : Planner ;
   - `Mail.Send` : notification des personnes assignées ;
   - `Mail.ReadWrite` : dossiers Outlook du projet.
4. Accorder le **consentement administrateur** si le tenant bloque le consentement utilisateur.

Variables de compilation (fichier `.env.local`, voir `.env.example`) :

```
VITE_MS_CLIENT_ID=<client id>
VITE_MS_TENANT=<id du tenant ou "organizations">
```

## Premier lancement sur le téléphone

Paramètres (icône en haut à droite) : pour chaque chemin, collez le **lien de partage**
OneDrive/SharePoint (ou un chemin depuis la racine de « Mon OneDrive ») :

- Racine projets, Dossier de référence, Répertoire chantier (le classeur `.xlsx`) ;
- Initiales utilisateur (écrites en C9) ;
- Outlook (arborescence, par défaut `[YYYY]/[PROJECT_FOLDER]`) ;
- Planner (plan, colonne par défaut, échéance) ;
- Modèles CAO SolidWorks / AutoCAD et sous-dossier de destination.

Les paramètres sont conservés sur le téléphone.

## Publication

Pousser un tag `mobile-vX.Y.Z` : le workflow `.github/workflows/mobile.yml` lance les tests,
compile et joint `projectflow-mobile-mobile-vX.Y.Z.zip` à la release GitHub. Les variables de
dépôt `PROJECTFLOW_MOBILE_CLIENT_ID` et `PROJECTFLOW_MOBILE_TENANT` remplacent les valeurs
par défaut si elles sont définies. (Les tags `vX.Y.Z` restent réservés aux installateurs du bureau.)

`npm run build` produit un site statique (`dist/`) avec chemins relatifs : il peut être publié
sur Azure Static Web Apps, SharePoint, GitHub Pages ou tout serveur HTTPS. L'adresse doit être
déclarée comme URI de redirection SPA (étape 2). Le service worker garde l'interface disponible
hors ligne ; les données passent toujours par le réseau.

## Différences avec l'application de bureau

- **SolidWorks** : les modèles `20XX-XXXX-*` sont copiés et renommés, mais la liaison de
  l'assemblage aux pièces et les propriétés personnalisées demandent *SolidWorks Document
  Manager* (COM, Windows). L'étape s'affiche en avertissement : lancer ensuite
  « Mettre à jour » depuis ProjectFlow sur le poste. AutoCAD est complet.
- **Outlook** : les dossiers sont créés dans la boîte du compte Microsoft (Outlook sur le web /
  Exchange), à partir de la racine de la boîte. L'application de bureau utilise le profil Outlook
  local et cherche aussi les dossiers déjà archivés.
- **Impression** : la fiche est convertie en PDF par OneDrive puis ouverte pour l'impression
  du téléphone.
- **Fiche Excel** : l'app écrit dans la première feuille visible du classeur ; le bureau écrit
  dans la feuille active.

## Structure

```
src/core/     logique métier (numéros, fiche, répertoire, projet, sortie, Planner, Outlook, e-mail)
src/graph/    Microsoft Graph : MSAL, OneDrive, classeurs Excel, Planner, Outlook
src/demo/     passerelles en mémoire et données de démonstration (reprises de la maquette)
src/ui/       écrans, feuilles et état de l'application
src/styles/   industry.css (système graphique) et app.css (mise en page mobile)
```
