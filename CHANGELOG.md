# Changelog

## Unreleased

- Ajoute les options `Ajouter arborescence SolidWorks` et `Ajouter modèle AutoCAD` dans la
  fenetre principale (cadre `Fichiers CAO`) et dans le formulaire rapide. Decochees par defaut,
  jamais memorisees, grisees avec une explication si le dossier modele manque. Elles copient
  les fichiers modeles `20XX-XXXX-*` en les renommant avec le numero du projet ou du
  sous-projet, sans jamais ecraser un fichier existant ; `Mettre a jour` ajoute seulement les
  fichiers manquants.
- SolidWorks : via SolidWorks Document Manager, sans lancer SolidWorks, l'assemblage copie est
  relie aux pieces du projet (references relues et verifiees : aucune ne doit rester vers les
  modeles) et les proprietes personnalisees `Projet`, `Client`, `Auteur`, `Description`
  (ENS-100) et `Révision` sont renseignees. Sans Document Manager, les assemblages ne sont pas
  copies et un avertissement s'affiche.
- Ajoute la section `Modèles CAO` des parametres : dossiers modeles SolidWorks et AutoCAD,
  sous-dossier de destination, noms des proprietes et cle Document Manager rangee dans le
  gestionnaire d'identifiants du systeme.
- Le journal et la confirmation de creation listent les fichiers CAO crees, ignores ou en
  erreur ; un echec CAO n'annule pas la creation du projet. Le mode demo fournit de faux
  modeles CAO.

## 0.1.44

- Corrige la connexion Microsoft de la 0.1.43 : le navigateur affichait
  « response_mode=query is not supported » et aucune connexion ni autorisation Planner
  n'aboutissait. La redirection utilise maintenant `form_post`, exige par MSAL 1.39.
  Le bouton `Annuler` de la fenetre de connexion interrompt bien l'attente.
- Enregistre la connexion Microsoft a un seul endroit. Le gestionnaire d'identifiants
  Windows refuse les donnees de plus d'environ 1280 caracteres ; une ancienne copie
  pouvait y rester et masquer la connexion en cours, qui etait alors perdue a chaque
  demarrage. Le fichier de secours est ecrit de facon atomique.
- Journalise la duree de chaque requete Microsoft et de chaque renouvellement de
  connexion, sans identifiant ni lien, pour diagnostiquer les postes lents.

## 0.1.43

- Affiche une fenetre `Connexion Microsoft` pendant la connexion, avec les boutons
  `Ouvrir la page de connexion`, `Copier le lien` et `Annuler`. La page est ouverte par
  Windows, y compris quand Edge tourne en arriere-plan, et la fenetre se ferme apres la
  connexion. Le delai de connexion passe a cinq minutes.
- Une seule connexion Microsoft a la fois pour le repertoire et Planner ; la seconde
  reutilise la connexion obtenue. Une autorisation supplementaire (Planner) reprend le
  compte deja connecte au lieu de redemander le choix du compte.
- Ne conserve que le compte Microsoft choisi lors de la connexion, pour ne plus utiliser
  un autre compte enregistre sur le poste sans acces au SharePoint.
- Limite les requetes Planner a 60 secondes : le chargement des membres ne tourne plus
  indefiniment. Les etapes de connexion sont journalisees, sans jeton.

## 0.1.42

- Renouvelle automatiquement la connexion Microsoft avant son expiration (environ une heure)
  et refait une seule fois une requete refusee pour jeton expire. Le repertoire SharePoint
  ne decroche plus apres une longue utilisation de l'application.
- Limite la connexion Microsoft dans le navigateur a trois minutes : une fenetre de
  connexion abandonnee ne bloque plus le chargement du repertoire. Les operations
  simultanees partagent un seul renouvellement de connexion.
- Ajoute dans les parametres le bouton `Se reconnecter au compte Microsoft` : il efface la
  connexion enregistree sur le poste, recree les connexions au repertoire et a Planner,
  puis ouvre le navigateur pour choisir le compte.

## 0.1.41

- Retablit le champ modifiable `Gere par` dans le formulaire principal et la creation rapide,
  a cote des initiales utilisateur. Le responsable est ecrit et relu en C6 ; les initiales
  des parametres restent en C9, pour les projets comme pour les sous-projets.
- Conserve le responsable saisi lors d'un changement d'initiales, et les initiales utilisateur
  lors de la reinitialisation du formulaire. Les dates B9/E2 gardent leur fonctionnement.

## 0.1.40

- Execute les copies de dossiers et les operations sur les fiches Excel en arriere-plan
  pour garder l'interface reactive, y compris dans Sortie dossier. Les acces aux fichiers
  sont serialises pour eviter les ecritures concurrentes.
