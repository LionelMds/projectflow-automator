# Ouvrir le répertoire dans Excel et conserver les paramètres

Depuis la version 0.1.38, l'ouverture du classeur et les modifications effectuées par
ProjectFlow ont des réglages distincts.

Dans **Paramètres** :

- **Répertoire chantier** désigne le classeur que ProjectFlow lit et modifie. Pour un
  classeur partagé, garder son lien OneDrive / SharePoint et le mode cloud.
- **Fichier synchronisé pour ouverture Excel** est facultatif. Sélectionner avec
  **Parcourir** le même classeur d'origine dans le dossier OneDrive du poste. Le bouton
  **Ouvrir répertoire** l'ouvre alors avec l'application associée aux fichiers Excel.
  Ce réglage ne change ni le classeur cloud ciblé ni la méthode d'écriture de ProjectFlow.

Sans fichier d'ouverture distinct, un chemin local est ouvert comme auparavant. Si la
cible est un lien cloud, ProjectFlow lit ses métadonnées, puis lance Excel avec l'adresse
du document d'origine. Il ne télécharge pas de copie et n'ouvre pas de session d'écriture
Excel pour cette action. Excel doit être installé et connecté au compte autorisé.
Si Microsoft ne fournit pas d'adresse compatible avec Excel, renseigner le fichier
synchronisé dans le champ d'ouverture. Ne pas sélectionner une copie non fusionnée.

Le [protocole d'ouverture Office](https://learn.microsoft.com/en-us/office/client-developer/office-uri-schemes)
permet de demander à Excel d'ouvrir le document cloud. L'acceptation de cette demande par
Windows ou macOS ne garantit pas que le compte Excel possède les droits nécessaires.

## Initiales et mémorisation

Renseigner une fois **Initiales utilisateur**, puis valider avec **OK**. Les initiales
sont enregistrées en majuscules et utilisées dans **C9** lors de la création ou mise à
jour des fiches et sous-projets. Le formulaire affiche les initiales des paramètres.
Si elles sont vides, une valeur C9 déjà présente est conservée.

La boîte mail, le plan Planner et sa colonne sont sélectionnés à la réouverture des
paramètres. Une détection ne les efface plus si la liste est vide ou indisponible.
Changer volontairement de plan remet la colonne à choisir pour ce nouveau plan.
Fermer avec **Annuler** conserve les derniers paramètres enregistrés.

La date de création du dossier est inscrite en **B9** au format **JJ.MM.AAAA**, même si
le modèle contient déjà une date ou un libellé (depuis la version 0.1.39). Cette date
reste conservée lors des mises à jour. **E2** est réservé à la sortie dossier :
la copie de sortie reçoit la date du jour, tandis que la fiche source reste inchangée.
À la création d'une nouvelle fiche, une ancienne date dans le libellé reconnu
« fiche d'atelier le » du modèle est retirée. Aucun nettoyage global des fiches existantes
n'est effectué.

## Sécurité du lien de partage

Aucun lien privé n'est intégré au code du programme. Le lien saisi est enregistré dans
le fichier de configuration de l'utilisateur, en clair, avec les autres paramètres.
La protection des droits dépend donc du type de lien créé dans Microsoft 365 :

- **Personnes disposant déjà d'un accès** convient lorsque les droits du répertoire sont
  déjà attribués à l'équipe : ce lien ne donne pas de droits supplémentaires.
- **Personnes spécifiques** limite l'accès aux destinataires autorisés et authentifiés.
- **Toute personne disposant du lien** est transmissible et peut fonctionner sans
  authentification. Le lien constitue alors un secret d'accès et ne convient pas à une
  configuration partagée ou publiée.

Voir les explications Microsoft sur les
[liens de partage](https://learn.microsoft.com/en-us/sharepoint/shareable-links-anyone-specific-people-organization)
et les [options OneDrive](https://support.microsoft.com/en-us/onedrive/share-files-and-folders-in-microsoft-onedrive).
Le logiciel ne vérifie pas automatiquement le type ni les destinataires du lien choisi.

Les nouveaux journaux masquent les liens Microsoft et les identifiants Graph qui les
encodent. Cette protection ne nettoie pas les anciens journaux et ne chiffre pas la
configuration locale. Ne pas publier un fichier de configuration contenant un lien privé.
