# Guide utilisateur

## Premier lancement

1. Ouvrir ProjectFlow Automator.
2. Choisir les chemins :
   - racine projets,
   - dossier de reference,
   - repertoire chantier Excel.
3. Valider l'assistant.

## Configurer Outlook local

Dans `Parametres`, section `Outlook` :

1. Cliquer sur `Detecter` pour lister les comptes et fichiers de donnees du profil Outlook local.
2. Selectionner le compte ou magasin qui recevra l'arborescence.
3. Choisir l'emplacement :
   - `Racine du compte` pour creer `2026 > 2026-xxxx` au premier niveau du compte.
   - `Boite de reception` pour creer `2026 > 2026-xxxx` dans la boite de reception.
4. Cliquer sur `Tester` pour verifier que ProjectFlow peut acceder au compte et a l'emplacement.
   Si le test reussit, l'option `Creer les dossiers Outlook` est activee automatiquement.

Sous Windows, ProjectFlow utilise Outlook classique installe localement. Le nouvel Outlook
Windows sans automation locale n'est pas supporte pour cette fonction.

## Creer un projet

1. Saisir l'annee et l'ID projet, par exemple `2026` et `4995`.
2. Renseigner la designation et les informations client disponibles.
3. Activer Outlook dans les parametres si l'arborescence mail doit etre creee.
4. Cliquer sur `Creer`.

ProjectFlow cree le dossier projet, copie le dossier de reference sans ecraser, remplit la
fiche client, inscrit la date de creation en `B9`, et met a jour le repertoire chantier local.

Si le dossier projet existe deja, `Creer` peut etre relance pour reappliquer Outlook et
l'epinglage Explorer sans toucher aux informations existantes. Si les informations du
formulaire different de la fiche existante, ProjectFlow demande confirmation avant de
mettre a jour la fiche et le repertoire.

## Creer un sous-projet

Ajouter le numero de sous-projet dans le champ inline, par exemple `2` pour `2026-4995-2`.

Le sous-projet reutilise le dossier parent. Il cree uniquement une nouvelle fiche et une ligne
de repertoire.

La ligne du sous-projet est inseree dans le groupe du projet parent. Elle ne consomme pas une
ligne disponible reservee aux projets principaux.

## Mettre a jour

Le bouton `Mettre a jour` reecrit la fiche et la ligne du repertoire apres confirmation. Il ne
cree pas de dossier et n'execute aucune integration externe.
