# Guide utilisateur

## Premier lancement

1. Ouvrir ProjectFlow Automator.
2. Choisir les chemins :
   - racine projets,
   - dossier de reference,
   - repertoire chantier Excel.
3. Valider l'assistant.

Si le repertoire chantier est dans OneDrive, ProjectFlow se connecte directement au fichier
cloud lors de la premiere lecture ou ecriture. Le navigateur Microsoft peut s'ouvrir une seule
fois pour confirmer le compte. Ensuite la connexion est reutilisee automatiquement.

## Installation

Sous Windows, utiliser `ProjectFlowAutomatorSetup.exe`. L'installation se fait dans le profil
utilisateur et ne demande normalement pas de droits administrateur.

Sous macOS, ouvrir le DMG, puis glisser `ProjectFlow Automator.app` dans `Applications`.

## Mises a jour

Le menu `ProjectFlow` -> `Rechercher une mise a jour` verifie la derniere release disponible.

Si une mise a jour est disponible :

- Windows telecharge l'installateur, verifie son empreinte SHA256, puis relance
  l'installation.
- macOS telecharge le DMG, verifie son empreinte SHA256, puis ouvre l'image disque.

Si la verification SHA256 echoue, ProjectFlow annule la mise a jour.

## Utilisation en arriere-plan

ProjectFlow reste disponible dans la zone de notification Windows ou dans la barre des menus
macOS quand le systeme le permet.

Depuis cette icone, le menu permet de :

- afficher la fenetre principale,
- ouvrir le mini-formulaire `Nouveau projet rapide`,
- ouvrir le repertoire chantier,
- rechercher une mise a jour,
- quitter ProjectFlow.

Fermer la fenetre principale masque ProjectFlow au lieu de quitter l'application. Pour quitter
completement, utiliser `Quitter ProjectFlow` dans le menu de l'icone.

Un clic simple sur l'icone ouvre directement le mini-formulaire rapide. La petite fleche dans ce
formulaire recopie les champs saisis vers la fenetre principale et ouvre le mode complet.

Le mini-formulaire rapide contient les champs essentiels du projet. Au clic sur `Creer`,
ProjectFlow recopie ces informations dans la fenetre principale et lance la creation normale, avec
les memes controles, confirmations et integrations que le formulaire complet. Le bouton
`Suivant disponible` y pre-remplit le prochain numero libre comme dans le formulaire principal.

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
fiche client, inscrit la date de creation en `B9`, et met a jour le repertoire chantier. Si ce
repertoire est dans OneDrive, l'ecriture se fait directement dans le classeur cloud partage et
non dans la copie locale synchronisee.
Apres une creation reussie, ProjectFlow ouvre le dossier projet et affiche une confirmation.

Si le dossier projet existe deja, `Creer` peut etre relance pour reappliquer Outlook et
l'epinglage Explorer sans toucher aux informations existantes. Si les informations du
formulaire different de la fiche existante, ProjectFlow demande confirmation avant de
mettre a jour la fiche et le repertoire.

Le bouton `Reinitialiser` vide uniquement les champs du formulaire pour passer a un autre
projet. Il ne modifie aucun dossier, aucune fiche, aucun repertoire et aucune configuration.

## Creer un sous-projet

Ajouter le numero de sous-projet dans le champ inline, par exemple `2` pour `2026-4995-2`.

Le sous-projet reutilise le dossier parent. Il cree uniquement une nouvelle fiche et une ligne
de repertoire.

La ligne du sous-projet est inseree dans le groupe du projet parent. Elle ne consomme pas une
ligne disponible reservee aux projets principaux. Elle reprend la mise en forme d'une ligne
disponible, mais ses valeurs viennent uniquement du formulaire.

## Mettre a jour

Le bouton `Mettre a jour` reecrit la fiche et la ligne du repertoire apres confirmation. Il ne
cree pas de dossier et n'execute aucune integration externe.