- Regroupe les lectures simultanees du repertoire pour le tableau, le prochain numero et
  les suggestions clients. Ignore les reponses devenues obsoletes apres un changement de
  configuration ou de projet, sans conserver un ancien repertoire pour les lectures suivantes.
- Mutualise les chargements Planner entre le formulaire principal et la creation rapide,
  empeche les detections en double et ferme les connexions Microsoft apres utilisation.
- Conserve les choix Planner et les suggestions lors d'un changement de parametres sans
  rapport avec eux. Applique les nouveaux parametres Planner aux deux formulaires.
- Empeche les doubles creations et les lectures d'une fiche pendant son ecriture. A la
  fermeture, laisse finir les operations engagees avant de liberer les connexions et le
  verrou d'instance, sans rouvrir de dialogue de confirmation.

## 0.1.39

- Inscrit en B9 la date de creation du dossier au format JJ.MM.AAAA, meme lorsque le
  modele contient deja une ancienne date ou un libelle. Cette date reste conservee lors
  des mises a jour et des sorties dossier. E2 reste reservee a la date de sortie.

## 0.1.38

- Separe l'ouverture du repertoire dans Excel de ses modifications cloud : un fichier
  OneDrive synchronise peut etre choisi pour le bouton d'ouverture, sans changer la cible
  Microsoft Graph. Les liens cloud sont resolus vers le document puis ouverts dans Excel.
- Conserve la boite mail Outlook, le plan et la colonne Planner a la reouverture des
  parametres, y compris apres une detection vide, partielle ou en erreur.
- Ajoute les initiales utilisateur dans les parametres et les inscrit en C9 lors de la
  creation ou de la mise a jour d'une fiche, y compris pour les sous-projets.
- Reserve E2 a la date de sortie dossier : une nouvelle fiche ne reprend pas une ancienne
  date d'atelier du modele, et seule la copie de sortie recoit la date du jour.
- Masque les liens de partage et leurs identifiants encodes dans les nouveaux journaux.
  Voir `docs/repertoire-opening.md` pour l'ouverture Excel et le choix des droits du lien.

## 0.1.37

- Reconnait les bibliotheques SharePoint synchronisees, les comptes OneDrive deplaces et les
  liens cloud afin d'eviter toute reecriture locale du repertoire partage.
- Les lectures du repertoire local n'enregistrent plus le classeur. Les modifications utilisent
  une sauvegarde atomique avec controle de concurrence et de verrouillage Excel.
- Isole les sessions Excel cloud concurrentes et nettoie leur etat apres erreur ; les insertions
  ne sont plus rejouees aveuglement apres une erreur serveur.
- Ajoute une reconnexion explicite au repertoire dans les parametres et refuse les copies non
  fusionnees et les recherches cloud ambigues. Avec plusieurs comptes OneDrive sur le poste,
  un lien explicite est demande lors de la resolution initiale d'un chemin local.
  Voir `docs/repertoire-recovery.md` pour recuperer
  les changements deja en conflit ou repartir de l'original sans les recuperer.

## 0.1.36

- Republie les changements de `0.1.35` apres l'echec du controle Ruff dans GitHub Actions.
- Fige la version de Ruff utilisee en developpement et en CI afin d'eviter qu'une nouvelle regle
  publiee automatiquement bloque les prochaines releases.

## 0.1.35

- Corrige la fiche dossier : `Gere par` est renseigne en `C9`, la date de creation en `B9`,
  et la date d'atelier en `E2` reste reservee a la copie produite par `Sortie dossier`.
- Ajoute l'auto-completion des champs `Societe` et `Contact` depuis le repertoire chantier de
  l'annee selectionnee, dans le formulaire principal comme dans la creation rapide.
- Normalise les doublons de casse, d'accents et d'espaces, puis filtre les contacts selon la
  societe reconnue, tout en conservant la saisie libre pour les nouveaux clients.
- Ajoute dans l'onglet `Repertoire chantier` les actions `Charger le projet`,
  `Creer sous-projet` et `Dupliquer` vers le prochain numero principal disponible.
- Ajoute une suppression globale avec confirmation : les informations `B:E` sont effacees sans
  toucher au numero `A` ni aux colonnes comptables `F:L`, le dossier OneDrive est place dans la
  corbeille, et les dossiers Outlook et taches Planner correspondants sont supprimes.
- Lors de la suppression d'un projet principal, traite aussi ses sous-projets lies et refuse
  l'operation si le repertoire partage a change depuis son chargement.

## 0.1.34

- Isole les integrations Outlook et Planner lors de la creation ou de la mise a jour d'un projet :
  une erreur Outlook ne bloque plus la creation de la tache Planner.
- Affiche l'erreur de l'integration en echec dans les logs au lieu de terminer silencieusement
  la tache asynchrone de creation.

## 0.1.33

