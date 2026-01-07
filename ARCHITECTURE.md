# Architecture Système - WhatsApp Driver Verification Bot (MVP)

## Vue d'Ensemble

Ce système est un bot WhatsApp qui gère la vérification d'identité et la réputation de chauffeurs pour les propriétaires de véhicules. L'architecture est conçue pour garantir la sécurité, la traçabilité et la conformité légale.

---

## 1. COUCHES ARCHITECTURALES

### 1.1 Interface WhatsApp (Couche Présentation)
**Responsabilité** : Gestion de la communication WhatsApp uniquement

- **Réception** : Messages entrants depuis l'API Meta WhatsApp
- **Émission** : Envoi de messages formatés avec menus numérotés
- **Validation** : Vérification que les entrées utilisateur correspondent aux choix proposés
- **Formatage** : Transformation des données métier en messages WhatsApp lisibles

**Contraintes** :
- Un seul message = une seule question
- Toujours proposer des choix numérotés
- Jamais de champs texte libre sans validation

---

### 1.2 Gestion d'État de Conversation (Couche Session)
**Responsabilité** : Suivi du contexte de chaque conversation avec contrôle strict des entrées

- **État de session** : Où en est l'utilisateur dans le flux (menu principal, création profil, etc.)
- **Données temporaires** : Informations collectées pendant un processus multi-étapes
- **Timeout** : Gestion des sessions expirées (retour au menu principal)
- **Persistance** : Stockage de l'état entre messages

**Structure** :
- Chaque utilisateur (numéro WhatsApp) a une session active
- État = point dans le flux conversationnel
- Données temporaires = valeurs en cours de collecte

**Contrôle d'Entrée (CRITIQUE)** :
Chaque état de conversation doit explicitement définir :
- **expected_input_type** : Type d'entrée attendu
  - `MENU_CHOICE` : Choix parmi une liste numérotée (ex: 1, 2, 3)
  - `CONFIRMATION` : Oui/Non uniquement (ex: O, N)
  - `PHONE_NUMBER` : Numéro de téléphone formaté (validation regex)
  - `PERMIT_NUMBER` : Numéro de permis (validation format)
  - `SANITIZED_TEXT` : Texte libre qui sera sanitized par LLM (limite caractères)
  - `SCORE_RATING` : Score numérique 1-5 uniquement
- **allowed_values** : Valeurs acceptées (ex: [1,2,3] pour MENU_CHOICE, [1,2,3,4,5] pour SCORE_RATING)
- **behavior_on_invalid_input** : Comportement déterminé
  - `RETRY_SAME_STATE` : Réafficher le même message avec indication d'erreur (max 3 tentatives)
  - `RETURN_TO_PREVIOUS` : Retourner à l'état précédent
  - `RETURN_TO_MAIN_MENU` : Retour au menu principal (après 3 erreurs consécutives)

**Objectif** : Empêcher toute entrée texte libre non contrôlée, rendre la validation déterministe, éviter les états ambigus.

---

### 1.3 Logique Métier (Couche Business)
**Responsabilité** : Application stricte des règles métier

**Modules principaux** :

#### A. Gestion des Rôles
- Identification du rôle (Owner/Driver)
- Vérification des permissions par action
- Séparation stricte des fonctionnalités par rôle

#### B. Gestion d'Identité
- Création de profil driver
- Hachage du permis (SHA-256 + salt unique)
- Verrouillage d'identité (irréversible)
- Recherche par téléphone ou permis (haché)
- Validation : pas de rating sans identité verrouillée

#### C. Gestion des Collaborations
- Déclaration de collaboration (Owner → Driver)
- Confirmation de collaboration (Driver)
- Transition d'états strictement contrôlée :
  - `DECLARED` → `PENDING_CONFIRMATION`
  - `PENDING_CONFIRMATION` → `CONFIRMED` (après confirmation driver)
  - `CONFIRMED` → `ELIGIBLE_FOR_RATING` (après ≥7 jours)
  - `ELIGIBLE_FOR_RATING` → `RATED` (après notation)
- Validation des durées (≥7 jours consécutifs)
- Un seul rating par collaboration

#### D. Système de Rating
- Validation des conditions d'éligibilité :
  - Collaboration existe et confirmée
  - Durée ≥7 jours
  - Identité driver verrouillée
  - Pas déjà noté
