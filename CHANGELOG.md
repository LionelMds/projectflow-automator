# Changelog

## Unreleased

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
