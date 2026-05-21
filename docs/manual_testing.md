# Checklist de test manuel

- Premier lancement sans config existante affiche l'assistant de chemins.
- Creation d'un projet principal cree le dossier annee/projet.
- Apres creation, le dossier projet s'ouvre et un message de confirmation s'affiche.
- La copie du dossier de reference n'ecrase pas les fichiers existants.
- La fiche est renommee au format `{numero} - Fiche dossier clients.xlsx`.
- La fiche contient la date de creation dans `B9`.
- Le repertoire chantier local ouvert dans Excel accepte une mise a jour apres sauvegarde.
- La colonne F du repertoire n'est pas modifiee par le champ `Gere par`.
- `Suivant disponible` ignore les lignes dont une cellule B, C, D ou E est deja remplie.
- `Charger` lit une fiche existante sans renommer ni creer une fiche standard vierge.
- Relancer `Creer` sur un projet existant reapplique Outlook/epingle sans modifier la fiche
  ni le repertoire quand les informations sont identiques.
- `Reinitialiser` vide les champs du formulaire sans modifier les chemins, logs, fichiers ou
  integrations.
- Un sous-projet cree une fiche distincte sans creer de dossier et insere sa ligne dans le
  groupe parent, sans utiliser une ligne disponible de projet principal.
- La ligne repertoire d'un sous-projet contient uniquement les valeurs saisies par l'utilisateur ;
  elle n'herite d'aucune cellule du projet parent, seulement du format d'une ligne disponible.
- `Mettre a jour` demande une confirmation et ne cree aucune integration externe.
- `Ouvrir fiche` ouvre Excel via l'application par defaut.
- L'icone ProjectFlow apparait dans la zone de notification Windows ou la barre des menus macOS.
- Fermer la fenetre principale masque l'application ; `Quitter ProjectFlow` dans le menu de
  l'icone ferme vraiment le processus.
- Un clic simple sur l'icone ouvre le mini-formulaire rapide.
- Un second clic sur l'icone ne cree pas un deuxieme mini-formulaire.
- Relancer ProjectFlow pendant qu'il tourne deja ramene la fenetre existante au premier plan et ne
  cree pas une deuxieme icone.
- `Nouveau projet rapide` contient `Suivant disponible`, une fleche vers la fenetre complete, et
  declenche la creation normale.
- Outlook local peut etre active, teste, puis utiliser `Racine du compte` ou `Boite de reception`.
- Sur macOS, Mail peut etre active, detecter les comptes Mail, puis creer l'arborescence projet.
- Sur macOS signe, la premiere creation affiche la demande d'autorisation Apple Events pour Mail.
- Planner peut etre active, detecter les plans, detecter les colonnes du plan choisi et tester
  l'acces.
- Creer un projet principal avec Planner actif cree une tache assignee a l'utilisateur connecte.
- Relancer `Creer` sur le meme projet avec Planner actif ne cree pas de doublon : la tache
  existante est reassignee/deplacee si necessaire.
- Un sous-projet ne cree pas de tache Planner.
- `py -m projectflow.build --target windows` produit `dist/ProjectFlowAutomator.exe`.
- La release GitHub produit `ProjectFlowAutomatorSetup.exe` et son `.sha256`.
- Sur Windows, `Rechercher une mise a jour` telecharge l'installateur, verifie le SHA256 et
  lance l'installation au lieu de remplacer directement l'executable.
- Sur macOS, `Rechercher une mise a jour` telecharge le DMG, verifie le SHA256 et ouvre l'image
  disque.
- L'executable Windows demarre en mode demo avec `ProjectFlowAutomator.exe --demo`.
