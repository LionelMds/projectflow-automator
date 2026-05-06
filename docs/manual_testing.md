# Checklist de test manuel

- Premier lancement sans config existante affiche l'assistant de chemins.
- Creation d'un projet principal cree le dossier annee/projet.
- La copie du dossier de reference n'ecrase pas les fichiers existants.
- La fiche est renommee au format `{numero} - Fiche dossier clients.xlsx`.
- La fiche contient la date de creation dans `B9`.
- Le repertoire chantier local ouvert dans Excel accepte une mise a jour apres sauvegarde.
- La colonne F du repertoire n'est pas modifiee par le champ `Gere par`.
- `Suivant disponible` ignore les lignes dont une cellule B, C, D ou E est deja remplie.
- `Charger` lit une fiche existante sans renommer ni creer une fiche standard vierge.
- Relancer `Creer` sur un projet existant reapplique Outlook/epingle sans modifier la fiche
  ni le repertoire quand les informations sont identiques.
- Un sous-projet cree une fiche distincte sans creer de dossier et insere sa ligne dans le
  groupe parent, sans utiliser une ligne disponible de projet principal.
- `Mettre a jour` demande une confirmation et ne cree aucune integration externe.
- `Ouvrir fiche` ouvre Excel via l'application par defaut.
- Outlook local peut etre active, teste, puis utiliser `Racine du compte` ou `Boite de reception`.
- `py -m projectflow.build --target windows` produit `dist/ProjectFlowAutomator.exe`.
- L'executable Windows demarre en mode demo avec `ProjectFlowAutomator.exe --demo`.
