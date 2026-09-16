# Répertoire chantier : conflit « non fusionné »

## Ce que montre l'incident

Le poste concerné utilise la release `0.1.36`, un chemin contenant
`OneDrive - Balz Metal Sa\Entreprise\REPERTOIR CHANTIER ET FACTURES.xlsx`, et Excel affiche
« Conflit de fusion » puis « Vos modifications non fusionnées ne peuvent pas être affichées ».
Dans cette release, ce chemin sélectionne déjà l'accès cloud Microsoft Graph.
L'ancienne détection des dossiers synchronisés ne suffit donc pas à expliquer cet incident.

Le message confirme qu'Excel possède des modifications qu'il n'a pas intégrées au
classeur serveur. Microsoft documente précisément ce panneau, parfois vide avec une
fusion automatique indisponible ; la récupération passe alors par la copie non fusionnée.
Une session Graph bloquée ou concurrente peut expliquer l'échec durable de ProjectFlow,
mais la cause exacte sur ce poste exige ses journaux et l'état du classeur cloud.
[Explication Microsoft du message](https://support.microsoft.com/fr-fr/excel/how-to-manage-merge-conflicts-in-excel-cloud-files).

## Défauts cloud reproduits et corrigés

Plusieurs actions de ProjectFlow peuvent accéder au répertoire en même temps : chargement
de l'onglet, recherche du prochain numéro et suggestions de clients. Le code partageait
l'état de session Excel entre ces opérations : une opération pouvait utiliser ou fermer
la session d'une autre. Les accès d'une même connexion sont désormais exécutés successivement,
et l'état de session est libéré même après une erreur ou une annulation.

Une erreur serveur pouvait aussi déclencher une seconde insertion de ligne alors que la
première avait déjà été appliquée. Les modifications ne sont plus répétées automatiquement
après une réponse dont le résultat est incertain ; l'utilisateur est invité à actualiser.
Les erreurs de lecture des tableaux bloquent l'insertion plutôt que de déclencher une
autre méthode d'écriture sur des informations incomplètes.

Enfin, une résolution cloud encore en cours pouvait rétablir une ancienne cible après une
reconnexion ou un changement de paramètres. La nouvelle configuration est maintenant isolée
de ces opérations. Ces cas sont reproduits par les tests de régression, sans utiliser le
classeur de production.

## Autres défauts corrigés pour prévenir les conflits

Le répertoire est un classeur Excel partagé. Écrire son fichier `.xlsx` local avec
`openpyxl` remplace le classeur entier ; OneDrive peut alors tenter de synchroniser cette
version pendant qu'Excel ou un collègue modifie la version cloud.

L'ancienne détection vérifiait seulement la présence du mot `OneDrive` dans le chemin.
Une bibliothèque SharePoint synchronisée sous un chemin tel que
`C:\Users\Utilisateur\Entreprise\Site - Documents\Répertoire.xlsx`, ou une racine
OneDrive personnalisée, pouvait donc être traitée comme un fichier local. De plus,
une simple lecture du répertoire enregistrait à nouveau le classeur local.

Ces défauts pouvaient produire d'autres conflits ou écrire dans un fichier différent
de celui attendu. Ils sont corrigés indépendamment du diagnostic du poste signalé.
Un redémarrage seul ne réconcilie pas les différentes versions du classeur.

## Repartir de l'original sans récupérer les modifications non fusionnées

Si les changements de la copie non fusionnée peuvent être abandonnés, leur récupération
n'est pas nécessaire pour installer le correctif. Quitter l'ancienne application via
son icône de notification, installer la version `0.1.37`, puis utiliser le lien du
classeur d'origine OneDrive / SharePoint dans les paramètres et cliquer sur
**Reconnecter a OneDrive / SharePoint** avant de valider par **OK**.

La mise à jour ne supprime ni ne fusionne la copie en conflit. Fermer cette copie dans
Excel et reprendre le travail dans l'original cloud ; si Excel propose d'abandonner
les modifications non fusionnées, ce choix concerne les changements de cette copie.
Actualiser ensuite le répertoire dans ProjectFlow et vérifier les données affichées.

## Récupérer le poste concerné en conservant les modifications

1. Utiliser ProjectFlow `0.1.37` contenant ce correctif, ou une version ultérieure, sur les postes qui
   utilisent le répertoire. Mettre en pause les nouvelles modifications du répertoire
   le temps de comparer ses versions. Quitter l'ancienne application par son icône dans la zone
   de notification, menu **Quitter**, avant de lancer la nouvelle : fermer seulement la fenêtre
   la laisse en arrière-plan et relancer un exécutable réactive alors l'ancienne instance.
   L'installateur Windows et l'application macOS sont disponibles dans la
   [release 0.1.37](https://github.com/LionelMds/projectflow-automator/releases/tag/v0.1.37).
2. Dans Excel, ouvrir la copie non fusionnée et en enregistrer une copie durable dans
   un dossier local de récupération, hors du dossier synchronisé. Sauvegarder également
   la version cloud avant toute réconciliation. Microsoft conserve automatiquement
   la copie non fusionnée pendant sept jours dans
   `%LOCALAPPDATA%\Microsoft\Excel\TemporaryBackupFile` sous Windows, ou
   `~/Library/Containers/com.microsoft.Excel/Data/Library/Application Support/Microsoft/TemporaryBackupFile`
   sous macOS. [Emplacements et durée de conservation Microsoft](https://support.microsoft.com/fr-fr/excel/how-to-manage-merge-conflicts-in-excel-cloud-files).
3. Ouvrir le classeur d'origine depuis OneDrive ou SharePoint dans le navigateur.
   Comparer les lignes modifiées avec la copie sauvegardée. Utiliser la fusion proposée
   par Excel si elle est disponible et si les changements correspondent à ceux attendus ;
   sinon reporter les changements manquants dans l'original. Vérifier aussi les colonnes
   comptables, pas seulement les colonnes A:E éditées par ProjectFlow.
4. Dans OneDrive ou SharePoint, utiliser **Copier le lien** sur ce classeur d'origine.
   Dans ProjectFlow, ouvrir **Parametres**, remplacer **Repertoire chantier** par ce
   lien HTTPS et cocher **Repertoire partage OneDrive / SharePoint**. Cliquer sur
   **Reconnecter a OneDrive / SharePoint**, puis **OK** : les anciens identifiants du
   classeur sont effacés et le lien est résolu au prochain accès. Le compte Microsoft
   connecté est conservé. Le lien désigne explicitement le classeur partagé, même si
   le dossier local a un autre nom ou si plusieurs classeurs portent le même nom.
5. Ouvrir à nouveau l'original dans Excel. Dans ProjectFlow, actualiser le répertoire,
   vérifier les données d'un projet connu, puis effectuer la modification métier prévue
   et vérifier son résultat dans le classeur cloud.

Conserver les copies de récupération jusqu'à validation des données par les personnes
concernées. Ne pas effacer le cache Office, les sauvegardes automatiques ou les fichiers
de verrouillage pour tenter de forcer la synchronisation. Ne pas configurer la copie
non fusionnée comme répertoire et ne pas la copier par-dessus l'original.

## Protections et limites

- Le choix du mode cloud s'appuie sur les racines de synchronisation du poste, y compris
  celles dont le nom ne contient pas `OneDrive`. Les racines déclarées à Windows servent
  précisément à identifier les fichiers synchronisés.
  [Documentation Microsoft des racines de synchronisation](https://learn.microsoft.com/en-us/windows/win32/shell/integrate-cloud-storage).
- Si un chemin synchronisé ne peut pas être rattaché avec certitude au classeur cloud,
  ProjectFlow demande son lien de partage. Il ne modifie pas la copie locale pour
  contourner une erreur Microsoft.
- La résolution d'un lien de partage utilise l'identifiant cloud du fichier. Elle est
  indépendante du nom du dossier synchronisé et utilise les autorisations Microsoft du
  compte connecté. [API Microsoft des éléments partagés](https://learn.microsoft.com/en-us/graph/api/shares-get?view=graph-rest-1.0).
- Les copies explicitement nommées « non fusionné » ou « unmerged », ainsi que les
  sauvegardes temporaires Excel, sont refusées. Cette protection ne peut pas reconnaître
  une copie que quelqu'un a renommée arbitrairement.
- Lors d'une recherche de fichier, la taille locale ne sert plus à choisir entre des
  homonymes. Les pages suivantes sont prises en compte ; une recherche ambiguë ou
  incomplète bloque la liaison. Un chemin OneDrive exact introuvable ne bascule pas vers
  un homonyme situé ailleurs.
- Si plusieurs racines de comptes OneDrive sont détectées sur le poste, une nouvelle
  liaison à partir d'un chemin local demande le lien du classeur d'origine pour éviter
  de sélectionner un fichier homonyme dans le compte Microsoft connecté.
- Les conflits déjà présents dans Office doivent être résolus en récupérant les changements
  ou en les abandonnant explicitement dans Excel. Des droits insuffisants,
  une coupure réseau ou un verrouillage Microsoft peuvent encore empêcher une opération ;
  ils ne justifient jamais de réécrire localement un classeur synchronisé.
- Le correctif prévient les défauts de ProjectFlow identifiés et testés. Il ne garantit pas
  l'absence de tout conflit futur entre Excel, le réseau et les autres personnes qui éditent
  simultanément le classeur.

Si le problème persiste, relever le chemin ou le lien configuré, la version de ProjectFlow,
le compte Microsoft utilisé, le message complet et l'heure de l'échec. Vérifier que le
même compte peut modifier l'original dans Excel pour le web. Conserver les journaux pour
diagnostiquer l'opération sans multiplier les tentatives d'écriture.