- Ajoute l'onglet `Sortie dossier` pour preparer une sortie complete par numero de projet.
- Repertorie les disponibilites de la fiche, du PDF de prise de cote, des photos et des plans,
  puis ouvre les dialogues `Parcourir` directement dans les bons sous-dossiers.
- Remplace la liste complete des photos par une liste des fichiers ajoutes manuellement et un
  apercu simple de la photo selectionnee.
- Remplace l'impression composite par un dossier horodate contenant une copie de chaque document
  selectionne, sans modifier les originaux ni melanger les formats A4/A3.
- Demande si l'utilisateur souhaite ouvrir le dossier de sortie juste apres sa creation.
- Complete la cellule `E2` des fiches avec `fiche d'atelier le JJ.MM.AAAA`, sans ajouter la date
  une seconde fois lors d'une mise a jour.
- Repare aussi `E2` lors de la recreation d'un projet existant, sans reecrire les informations
  deja presentes dans la fiche ni modifier `B9`.
- Ajoute l'onglet `Repertoire chantier` avec recherche par numero/client/contact/designation,
  defilement libre dans les anciennes lignes et positionnement initial autour de la prochaine
  ligne disponible.
- Permet de modifier directement les colonnes ProjectFlow `A:E` avec verification de conflit
  avant ecriture. Les colonnes comptables `F:L` restent hors du modele et ne sont jamais reecrites.
- Ajoute `Mettre à jour le projet` dans cet onglet : après confirmation, la fiche et le répertoire
  sont mis à jour, puis Outlook, Planner et l'épinglage configurés sont réappliqués sans doublon.
- Surligne les cellules modifiées dans le tableau et retire la surbrillance après application
  de la modification.

## 0.1.32

- Supprime le timeout applicatif de 30 secondes sur les appels Microsoft Graph et passe
  explicitement le flux de connexion Microsoft en attente illimitee.
- Ajoute un bouton `Ouvrir dossier` dans la barre d'actions du formulaire principal.

## 0.1.31

- `Charger` et `Ouvrir fiche` retrouvent aussi les fiches rangees dans un sous-dossier
  numerote, par exemple `2026-5093/2026-5093/` ou `2026-5093/2026-5093-2/`.
- Le formulaire Planner charge automatiquement les colonnes et les membres en arriere-plan
  quand l'utilisateur active Planner ou interagit avec la selection.

## 0.1.30

- `Mettre a jour` reapplique maintenant les integrations selectionnees sans doublon.
- Les sous-projets peuvent creer ou mettre a jour leur propre tache Planner, sans creation Outlook.
- Outlook Windows reutilise et renomme le dossier projet existant si seule la designation change.

## 0.1.29

- Corrige l'affichage des membres Planner en ajoutant `User.ReadBasic.All`.
- Affiche une erreur claire si Microsoft Graph ne renvoie que les identifiants techniques des membres.
- Garde la confirmation de creation rapide ouverte apres `Ouvrir fiche` ou `Ouvrir repertoire`.
- Corrige le clic simple sur l'icone macOS pour ouvrir uniquement le formulaire rapide, sans afficher le menu.

## 0.1.28

- Ajoute la selection Planner directement dans le formulaire principal et le menu rapide.
- Permet de choisir la colonne Planner, un ou plusieurs membres assignes et une echeance par projet.
- Garde l'echeance Planner desactivee par defaut pour chaque nouvelle creation.
- Met a jour les taches Planner existantes sans doublon en appliquant la colonne et les assignations choisies.
- Remplace le droit Planner `Group.Read.All` par `GroupMember.Read.All` pour lister les membres du plan.

## 0.1.27

- Republie les corrections de `0.1.26` apres un echec GitHub Actions du job de publication.
- Garde la creation depuis le menu rapide dans le flux rapide, sans ouvrir la fenetre principale.
- Ajoute une confirmation rapide avec `Ouvrir fiche`, `Ouvrir repertoire`, `Modifier` et `Suivant`.
- Demande `Group.Read.All` pour Planner et charge les colonnes via l'endpoint officiel du plan.

## 0.1.26

- Republie les corrections de `0.1.25` apres un echec du runner GitHub macOS au checkout.
- Garde la creation depuis le menu rapide dans le flux rapide, sans ouvrir la fenetre principale.
- Ajoute une confirmation rapide avec `Ouvrir fiche`, `Ouvrir repertoire`, `Modifier` et `Suivant`.
- Demande `Group.Read.All` pour Planner et charge les colonnes via l'endpoint officiel du plan.

## 0.1.25

- Garde la creation depuis le menu rapide dans le flux rapide, sans ouvrir la fenetre principale.
- Ajoute une confirmation rapide avec `Ouvrir fiche`, `Ouvrir repertoire`, `Modifier` et `Suivant`.
- Demande `Group.Read.All` pour Planner et charge les colonnes via l'endpoint officiel du plan.

