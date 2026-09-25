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

Si ProjectFlow est deja lance, cliquer de nouveau sur l'application n'ouvre pas une deuxieme
instance : la fenetre existante revient au premier plan.

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

Sous macOS, la meme section devient `Mail macOS` et pilote l'application native Mail. Au premier
test ou a la premiere creation, macOS peut demander l'autorisation de controler Mail.

## Configurer Microsoft Planner

Dans `Parametres`, section `Microsoft Planner` :

1. Cliquer sur `Detecter` pour lister les plans accessibles au compte Microsoft connecte.
2. Selectionner le plan qui doit recevoir les taches ProjectFlow.
3. Cliquer sur `Detecter colonnes` et choisir la colonne cible.
4. Regler le nombre de jours d'echeance par defaut, puis cliquer sur `Tester`.

Dans le formulaire de creation, les colonnes et membres Planner sont charges automatiquement
quand la case Planner est activee ou quand l'utilisateur ouvre la selection.

Lors de la creation d'un projet principal, ProjectFlow cree une tache nommee
`2026-4995 - Designation`. Si une tache existe deja pour ce numero dans le plan choisi,
ProjectFlow la reutilise, applique la colonne selectionnee dans le formulaire et ajoute les
membres choisis. L'echeance est desactivee par defaut pour chaque projet ; cocher `Echeance`
permet de renseigner le nombre de jours. Les sous-projets peuvent creer leur propre tache Planner
si la case Planner est activee dans le formulaire.

Si la selection des membres affiche uniquement des identifiants techniques ou refuse de charger
les noms, l'administrateur Microsoft doit autoriser `User.ReadBasic.All` pour ProjectFlow, puis
l'utilisateur doit se reconnecter a Microsoft depuis l'application.

Si le repertoire ne charge plus ou si Microsoft refuse la connexion, ouvrir `Parametres`,
cliquer sur `Se reconnecter au compte Microsoft` puis `OK`. La connexion enregistree sur le
poste est effacee et le navigateur s'ouvre pour choisir le compte. Pour aussi retrouver le
classeur partage, cliquer egalement sur `Reconnecter a OneDrive / SharePoint` avant `OK`.

Pendant une connexion, ProjectFlow affiche la fenetre `Connexion Microsoft`. Si la page ne
s'affiche pas dans le navigateur, cliquer sur `Ouvrir la page de connexion`, ou sur
`Copier le lien` puis le coller dans la barre d'adresse d'Edge ou de Chrome. La fenetre se
ferme d'elle-meme une fois la connexion terminee.

## Fichiers CAO (SolidWorks et AutoCAD)

Deux cases, sous le cadre Planner de la fenetre principale et au-dessus du bouton `Creer` du
formulaire rapide, ajoutent les fichiers CAO au projet :

- `Ajouter arborescence SolidWorks` copie les fichiers SolidWorks modeles, les renomme avec le
  numero du projet, relie l'assemblage aux pieces du projet et renseigne les proprietes ;
- `Ajouter modèle AutoCAD` copie et renomme le plan DWG modele.

Les cases sont decochees au demarrage, apres `Reinitialiser` et apres chaque creation reussie :
leur etat n'est jamais memorise. Une case grisee indique que son dossier modele n'est pas
configure ou est introuvable ; l'infobulle en donne la raison.