- Collecte des scores (1-5) pour :
  - Ponctualité
  - Respect du véhicule
  - Relation client
  - Fiabilité globale
- Collecte du commentaire texte (brut)

#### E. Traitement LLM (Sanitisation uniquement)
- **Entrée** : Commentaire brut de l'owner
- **Traitement** :
  - Suppression d'insultes
  - Suppression de données personnelles
  - Reformulation en langage neutre et factuel
- **Sortie** : Commentaire nettoyé + aperçu pour validation
- **Validation** : Owner doit approuver avant publication
- **Limites** : LLM ne décide jamais, ne juge jamais, ne publie jamais automatiquement

**Gestion du Rejet de Commentaire Sanitisé (CRITIQUE)** :
Quand l'owner rejette l'aperçu du commentaire sanitisé :
1. **Premier rejet** : Owner peut modifier le commentaire original (retour à saisie texte)
2. **Deuxième rejet** : Owner peut modifier à nouveau (dernière chance)
3. **Troisième rejet** : Deux options proposées :
   - Publier le rating **sans commentaire** (scores uniquement)
   - Annuler le rating complètement
4. **Limite** : Maximum 3 tentatives de sanitisation par rating
5. **Résultat** : Le rating peut être publié avec scores uniquement (pas de commentaire), ce qui est valide et résistant aux abus
6. **Audit** : Tous les rejets sont loggés avec le commentaire original pour traçabilité

#### F. Calcul de Réputation
- **Méthode** : MÉDIANE (pas moyenne)
- **Pondération** :
  - Collaborations récentes pèsent plus
  - Collaborations longues pèsent plus
- **États de réputation** :
  - `NEW` : 0-1 collaboration
  - `ESTABLISHED` : 2-5 collaborations
  - `CONFIRMED` : 5+ collaborations
  - `REBUILDING` : Tendance négative récente
- **Pas de punition permanente** : Système permet la rédemption

**Cache de Réputation - Règles d'Invalidation (CRITIQUE)** :
- **Stockage** : Réputation calculée stockée dans `DriverProfile.reputation_cached` (JSON : score, état, date_calcul)
- **Recalcul** : La réputation est **recalculée uniquement lors d'événements spécifiques**, jamais à la lecture
- **Événements déclenchant le recalcul** :
  1. Nouveau rating publié (collaboration passe à RATED)
  2. Réponse du driver à un rating existant
  3. Confirmation d'une nouvelle collaboration (changement d'état vers CONFIRMED)
