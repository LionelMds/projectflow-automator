# Configuration administrateur Microsoft 365

## App registration Azure AD

Creer une app registration multi-tenant :

- Supported account types: comptes dans tout annuaire organisationnel et comptes Microsoft personnels.
- Redirect URI: `Public client/native` avec `http://localhost`.
- Public client flows: active.

Permissions deleguees Microsoft Graph :

- `User.Read`
- `Files.ReadWrite.All`
- `Mail.ReadWrite`
- `MailboxSettings.Read`
- `Tasks.ReadWrite`

Ces permissions deleguees ne necessitent normalement pas de consentement administrateur dans
une configuration Microsoft 365 standard. Chaque utilisateur consent une fois lors de la
connexion Microsoft dans ProjectFlow. Aucun utilisateur ne doit aller modifier Entra ID.

Ne pas ajouter `Group.Read.All` pour le MVP sans admin : cette permission demande souvent un
consentement administrateur et n'est pas necessaire au flux ProjectFlow actuel. Planner est
utilise via `Tasks.ReadWrite`.

Le Client ID est public et peut etre embarque dans l'application. Aucun secret client ne doit
etre ajoute a l'application desktop.

## Client ID embarque

Pour les postes utilisateurs, aucun parametrage manuel n'est attendu. Le Client ID public est
resolu dans cet ordre :

1. argument explicite interne aux tests ;
2. variable `PROJECTFLOW_CLIENT_ID` pour le developpement ;
3. fichier embarque `projectflow/resources/app_settings.json`.

Le workflow release genere `app_settings.json` depuis le secret GitHub `PROJECTFLOW_CLIENT_ID`
avant le build Windows/macOS. Ce secret n'est pas un client secret OAuth : il contient seulement
l'identifiant public de l'app registration.

## Mises a jour GitHub Releases

Le meme fichier embarque contient `github_owner` et `github_repo`, generes depuis le depot
GitHub Actions courant. Ces champs activent la verification de mise a jour au demarrage.

Publier un tag `v*.*.*` declenche `.github/workflows/release.yml`, qui attache les artefacts
Windows `.exe` et macOS `.dmg` a la GitHub Release. Les postes utilisateurs detectent ensuite
la derniere release via l'API GitHub publique.

## Consentement admin optionnel

Un administrateur Balz Metal peut pre-consentir les scopes avec l'URL `/adminconsent` de l'app
registration. Cela evite aux utilisateurs de devoir approuver les permissions individuellement,
mais ce n'est pas obligatoire si le tenant autorise le consentement utilisateur pour les
permissions deleguees ci-dessus.

Si le tenant bloque tout consentement utilisateur, Microsoft impose une validation admin. Dans
ce cas, ProjectFlow ne peut pas contourner cette politique de securite ; il faut soit faire
valider l'app une fois par un admin, soit utiliser uniquement le mode demo/local.

## Distribution

- Windows: artefact `.exe` produit par PyInstaller.
- macOS: artefact `.dmg` signe Developer ID et notarise Apple quand les secrets CI sont fournis.

Secrets GitHub attendus pour macOS :

- `PROJECTFLOW_CLIENT_ID`
- `APPLE_ID`
- `APPLE_TEAM_ID`
- `APPLE_APP_PWD`
- `APPLE_DEVELOPER_ID`