Les cases fonctionnent aussi pour les sous-projets (`2026-5233-2-ENS-100.SLDASM`, place dans le
sous-dossier `2026-5233-2` s'il existe) et avec `Mettre a jour` ou une nouvelle creation sur un
projet existant : seuls les fichiers manquants sont ajoutes, aucun fichier existant n'est
jamais ecrase. Le journal liste les fichiers crees, ignores (deja presents) ou en erreur. Un
echec CAO n'annule pas la creation du projet.

### Preparer les modeles

Dans `Parametres`, section `Modèles CAO` :

- `Dossier modele SolidWorks` : dossier distinct du dossier de reference (sinon les modeles
  seraient copies a chaque creation), par exemple
  `C:\Users\Lionel\OneDrive - Balz Metal Sa\Entreprise\00-Bibliothèque CAO\10-Modèles et documents\11-Racine Solidworks` ;
- `Dossier modele AutoCAD`, par exemple `...\10-Modèles et documents\12-Racine AutoCAD` ;
- `Sous-dossier dans le projet` : `Plans\Plan d'exécution` par defaut ; vide pour la racine du
  projet. Un dossier existant est reutilise meme si les accents ou la casse different ;
- `Cle Document Manager` : voir ci-dessous ;
- `Proprietes` : noms exacts, accents compris, des proprietes SolidWorks renseignees.

Les noms des fichiers modeles contiennent le marqueur `20XX-XXXX` (sans accolades), remplace a la
copie par le numero du projet, y compris dans les noms de sous-dossiers :

```text
20XX-XXXX-ENS-100.SLDASM   assemblage general (contient ENV-100, PRT-100, PRT-200)
20XX-XXXX-ENV-100.SLDPRT   environnement
20XX-XXXX-PRT-100.SLDPRT
20XX-XXXX-PRT-200.SLDPRT
20XX-XXXX-ENS-100.dwg      dossier AutoCAD ; le cartouche utilise le champ « nom de fichier »
```

Seuls les fichiers dont le nom contient le marqueur sont copies. Les fichiers temporaires
(`~$*`, `*.bak`, `*.dwl`, `*.dwl2`) sont ignores. Les copies sont rendues modifiables (attribut
lecture seule retire).

Proprietes personnalisees ecrites dans les copies (au niveau fichier, sauf la Description) :

| Propriete | Valeur |
|---|---|
| toutes | le marqueur `20XX-XXXX` est remplace par le numero (les modeles contiennent `Projet = 20XX-XXXX`) |
| `Projet` | le numero du projet si la propriete est vide |
| `Client` | la societe du formulaire |
| `Auteur` | les initiales utilisateur des parametres |
| `Description` | la designation, seulement pour `ENS-100`, dans les proprietes de chaque configuration (pas au niveau fichier) |
| `Révision` | `A` si vide |
| `Fournisseur` et les autres | valeur du modele conservee |

### Garder les modeles disponibles hors connexion

Les dossiers modeles sont sur OneDrive : dans l'Explorateur, clic droit sur chaque dossier
modele -> `Toujours conserver sur cet appareil`. Un fichier present seulement en ligne peut
faire echouer la copie ; ProjectFlow l'indique alors dans le journal.

### Cle SolidWorks Document Manager

ProjectFlow copie les fichiers SolidWorks et renseigne leurs proprietes avec SolidWorks Document
Manager, sans lancer SolidWorks. Seul le remplacement des references de l'assemblage passe par
SolidWorks (voir plus bas). Document Manager s'installe avec `swdocmgr.exe`, telecharge avec la cle (le kit
`apisdk.exe` n'est pas necessaire). Il faut aussi une cle de licence Document Manager :

1. Se connecter au portail client SOLIDWORKS (`customerportal.solidworks.com`) avec un compte
   rattache a une licence sous abonnement.
2. Demander une cle `Document Manager API` (menu API Support / Document Manager Key Request).
3. Coller la cle recue dans `Parametres` -> `Modèles CAO` -> `Cle Document Manager`, puis `OK`.

Le bouton `Tester`, a cote de la cle, verifie en arriere-plan, sans creer de projet :

1. que Document Manager est installe et que la cle ouvre l'assemblage modele ;
2. que les references de cet assemblage sont lisibles (le message indique le nombre lu par
   chaque source : `liste` et `composants`) ;
3. qu'une copie complete fonctionne : les modeles sont copies dans un dossier temporaire sous
   le numero `2099-9999`, l'assemblage est relie aux pieces copiees puis verifie dans une
   nouvelle session Document Manager, et le dossier temporaire est efface. Les modeles ne sont
   ouverts qu'en lecture. En cas d'echec, la copie d'essai est conservee dans
   `%TEMP%\ProjectFlow-essai-CAO` : l'ouvrir dans SolidWorks puis `Fichier > Chercher les
   references`, sans enregistrer, montre ou pointent vraiment ses references.

Si le test reussit, la creation d'un projet avec `Ajouter arborescence SolidWorks` fonctionnera.
En cas d'echec, le message donne la cause :

- `SwDocumentMgr.dll est introuvable` : Document Manager n'est pas installe sur ce poste ;
  installer SOLIDWORKS ou le SOLIDWORKS Document Manager fourni avec la cle ;
- `present mais n'est pas inscrit` : la DLL existe mais Windows ne la connait pas ; la commande
  `regsvr32` affichee doit etre lancee par un administrateur ;
- `inscrit en 32 bits seulement` : installer la version 64 bits de Document Manager ;
- `cle de licence ... refusee` ou `invalide` : recoller la cle complete, les deux parties
  `swdocmgr_general` et `swdocmgr_previews` separees par la virgule, sans espace.

La cle est rangee dans le gestionnaire d'identifiants du systeme, jamais dans le fichier de
configuration ni dans les journaux. Laisser le champ vide conserve la cle ; `Effacer la cle` la
supprime.

Sans Document Manager (logiciel absent, cle manquante ou invalide, ou macOS), l'option
SolidWorks ne copie pas les assemblages ni les mises en plan, pour ne jamais laisser un
assemblage relie aux modeles ; elle copie les pieces sans renseigner les proprietes et affiche un
avertissement. L'option AutoCAD fonctionne partout.

### References de l'assemblage

L'assemblage modele pointe vers les pieces modeles. Apres la copie, ProjectFlow lit ces
references avec Document Manager, puis les remplace par les fichiers renommes du projet avec
SolidWorks (`ReplaceReferencedDocument`, le remplacement a fichier ferme de SOLIDWORKS Explorer :
l'assemblage n'est pas ouvert a l'ecran). Document Manager seul ne sait pas modifier les
composants d'un assemblage SolidWorks 2026. Si SolidWorks est deja ouvert, sa session est
utilisee ; sinon il est demarre et laisse ouvert (une connexion 3DEXPERIENCE peut etre demandee).
ProjectFlow demande ensuite a SolidWorks les references de la copie, comme `Chercher les
references`, pour verifier qu'aucune ne pointe encore vers les modeles. Si une reference pointe encore vers le dossier modele, la copie de l'assemblage est
supprimee et une erreur explicite est affichee : les modeles ne peuvent pas etre modifies par
erreur depuis un projet. Les references enregistrees sur un autre poste (autre chemin
OneDrive) sont reconnues par le nom du fichier.

Les copies conservent les identifiants internes SolidWorks des modeles, comme avec `Pack and Go`.
SolidWorks accepte donc sans avertissement l'assemblage relie aux pieces copiees. En contrepartie,
tous les projets partagent ces identifiants : remplacer un composant par la piece du meme modele
d'un autre projet ne declenchera pas d'alerte, et les modeles ne doivent pas etre recrees de zero
(un nouveau fichier aurait un autre identifiant et SolidWorks signalerait une reference qui ne
correspond pas dans les assemblages existants).

## Creer un projet

1. Saisir l'annee et l'ID projet, par exemple `2026` et `4995`.
2. Renseigner la designation et les informations client disponibles.
3. Activer Outlook dans les parametres si l'arborescence mail doit etre creee.
4. Configurer Planner dans les parametres si une tache peut etre creee.
5. Dans le formulaire, cocher `Creer une tache Planner`, choisir la colonne, les membres et
   l'echeance si necessaire.
6. Cocher si besoin `Ajouter arborescence SolidWorks` et `Ajouter modèle AutoCAD`.
7. Cliquer sur `Creer`.

ProjectFlow cree le dossier projet, copie le dossier de reference sans ecraser, remplit la
fiche client, inscrit la date d'atelier en `E2` sans modifier `B9`, et met a jour le repertoire chantier. Si ce
repertoire est dans OneDrive, l'ecriture se fait directement dans le classeur cloud partage et
non dans la copie locale synchronisee.
Apres une creation reussie, ProjectFlow ouvre le dossier projet et affiche une confirmation.

Si le dossier projet existe deja, `Creer` peut etre relance pour reappliquer Outlook et
l'epinglage Explorer, ainsi que la tache Planner si activee, sans toucher aux informations
existantes. Si les informations du
formulaire different de la fiche existante, ProjectFlow demande confirmation avant de
mettre a jour la fiche et le repertoire.

Le bouton `Reinitialiser` vide uniquement les champs du formulaire pour passer a un autre
projet. Il ne modifie aucun dossier, aucune fiche, aucun repertoire et aucune configuration.

Le bouton `Ouvrir dossier` ouvre le dossier projet courant dans l'explorateur Windows ou le
Finder macOS. Pour un sous-projet, il ouvre le dossier parent du projet.

## Repertoire chantier

L'onglet `Repertoire chantier` affiche une vue de travail du classeur Excel partage. A l'ouverture
de l'onglet, ProjectFlow charge l'annee selectionnee et place la vue quelques lignes avant la
prochaine ligne disponible. Le tableau reste ensuite defilable vers les lignes precedentes et la
recherche filtre par numero, client, contact ou designation.

Les colonnes `A:E` sont les seules colonnes ProjectFlow affichees et modifiables. Selectionner une
ligne, modifier la date, le client, le contact ou la designation, puis cliquer sur `Enregistrer la
ligne`. ProjectFlow relit la ligne avant d'ecrire : si elle a change dans le classeur partage,
l'enregistrement est refuse et il faut cliquer sur `Actualiser` avant de recommencer. Les colonnes
comptables `F:L` ne sont jamais copiees ni reecrites par cet onglet.

Les cellules modifiees sont surlignees en vert pale tant qu'elles ne sont pas appliquees. Si la
valeur revient a son contenu initial, la surbrillance disparait. Elle est aussi retiree apres un
enregistrement reussi ou une mise a jour complete du projet.

Le bouton `Nouveau projet` bascule vers le formulaire principal avec l'annee selectionnee afin de
creer un projet selon le flux habituel.

Le bouton `Mettre à jour le projet` agit sur la ligne sélectionnée après confirmation. Il reprend
la désignation, le client et le contact du tableau, relit la fiche pour conserver la localisation
et la personne responsable, puis réapplique la fiche, le répertoire et les intégrations activées
dans les paramètres. Outlook, Planner et l'épinglage restent idempotents : les éléments existants
sont réutilisés ou ajustés, sans doublon. La copie du dossier de référence n'est pas relancée.

## Sortie dossier

L'onglet `Sortie dossier` prepare un dossier de documents a transmettre ou imprimer sans modifier
les fichiers du projet.

1. Saisir l'annee et le numero du projet, puis cliquer sur `Charger`.
2. Choisir la fiche dossier Excel. La premiere fiche est selectionnee automatiquement, mais
   toutes les fiches trouvees a la racine et dans le sous-dossier numerote restent disponibles.
3. Choisir, si necessaire, le PDF de prise de cote initiale.
4. Cliquer sur `Parcourir` dans le groupe `Photos`. Le dialogue s'ouvre directement dans le
   sous-dossier `photos`; selectionner une ou plusieurs images. Seules les images ajoutees sont
   reprises dans la sortie. Cliquer sur une image pour afficher son apercu simple.
5. Cliquer sur `Parcourir` dans le groupe `Plans d'execution`. Le dialogue s'ouvre directement
   dans `Plans/Plan d'exécution` (le dossier existant est trouve meme si les accents ou la
   casse different) ; selectionner les PDF a copier.
6. Cliquer sur `Creer dossier de sortie`.

ProjectFlow cree un dossier horodate sous `Sorties dossier`, avec les sous-dossiers `01 - Fiche
dossier`, `02 - Prise de cote`, `03 - Photos` et `04 - Plans`. Chaque document selectionne y est
copie individuellement. Les fichiers sources ne sont ni renommes, ni modifies, ni ecrases.
Une confirmation propose ensuite d'ouvrir le dossier de sortie.

## Charger une fiche existante

Le bouton `Charger` retrouve la fiche a la racine du dossier projet, mais aussi dans un
sous-dossier portant le numero du projet ou du sous-projet. Par exemple, ProjectFlow sait lire
`2026-5093/2026-5093/2026-5093 - Fiche dossier clients.xlsx` et
`2026-5093/2026-5093-2/2026-5093-2 - Fiche dossier clients.xlsx`.

## Creer un sous-projet

Ajouter le numero de sous-projet dans le champ inline, par exemple `2` pour `2026-4995-2`.

Le sous-projet reutilise le dossier parent. Il cree uniquement une nouvelle fiche et une ligne
de repertoire.

La ligne du sous-projet est inseree dans le groupe du projet parent. Elle ne consomme pas une
ligne disponible reservee aux projets principaux. Elle reprend la mise en forme d'une ligne
disponible, mais ses valeurs viennent uniquement du formulaire.

## Mettre a jour

Le bouton `Mettre a jour` reecrit la fiche et la ligne du repertoire apres confirmation. Il ne
recree pas le dossier projet, mais reapplique les integrations selectionnees sans doublon :
Outlook pour les projets principaux, Planner pour les projets principaux et les sous-projets.
