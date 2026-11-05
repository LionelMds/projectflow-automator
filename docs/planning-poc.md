# Planning : preuve de faisabilité (Lot 0)

Le Lot 0 vérifie deux choses avant de construire l'onglet Planning :

1. une **liste SharePoint** suffit-elle pour synchroniser le planning entre les postes en
   quelques secondes, sans écraser les modifications des autres ?
2. une grille **QGraphicsView** reste-t-elle fluide avec 40 lignes, 3 mois de tâches et une
   précision à la demi-heure ?

Les deux outils sont dans `tools/planning_poc/`. Ils ne font pas partie de l'application
livrée et n'en modifient pas les données.

## Préalable Microsoft (une fois)

1. Dans Entra : **Applications** › **ProjectFlow Automator** › **Autorisations d'API**.
   `Sites.ReadWrite.All` (déléguée) doit afficher **Accordé pour Balz Métal SA**. S'il
   n'y a pas encore de statut, cliquer **Accorder un consentement d'administrateur pour
   Balz Métal SA**. Sinon le compte partagé devra accepter l'autorisation à la prochaine
   connexion, sur un poste, dans le navigateur.
2. Créer la liste de test. Deux possibilités :
   - **Automatique** : ajouter aussi `Sites.Manage.All` (déléguée) et accorder le
     consentement. Microsoft réserve la création de listes à cette autorisation ;
     `Sites.ReadWrite.All` ne suffit pas. `setup --create` crée alors la liste.
   - **À la main** : sur le site SharePoint du groupe Microsoft 365 du plan Planner,
     **Nouveau** › **Liste** › **Liste vide**, nommée `ProjectFlow Planning PoC`, puis
     ajouter quatre colonnes :

     | Colonne | Type |
     |---|---|
     | `PfKind` | Une seule ligne de texte |
     | `PfDocId` | Une seule ligne de texte |
     | `PfProject` | Une seule ligne de texte |
     | `PfData` | Plusieurs lignes de texte, texte brut |

     Taper les noms exactement ainsi (sans espace) : SharePoint garde ce premier nom comme
     nom interne de la colonne.

La liste de test est indépendante de la future liste « ProjectFlow Planning ». On peut la
supprimer après le Lot 0.

## Mesures Graph sur un poste

Lancer depuis la racine du dépôt, avec le compte Microsoft partagé déjà connecté dans
ProjectFlow sur ce poste. Le script réutilise cette connexion : il ne demande pas de
nouvelle connexion tant que l'autorisation est accordée. Sinon il s'arrête sur
« Connexion Microsoft requise ». Ajouter alors `--interactive` à la commande, une seule
fois : le navigateur s'ouvre sur le compte partagé. Choisir ce même compte, sinon
l'application installée change de compte elle aussi.

```bash
py tools/planning_poc/graph_bench.py setup --create
py tools/planning_poc/graph_bench.py fields-check
py tools/planning_poc/graph_bench.py conflict
py tools/planning_poc/graph_bench.py latency --writes 100 --interval 3 --poll 2
py tools/planning_poc/graph_bench.py load --pollers 8 --poll 2 --minutes 3
py tools/planning_poc/graph_bench.py cleanup
```

`setup` retrouve le site à partir du plan Planner configuré dans ProjectFlow. Pour viser un
autre site : `--site-url https://<tenant>.sharepoint.com/sites/<site>`. Avec `--create`, il
crée la liste si elle manque ; sans `Sites.Manage.All`, il affiche la marche à suivre pour
la créer à la main. `cleanup` supprime les éléments de test.

