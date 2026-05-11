# Changelog

## 0.1.13

- Corrige les conflits de fusion OneDrive du repertoire chantier en pilotant Excel localement sur Windows pour les fichiers OneDrive.
- Evite les copies non fusionnees creees lorsque le fichier partage etait modifie directement par `openpyxl`.
- Conserve `openpyxl` pour les fichiers locaux hors OneDrive et le mode test.
- Ajoute des tests pour le choix automatique du moteur Excel/OneDrive.

## 0.1.12

- Renforce la synchronisation du repertoire chantier face aux fusions OneDrive qui peuvent effacer une ecriture juste apres son application.
- Conserve chaque ecriture en surveillance pendant 10 minutes avant de la considerer definitivement synchronisee.
- Rejoue automatiquement une ecriture si Excel/OneDrive la fait disparaitre pendant une fusion.
- Clarifie le journal de synchronisation avec l'etat `en surveillance OneDrive`.

## 0.1.11

- Ajoute une file locale d'ecritures du repertoire chantier pour conserver les modifications si OneDrive ou Excel est temporairement indisponible.
- Synchronise automatiquement les ecritures en attente au demarrage, apres creation ou mise a jour de projet, puis en arriere-plan.
- Ajoute un bouton `Synchroniser repertoire` pour relancer manuellement la synchronisation si necessaire.
- Verifie les ecritures du repertoire apres sauvegarde et conserve une fenetre de securite avant de supprimer les transactions locales.
- Ameliore le mode demo pour tester le meme mecanisme de synchronisation locale.

## 0.1.10

- Corrige l'installation automatique des mises a jour Windows apres confirmation utilisateur.
- Ajoute un installateur Windows detache avec journal, retries de copie, verification de taille et relance de l'application mise a jour.
- Affiche un message d'erreur avec chemin du journal si la copie Windows echoue au lieu d'echouer silencieusement.
- Conserve les corrections critiques de `0.1.9` protegeant les colonnes comptables `F:L` du repertoire chantier.

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