## 0.1.24

- Remplace le logo ProjectFlow et regenere les icones Windows/macOS aux formats natifs.
- Libere correctement les fichiers Excel de fiche dossier apres creation, mise a jour ou lecture.

## 0.1.23

- Decouple les droits Microsoft du repertoire OneDrive et de Planner pour ne plus bloquer le
  repertoire tant que Planner n'est pas autorise.
- Ajoute un connecteur macOS pour creer l'arborescence dans l'application native Mail.
- Ajoute l'entitlement macOS Apple Events necessaire au pilotage de Mail dans l'app signee.

## 0.1.22

- Ajoute l'integration Microsoft Planner optionnelle.
- Permet de detecter les plans et les colonnes Planner dans les parametres.
- Cree ou met a jour une tache Planner par projet principal, sans doublon, assignee a l'utilisateur connecte.

## 0.1.21

- Empeche l'ouverture de plusieurs mini-formulaires rapides depuis l'icone ProjectFlow.
- Empeche le lancement simultane de plusieurs instances de ProjectFlow.
- La deuxieme ouverture de l'application renvoie vers la fenetre deja ouverte.

## 0.1.20

- Ouvre le mini-formulaire rapide au clic simple sur l'icone ProjectFlow.
- Ajoute une fleche dans `Nouveau projet rapide` pour basculer vers la fenetre complete.
- Ajoute `Suivant disponible` dans le mini-formulaire rapide.

## 0.1.19

- Ajoute une icone de zone de notification Windows / barre des menus macOS.
- Ajoute un mini-formulaire `Nouveau projet rapide` accessible depuis cette icone.
- Garde ProjectFlow actif en arriere-plan quand la fenetre principale est fermee.

## 0.1.18

- Ajoute un vrai installateur Windows par utilisateur avec Inno Setup.
- Securise les mises a jour par verification SHA256 avant installation.
- Remplace la mise a jour Windows par copie d'executable par le lancement de l'installateur.

## 0.1.17

- Embarque le Client ID Microsoft public par defaut pour eviter une release macOS sans connecteur Graph.
- Ameliore l'affichage macOS des chemins longs avec une troncature au milieu et le chemin complet en infobulle.
- Stabilise la hauteur des sections du formulaire pour eviter les grands espaces verticaux sur macOS.
- Remplace les icones Windows, macOS et Qt par le nouveau logo ProjectFlow.

## 0.1.16

- Ajoute l'ecriture cloud directe du repertoire chantier OneDrive via Microsoft Graph Excel.
- Ajoute un bouton `Ouvrir repertoire` dans la barre d'actions.
- Demande `Files.ReadWrite.All` pour acceder aux repertoires synchronises via raccourcis ou bibliotheques partagees.
- Refuse l'ecriture locale dans un fichier OneDrive synchronise quand le connecteur cloud n'est pas embarque.
- Ajoute une connexion Microsoft silencieuse apres le premier login, avec cache token local.
- Conserve la logique de securite: ProjectFlow n'ecrit que `A:E` et nettoie la ligne inseree `A:L` avant un sous-projet.
- Corrige l'insertion des sous-projets dans les tableaux Excel structures via l'API `tables/.../rows/add`.

## 0.1.15

- Version de secours: retour au comportement applicatif de `0.1.9`.
- Retire les changements experimentaux de synchronisation automatique du repertoire chantier.
- Retire le moteur Excel COM ajoute apres `0.1.9`.
- Retire les modifications de mise a jour introduites apres `0.1.9`.
- Conserve les protections critiques de `0.1.9` sur les colonnes comptables `F:L`.

## 0.1.9

- Corrige un risque critique de duplication des colonnes comptables `F:L` lors de la creation d'un sous-projet.
- Verrouille le repertoire chantier: ProjectFlow n'ecrit plus jamais au-dela de `A:E`.
- Insere les sous-projets comme lignes entieres pour conserver l'alignement du tableau `A:L`, puis laisse `F:L` vides sur la nouvelle ligne.
- Ajoute des tests anti-regression garantissant que les valeurs comptables existantes en `F:L` restent intactes.

## 0.1.8

- Corrige le remplissage du repertoire chantier: date en colonne B, societe en colonne C, contact en colonne D et designation en colonne E.
- Ne renseigne plus la localisation dans le repertoire chantier; elle reste uniquement dans la fiche projet.
- Ajoute la date du jour au repertoire pour les projets et sous-projets.
- Renomme les dossiers Outlook projet avec la designation, par exemple `2026-4995 (Escalier)`.
- Affiche les notes de version dans la fenetre de mise a jour.
- Ameliore le DMG macOS avec un raccourci Applications pour installer par glisser-deposer.

## 0.1.0

- Initialisation du socle ProjectFlow Automator.
