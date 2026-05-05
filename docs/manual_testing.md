# Checklist de test manuel

- Premier lancement sans config existante affiche l'onboarding.
- Connexion Microsoft revient automatiquement a l'application via localhost.
- Creation d'un projet principal cree le dossier annee/projet.
- La copie du dossier de reference n'ecrase pas les fichiers existants.
- La fiche est renommee au format `{numero} - Fiche dossier clients.xlsx`.
- Le repertoire chantier ouvert dans Excel accepte une mise a jour via Graph.
- Un sous-projet cree une fiche distincte sans creer de dossier.
- `Mettre a jour` demande une confirmation et ne cree aucune integration externe.
- `Ouvrir fiche` ouvre Excel via l'application par defaut.
- `py -m projectflow.build --target windows` produit `dist/ProjectFlowAutomator.exe`.
- L'executable Windows demarre en mode demo avec `ProjectFlowAutomator.exe --demo`.