- **Invalidation du cache** : Le cache est invalidé (marqué comme stale) mais **pas recalculé immédiatement** sauf si :
  - Un utilisateur demande à voir le profil (recalcul synchrone)
  - Un job asynchrone de maintenance (optionnel, MVP peut l'omettre)
- **Principe** : Éviter les recalculs à chaque lecture, garantir la cohérence lors des mises à jour critiques
- **Affichage** : Si cache stale > 24h, recalculer avant affichage (sécurité)

#### G. Détection d'Abus (Silencieuse)
- Vérification de cohérence des déclarations
- Détection de patterns suspects (multiples déclarations simultanées, etc.)
- Logging pour audit (pas de blocage automatique)

**Patterns Détectés (Explicites)** :
1. **Spam de Collaborations** :
   - Même owner déclare >3 collaborations différentes en <24h
   - Même paire owner-driver déclarée >2 fois en <30 jours
2. **Patterns de Confiance** :
   - Owner déclare toujours les mêmes drivers (potentiel réseau fermé)
   - Driver confirme systématiquement sans délai (potentiel automatisation)
3. **Comportement** :
   - Détection **silencieuse** : Aucune notification à l'utilisateur
   - **Pas de blocage automatique** : Les actions restent possibles
   - **Logging uniquement** : Enregistrement dans OwnerProfile.flags_abus pour audit
   - **Pas d'impact sur réputation** : La détection n'affecte pas le calcul de réputation ni l'affichage
   - **Non-punitif et non-visible** : L'utilisateur ne voit jamais ces flags

---

### 1.4 Couche Données (Persistance)
**Responsabilité** : Stockage sécurisé et structuré

**Entités principales** :

#### A. User (Utilisateur)
- Numéro WhatsApp (identifiant principal)
- Rôle (Owner/Driver)
- Date de création
- Dernière activité

#### B. OwnerProfile
- Référence au User (relation 1-to-1)
- Nombre de collaborations déclarées (compteur)
- Dernière déclaration (timestamp)
- Flags d'abus détectés (JSON : patterns suspects, silencieux, non-bloquant)
- **Usage MVP** :
  - Détection de patterns d'abus (spam de déclarations)
  - Audit et analyse de confiance
  - Traçabilité des actions owner
- **Champs minimaux** : Pas de gestion de véhicules en MVP

#### C. DriverProfile
- Référence au User
- Nom (optionnel, pour affichage)
- Permis haché (SHA-256 + salt)
- Salt unique (stocké séparément)
- Identité verrouillée (booléen)
- Date de verrouillage
- Réputation calculée (cache)

#### D. Collaboration
- Owner (référence User)
- Driver (référence User)
- État (DECLARED, PENDING_CONFIRMATION, CONFIRMED, ELIGIBLE_FOR_RATING, RATED)
- Date de déclaration
- Date de confirmation
- Date de début
- Date de fin
- Durée calculée (jours)
- Rating associé (optionnel, une fois noté)

#### E. Rating
- Collaboration (référence unique)
- Scores (ponctualité, respect véhicule, relation client, fiabilité)
- Commentaire brut (stocké pour audit)
- Commentaire nettoyé (après LLM)
- Commentaire publié (après validation owner)
- Date de création
- Réponse du driver (optionnel)
- Date de réponse

#### F. ConversationState
- User (référence)
- État actuel (string : nom du point dans le flux)
- Données temporaires (JSON)
- Dernière mise à jour
- Expiration (timeout)
- **Champs de contrôle d'entrée** :
  - expected_input_type (MENU_CHOICE, CONFIRMATION, etc.)
  - allowed_values (array)
  - invalid_input_count (compteur pour gestion retry)
  - behavior_on_invalid_input (RETRY_SAME_STATE, RETURN_TO_PREVIOUS, etc.)

#### G. ConsentLog (Traçabilité Légale)
- User (référence)
- Action (string : PROFILE_CREATION, COLLABORATION_CONFIRMATION, RATING_PUBLICATION)
- Timestamp (date/heure exacte)
- Contexte (JSON : collaboration_id, rating_id, etc. selon l'action)
- **Usage** : Enregistrement de tous les consentements explicites pour conformité légale
- **Règle** : Un consentement doit être loggé avant chaque action critique

---

### 1.5 Système de Notifications (Événementiel)
**Responsabilité** : Envoi de notifications uniquement pour événements spécifiques

**Événements déclencheurs** :
1. Invitation driver à créer profil (Owner déclare collaboration avec driver non-inscrit)
2. Demande de confirmation collaboration (Driver doit confirmer)
3. Rappel de rating (7 jours après confirmation)
4. Nouvelle review (Driver reçoit notification)
5. Droit de réponse (Driver peut répondre à une review)

**Principe** : Pas de spam. Le silence est une fonctionnalité.

---

### 1.6 Couche Sécurité
**Responsabilité** : Protection des données sensibles

- **Hachage permis** :
  - SHA-256
  - Salt unique par driver
  - Stockage séparé du salt
  - Jamais d'exposition du permis en clair
- **Recherche par permis** :
  - Hashage de la requête
  - Comparaison avec hash stocké
- **Audit trail** : Logging de toutes les actions critiques
- **Validation d'entrée** : Toutes les entrées utilisateur validées avant traitement

---

## 2. FLUX DE DONNÉES

### 2.1 Message Entrant
```
WhatsApp API → Interface WhatsApp → Validation format
→ Gestion État → Logique Métier → Base de Données
→ Réponse formatée → Interface WhatsApp → WhatsApp API
```

### 2.2 Recherche Driver
```
Owner demande recherche → Validation entrée
→ Si téléphone : recherche directe User
→ Si permis : hashage → recherche DriverProfile
→ Vérification identité verrouillée
→ Affichage profil (sans permis)
```

**Règles de Messaging pour Résultats de Recherche (EXPLICITES)** :

Chaque cas de recherche doit afficher un message déterminé :

1. **Profil non trouvé** :
   - Message : "Aucun profil trouvé pour ce numéro/permis. Le chauffeur peut créer un profil en s'inscrivant."
   - Actions : Retour menu, Inviter driver (si permis fourni)

2. **Profil trouvé mais identité non verrouillée** :
   - Message : "Profil trouvé mais identité non vérifiée. Le chauffeur doit verrouiller son identité pour recevoir des évaluations."
   - Affichage : Nom uniquement (si disponible), pas de réputation
   - Actions : Voir profil limité, Contacter, Retour

3. **Profil verrouillé mais aucune collaboration confirmée** :
   - Message : "Profil vérifié. Aucune collaboration confirmée pour le moment."
   - Affichage : Nom, État réputation = NEW, Pas de scores
   - Actions : Voir profil, Contacter, Retour

4. **Profil avec historique confirmé** :
   - Message : "Profil vérifié avec [X] collaboration(s) confirmée(s)."
   - Affichage : Nom, État réputation, Score médian, Nombre de collaborations
   - Actions : Voir profil complet, Contacter, Retour

**Principe** : Éviter tout dommage implicite à la réputation, maintenir la neutralité, messages factuels uniquement.

### 2.3 Processus de Rating
```
Owner initie rating → Validation éligibilité
→ Collecte scores (menu, expected_input_type: SCORE_RATING)
→ Collecte commentaire (texte, expected_input_type: SANITIZED_TEXT)
→ Envoi LLM (sanitisation)
→ Aperçu à owner (expected_input_type: CONFIRMATION)
→ Si rejeté : Retry (max 3) ou Publication sans commentaire
→ Si accepté : Publication avec commentaire
→ Enregistrement ConsentLog (RATING_PUBLICATION)
→ Notification driver
```

---

## 3. SÉPARATION DES RESPONSABILITÉS

### Règles strictes :
1. **Interface WhatsApp** : Ne connaît que le formatage de messages
2. **Gestion d'État** : Ne connaît que la navigation conversationnelle
3. **Logique Métier** : Ne connaît pas WhatsApp, seulement les règles business
4. **LLM** : Ne connaît que la sanitisation de texte, jamais les décisions
5. **Base de Données** : Ne contient aucune logique, seulement la persistance

---

## 4. GESTION DES ERREURS

- **Entrée invalide** : Message d'erreur clair + retour au menu précédent
- **Timeout session** : Retour au menu principal
- **Action non autorisée** : Message explicatif + redirection
- **Erreur système** : Message générique + logging détaillé côté serveur

---

## 5. POINTS CRITIQUES DE SÉCURITÉ

1. **Permis** : Hashé à la création, jamais en clair, jamais exposé
2. **Identité** : Verrouillage irréversible, requis pour rating
3. **Collaborations** : États stricts, pas de shortcuts
4. **Ratings** : Validation multi-niveaux avant publication
5. **Commentaires** : Toujours nettoyés par LLM avant affichage, gestion explicite du rejet (max 3 tentatives, possibilité de publier sans commentaire)
6. **Recherche** : Pas d'exposition de données sensibles dans les résultats, messages explicites et neutres
7. **Contrôle d'entrée** : Chaque état définit expected_input_type, allowed_values, behavior_on_invalid_input (pas de texte libre non contrôlé)
8. **Consentement** : ConsentLog enregistre tous les consentements explicites pour traçabilité légale
9. **Cache réputation** : Règles d'invalidation explicites, recalcul uniquement sur événements critiques, jamais à la lecture
10. **Détection d'abus** : Silencieuse, non-bloquante, non-punitive, logging uniquement

---

## 6. ÉVOLUTIVITÉ (Pensée mais non implémentée en MVP)

- Architecture modulaire pour ajout de nouveaux rôles
- Système de plugins pour nouvelles fonctionnalités
- Queue pour notifications asynchrones (actuellement synchrone en MVP)
- Job asynchrone de maintenance pour recalcul de réputation (optionnel en MVP)

---

## 7. CONFORMITÉ LÉGALE

- Traçabilité complète (audit trail)
- **Consentement explicite** : Enregistré dans ConsentLog pour chaque action critique :
  - Création de profil (driver)
  - Confirmation de collaboration (driver)
  - Publication de rating (owner)
- Droit de réponse garanti
- Pas de diffamation possible (sanitisation LLM)
- Pas d'exposition de données sensibles
- Pas de décision automatique (humain toujours en contrôle)
- **ConsentLog** : Chaque consentement enregistré avec user, action, timestamp, contexte pour audit légal

---

## PROCHAINES ÉTAPES

Après validation de cette architecture :
1. Définition des modèles de données détaillés
2. Définition des états et transitions
3. Design des flux conversationnels exacts
4. Implémentation incrémentale