Chaque commande affiche un résumé et écrit un rapport JSON dans
`%LOCALAPPDATA%\Balz Metal Sa\ProjectFlow\planning-poc\` (macOS :
`~/Library/Application Support/ProjectFlow/planning-poc/`). Code de sortie : 0 mesuré,
1 erreur Microsoft ou de connexion, 3 mesuré mais le comportement attendu a échoué (ligne
`ECHEC` dans le résumé), 130 interrompu par Ctrl+C (les résultats partiels sont gardés).

## Mesure entre deux postes

Les horloges de deux postes ne sont jamais parfaitement d'accord : on mesure donc un aller
et retour. Sur le poste B d'abord, puis sur le poste A :

```bash
py tools/planning_poc/graph_bench.py pingpong --role pong --rounds 50 --poll 2
py tools/planning_poc/graph_bench.py pingpong --role ping --rounds 50 --poll 2
```

Le délai d'un poste à l'autre vaut environ la moitié de l'aller et retour. Le poste B
s'arrête de lui-même quand il ne reçoit plus de « ping ». Lancer `setup` une fois sur
chaque poste avant : les deux retrouvent la même liste par son nom.

## Prototype de la grille

```bash
py tools/planning_poc/view_poc.py
py tools/planning_poc/view_poc.py --view day
py tools/planning_poc/view_poc.py --bench
```

La fenêtre montre 40 lignes et 3 mois de tâches fictives, à la demi-heure, sans samedi ni
dimanche, avec les fériés GE et VD. On peut glisser, étirer, changer de ligne, annuler un
glisser avec Échap et changer de vue. `--bench` affiche les temps de construction, de
dessin, de clic, de glisser et d'export PDF A3 ; `--screenshot fichier.png` enregistre une
capture. Les tests du prototype : `py -m pytest tools/planning_poc/test_planning_poc.py`.

## Critères de décision

| Mesure | « Go » si… | Sinon |
|---|---|---|
| `fields-check` : le delta renvoie `PfData` | oui | Une lecture de plus par élément modifié ; acceptable si la latence reste bonne |
| `fields-check` : l'eTag reste le même entre l'écriture, la relecture et le delta | oui | Faux conflits : comparer le numéro de version de l'eTag plutôt que l'eTag complet |
| `conflict` : une écriture avec un eTag périmé est refusée (412) | oui | Bloquant : passer au plan B (fichiers JSON) |
| `latency` : P95 entre l'écriture et sa lecture | ≤ 5 s | Raccourcir la cadence ou passer au plan B |
| `pingpong` : moitié de l'aller et retour, P95 | ≤ 5 s | Idem |
| `load` : 8 postes simulés pendant 3 min | aucune erreur, réponses 429 rares | Cadence adaptative plus lente hors de l'onglet Planning |
| Grille : dessin complet de la vue | P95 ≤ 16 ms | Allègement du dessin (cache, moins de texte) |
| Grille : un pas de glisser | ≤ 8 ms | Idem |
| Grille : export A3 de la semaine | ≤ 1 s | Acceptable jusqu'à quelques secondes |

## Résultats

### Grille Qt : mesurée le 22.10.2026

Mesure faite sur le PC de développement (Windows 11, Intel 24 cœurs, Python 3.14, PySide6
6.11), hors écran, avec 40 lignes, 92 jours, 1 315 tâches et 1 576 barres.

| Mesure | Résultat | Seuil |
|---|---|---|
| Construction de la scène | 32 ms | — |
| Dessin complet, vue Jour (37 barres visibles) | P95 5,2 ms | ≤ 16 ms |
| Dessin complet, vue Semaine (82 barres visibles) | P95 4,6 ms | ≤ 16 ms |
| Dessin complet, vue Mois (537 barres visibles) | P95 12,5 ms | ≤ 16 ms |
| Clic sur une barre (2 000 essais) | P95 0,006 ms | — |
| Un pas de glisser, dessin compris | P95 2,2 ms | ≤ 8 ms |
| Export A3 paysage de la semaine | 43 ms, 2 pages | ≤ 1 s |

Verdict : **go** pour `QGraphicsView`. La vue Mois est la plus chargée ; elle reste sous le
seuil. À refaire sur un poste d'atelier plus modeste et sur macOS avant le MVP.

### Microsoft Graph : liste SharePoint

À mesurer, une fois l'autorisation `Sites.ReadWrite.All` accordée (préalable 1).
L'administrateur refuse pour l'instant les autorisations `Sites.*` : le planning partagé
passe donc par OneDrive (ci-dessous).

### Microsoft Graph : fichiers OneDrive, mesuré le 05.11.2026

Plan B, sans nouvelle autorisation : le planning est un dossier du OneDrive du compte
partagé (`/me/drive`), un fichier JSON par élément (`task__<id>.json`, `resource__…`,
`absence__…`, `meta__meta.json`), lu et écrit avec la seule autorisation déléguée
`Files.ReadWrite.All` déjà accordée pour le répertoire. Sonde manuelle sur le vrai compte :

| Mesure | Résultat |
|---|---|
| Création `PUT …/{dossier}:/{nom}:/content?@microsoft.graph.conflictBehavior=fail` d'un fichier existant | 409, fichier inchangé |
| Mise à jour `PUT /items/{id}/content` avec `If-Match` à jour | 200 et nouvel eTag |
| Mise à jour ou suppression avec un eTag périmé | 412, rien de modifié |
| eTag rendu par l'écriture, par `GET ?$select=id,eTag` et par le delta | identiques |
| `/me/drive/root/delta?token=latest` puis le lien delta | une écriture vue en ~0,1 s |
| Latence écriture → lecture, lecteur interrogeant toutes les 2 s | médiane 1,2 s, max 2,2 s |
| Idem, lecteur interrogeant toutes les 10 s | max 10 s |

Le delta porte sur tout le OneDrive : ProjectFlow ne garde que les éléments du dossier du
planning (identifiant du dossier parent). Il donne le dernier état de chaque élément modifié
(les versions intermédiaires se confondent) et signale les suppressions par la facette
`deleted`. Le contenu se lit par `@microsoft.graph.downloadUrl` quand le delta la donne, sinon
par `GET /items/{id}/content`, qui répond 302 vers une adresse de téléchargement
pré-authentifiée (suivie sans le jeton).

Verdict : **go** pour OneDrive. Les écritures conditionnelles (409, 412) empêchent d'écraser
la modification d'un autre poste et la latence tient sous le seuil de 5 s à la cadence de
2 s de l'onglet Planning visible.

Migration d'un poste qui avait un planning local, sans rien remplacer dans OneDrive :

    py tools/planning_poc/copy_to_onedrive.py --dry-run
    py tools/planning_poc/copy_to_onedrive.py

(`--folder "Nom du dossier"` pour un autre dossier que celui du poste). Le même passage
existe dans `Paramètres > Planning > Copier le planning local vers OneDrive…`.
