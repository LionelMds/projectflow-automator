# ProjectFlow Automator

Application desktop pour automatiser la creation de projets Balz Metal Sa.

ProjectFlow fonctionne principalement en local : dossiers projet, fiche Excel et Outlook local
en option. Le repertoire chantier, s'il est place dans OneDrive, est ecrit directement dans le
classeur cloud pour eviter les copies non fusionnees creees par la synchronisation locale.

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

Quand la zone de notification Windows ou la barre des menus macOS est disponible, ProjectFlow y
place une icone permanente. Fermer la fenetre masque l'application, mais elle reste disponible en
arriere-plan depuis cette icone. Un clic simple sur l'icone ouvre le mini-formulaire
`Nouveau projet rapide`; sa fleche permet de basculer vers la fenetre complete. Le menu de l'icone
permet aussi d'afficher ProjectFlow, d'ouvrir le repertoire, de chercher une mise a jour ou de
quitter vraiment l'application.

ProjectFlow fonctionne en instance unique : relancer l'application quand elle est deja ouverte
ramene simplement la fenetre existante au premier plan.

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

Il permet de tester `Suivant disponible`, `Creer`, `Charger`, `Ouvrir dossier`, `Ouvrir fiche`,
`Mettre a jour`, et Outlook local si un profil Outlook classique est disponible.

## Variables utiles

- `PROJECTFLOW_APP_SETTINGS` : chemin vers un `app_settings.json` local pour tester la
  verification de mise a jour sans modifier le code source.
- `PROJECTFLOW_MICROSOFT_CLIENT_ID` : Client ID Microsoft optionnel pour remplacer le Client ID
  public embarque dans les releases.
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
  "github_repo": "projectflow-automator",
  "microsoft_client_id": "client-id-public-de-l-app"
}
```

Ces champs activent la verification de mise a jour via GitHub Releases.
Le champ `microsoft_client_id` active l'ecriture cloud du repertoire chantier OneDrive. Il est
public par nature pour une application desktop, dispose d'une valeur embarquee par defaut, et ne
doit jamais etre accompagne d'un secret.

La permission Microsoft deleguee attendue pour le repertoire chantier est `Files.ReadWrite.All`.
Planner reste optionnel et demande separement `Tasks.ReadWrite`, `User.Read`,
`User.ReadBasic.All` et `GroupMember.Read.All` uniquement quand la fonction Planner est utilisee.

## Mises a jour

Au demarrage, l'application interroge `releases/latest` du depot GitHub configure. Si une
version plus recente est disponible, ProjectFlow selectionne automatiquement l'artefact adapte :

- Windows : `ProjectFlowAutomatorSetup.exe` en priorite
- macOS : `.dmg`, puis `.zip` si aucun DMG n'est publie

Le fichier est telecharge dans le dossier de donnees utilisateur ProjectFlow, puis compare au
fichier `.sha256` publie avec la release. Si la verification echoue, l'installation est
annulee.

Sous Windows, ProjectFlow lance l'installateur Inno Setup en mode silencieux, ferme
l'application et laisse l'installateur remplacer proprement la version en place. L'ancien mode
par copie d'executable reste uniquement comme filet de compatibilite pour les anciennes
releases portables. Sous macOS, le DMG est ouvert avec l'application par defaut pour laisser
l'utilisateur glisser l'app dans `Applications`.

## Packaging

Build Windows local :

```powershell
py -m projectflow.build --target windows
```

L'executable est produit dans `dist/ProjectFlowAutomator.exe`.

La release GitHub construit en plus `ProjectFlowAutomatorSetup.exe` avec Inno Setup. C'est
l'artefact recommande pour les utilisateurs Windows.

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

Chaque release publie egalement un fichier `.sha256` pour l'installateur Windows, l'executable
portable Windows et le DMG macOS.

## Etat courant

Le MVP couvre :

- validation des numeros `YYYY-NNN[-S]`,
- creation projet principal et sous-projet,
- copie non destructive du dossier de reference,
- fiche dossier locale via `openpyxl`,
- date d'atelier inscrite dans `E2` de la fiche, sans modifier `B9`,
- repertoire chantier via fichier Excel local hors OneDrive, ou via Microsoft Graph Excel si le
  fichier est dans OneDrive,
- tache Microsoft Planner optionnelle via Graph, sans doublon par numero de projet, avec choix
  de colonne, de membres assignes et d'echeance par projet,
- blocage de l'ecriture locale dans un fichier OneDrive synchronise pour eviter les copies non
  fusionnees,
- conservation de la colonne F du repertoire, le champ `Gere par` restant limite a la fiche,
- bouton `Suivant disponible` base sur une ligne projet principal dont B, C, D et E sont vides,
- onglet `Repertoire chantier` avec recherche, positionnement pres de la prochaine ligne
  disponible, defilement vers les anciennes lignes et edition sure de `A:E` uniquement,
- `Charger`, `Ouvrir dossier`, `Ouvrir fiche`, `Mettre a jour`,
- onglet `Sortie dossier` pour selectionner la fiche, la prise de cote, les photos et les plans,
  avec selection manuelle des photos/plans, apercu photo simple et creation d'un dossier de sortie,
- relance de `Creer` sur projet existant pour reappliquer Outlook/epingle sans ecraser,
- icone de zone de notification Windows / barre des menus macOS avec mini-formulaire de creation,
- Outlook local Windows via le profil Outlook classique, desactive par defaut,
- Mail macOS via l'application native Mail, desactive par defaut,
- Planner configurable par plan/bucket, desactive par defaut dans chaque nouveau formulaire,
- assistant de premiere configuration base uniquement sur les chemins.

Outlook local utilise le profil Outlook classique du poste Windows : `Parametres` -> `Outlook`
-> `Detecter`, puis selection du compte ou magasin cible. Sur macOS, ProjectFlow utilise
l'application native Mail : `Parametres` -> `Mail macOS` -> `Detecter`, puis selection du compte
Mail cible.
