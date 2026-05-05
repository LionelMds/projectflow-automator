# ProjectFlow Automator

Application desktop cross-platform pour automatiser la creation de projets Balz Metal Sa.

## Developpement

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev,packaging]"
```

Qualite locale :

```powershell
py -m ruff check . --no-cache
py -m mypy
$env:QT_QPA_PLATFORM='offscreen'; py -m pytest --cov=projectflow --cov-report=term-missing
```

## Lancement

```powershell
$env:PROJECTFLOW_CLIENT_ID='<client-id-azure-ad>'
py -m projectflow
```

La premiere ouverture affiche l'onboarding : connexion Microsoft, configuration des chemins, puis selection Planner.

Smoke test non bloquant :

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PROJECTFLOW_CLIENT_ID='<client-id-azure-ad>'
$env:PROJECTFLOW_SMOKE_EXIT_MS='1000'
py -m projectflow
```

Mode demo local sans Microsoft Graph :

PowerShell :

```powershell
$env:PROJECTFLOW_DEMO_MODE='1'
py -m projectflow
```

Invite de commandes Windows (`cmd.exe`) :

```bat
set PROJECTFLOW_DEMO_MODE=1
py -m projectflow
```

Ou, plus simple dans les deux shells :

```powershell
py -m projectflow --demo
```

Ce mode cree un environnement local dans le dossier de donnees utilisateur ProjectFlow :

- `Clients/`
- `Modeles/10-Racine/`
- `Repertoire chantier demo.xlsx`

Il permet de tester `Suivant disponible`, `Creer`, `Charger`, `Ouvrir fiche` et `Mettre a jour` sans compte Microsoft.

## Variables utiles

- `PROJECTFLOW_CLIENT_ID` : client id Azure AD public embarquable.
- `PROJECTFLOW_APP_SETTINGS` : chemin vers un `app_settings.json` local pour tester un Client ID embarque sans modifier le code source.
- `PROJECTFLOW_SMOKE_EXIT_MS` : ferme automatiquement l'app apres le delai indique, pour tests smoke.
- `PROJECTFLOW_DEMO_MODE` : lance l'app avec un repertoire Excel local et sans appels Graph.

## Configuration applicative embarquee

Pour une distribution reelle, le Client ID Microsoft peut etre integre dans :

```text
src/projectflow/resources/app_settings.json
```

Format :

```json
{
  "microsoft_client_id": "00000000-0000-0000-0000-000000000000",
  "github_owner": "balz-metal",
  "github_repo": "projectflow-automator"
}
```

En developpement, `PROJECTFLOW_CLIENT_ID` garde la priorite. En release GitHub Actions,
le fichier est genere automatiquement depuis le secret `PROJECTFLOW_CLIENT_ID` et le depot
GitHub courant. Les champs GitHub activent la verification de mise a jour via GitHub Releases.

## Mises a jour

Au demarrage, l'application interroge `releases/latest` du depot GitHub configure. Si une
version plus recente est disponible, ProjectFlow selectionne automatiquement l'artefact adapte :

- Windows : `.exe`
- macOS : `.dmg`, puis `.zip` si aucun DMG n'est publie

Le fichier est telecharge dans le dossier de donnees utilisateur ProjectFlow. Sous Windows,
l'app lance un helper PowerShell qui attend la fermeture de ProjectFlow, remplace l'executable,
puis redemarre l'application. Sous macOS, le DMG/ZIP est ouvert avec l'application par defaut.

## Packaging

Build Windows local :

```powershell
py -m projectflow.build --target windows
```

L'executable est produit dans `dist/ProjectFlowAutomator.exe`.

Si UPX est installe, le builder peut l'utiliser pour compresser davantage l'artefact :

```powershell
py -m projectflow.build --target windows --upx-dir C:\Tools\upx
```

Build macOS depuis un runner macOS :

```bash
python -m projectflow.build --target macos
```

L'app bundle est produit dans `dist/ProjectFlow Automator.app`. La signature Developer ID,
la notarisation Apple et la creation du DMG sont orchestrees par `.github/workflows/release.yml`
au push d'un tag `v*.*.*`.

## Etat courant

Le MVP couvre deja :

- validation des numeros `YYYY-NNN[-S]`,
- creation projet principal et sous-projet,
- fiche dossier local via `openpyxl`,
- repertoire chantier via Microsoft Graph Excel avec sessions persistantes,
- Outlook via arborescence configurable,
- Planner via selection onboarding Graph,
- parametrage et deconnexion Microsoft.

Les appels Microsoft reels necessitent une app registration multi-tenant configuree selon [docs/admin-setup.md](docs/admin-setup.md).
