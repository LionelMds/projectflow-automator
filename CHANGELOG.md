# Changelog

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
