# Changelog

## Unreleased

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
