# Architecture

ProjectFlow separe strictement UI, logique metier et integrations locales.

```mermaid
flowchart LR
  UI["PySide6 UI"] --> Service["ProjectService"]
  Service --> FS["Systeme fichiers"]
  Service --> Fiche["FicheService openpyxl"]
  Service --> Repertoire["RepertoireService"]
  Repertoire --> LocalExcel["Excel local openpyxl hors OneDrive"]
  Repertoire --> GraphExcel["Graph Excel cloud pour OneDrive"]
  Service -. optionnel .-> Outlook["Connecteur Outlook local"]
  Outlook --> WinOutlook["Profil Outlook classique Windows"]
  Outlook --> MacMail["Mail natif macOS via Apple Events"]
  Service -. optionnel .-> Planner["Graph Planner"]
  Planner --> M365["Microsoft Planner"]
  Service -. optionnel .-> Cad["CadTemplateService"]
  Cad --> Models["Dossiers modeles CAO"]
  Cad --> DM["SolidWorks Document Manager (COM)"]
```

Les operations CAO (`projectflow.cad`) tournent dans le thread de fichiers
(`run_file_io`). L'acces COM est isole derriere les protocoles de
`cad/solidworks_properties.py` ; les tests et le mode demo utilisent un faux Document Manager
(`cad/demo_document_manager.py`).

## Demandes de MailFlow Archivist

`projectflow.bridge` sert les demandes de MailFlow sans interface. `__main__` les
reconnait a `--mailflow-request` avant de creer la `QApplication` : pas de fenetre, pas
de transfert vers l'instance unique. La demande est un fichier JSON
(`protocol: 1`, `action: ensure_outlook_folders`, `numbers`), la reponse un autre
fichier ecrit par remplacement atomique, car l'executable fenetre n'a pas de console.

```mermaid
sequenceDiagram
  participant MailFlow as MailFlow Archivist
  participant Bridge as projectflow.bridge
  participant Rep as RepertoireService
  participant Outlook as Outlook local

  MailFlow->>Bridge: demande.json (numeros sans dossier Outlook)
  Bridge->>Outlook: validate_target_sync
  Bridge->>Rep: read_snapshot par annee (session Microsoft sans interaction)
  Bridge->>Outlook: ensure_folder_path_sync (arborescence des parametres)
  Bridge->>MailFlow: resultat.json (ok / unknown / error par projet)
```

Seul un projet dont les colonnes B:E du repertoire sont renseignees recoit un dossier.
`ServiceContainer(interactive_sign_in=False)` transmet `allow_interactive=False` au
fournisseur MSAL : une session expiree devient une erreur explicite au lieu d'une page
de connexion. Les appels Outlook se font sur le fil principal du processus.

Avant de creer un dossier projet Outlook, `WindowsLocalOutlookClient` verifie son
emplacement habituel ; s'il n'y est pas, il cherche le numero dans tout le dossier de
base (six niveaux au plus, sans descendre dans les dossiers projet, hors Elements
supprimes et Courrier indesirable) et reutilise le dossier trouve, par exemple dans
`00-Archives/2026`, sans le renommer. Mail sur macOS garde le comportement precedent.

## Premier lancement

```mermaid
sequenceDiagram
  participant User as Utilisateur
  participant App as ProjectFlow
  participant Config as Config locale

  User->>App: Ouvre l'application
  App->>User: Assistant de configuration
  User->>App: Choisit racine, reference, repertoire Excel
  App->>Config: Sauvegarde les chemins
  App->>User: Affiche le formulaire projet
```

## Creation Projet Principal

```mermaid
sequenceDiagram
  participant UI as UI
  participant Service as ProjectService
  participant FS as Systeme fichiers
  participant Fiche as FicheService
  participant Rep as RepertoireService
  participant Excel as Excel cloud/local
  participant Outlook as Outlook local
  participant Planner as Graph Planner

  UI->>Service: create_project(ProjectInput)
  Service->>FS: Cree annee/projet si absent
  Service->>FS: Copie reference sans ecraser
  Service->>Fiche: Remplit fiche dossier
  Service->>Rep: upsert_project
  Rep->>Excel: Lit/ecrit via Graph si OneDrive, sinon fichier local
  Service->>Outlook: Cree l'arborescence si activee
  Service->>Planner: Cree ou met a jour la tache si active
```

## Sous-projet

```mermaid
sequenceDiagram
  participant Service as ProjectService
  participant Fiche as FicheService
  participant Rep as RepertoireService
  participant Excel as Excel cloud/local

  Service->>Fiche: Duplique/remplit fiche sous-projet
  Service->>Rep: upsert_project sous-projet
  Rep->>Excel: Lit le repertoire
  Rep->>Rep: Calcule la position apres le groupe consecutif
  Rep->>Excel: Insere une ligne vide puis ecrit seulement A:E
```
