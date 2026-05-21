# Changelog

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
