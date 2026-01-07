# Modèles de Données, États et Transitions - WhatsApp Driver Verification Bot

## 1. MODÈLES DE DONNÉES

### 1.1 User (Utilisateur)
**Description** : Entité centrale représentant un utilisateur du système (Owner ou Driver)

**Champs** :
- `id` : Identifiant unique (UUID ou auto-increment)
- `phone_number` : Numéro WhatsApp (string, unique, indexé)
  - Format : E.164 (ex: +221771234567)
  - Validation : Regex strict, pas de doublons
- `role` : Rôle utilisateur (enum : OWNER, DRIVER)
  - Un utilisateur peut avoir un seul rôle à la fois
  - Peut être modifié (rare, mais possible)
- `created_at` : Date de création (timestamp)
- `last_activity` : Dernière activité (timestamp, mis à jour à chaque interaction)
- `is_active` : Statut actif (booléen, par défaut True)

**Contraintes** :
- `phone_number` : UNIQUE, NOT NULL, indexé pour recherche rapide
- `role` : NOT NULL, valeurs limitées à OWNER/DRIVER
- Un User peut avoir soit un OwnerProfile, soit un DriverProfile (pas les deux)

**Relations** :
- 1-to-1 avec OwnerProfile (si role=OWNER)
- 1-to-1 avec DriverProfile (si role=DRIVER)
- 1-to-many avec ConversationState
- 1-to-many avec ConsentLog
- Many-to-many avec Collaboration (via owner ou driver)

---

### 1.2 OwnerProfile
**Description** : Profil spécifique aux propriétaires de véhicules

**Champs** :
- `id` : Identifiant unique
- `user_id` : Référence User (Foreign Key, UNIQUE, NOT NULL)
- `collaborations_declared_count` : Nombre de collaborations déclarées (integer, défaut 0)
- `last_declaration_at` : Timestamp de la dernière déclaration (timestamp, nullable)
- `abuse_flags` : Flags d'abus détectés (JSON, défaut {})
  - Structure : `{"spam_collaborations": false, "repeated_pairs": false, "trust_patterns": []}`
  - Détection silencieuse uniquement
- `created_at` : Date de création
- `updated_at` : Date de mise à jour

**Contraintes** :
- `user_id` : UNIQUE, NOT NULL, Foreign Key vers User
- Un User avec role=OWNER doit avoir exactement un OwnerProfile
- `abuse_flags` : JSON valide, jamais exposé à l'utilisateur

