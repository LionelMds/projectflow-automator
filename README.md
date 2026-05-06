# ProjectFlow Automator

Application desktop pour automatiser la creation de projets Balz Metal Sa.

ProjectFlow fonctionne maintenant en local : dossiers projet, fiche Excel, repertoire chantier
Excel synchronise sur le poste, et Outlook local en option. Aucun identifiant d'application,
aucune connexion cloud et aucune configuration de portail admin ne sont necessaires pour
utiliser le flux principal.

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
py -m projectflow
```

La premiere ouverture affiche un assistant simple pour choisir :

- la racine projets,
- le dossier de reference,
- le repertoire chantier Excel.

Smoke test non bloquant :

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PROJECTFLOW_SMOKE_EXIT_MS='1000'
py -m projectflow
```

## Mode demo

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

Il permet de tester `Suivant disponible`, `Creer`, `Charger`, `Ouvrir fiche`,
`Mettre a jour`, et Outlook local si un profil Outlook classique est disponible.

## Variables utiles

- `PROJECTFLOW_APP_SETTINGS` : chemin vers un `app_settings.json` local pour tester la
  verification de mise a jour sans modifier le code source.
- `PROJECTFLOW_SMOKE_EXIT_MS` : ferme automatiquement l'app apres le delai indique, pour
  tests smoke.
- `PROJECTFLOW_DEMO_MODE` : lance l'app avec un repertoire Excel local de demonstration.

## Configuration embarquee

La verification de mise a jour peut etre configuree dans :

```text
src/projectflow/resources/app_settings.json
```

Format :

```json
{
  "github_owner": "balz-metal",
  "github_repo": "projectflow-automator"
}
```

Ces champs activent la verification de mise a jour via GitHub Releases.

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

Le MVP couvre :

- validation des numeros `YYYY-NNN[-S]`,
- creation projet principal et sous-projet,
- copie non destructive du dossier de reference,
- fiche dossier locale via `openpyxl`,
- repertoire chantier via fichier Excel local synchronise sur le poste,
- conservation de la colonne F du repertoire, le champ `Gere par` restant limite a la fiche,
- bouton `Suivant disponible`,
- `Charger`, `Ouvrir fiche`, `Mettre a jour`,
- relance de `Creer` sur projet existant pour reappliquer Outlook/epingle sans ecraser,
- Outlook local Windows via le profil Outlook classique, desactive par defaut,
- assistant de premiere configuration base uniquement sur les chemins.

Outlook local utilise le profil Outlook classique du poste Windows : `Parametres` -> `Outlook`
-> `Detecter`, puis selection du compte ou magasin cible. macOS reste a valider separement,
car Outlook Mac ne fournit pas le meme modele d'automation locale.
