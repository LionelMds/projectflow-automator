# Déployer ProjectFlow Mobile sur iPhone (interne, sans App Store)

ProjectFlow Mobile est une application web installable (PWA). Sur iPhone, elle s'installe
depuis Safari sur l'écran d'accueil, s'ouvre en plein écran comme une app, garde sa connexion
Microsoft et fonctionne sans Mac, sans compte Apple Developer et sans passer par l'App Store.

Adresse de production : **https://lionelmds.github.io/projectflow-automator/**

## 1. Activer GitHub Pages (une fois)

1. GitHub → dépôt `projectflow-automator` → **Settings → Pages**.
2. *Build and deployment* → Source : **Deploy from a branch**.
3. Branche : **gh-pages**, dossier **/ (root)** → **Save**.

Après une minute, l'adresse ci-dessus répond. La branche `gh-pages` est mise à jour
automatiquement par le workflow `Mobile` à chaque modification de `mobile/` sur `main`, ou à la
demande (Actions → Mobile → *Run workflow*).

> Le dépôt étant public, la page l'est aussi. Elle ne contient aucune donnée : tout passe par la
> connexion Microsoft de l'utilisateur et les droits qu'il a déjà sur OneDrive, Planner et Outlook.

## 2. Autoriser l'adresse dans Microsoft Entra ID (une fois, administrateur)

1. [Entra ID](https://entra.microsoft.com) → **Applications → Inscriptions d'applications** →
   *ProjectFlow Automator* (Client ID `ced436ff-2be9-4792-8551-02e12351c6c9`).
2. **Authentification → Ajouter une plateforme → Application monopage (SPA)** :
   `https://lionelmds.github.io/projectflow-automator/` (barre oblique finale comprise).
3. **Autorisations d'API → Microsoft Graph → Déléguées** : ajouter `Mail.Send` et
   `Mail.ReadWrite` (les autres sont déjà utilisées par ProjectFlow bureau :
   `User.Read`, `Files.ReadWrite.All`, `Tasks.ReadWrite`, `User.ReadBasic.All`,
   `GroupMember.Read.All`).
4. **Accorder un consentement d'administrateur pour Balz Métal SA**.

Sans l'étape 2, la connexion échoue avec l'erreur `AADSTS50011` (URI de redirection).

## 3. Installer sur chaque iPhone

### À la main (quelques postes)

1. Ouvrir **Safari** sur l'iPhone, aller sur l'adresse de production.
2. Toucher **Partager** (carré avec flèche) → **Sur l'écran d'accueil** → **Ajouter**.
3. Ouvrir **ProjectFlow** depuis l'icône, **Se connecter avec Microsoft**.
4. Première fois : **Paramètres** → coller les liens de partage de la racine projets, du dossier
   de référence et du répertoire chantier, les initiales, Planner et Outlook.

L'app affiche elle-même ces instructions quand elle est ouverte dans Safari sans être installée.
iOS 16.4 ou plus récent recommandé.

### En masse avec Intune (appareils gérés)

Microsoft Intune → **Applications → iOS/iPadOS → Ajouter → Lien web** (ou
*Appareils → Configuration → Web clip*) :

- Nom : `ProjectFlow` · URL : `https://lionelmds.github.io/projectflow-automator/`
- **Full screen / Plein écran** : Oui · Icône : `mobile/public/icons/icon-512.png`
- Affecter au groupe des collaborateurs concernés.

L'icône apparaît alors sur les iPhones sans action de l'utilisateur.

## 4. Mises à jour

Rien à faire sur les téléphones : à chaque ouverture, l'app vérifie la version publiée ;
*Paramètres → Rechercher une mise à jour* force la vérification (relancer l'app ensuite).

## Bon à savoir sur iPhone

- L'app installée a son propre stockage : on se connecte une fois dans l'app, même si on est
  déjà connecté dans Safari. Supprimer l'icône efface la connexion et les paramètres.
- La connexion Microsoft s'ouvre dans une fenêtre intégrée puis revient dans l'app.
- Pour héberger ailleurs (SharePoint, Azure Static Web Apps, serveur interne), publier le zip
  de la release `mobile-v…` sur une adresse HTTPS et déclarer cette adresse à l'étape 2.

## Et une vraie app native ?

Si un jour une app native est nécessaire (notifications push fiables, accès fichiers étendu),
le code peut être emballé avec Capacitor. Mais la diffuser hors App Store exige :

- un **Mac avec Xcode** et un compte **Apple Developer** (99 $/an) ;
- soit la distribution **Ad Hoc** : 100 iPhones maximum par an, chaque appareil enregistré
  par son identifiant, profil à renouveler chaque année ;
- soit le programme **Apple Developer Enterprise** (299 $/an), réservé aux organisations de
  plus de 100 employés et accordé sur dossier par Apple.

Pour l'usage interne de Balz Métal SA, la PWA offre les mêmes écrans sans ces contraintes.