**Relations** :
- Many-to-many avec Collaboration (en tant qu'owner)

---

### 1.3 DriverProfile
**Description** : Profil spécifique aux chauffeurs avec identité vérifiée

**Champs** :
- `id` : Identifiant unique
- `user_id` : Référence User (Foreign Key, UNIQUE, NOT NULL)
- `display_name` : Nom d'affichage (string, nullable, max 100 caractères)
  - Optionnel, peut être ajouté plus tard
- `permit_hash` : Hash du numéro de permis (string, NOT NULL, indexé)
  - SHA-256 du permis normalisé + salt unique
  - Format : hexadécimal 64 caractères
  - Jamais stocké en clair, jamais exposé
- `permit_salt` : Salt unique pour le hachage (string, NOT NULL)
  - Généré aléatoirement à la création
  - Stocké séparément pour sécurité
  - Jamais exposé dans logs ou réponses
- `identity_locked` : Identité verrouillée (booléen, défaut False)
  - Une fois True, ne peut jamais revenir à False
- `identity_locked_at` : Date de verrouillage (timestamp, nullable)
- `reputation_cached` : Cache de réputation (JSON, nullable)
  - Structure : `{"score_median": 4.5, "state": "ESTABLISHED", "calculated_at": "2024-01-01T12:00:00Z", "is_stale": false}`
  - `score_median` : Médiane des scores (float, 1.0-5.0)
  - `state` : État réputation (NEW, ESTABLISHED, CONFIRMED, REBUILDING)
  - `calculated_at` : Timestamp du calcul
  - `is_stale` : Indicateur si cache doit être recalculé
- `created_at` : Date de création
- `updated_at` : Date de mise à jour

**Contraintes** :
- `user_id` : UNIQUE, NOT NULL, Foreign Key vers User
- Un User avec role=DRIVER doit avoir exactement un DriverProfile
- `permit_hash` : UNIQUE, NOT NULL (un permis = un profil)
- `permit_salt` : NOT NULL, jamais exposé
- `identity_locked` : Une fois True, immutable
- `permit_hash` + `permit_salt` : Index composite pour recherche rapide

**Relations** :
- Many-to-many avec Collaboration (en tant que driver)

---

### 1.4 Collaboration
**Description** : Relation de travail entre Owner et Driver

**Champs** :
- `id` : Identifiant unique
- `owner_id` : Référence User (Foreign Key, NOT NULL)
- `driver_id` : Référence User (Foreign Key, NOT NULL)
- `state` : État de la collaboration (enum, NOT NULL)
  - Valeurs : DECLARED, PENDING_CONFIRMATION, CONFIRMED, ELIGIBLE_FOR_RATING, RATED
- `declared_at` : Date de déclaration par owner (timestamp, NOT NULL)
- `confirmed_at` : Date de confirmation par driver (timestamp, nullable)
- `start_date` : Date de début de collaboration (date, nullable)
  - Saisie par owner lors de la déclaration
- `end_date` : Date de fin de collaboration (date, nullable)
  - Saisie par owner lors de la déclaration
- `duration_days` : Durée calculée en jours (integer, nullable)
  - Calculé : end_date - start_date (inclusif)
  - Doit être ≥ 7 pour être éligible au rating
- `rating_id` : Référence Rating (Foreign Key, nullable, UNIQUE)
  - Une collaboration ne peut avoir qu'un seul rating
- `created_at` : Date de création
- `updated_at` : Date de mise à jour

**Contraintes** :
- `owner_id` ≠ `driver_id` (un owner ne peut pas être son propre driver)
- `state` : NOT NULL, valeurs limitées
- `declared_at` : NOT NULL
- `start_date` ≤ `end_date` (validation logique)
- `duration_days` : Calculé automatiquement, ≥ 7 pour ELIGIBLE_FOR_RATING
- `rating_id` : UNIQUE (une collaboration = un rating max)
- Index composite sur (owner_id, driver_id) pour détection de doublons

**Relations** :
- Many-to-1 avec User (owner)
- Many-to-1 avec User (driver)
- 1-to-1 avec Rating (optionnel)

---

### 1.5 Rating
**Description** : Évaluation d'une collaboration par l'owner

**Champs** :
- `id` : Identifiant unique
- `collaboration_id` : Référence Collaboration (Foreign Key, UNIQUE, NOT NULL)
- `score_punctuality` : Score ponctualité (integer, NOT NULL)
  - Valeurs : 1, 2, 3, 4, 5
- `score_vehicle_respect` : Score respect véhicule (integer, NOT NULL)
  - Valeurs : 1, 2, 3, 4, 5
- `score_client_relation` : Score relation client (integer, NOT NULL)
  - Valeurs : 1, 2, 3, 4, 5
- `score_reliability` : Score fiabilité globale (integer, NOT NULL)
  - Valeurs : 1, 2, 3, 4, 5
- `comment_raw` : Commentaire brut original (text, nullable)
  - Stocké pour audit, jamais exposé publiquement
- `comment_sanitized` : Commentaire nettoyé par LLM (text, nullable)
  - Version après sanitisation LLM
- `comment_published` : Commentaire publié (text, nullable)
  - Version finale après validation owner
  - Peut être NULL si owner a rejeté 3 fois (rating sans commentaire)
- `sanitization_rejections_count` : Nombre de rejets de sanitisation (integer, défaut 0)
  - Max 3, après quoi options : publier sans commentaire ou annuler
- `is_published` : Statut de publication (booléen, défaut False)
  - True = visible dans le profil driver
- `driver_response` : Réponse du driver (text, nullable)
  - Optionnel, peut être ajouté après publication
- `driver_response_at` : Date de réponse (timestamp, nullable)
- `created_at` : Date de création
- `updated_at` : Date de mise à jour
- `published_at` : Date de publication (timestamp, nullable)

**Contraintes** :
- `collaboration_id` : UNIQUE, NOT NULL, Foreign Key vers Collaboration
- Tous les scores : NOT NULL, valeurs 1-5 uniquement
- `sanitization_rejections_count` : ≤ 3
- `is_published` : Si True, alors `published_at` NOT NULL
- `comment_published` : Peut être NULL même si `is_published` = True (rating sans commentaire valide)

**Relations** :
- 1-to-1 avec Collaboration

---

### 1.6 ConversationState
**Description** : État de conversation WhatsApp pour chaque utilisateur

**Champs** :
- `id` : Identifiant unique
- `user_id` : Référence User (Foreign Key, NOT NULL)
- `current_state` : État actuel dans le flux (string, NOT NULL)
  - Exemples : "MAIN_MENU", "OWNER_VERIFY_DRIVER", "DRIVER_CREATE_PROFILE", "RATING_COLLECT_SCORES", etc.
- `temp_data` : Données temporaires (JSON, défaut {})
  - Stocke les valeurs collectées pendant un processus multi-étapes
  - Exemple : `{"driver_phone": "+221771234567", "scores": {"punctuality": 4}}`
- `expected_input_type` : Type d'entrée attendu (enum, NOT NULL)
  - Valeurs : MENU_CHOICE, CONFIRMATION, PHONE_NUMBER, PERMIT_NUMBER, SANITIZED_TEXT, SCORE_RATING
- `allowed_values` : Valeurs acceptées (JSON array, nullable)
  - Exemple : [1, 2, 3] pour MENU_CHOICE, [1, 2, 3, 4, 5] pour SCORE_RATING
  - NULL si aucune restriction (ex: SANITIZED_TEXT avec validation regex)
- `invalid_input_count` : Compteur d'entrées invalides (integer, défaut 0)
  - Réinitialisé à 0 après entrée valide
  - Max 3 avant retour au menu principal
- `behavior_on_invalid_input` : Comportement sur entrée invalide (enum, NOT NULL)
  - Valeurs : RETRY_SAME_STATE, RETURN_TO_PREVIOUS, RETURN_TO_MAIN_MENU
- `last_updated` : Dernière mise à jour (timestamp, NOT NULL)
- `expires_at` : Date d'expiration (timestamp, NOT NULL)
  - Calculé : last_updated + timeout (ex: 30 minutes)
  - Si expiré, retour automatique au menu principal

**Contraintes** :
- `user_id` : NOT NULL, Foreign Key vers User
- `current_state` : NOT NULL, valeurs prédéfinies
- `expected_input_type` : NOT NULL, valeurs limitées
- `invalid_input_count` : ≤ 3
- `expires_at` > `last_updated`
- Index sur `user_id` pour recherche rapide
- Index sur `expires_at` pour nettoyage automatique des sessions expirées

**Relations** :
- Many-to-1 avec User

---

### 1.7 ConsentLog
**Description** : Journal de tous les consentements explicites pour conformité légale

**Champs** :
- `id` : Identifiant unique
- `user_id` : Référence User (Foreign Key, NOT NULL)
- `action` : Action consentie (enum, NOT NULL)
  - Valeurs : PROFILE_CREATION, COLLABORATION_CONFIRMATION, RATING_PUBLICATION
- `context` : Contexte de l'action (JSON, nullable)
  - Structure variable selon l'action
  - Exemple : `{"collaboration_id": 123}` pour COLLABORATION_CONFIRMATION
  - Exemple : `{"rating_id": 456}` pour RATING_PUBLICATION
- `timestamp` : Date/heure du consentement (timestamp, NOT NULL)
  - Précision : millisecondes pour traçabilité exacte

**Contraintes** :
- `user_id` : NOT NULL, Foreign Key vers User
- `action` : NOT NULL, valeurs limitées
- `timestamp` : NOT NULL, indexé pour recherche temporelle
- Index composite sur (user_id, action, timestamp) pour audit

**Relations** :
- Many-to-1 avec User

---

## 2. ÉTATS ET TRANSITIONS

### 2.1 États de Collaboration

**États possibles** :
1. `DECLARED` : Collaboration déclarée par owner, en attente de confirmation driver
2. `PENDING_CONFIRMATION` : Notification envoyée au driver, attente de sa confirmation
3. `CONFIRMED` : Collaboration confirmée par driver, en cours ou terminée
4. `ELIGIBLE_FOR_RATING` : Collaboration éligible pour rating (≥7 jours, confirmée)
5. `RATED` : Collaboration notée, processus terminé

**Transitions autorisées** :

```
DECLARED
  ↓ (driver reçoit notification)
PENDING_CONFIRMATION
  ↓ (driver confirme)
CONFIRMED
  ↓ (≥7 jours après confirmation)
ELIGIBLE_FOR_RATING
  ↓ (owner publie rating)
RATED
```

**Règles de transition** :

1. **DECLARED → PENDING_CONFIRMATION** :
   - Automatique après déclaration
   - Notification envoyée au driver
   - Pas de validation supplémentaire

2. **PENDING_CONFIRMATION → CONFIRMED** :
   - Driver doit confirmer explicitement
   - ConsentLog enregistré (COLLABORATION_CONFIRMATION)
   - `confirmed_at` mis à jour
   - `duration_days` calculé et validé (≥7 requis pour transition suivante)

3. **CONFIRMED → ELIGIBLE_FOR_RATING** :
   - Automatique après ≥7 jours consécutifs
   - Vérification : `duration_days ≥ 7`
   - Vérification : `identity_locked = True` (driver)
   - Notification de rappel envoyée à owner

4. **ELIGIBLE_FOR_RATING → RATED** :
   - Owner publie un rating
   - Rating créé et lié à la collaboration
   - `rating_id` mis à jour
   - `is_published = True` dans Rating
   - ConsentLog enregistré (RATING_PUBLICATION)

**Transitions interdites** :
- Aucun retour en arrière (pas de DECLARED après CONFIRMED)
- Pas de saut d'états (pas de DECLARED → CONFIRMED directement)
- Pas de modification après RATED

---

### 2.2 États de Conversation (Flux WhatsApp)

**États principaux** :

#### A. États Communs
- `INITIAL` : Première interaction, pas encore de rôle défini
- `MAIN_MENU` : Menu principal selon le rôle
- `SESSION_EXPIRED` : Session expirée, retour au menu

#### B. États Owner
- `OWNER_VERIFY_DRIVER` : Recherche de driver
- `OWNER_SEARCH_BY_PHONE` : Saisie numéro téléphone
- `OWNER_SEARCH_BY_PERMIT` : Saisie numéro permis
- `OWNER_VIEW_PROFILE` : Affichage profil driver
- `OWNER_DECLARE_COLLABORATION` : Déclaration collaboration
- `OWNER_RATE_COLLABORATION` : Processus de rating
- `OWNER_RATE_COLLECT_SCORES` : Collecte des scores
- `OWNER_RATE_COLLECT_COMMENT` : Collecte commentaire
- `OWNER_RATE_REVIEW_SANITIZED` : Aperçu commentaire sanitisé
- `OWNER_RATE_CONFIRM_PUBLICATION` : Confirmation publication

#### C. États Driver
- `DRIVER_CREATE_PROFILE` : Création profil
- `DRIVER_ENTER_PERMIT` : Saisie permis
- `DRIVER_LOCK_IDENTITY` : Confirmation verrouillage identité
- `DRIVER_VIEW_REPUTATION` : Affichage réputation
- `DRIVER_CONFIRM_COLLABORATION` : Confirmation collaboration
- `DRIVER_RESPOND_TO_REVIEW` : Réponse à une review

**Transitions principales** :

```
INITIAL
  ↓ (sélection rôle)
MAIN_MENU
  ↓ (choix action)
[État spécifique selon action]
  ↓ (action terminée ou timeout)
MAIN_MENU
```

**Règles de transition** :
- Toute entrée invalide → `invalid_input_count++`
- Si `invalid_input_count ≥ 3` → `RETURN_TO_MAIN_MENU`
- Si `expires_at < now()` → `SESSION_EXPIRED` → `MAIN_MENU`
- Après action réussie → retour `MAIN_MENU` (sauf processus multi-étapes)

---

### 2.3 États de Réputation (DriverProfile)

**États possibles** :
1. `NEW` : 0-1 collaboration confirmée
2. `ESTABLISHED` : 2-5 collaborations confirmées
3. `CONFIRMED` : 5+ collaborations confirmées
4. `REBUILDING` : Tendance négative récente (détection automatique)

**Calcul de l'état** :
- Basé sur le nombre de collaborations `CONFIRMED` ou `RATED`
- `REBUILDING` : Si dernière collaboration (≤30 jours) a un score médian < 3.0
- Transition automatique lors du recalcul de réputation

**Pas de transitions manuelles** : États calculés, pas de modification directe

---

## 3. CONTRAINTES ET VALIDATIONS

### 3.1 Contraintes d'Intégrité

**Base de données** :
- Toutes les Foreign Keys avec `ON DELETE CASCADE` ou `ON DELETE RESTRICT` selon logique métier
- Unicité : `phone_number` (User), `permit_hash` (DriverProfile), `collaboration_id` (Rating)
- Index sur toutes les clés de recherche fréquente

**Logique métier** :
- Un User ne peut pas être owner et driver de la même collaboration
- Un Rating ne peut être créé que si collaboration est `ELIGIBLE_FOR_RATING`
- Un Rating ne peut être publié que si `identity_locked = True` (driver)

---

### 3.2 Contraintes de Sécurité

**Permis (Côte d'Ivoire - MVP)** :
- **Normalisation obligatoire** avant traitement :
  1. Convertir en majuscules (A-Z)
  2. Supprimer espaces et tirets
  3. Valider longueur finale : 8-15 caractères
  4. Valider format : alphanumérique uniquement (A-Z, 0-9)
- **Hashage** : SHA-256(permis_normalisé + salt_unique)
- **Stockage** : Uniquement permit_hash et permit_salt, jamais le permis en clair
- **Salt** : Jamais exposé dans les logs ou réponses API
- **Recherche** : Normalisation + hashage de la requête avant comparaison, correspondance exacte uniquement
- **Pas de validation externe** : Aucune vérification auprès d'autorités en MVP
- **Affichage** : Toujours masqué (2 premiers caractères + 2-3 derniers, ex: CI****567)
- **Jamais stocké en clair** : Même temporairement dans ConversationState.temp_data, utiliser version normalisée uniquement

**Identité** :
- Verrouillage irréversible : `identity_locked` ne peut jamais passer de True à False
- Permis devient immuable après verrouillage : `permit_hash` et `permit_salt` ne peuvent plus être modifiés
- Pas de rating possible sans identité verrouillée
- Pas de modification du permis après verrouillage

**Données sensibles** :
- `comment_raw` : Jamais exposé publiquement, audit uniquement
- `abuse_flags` : Jamais exposé à l'utilisateur
- `permit_salt` : Jamais exposé

---

### 3.3 Contraintes de Validation

**Entrées utilisateur** :
- `phone_number` : Format E.164 strict (regex)
- `permit_number` : Format permis ivoirien (Côte d'Ivoire - MVP)
  - **Normalisation obligatoire** :
    1. Convertir en majuscules (A-Z)
    2. Supprimer espaces et tirets
    3. Vérifier longueur finale : 8-15 caractères
    4. Vérifier format : alphanumérique uniquement (A-Z, 0-9)
  - **Validation** : Format flexible, pas de regex strict pays-spécifique
  - **Sécurité** : Jamais stocké en clair, hashage SHA-256 + salt unique
  - **Immutable** : Permis devient immuable après verrouillage identité
  - **Affichage** : Toujours masqué (ex: CI****567 - 2 premiers + 2-3 derniers)
  - **Recherche** : Normalisation + hashage de la requête, correspondance exacte uniquement
- `scores` : Uniquement 1, 2, 3, 4, 5
- `menu_choice` : Uniquement valeurs dans `allowed_values`
- `comment` : Limite de caractères (ex: 500), validation après sanitisation

**Dates** :
- `start_date` ≤ `end_date`
- `duration_days` calculé automatiquement (inclusif)
- `duration_days` ≥ 7 pour éligibilité rating

**Sessions** :
- `expires_at` : Timeout 30 minutes (configurable)
- Nettoyage automatique des sessions expirées (job asynchrone)

---

### 3.4 Contraintes Anti-Abus

**Collaborations** :
- Détection de doublons : même owner-driver dans <30 jours
- Détection de spam : >3 déclarations owner en <24h
- Flags silencieux dans `OwnerProfile.abuse_flags`
- Pas de blocage automatique

**Ratings** :
- Un seul rating par collaboration
- Rating possible uniquement si collaboration confirmée et ≥7 jours
- Sanitisation obligatoire du commentaire (sauf rejet 3 fois)
- Max 3 tentatives de sanitisation

**Réputation** :
- Calcul basé sur médiane (pas moyenne) pour résistance aux outliers
- Pas de punition permanente (système permet rédemption)
- Cache invalidé mais pas recalculé à chaque lecture

---

## 4. RÈGLES DE CALCUL

### 4.1 Calcul de Réputation

**Méthode** : Médiane des scores globaux (moyenne des 4 critères par rating)

**Pondération** :
- Collaborations récentes (≤90 jours) : poids ×1.5
- Collaborations longues (≥30 jours) : poids ×1.3
- Collaborations normales : poids ×1.0

**Formule** :
1. Pour chaque rating : `score_global = (punctuality + vehicle_respect + client_relation + reliability) / 4`
2. Appliquer pondération selon récence et durée
3. Calculer médiane des scores pondérés
4. Déterminer état selon nombre de collaborations

**États** :
- `NEW` : 0-1 collaboration
- `ESTABLISHED` : 2-5 collaborations
- `CONFIRMED` : 5+ collaborations
- `REBUILDING` : Si dernière collaboration (≤30 jours) a score < 3.0

**Recalcul** :
- Événements déclencheurs : nouveau rating publié, réponse driver, nouvelle collaboration confirmée
- Jamais à la lecture
- Si cache stale > 24h, recalculer avant affichage

---

### 4.2 Calcul de Durée

**Formule** : `duration_days = (end_date - start_date) + 1` (inclusif)

**Validation** :
- `duration_days` ≥ 7 pour transition vers `ELIGIBLE_FOR_RATING`
- Calculé automatiquement à la confirmation de collaboration

---

### 4.3 Gestion des Timeouts

**Session** :
- Timeout : 30 minutes d'inactivité
- `expires_at = last_updated + 30 minutes`
- Si expiré : retour automatique à `MAIN_MENU`

**Notification** :
- Confirmation collaboration : rappel après 24h si non confirmée
- Rating : rappel après 7 jours si collaboration éligible

---

## 5. INDEX ET PERFORMANCE

### 5.1 Index Requis

**User** :
- `phone_number` : UNIQUE INDEX (recherche rapide)

**DriverProfile** :
- `permit_hash` : UNIQUE INDEX
- `user_id` : UNIQUE INDEX
- `identity_locked` : INDEX (filtrage)

**Collaboration** :
- `(owner_id, driver_id)` : INDEX composite (détection doublons)
- `state` : INDEX (filtrage par état)
- `driver_id, state` : INDEX composite (recherche collaborations driver)

**Rating** :
- `collaboration_id` : UNIQUE INDEX
- `is_published` : INDEX (filtrage)

**ConversationState** :
- `user_id` : INDEX (recherche session)
- `expires_at` : INDEX (nettoyage automatique)

**ConsentLog** :
- `(user_id, action, timestamp)` : INDEX composite (audit)

---

## 6. RÈGLES MÉTIER CRITIQUES

### 6.1 Règles Non-Négociables

1. **Permis jamais exposé** : Hash uniquement, salt séparé, jamais en clair
2. **Identité verrouillée** : Irréversible, requis pour rating
3. **Collaborations** : États stricts, pas de shortcuts
4. **Ratings** : Un seul par collaboration, validation multi-niveaux
5. **Commentaires** : Sanitisation obligatoire (sauf rejet 3 fois)
6. **Réputation** : Médiane, pas moyenne, recalcul sur événements uniquement
7. **Consentement** : ConsentLog pour chaque action critique
8. **Contrôle d'entrée** : Chaque état définit expected_input_type et allowed_values

---

### 6.2 Règles de Détection d'Abus

**Patterns détectés** :
- Spam collaborations : >3 déclarations owner en <24h
- Paires répétées : même owner-driver >2 fois en <30 jours
- Patterns de confiance : owner déclare toujours mêmes drivers

**Comportement** :
- Détection silencieuse
- Logging dans `OwnerProfile.abuse_flags`
- Pas de blocage automatique
- Pas d'impact sur réputation
- Non-visible pour l'utilisateur

---

## PROCHAINES ÉTAPES

Après validation de ce document :
1. Design des flux conversationnels exacts (message par message)
2. Implémentation incrémentale
