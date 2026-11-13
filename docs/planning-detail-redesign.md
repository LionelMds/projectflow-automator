# Planning : refonte de l'onglet « Détail »

Maquette validée : https://claude.ai/artifact/U6RJmR5hPUxbyXLYfRTcjh (artboard « Détail — proposé »).
La maquette donne l'ORDRE, le CONTENU et le COMPORTEMENT. Le STYLE vient du système Industry
(`ui/theme.py`, `ui/widgets/industry.py`) : ne pas recopier les couleurs, arrondis ou ombres de la maquette.

Fichier principal : `src/projectflow/ui/planning/detail.py` (+ `planning_controller.py` pour les signaux).
Ne pas toucher aux onglets « À planifier » ni à la saisie rapide dans ce lot.

## Règles de style Industry (prioritaires sur la maquette)

- Angles droits partout (`border-radius: 0`), objets « fil de fer » : bordures fines, pas d'ombre, pas de pastille arrondie.
- Polices : Barlow (texte), Barlow Condensed via `heading_font()` pour le n° de projet.
- Couleurs uniquement par jetons du thème (`t["accent"]`, `neutral-*`, `accent-*`, `err`…), jamais en dur ; clair ET sombre doivent marcher.
- Légendes de champ : `label(text, "field")` (helper `_caption`). Titres de section : `label(text, "caps")`.
- Boutons : `button(...)` (variants existants : `ghost`, `chip`, `danger`, `primary`, `size="sm"`). Bascules : `QPushButton` checkable `variant="chip"` (état `:checked` déjà stylé).
- Icônes : `set_icon()` / `icon()` du thème. Pas d'emoji.
- Cibles cliquables ≥ 32 px de haut dans le panneau.

## Ordre des blocs (de haut en bas)

1. **N° de projet** — `QLineEdit` en `heading_font` grand (≈ 30 px), soulignement accent, masque/validation `AAAA-XXXX` (vide = tâche interne, comme aujourd'hui avec `dossier_edit`). Client du dossier à droite en petit (`muted`).
2. **Sous-désignation** — c'est le champ existant `task.title` (le modèle a déjà `designation = title or project_cache.designation`). Libellé « Sous-désignation », texte 16 px demi-gras, placeholder « Nom libre de la tâche (sinon : désignation du projet) ».
   Dessous, une ligne secondaire `small`/`muted` : « Projet : <project_cache.designation> ». Si la sous-désignation est vide, cette désignation reste le titre affiché partout (comportement actuel de `display_title`).
3. **Type** — 3 bascules exclusives `chip` sur une ligne, issues de `task_types.py` (Atelier / Montage / Autre), à la place du combo `type_combo`. Un `QButtonGroup` exclusif.
4. **Début / Fin** — un bloc encadré à deux lignes cliquables : « Début … lun 16 nov · 07:00 » et « Fin … mar 17 nov · 12:00 ». Cliquer une ligne déplie SOUS elle un sélecteur à rouleaux (style calendrier iOS) ; une seule ligne dépliée à la fois ; la valeur de la ligne ouverte passe en couleur accent.
   - Rouleaux : Jour (« lun 16 nov »), Heure (00–23), Minutes (00 / 30 — même pas d'une demi-heure que la grille).
   - 5 valeurs visibles, la centrale en gras dans une bande `neutral` claire, les autres de plus en plus pâles.
   - Molette, glisser vertical, clic sur une valeur, flèches ↑/↓ au clavier ; Échap ou clic ailleurs referme.
   - Widget à créer : `ui/planning/wheel_picker.py` (`QWidget` peint à la main, pas de `QDateTimeEdit`). Couleurs depuis le thème.
   - Sous le bloc, à droite, en `small` : « Durée : 1,5 j ouvré » (calcul existant de `duration_label`).
   - Fin < Début : la ligne Fin passe en `tone="error"`, rien n'est enregistré.
5. **Échéance + Urgent** — une ligne : champ « Échéance » (valeur ou « Aucune », clic = même sélecteur à rouleaux jour seul, petite croix pour effacer) puis bascule `chip` « ! Urgent » (`:checked` en ton erreur).
   Ligne suivante : bascule `chip` « Travail de nuit » (reprend `night_check`, même comportement qu'aujourd'hui) ; le texte de repos qui l'accompagne (`rest_label`) reste affiché dessous en `small` quand il y a lieu. Fonction IMPORTANTE : ne pas la supprimer.
6. **Qui** — titre « QUI » + compteur à droite (« 2 sélectionnés »).
   Toute l'équipe du planning affichée en boutons d'initiales (`chip` checkable, carrés ~44 px de haut, 5 par ligne), groupés par équipe dans l'ordre `Team.order` (Bureau technique, Atelier, Montage, Sous-traitants) avec le nom d'équipe en `small` au-dessus. Infobulle = nom complet. Personne absente sur la période : bouton grisé hachuré + infobulle du motif, mais sélectionnable.
   Remplace `people_caption`, la liste d'affectés, `add_person_button` et `person_edit`.
7. **Notes** — `notes_edit` inchangé, hauteur ~3 lignes.
8. **Statut** — tout en bas de la zone de contenu (poussé par un stretch) : contrôle segmenté 3 positions Provisoire / Confirmé / Terminé (`STATUS_LABELS`), angles droits.
9. **Pied fixe** (hors défilement) : ligne `small` « Créé par … · date » (`author_label`), puis boutons `size="sm"` : « Renvoyer à planifier », « Dupliquer », et « Supprimer » (`danger`) aligné à droite.

## Retiré du panneau

- « Décaler en jours ouvrés » (Plus tôt / Plus tard).
- « Estimation » (`estimate_edit`) — garder la donnée dans le modèle, juste ne plus l'afficher ici.
- Bloc « À vérifier ».
- « Requis » (`required_spin`, `required_caption`) : champ inutilisé, à SUPPRIMER (UI du panneau, et tout ce qui n'existe que pour lui : constat `missing_people`, badge « N pers. » des cartes, syntaxe `2p` de la saisie rapide…). Lister ce qui a été retiré dans le compte rendu.
- Rangée Dossier / Fiche / Répertoire / Création / Sortie, et « Détacher ».

À CONFIRMER avec Lionel avant de supprimer définitivement : « Détacher ».

## Ne pas changer

- Le modèle de données et la synchro OneDrive/Outlook (la sous-désignation = `title` existant).
- Le mode multi-sélection (`DetailMode = "multi"`) : appliquer le même ordre de blocs, champs mixtes affichés « — ».
- Les raccourcis existants (Échap = retour « À planifier »).

## Vérification

- `ruff`, `mypy`, `pytest` verts ; ajouter des tests pour `wheel_picker` (valeur, pas de 30 min, clavier) et pour l'affichage sous-désignation vide / remplie.
- Vérifier visuellement en thème clair ET sombre, et à 100 % / 150 % d'échelle Windows.
- Entrée dans `CHANGELOG.md` (style des entrées existantes « Planning : … »).
