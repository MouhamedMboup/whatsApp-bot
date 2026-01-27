# Flux Conversationnels Bot (WhatsApp / Telegram) — Flow “Trust” (Propriétaires Yango)

## Vue d'Ensemble

Ce document définit **exactement** chaque message du bot envoyé et reçu (WhatsApp + Telegram), avec les choix proposés, les validations, et les transitions d'état. Tous les messages sont en français.

**Principes** :
- Un message = une seule question
- Toujours proposer des choix numérotés
- Validation stricte de chaque entrée
- Gestion explicite des erreurs
- Timeout = retour au menu principal

**Notes multi-canaux (implémenté)** :
- **Telegram** :
  - `/start` réinitialise la session et affiche toujours le message de bienvenue.
  - Le bot ne reçoit pas automatiquement le numéro de téléphone Telegram → le driver doit saisir son **numéro E.164** (stocké dans `User.contact_phone_number`).
  - Identifiant interne du user : `tg:<chat_id>`.
- **WhatsApp** :
  - Identifiant interne du user : `+<E.164>` (ex: `+221771234567`).
  - Le numéro WhatsApp est aussi stocké comme `contact_phone_number` (si absent) pour permettre la recherche “par téléphone”.

---

## 1. MESSAGE D’ENTRÉE (MENU PRINCIPAL)

### 1.1 Menu principal (Trust-focused)

**Déclencheur** :
- Premier message reçu
- **Telegram** : `/start` (même si l'utilisateur est déjà enregistré) → reset et retour au menu principal

**Message envoyé** :
```
Bienvenue.
Ce service est réservé aux propriétaires de véhicules Yango.

Il permet de :
1️⃣ Vérifier la réputation d’un chauffeur
2️⃣ Signaler / évaluer un chauffeur
3️⃣ Comprendre le fonctionnement
```

**État ConversationState** :
- `current_state` : "INITIAL"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `expires_at` : now() + 30 minutes

**Actions possibles** :
- **1** → Flow 1 : Vérifier un chauffeur (par permis) → `VERIFY_ENTER_PERMIT`
- **2** → Flow 2 : Signaler / évaluer → `REPORT_CONFIRM_OWNER`
- **3** → Flow 3 : Comprendre le fonctionnement → `INFO_SYSTEM`

**Gestion erreur** :
- Entrée invalide → Réafficher message avec : "❌ Choix invalide. Veuillez répondre 1, 2 ou 3."
- Après 3 erreurs → Retour au message de bienvenue

---

## 2. FLOW 1 — 🔍 Vérifier un chauffeur (Reflex / Benchmark)

### 2.1 Step 1 — Strong Identification (Permis)

**Message envoyé** :
```
Entrez le numéro de permis de conduire du chauffeur.

0️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "VERIFY_ENTER_PERMIT"
- `expected_input_type` : PERMIT_NUMBER
- `allowed_values` : null (validation format)
- `behavior_on_invalid_input` : RETRY_SAME_STATE (max 3 puis retour menu)

### 2.2 Step 2 — Synthesized Reputation Result

**Message envoyé** (format) :
```
Chauffeur identifié
Nom : {driver_name}
Permis : **{masked_permit}**

Statut actuel : {🟢 FIABLE | 🟠 À SURVEILLER | 🔴 DÉCONSEILLÉ}
Basé sur {N} propriétaire(s) distinct(s)

Motifs les plus signalés :
– {reason_1}
– {reason_2}

Dernier signalement : {relative_time}

Options :
0️⃣ Changer le permis
1️⃣ Voir plus de détails
2️⃣ Signaler ce chauffeur
3️⃣ Revenir au menu
```

**État ConversationState** :
- `current_state` : "VERIFY_RESULT"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]

### 2.3 Step 3 — Details (Optional, Dissuasion-Oriented)

**Message envoyé** :
```
Historique résumé :
🔴 Incidents graves : {count}
🟠 Alertes sérieuses : {count}
🟢 Collaborations satisfaisantes : {count}

Le statut est maintenu jusqu’à réévaluation.

Options :
0️⃣ Changer le permis
1️⃣ Voir plus de détails
2️⃣ Signaler ce chauffeur
3️⃣ Revenir au menu
```

**État ConversationState** :
- `current_state` : "VERIFY_DETAILS"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]

---

## 3. FLOW 2 — ⚠️ Signaler / Évaluer un chauffeur (Anti-abuse)

### 3.1 Step 1 — Owner Confirmation

**Message envoyé** :
```
Confirmez-vous être propriétaire d’un véhicule confié à ce chauffeur ?
1️⃣ Oui
2️⃣ Non
```

**État ConversationState** :
- `current_state` : "REPORT_CONFIRM_OWNER"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]

### 3.2 Step 2 — Driver Permit

**Message envoyé** :
```
Entrez le numéro de permis du chauffeur concerné.

0️⃣ Retour
```

**État ConversationState** :
- `current_state` : "REPORT_ENTER_PERMIT"
- `expected_input_type` : PERMIT_NUMBER

### 3.3 Step 3 — Phone Number Enrichment

**Message envoyé** :
```
Entrez un numéro de téléphone utilisé par ce chauffeur (format +225…).

0️⃣ Retour
```

**État ConversationState** :
- `current_state` : "REPORT_ENTER_PHONE"
- `expected_input_type` : PHONE_NUMBER

### 3.4 Step 4 — Identity Confirmation

**Message envoyé** :
```
Chauffeur identifié :
Nom : {driver_name}
Permis : **{masked_permit}**
Téléphone déclaré : {masked_phone}

Confirmez-vous ?
0️⃣ Retour
1️⃣ Oui
2️⃣ Non
```

**État ConversationState** :
- `current_state` : "REPORT_CONFIRM_IDENTITY"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]

### 3.5 Step 5 — Collaboration Duration

**Message envoyé** :
```
Combien de temps ce chauffeur a-t-il travaillé avec vous ?
0️⃣ Retour
1️⃣ Moins d’1 mois
2️⃣ 1 à 3 mois
3️⃣ Plus de 3 mois
```

**État ConversationState** :
- `current_state` : "REPORT_DURATION"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]

### 3.6 Step 6 — Main Problem

**Message envoyé** :
```
Quel problème principal avez-vous rencontré ?
0️⃣ Retour
1️⃣ Versements journaliers irréguliers
2️⃣ Mensonges / manque de transparence
3️⃣ Mauvaise gestion du véhicule
4️⃣ Refus de consignes
5️⃣ Problèmes avec la police
6️⃣ Abandon / injoignable
7️⃣ Autre
```

**État ConversationState** :
- `current_state` : "REPORT_MAIN_PROBLEM"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1..7]

**Si Autre (7)** :
```
Décrivez en une seule phrase (courte) :

0️⃣ Retour
```

**État** :
- `current_state` : "REPORT_OTHER_TEXT"
- `expected_input_type` : SANITIZED_TEXT (max 140 chars)

### 3.7 Step 7 — Severity

**Message envoyé** :
```
Ce problème était :
0️⃣ Retour
1️⃣ Mineur
2️⃣ Sérieux
3️⃣ Grave
```

**État** :
- `current_state` : "REPORT_SEVERITY"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]

### 3.8 Step 8 — Final Recommendation

**Message envoyé** :
```
Recommanderiez-vous ce chauffeur à un autre propriétaire ?

⚠️ Un “Non” a un poids négatif important dans le calcul.

1️⃣ Oui
2️⃣ Non

0️⃣ Retour
```

**État** :
- `current_state` : "REPORT_RECOMMEND"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]

### 3.9 Step 9 — Legal / Moral Confirmation

**Message envoyé** :
```
Confirmez-vous que ces informations sont exactes ?
0️⃣ Retour
1️⃣ Oui, je confirme
2️⃣ Annuler
```

**État** :
- `current_state` : "REPORT_CONFIRM_LEGAL"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]

### 3.10 Step 10 — Closure

**Message envoyé** :
```
Merci.
Votre signalement a été enregistré.
Il contribue à protéger les propriétaires et à responsabiliser les chauffeurs.
```

Retour automatique au menu principal.

---

## 4. FLOW 3 — ℹ️ Comprendre le système (Controlled Transparency)

**Message envoyé** :
```
Fonctionnement du système :
• Le permis de conduire est l’identifiant principal
• Les numéros de téléphone peuvent changer
• Seuls les propriétaires peuvent signaler
• Les signalements graves pèsent plus que les positifs
• Un chauffeur ne peut pas effacer son historique
• Une réévaluation est possible après une période sans incident

Statuts affichés :
🟢 FIABLE — Aucun incident grave récent, collaborations stables
🟠 À SURVEILLER — Alertes répétées ou comportement instable
🔴 DÉCONSEILLÉ — Problèmes graves signalés par plusieurs propriétaires

1️⃣ Vérifier un chauffeur
2️⃣ Signaler un chauffeur
3️⃣ Quitter

0️⃣ Retour au menu
```

**État** :
- `current_state` : "INFO_SYSTEM"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]

---

> Note: Les anciens flux OWNER/DRIVER (création de profil, collaborations, ratings “classiques”) sont conservés dans le code pour compatibilité,
> mais le **menu d’entrée** a été remplacé par ce flow “Trust”.

**Déclencheur** : Choix "3" dans le message de bienvenue

**Message envoyé** :
```
ℹ️ AIDE

**Pour les propriétaires :**
• Vérifiez un chauffeur avant de l'embaucher
• Déclarez vos collaborations
• Évaluez vos chauffeurs (après 7 jours minimum)

**Pour les chauffeurs :**
• Créez votre profil professionnel
• Verrouillez votre identité avec votre permis
• Consultez votre réputation
• Répondez aux évaluations

**Sécurité :**
• Votre numéro de permis n'est jamais affiché
• Seules les collaborations confirmées comptent
• Les commentaires sont vérifiés avant publication

Retour au menu :
1️⃣ Retour
```

**État ConversationState** :
- `current_state` : "INITIAL"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Action** :
- **1** → Retour au message de bienvenue

---

## 2. FLUX OWNER (Propriétaire)

### 2.1 Menu Principal Owner

**Déclencheur** : User avec role=OWNER, état INITIAL ou retour depuis une action

**Message envoyé** :
```
🏠 MENU PRINCIPAL - PROPRIÉTAIRE

Que souhaitez-vous faire ?

1️⃣ Vérifier un chauffeur
2️⃣ Déclarer une collaboration
3️⃣ Voir mes collaborations
4️⃣ Quitter
```

**État ConversationState** :
- `current_state` : "OWNER_MAIN_MENU"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3, 4]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions possibles** :
- **1** → Transition vers "OWNER_VERIFY_DRIVER"
- **2** → Transition vers "OWNER_DECLARE_COLLABORATION"
- **3** → Afficher liste collaborations, retour menu
- **4** → Message de fin, session terminée

---

### 2.2 Vérifier un Chauffeur - Choix Méthode

**Déclencheur** : Choix "1" dans menu principal owner

**Message envoyé** :
```
🔍 VÉRIFICATION DE CHAUFFEUR

Comment souhaitez-vous rechercher ?

1️⃣ Par numéro de téléphone
2️⃣ Par numéro de permis
3️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "OWNER_VERIFY_DRIVER"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions possibles** :
- **1** → Transition vers "OWNER_SEARCH_BY_PHONE"
- **2** → Transition vers "OWNER_SEARCH_BY_PERMIT"
- **3** → Retour "OWNER_MAIN_MENU"

---

### 2.3 Recherche par Téléphone

**Déclencheur** : Choix "1" dans vérification chauffeur

**Message envoyé** :
```
📱 RECHERCHE PAR TÉLÉPHONE

Entrez le numéro de téléphone du chauffeur.

Format : +221771234567
(Code pays + numéro, sans espaces)

Ou tapez 0 pour annuler.
```

**État ConversationState** :
- `current_state` : "OWNER_SEARCH_BY_PHONE"
- `expected_input_type` : PHONE_NUMBER
- `allowed_values` : null (validation regex)
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {}

**Validation** :
- Format E.164 : `^\+[1-9]\d{1,14}$`
- Si valide → Recherche d'un **driver actif** (role=DRIVER, is_active=True) par :
  - `User.phone_number == <E.164>` (cas WhatsApp)
  - ou `User.contact_phone_number == <E.164>` (cas Telegram)
- Si invalide → Message erreur + retry

**Message erreur** :
```
❌ Format invalide. 

Format attendu : +221771234567
(Code pays + numéro, sans espaces)

Réessayez ou tapez 0 pour annuler.
```

**Actions après validation** :
- **Driver actif trouvé** → Transition vers "OWNER_VIEW_PROFILE_RESULT"
- **Non trouvé** → Message "Profil non trouvé" avec navigation (nouvelle recherche / retour menu)
- **0** → Retour "OWNER_VERIFY_DRIVER"

---

### 2.4 Recherche par Permis

**Déclencheur** : Choix "2" dans vérification chauffeur

**Message envoyé** :
```
🪪 RECHERCHE PAR PERMIS

Entrez le numéro de permis de conduire ivoirien du chauffeur (sans espaces ni tirets).

Exemple : CI1234567

Ou tapez 0 pour annuler.
```

**État ConversationState** :
- `current_state` : "OWNER_SEARCH_BY_PERMIT"
- `expected_input_type` : PERMIT_NUMBER
- `allowed_values` : null (validation format)
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Validation** :
- Normalisation obligatoire :
  1. Convertir en majuscules
  2. Supprimer espaces et tirets
  3. Vérifier longueur finale : 8-15 caractères
  4. Vérifier format : alphanumérique uniquement (A-Z, 0-9)
- Hashage : SHA-256(permis_normalisé + salt) pour chaque DriverProfile
- Recherche par permit_hash (correspondance exacte uniquement)

**Message erreur** :
```
❌ Numéro invalide.

Le permis doit contenir 8 à 15 caractères, lettres et chiffres uniquement.

Réessayez ou tapez 0 pour annuler.
```

**Actions après validation** :
- **DriverProfile trouvé** → Transition vers "OWNER_VIEW_PROFILE_RESULT"
- **DriverProfile non trouvé** → Message "Profil non trouvé", transition vers "OWNER_INVITE_DRIVER"
- **0** → Retour "OWNER_VERIFY_DRIVER"

---

### 2.5 Résultat Recherche - Profil Non Trouvé

**Déclencheur** : Recherche infructueuse (téléphone ou permis)

**Message envoyé** :
```
❌ PROFIL NON TROUVÉ

Aucun profil trouvé pour ce numéro/permis.

Le chauffeur peut créer un profil en s'inscrivant.

Que souhaitez-vous faire ?

1️⃣ Nouvelle recherche
2️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "OWNER_PHONE_NOT_FOUND" (cas recherche par téléphone)
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {"searched_phone": "+221771234567"} (si recherche par téléphone)

**Actions possibles** :
- **1** → Nouvelle recherche (retour "OWNER_SEARCH_BY_PHONE")
- **2** → Retour "OWNER_MAIN_MENU"

**Note (recherche par permis)** :
- Si recherche par permis et profil non trouvé, le bot peut proposer une étape **invitation** via l'état `OWNER_INVITE_DRIVER` (selon les règles internes).

**Message confirmation invitation** :
```
✅ Invitation envoyée au chauffeur.

Il recevra un message pour créer son profil.

Retour au menu principal.
```

---

### 2.6 Résultat Recherche - Affichage Profil

**Déclencheur** : DriverProfile trouvé

**Cas 1 : Identité non verrouillée**

**Message envoyé** :
```
👤 PROFIL TROUVÉ

Nom : [display_name ou "Non renseigné"]
Identité : ⚠️ Non vérifiée

Le chauffeur doit verrouiller son identité pour recevoir des évaluations.

Que souhaitez-vous faire ?

1️⃣ Contacter le chauffeur
2️⃣ Nouvelle recherche
3️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "OWNER_VIEW_PROFILE_NOT_LOCKED"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions possibles** :
- **1** → Afficher numéro téléphone (si autorisé), retour menu
- **2** → Retour "OWNER_VERIFY_DRIVER"
- **3** → Retour "OWNER_MAIN_MENU"

---

**Cas 2 : Identité verrouillée, aucune collaboration**

**Message envoyé** :
```
👤 PROFIL VÉRIFIÉ

Nom : [display_name]
Identité : ✅ Vérifiée
Réputation : Nouveau (0 collaboration confirmée)

Aucune collaboration confirmée pour le moment.

Que souhaitez-vous faire ?

1️⃣ Contacter le chauffeur
2️⃣ Déclarer une collaboration
3️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "OWNER_VIEW_PROFILE_NEW"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions possibles** :
- **1** → Afficher numéro téléphone, retour menu
- **2** → Transition vers "OWNER_DECLARE_COLLABORATION" (avec driver_id pré-rempli)
- **3** → Retour "OWNER_MAIN_MENU"

---

**Cas 3 : Profil avec historique**

**Message envoyé** :
```
👤 PROFIL VÉRIFIÉ

Nom : [display_name]
Identité : ✅ Vérifiée
Réputation : [ESTABLISHED/CONFIRMED/REBUILDING]
Score médian : [X.X]/5
Collaborations : [X] confirmée(s)

Que souhaitez-vous faire ?

1️⃣ Voir le profil complet
2️⃣ Contacter le chauffeur
3️⃣ Déclarer une collaboration
4️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "OWNER_VIEW_PROFILE_WITH_HISTORY"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3, 4]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions possibles** :
- **1** → Transition vers "OWNER_VIEW_FULL_PROFILE"
- **2** → Afficher numéro téléphone, retour menu
- **3** → Transition vers "OWNER_DECLARE_COLLABORATION" (avec driver_id pré-rempli)
- **4** → Retour "OWNER_MAIN_MENU"

---

### 2.7 Profil Complet (avec Reviews)

**Déclencheur** : Choix "1" dans profil avec historique

**Message envoyé** :
```
📊 PROFIL COMPLET

Nom : [display_name]
Identité : ✅ Vérifiée
Réputation : [état]
Score médian : [X.X]/5
Collaborations : [X] confirmée(s)

─── ÉVALUATIONS ───

[Pour chaque Rating publié, afficher :]

⭐ [Score médian]/5
Ponctualité: [X]/5 | Véhicule: [X]/5
Client: [X]/5 | Fiabilité: [X]/5

"[commentaire_published]"

[Réponse driver si présente :]
💬 Réponse : "[driver_response]"

───

1️⃣ Contacter le chauffeur
2️⃣ Déclarer une collaboration
3️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "OWNER_VIEW_FULL_PROFILE"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions possibles** :
- **1** → Afficher numéro téléphone, retour menu
- **2** → Transition vers "OWNER_DECLARE_COLLABORATION" (avec driver_id pré-rempli)
- **3** → Retour "OWNER_MAIN_MENU"

**Note** : Si trop de reviews, paginer (max 5 par message)

---

### 2.8 Déclarer une Collaboration - Sélection Driver

**Déclencheur** : Choix "2" dans menu principal ou depuis profil

**Message envoyé** (si driver_id non pré-rempli) :
```
🤝 DÉCLARER UNE COLLABORATION

Entrez le numéro de téléphone du chauffeur.

Format : +221771234567

Ou tapez 0 pour annuler.
```

**État ConversationState** :
- `current_state` : "OWNER_DECLARE_COLLABORATION"
- `expected_input_type` : PHONE_NUMBER (si driver à saisir) ou MENU_CHOICE (si driver pré-rempli)
- `allowed_values` : null (validation regex) ou [1, 2] (si driver pré-rempli)
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {"driver_id": null ou driver_id si pré-rempli}

**Si driver pré-rempli depuis profil** :
```
🤝 DÉCLARER UNE COLLABORATION

Chauffeur : [display_name]

Confirmer la déclaration ?

1️⃣ Oui, continuer
2️⃣ Non, annuler
```

**Actions** :
- Si driver actif non trouvé → Message "Chauffeur non trouvé. Il doit créer un profil.", retour menu
- Si driver actif trouvé (role=DRIVER, is_active=True, et correspondance sur `phone_number` OU `contact_phone_number`) → Transition vers "OWNER_DECLARE_COLLABORATION_DATES"

---

### 2.9 Déclarer une Collaboration - Dates

**Déclencheur** : Driver identifié

**Message envoyé** :
```
📅 DATES DE COLLABORATION

Entrez la date de début (format JJ/MM/AAAA) :

Exemple : 01/01/2024

Ou tapez 0 pour annuler.
```

**État ConversationState** :
- `current_state` : "OWNER_DECLARE_COLLABORATION_DATES"
- `expected_input_type` : DATE (format JJ/MM/AAAA)
- `allowed_values` : null (validation format)
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {"driver_id": X, "start_date": null, "end_date": null}

**Validation** :
- Format : `^\d{2}/\d{2}/\d{4}$`
- Date valide (pas dans le futur pour début, logique start ≤ end)

**Message erreur** :
```
❌ Format invalide. 

Format attendu : JJ/MM/AAAA
Exemple : 01/01/2024

Réessayez ou tapez 0 pour annuler.
```

**Après validation start_date** :
```
📅 DATES DE COLLABORATION

Date de début : [start_date]

Entrez la date de fin (format JJ/MM/AAAA) :

Exemple : 07/01/2024

Ou tapez 0 pour annuler.
```

**Validation end_date** :
- Format identique
- `end_date` ≥ `start_date`
- Calcul automatique : `duration_days = (end_date - start_date) + 1`

**Message erreur end_date** :
```
❌ La date de fin doit être après la date de début.

Durée minimale : 7 jours consécutifs.

Réessayez ou tapez 0 pour annuler.
```

**Si duration_days < 7** :
```
⚠️ ATTENTION

Durée : [duration_days] jour(s)

La durée minimale est de 7 jours consécutifs pour être éligible à une évaluation.

Souhaitez-vous continuer quand même ?

1️⃣ Oui, continuer
2️⃣ Non, modifier les dates
```

**Actions** :
- **1** → Créer Collaboration (state=DECLARED), enregistrer ConsentLog, notification driver, message confirmation
- **2** → Retour saisie dates

**Message confirmation** :
```
✅ COLLABORATION DÉCLARÉE

Chauffeur : [display_name]
Période : [start_date] au [end_date]
Durée : [duration_days] jour(s)

Le chauffeur recevra une notification pour confirmer.

Retour au menu principal.
```

---

### 2.10 Évaluer une Collaboration - Sélection

**Déclencheur** : Notification automatique (collaboration ELIGIBLE_FOR_RATING) ou choix manuel depuis "Voir mes collaborations"

**Message envoyé** (si plusieurs éligibles) :
```
⭐ ÉVALUER UNE COLLABORATION

Collaborations éligibles :

1️⃣ [display_name] - [start_date] au [end_date]
2️⃣ [display_name] - [start_date] au [end_date]
3️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "OWNER_RATE_SELECT_COLLABORATION"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, ..., N, N+1] (N = nombre collaborations)
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Si une seule collaboration éligible** :
```
⭐ ÉVALUER UNE COLLABORATION

Chauffeur : [display_name]
Période : [start_date] au [end_date]
Durée : [duration_days] jour(s)

Souhaitez-vous évaluer cette collaboration ?

1️⃣ Oui, commencer l'évaluation
2️⃣ Non, plus tard
```

**Actions** :
- Sélection collaboration → Vérification éligibilité (state=ELIGIBLE_FOR_RATING, identity_locked=True, duration≥7, pas déjà noté)
- Si éligible → Transition vers "OWNER_RATE_COLLECT_SCORES"
- Si non éligible → Message erreur, retour menu

---

### 2.11 Évaluer - Collecte Scores

**Déclencheur** : Collaboration sélectionnée et éligible

**Message envoyé** :
```
⭐ ÉVALUATION - PONCTUALITÉ

Chauffeur : [display_name]

Notez la ponctualité (1 à 5) :

1️⃣ ⭐ (Très mauvais)
2️⃣ ⭐⭐ (Mauvais)
3️⃣ ⭐⭐⭐ (Moyen)
4️⃣ ⭐⭐⭐⭐ (Bon)
5️⃣ ⭐⭐⭐⭐⭐ (Excellent)
```

**État ConversationState** :
- `current_state` : "OWNER_RATE_COLLECT_SCORES"
- `expected_input_type` : SCORE_RATING
- `allowed_values` : [1, 2, 3, 4, 5]
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {"collaboration_id": X, "scores": {"punctuality": null, "vehicle_respect": null, "client_relation": null, "reliability": null}}

**Après chaque score** :
- Stocker dans `temp_data.scores`
- Passer au score suivant

**Messages suivants** :
```
⭐ ÉVALUATION - RESPECT DU VÉHICULE

Notez le respect du véhicule (1 à 5) :
[Menu identique]
```

```
⭐ ÉVALUATION - RELATION CLIENT

Notez la relation avec le client (1 à 5) :
[Menu identique]
```

```
⭐ ÉVALUATION - FIABILITÉ GLOBALE

Notez la fiabilité globale (1 à 5) :
[Menu identique]
```

**Après 4 scores collectés** :
- Transition vers "OWNER_RATE_COLLECT_COMMENT"

---

### 2.12 Évaluer - Collecte Commentaire

**Déclencheur** : 4 scores collectés

**Message envoyé** :
```
💬 ÉVALUATION - COMMENTAIRE

Scores enregistrés :
• Ponctualité : [X]/5
• Respect véhicule : [X]/5
• Relation client : [X]/5
• Fiabilité : [X]/5

Souhaitez-vous ajouter un commentaire ?

1️⃣ Oui, ajouter un commentaire
2️⃣ Non, publier sans commentaire
```

**État ConversationState** :
- `current_state` : "OWNER_RATE_COLLECT_COMMENT"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions** :
- **1** → Transition vers "OWNER_RATE_ENTER_COMMENT"
- **2** → Créer Rating sans commentaire, publier directement, ConsentLog, notification driver, message confirmation

**Si choix "1"** :
```
💬 COMMENTAIRE

Écrivez votre commentaire (max 500 caractères) :

Votre commentaire sera vérifié avant publication pour garantir la neutralité et le respect.

Ou tapez 0 pour annuler.
```

**État ConversationState** :
- `current_state` : "OWNER_RATE_ENTER_COMMENT"
- `expected_input_type` : SANITIZED_TEXT
- `allowed_values` : null (validation longueur ≤500)
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {..., "comment_raw": null, "sanitization_attempts": 0}

**Validation** :
- Longueur ≤ 500 caractères
- Si valide → Stocker dans `temp_data.comment_raw`, envoyer à LLM pour sanitisation
- Si invalide → Message erreur + retry

**Message erreur** :
```
❌ Commentaire trop long (max 500 caractères).

Réessayez ou tapez 0 pour annuler.
```

**Après sanitisation LLM** :
- Transition vers "OWNER_RATE_REVIEW_SANITIZED"

---

### 2.13 Évaluer - Aperçu Commentaire Sanitisé

**Déclencheur** : Commentaire sanitisé par LLM

**Message envoyé** :
```
✅ COMMENTAIRE VÉRIFIÉ

Votre commentaire original :
"[comment_raw]"

───

Version vérifiée (sera publiée) :
"[comment_sanitized]"

───

Souhaitez-vous publier cette version ?

1️⃣ Oui, publier
2️⃣ Non, modifier le commentaire
3️⃣ Publier sans commentaire
```

**État ConversationState** :
- `current_state` : "OWNER_RATE_REVIEW_SANITIZED"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {..., "sanitization_attempts": X (incrémenté à chaque rejet)}

**Actions** :
- **1** → Publier Rating avec commentaire_sanitized, ConsentLog, notification driver, message confirmation
- **2** → Si `sanitization_attempts < 3` → Retour "OWNER_RATE_ENTER_COMMENT" (modification)
- **2** → Si `sanitization_attempts >= 3` → Message limite atteinte, options réduites
- **3** → Publier Rating sans commentaire, ConsentLog, notification driver, message confirmation

**Si limite atteinte (3 rejets)** :
```
⚠️ LIMITE ATTEINTE

Vous avez rejeté 3 fois la version vérifiée.

Options disponibles :

1️⃣ Publier l'évaluation sans commentaire
2️⃣ Annuler l'évaluation
```

**État ConversationState** :
- `current_state` : "OWNER_RATE_LIMIT_REACHED"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions** :
- **1** → Publier Rating sans commentaire, ConsentLog, notification driver, message confirmation
- **2** → Annuler, supprimer Rating en cours, retour menu

**Message confirmation publication** :
```
✅ ÉVALUATION PUBLIÉE

Chauffeur : [display_name]
Scores : [moyenne]/5
[Commentaire si présent]

Le chauffeur recevra une notification.

Retour au menu principal.
```

---

## 3. FLUX DRIVER (Chauffeur)

### 3.1 Menu Principal Driver

**Déclencheur** : User avec role=DRIVER, état INITIAL ou retour depuis une action

**Message envoyé** :
```
🏠 MENU PRINCIPAL - CHAUFFEUR

Que souhaitez-vous faire ?

1️⃣ Créer mon profil / Voir mon profil (selon si un profil existe)
2️⃣ Voir ma réputation
3️⃣ Voir mes collaborations
4️⃣ Quitter
```

**État ConversationState** :
- `current_state` : "DRIVER_MAIN_MENU"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3, 4]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions possibles** :
- **1** → Si profil existe → "DRIVER_VIEW_PROFILE", sinon → "DRIVER_CREATE_PROFILE"
- **2** → "DRIVER_VIEW_REPUTATION"
- **3** → "DRIVER_VIEW_COLLABORATIONS"
- **4** → Message de fin, session terminée

---

### 3.2 Créer/Mettre à Jour Profil

**Déclencheur** : Choix "1" dans menu principal driver

**Si DriverProfile n'existe pas** :
```
👤 CRÉATION DE PROFIL

Pour créer votre profil professionnel, vous devez :

1️⃣ Entrer votre nom d'affichage (optionnel)
2️⃣ Entrer votre numéro de permis
3️⃣ Verrouiller votre identité

Commencer ?

1️⃣ Oui, commencer
2️⃣ Non, plus tard
```

**État ConversationState** :
- `current_state` : "DRIVER_CREATE_PROFILE"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Note Telegram (implémenté)** :
- Si l'utilisateur est sur Telegram (`phone_number` = `tg:<chat_id>`) et que `contact_phone_number` est vide :
  - le bot demande d'abord le numéro du driver (état `DRIVER_ENTER_PHONE`) avant de continuer la création.

### 3.2.1 Saisir Numéro de Téléphone (Telegram)

**Déclencheur** : Driver sur Telegram sans `contact_phone_number`

**Message envoyé** :
```
📞 NUMÉRO DE TÉLÉPHONE

Entrez votre numéro de téléphone (format international).

Format : +221771234567

Ou tapez 0 pour annuler.
```

**État ConversationState** :
- `current_state` : "DRIVER_ENTER_PHONE"
- `expected_input_type` : PHONE_NUMBER
- `allowed_values` : null (validation regex)
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions** :
- **E.164 valide** → stocker dans `User.contact_phone_number` puis continuer vers `DRIVER_CREATE_PROFILE`
- **0** → retour `DRIVER_MAIN_MENU`

**Si nouveau profil, choix "1"** :
```
👤 NOM D'AFFICHAGE

Entrez votre nom d'affichage (optionnel, max 100 caractères) :

Ce nom sera visible par les propriétaires.

Ou tapez 0 pour passer cette étape.
```

**État ConversationState** :
- `current_state` : "DRIVER_ENTER_DISPLAY_NAME"
- `expected_input_type` : SANITIZED_TEXT
- `allowed_values` : null (validation longueur ≤100)
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Après nom (ou skip)** :
- Transition vers "DRIVER_ENTER_PERMIT"

---

### 3.3 Entrer Numéro de Permis

**Déclencheur** : Après nom ou depuis création profil

**Message envoyé** :
```
🪪 NUMÉRO DE PERMIS

Entrez le numéro de votre permis de conduire ivoirien (sans espaces ni tirets).

Exemple : CI1234567

Ce numéro ne sera jamais affiché.

3️⃣ Retour
0️⃣ Annuler
```

**État ConversationState** :
- `current_state` : "DRIVER_ENTER_PERMIT"
- `expected_input_type` : PERMIT_NUMBER
- `allowed_values` : null (validation format)
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {"display_name": X ou null, "permit_number": null, "permit_normalized": null}

**Validation** :
- Normalisation obligatoire :
  1. Convertir en majuscules
  2. Supprimer espaces et tirets
  3. Vérifier longueur finale : 8-15 caractères
  4. Vérifier format : alphanumérique uniquement (A-Z, 0-9)
- Stocker version normalisée dans `temp_data.permit_normalized`
- Vérifier unicité : hashage (SHA-256 + salt) et recherche permit_hash existant

**Message erreur** :
```
❌ Numéro invalide.

Le permis doit contenir 8 à 15 caractères, lettres et chiffres uniquement.

Réessayez ou tapez 0 pour annuler.
```

**Si permis déjà utilisé** :
```
❌ PERMIS DÉJÀ UTILISÉ

Ce numéro de permis est déjà associé à un profil.

Si c'est votre permis, contactez le support.

1️⃣ Réessayer
2️⃣ Retour au menu
```

**Si permis valide** :
- Stocker version normalisée dans `temp_data.permit_normalized`
- Transition vers "DRIVER_CONFIRM_PERMIT"

---

### 3.4 Confirmer Permis

**Déclencheur** : Permis valide saisi et normalisé

**Message envoyé** :
```
✅ CONFIRMATION

Permis détecté : [Afficher masqué : CI****567]

Une fois confirmé, ce numéro ne pourra plus être modifié.

Confirmer ce numéro de permis ?

1️⃣ Oui, confirmer
2️⃣ Non, modifier
```

**État ConversationState** :
- `current_state` : "DRIVER_CONFIRM_PERMIT"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {..., "permit_normalized": "CI1234567"}

**Règle d'affichage masqué** :
- Afficher seulement les 2 premiers caractères et les 2-3 derniers
- Exemple : "CI1234567" → "CI****567"
- Exemple : "ABC123456789" → "AB****789"

**Actions** :
- **1** → Mettre `User.role = DRIVER` puis créer DriverProfile :
  - Générer salt unique
  - Hasher : SHA-256(permit_normalized + salt)
  - Stocker permit_hash et permit_salt
  - Enregistrer ConsentLog (PROFILE_CREATION)
- Transition persistée vers `DRIVER_LOCK_IDENTITY` (prompt de verrouillage)
- **2** → Retour "DRIVER_ENTER_PERMIT"

**Message confirmation** :
```
✅ PROFIL CRÉÉ

Votre profil a été créé avec succès.

⚠️ IMPORTANT : Vous devez maintenant verrouiller votre identité pour recevoir des évaluations.

1️⃣ Verrouiller mon identité maintenant
2️⃣ Plus tard
```

**Actions** :
- **1** → Transition vers "DRIVER_LOCK_IDENTITY"
- **2** → Retour menu

---

### 3.5 Verrouiller Identité

**Déclencheur** : Choix "2" dans menu principal ou depuis création profil

**Si DriverProfile n'existe pas** :
```
❌ PROFIL REQUIS

Vous devez d'abord créer votre profil.

1️⃣ Créer mon profil
2️⃣ Retour au menu
```

**Si identity_locked = True** :
```
✅ IDENTITÉ DÉJÀ VERROUILLÉE

Votre identité est déjà verrouillée depuis le [identity_locked_at].

Aucune modification possible.

1️⃣ Retour au menu
```

**Si identity_locked = False** :
```
🔒 VERROUILLAGE D'IDENTITÉ

⚠️ ATTENTION : Cette action est IRRÉVERSIBLE.

Une fois votre identité verrouillée :
✅ Vous pourrez recevoir des évaluations
✅ Votre réputation sera visible
❌ Vous ne pourrez plus modifier votre permis

Confirmer le verrouillage ?

1️⃣ Oui, verrouiller définitivement
2️⃣ Non, annuler
```

**État ConversationState** :
- `current_state` : "DRIVER_LOCK_IDENTITY"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions** :
- **1** → Mettre `identity_locked = True`, `identity_locked_at = now()`, message confirmation
- **2** → Retour menu

---

### 3.6 Mon Profil (voir / modifier / désactiver)

**Déclencheur** : Menu driver, choix "1" quand un profil existe

**Message envoyé** :
```
👤 MON PROFIL

Nom : [display_name ou "(non renseigné)"]
Téléphone : [contact_phone_number ou "(non renseigné)"]
Identité : [✅ Verrouillée / ⚠️ Non verrouillée]

1️⃣ Modifier mon nom
2️⃣ Supprimer mon profil
3️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "DRIVER_VIEW_PROFILE"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]

#### 3.6.1 Modifier mon nom
**Message envoyé** :
```
✏️ MODIFIER MON NOM

Entrez votre nouveau nom d'affichage (max 100 caractères).

Ou tapez 0 pour annuler.
```

**Action** :
- Sauvegarder `DriverProfile.display_name`
- Retour à "MON PROFIL"

#### 3.6.2 Supprimer mon profil (implémenté = désactivation)
**Message envoyé** :
```
🗑️ SUPPRIMER MON PROFIL

Souhaitez-vous désactiver votre profil ?

- Vous ne serez plus trouvable par numéro
- Vos collaborations/évaluations restent enregistrées

1️⃣ Oui, désactiver
2️⃣ Non, annuler
```

**Action** :
- **1** → `User.is_active = False` + suppression des `ConversationState`, puis fin : "Tapez /start pour réactiver."
- **2** → Retour à "MON PROFIL"

**Message confirmation** :
```
✅ IDENTITÉ VERROUILLÉE

Identité verrouillée avec succès. Votre profil est maintenant sécurisé.

Vous pouvez maintenant recevoir des évaluations de la part des propriétaires.

Retour au menu principal.
```

---

### 3.6 Voir Réputation

**Déclencheur** : Choix "3" dans menu principal driver

**Si identity_locked = False** :
```
⚠️ IDENTITÉ NON VERROUILLÉE

Vous devez verrouiller votre identité pour voir votre réputation.

1️⃣ Verrouiller mon identité
2️⃣ Retour au menu
```

**Si identity_locked = True** :
```
📊 MA RÉPUTATION

Nom : [display_name]
Identité : ✅ Vérifiée
État : [NEW/ESTABLISHED/CONFIRMED/REBUILDING]
Score médian : [X.X]/5
Collaborations : [X] confirmée(s)

─── ÉVALUATIONS ───

[Pour chaque Rating publié :]

⭐ [Score médian]/5
Ponctualité: [X]/5 | Véhicule: [X]/5
Client: [X]/5 | Fiabilité: [X]/5

"[commentaire_published]"

[Si pas de réponse :]
💬 Répondre à cette évaluation

[Si réponse présente :]
💬 Votre réponse : "[driver_response]"

───

1️⃣ Répondre à une évaluation
2️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "DRIVER_VIEW_REPUTATION"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions** :
- **1** → Transition vers "DRIVER_RESPOND_TO_REVIEW"
- **2** → Retour menu

---

### 3.7 Confirmer Collaboration

**Déclencheur** : Choix "4" dans menu principal ou notification

**Si aucune collaboration en attente** :
```
ℹ️ AUCUNE COLLABORATION EN ATTENTE

Vous n'avez aucune collaboration en attente de confirmation.

1️⃣ Retour au menu
```

**Si collaborations en attente** :
```
🤝 COLLABORATIONS EN ATTENTE

Collaborations à confirmer :

1️⃣ Propriétaire : [phone_number] - [start_date] au [end_date]
2️⃣ Propriétaire : [phone_number] - [start_date] au [end_date]
3️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "DRIVER_CONFIRM_COLLABORATION"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, ..., N, N+1]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Après sélection** :
```
🤝 CONFIRMER COLLABORATION

Propriétaire : [phone_number]
Période : [start_date] au [end_date]
Durée : [duration_days] jour(s)

Confirmer cette collaboration ?

1️⃣ Oui, confirmer
2️⃣ Non, refuser
3️⃣ Retour
```

**État ConversationState** :
- `current_state` : "DRIVER_CONFIRM_COLLABORATION_DETAIL"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, 3]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Actions** :
- **1** → Mettre Collaboration state=CONFIRMED, `confirmed_at = now()`, calculer `duration_days`, enregistrer ConsentLog (COLLABORATION_CONFIRMATION), notification owner, message confirmation
- **2** → Refuser (optionnel : marquer comme refusée, ou simplement ne pas confirmer)
- **3** → Retour liste

**Message confirmation** :
```
✅ COLLABORATION CONFIRMÉE

Collaboration confirmée avec succès.

Vous recevrez un rappel dans 7 jours pour évaluer cette collaboration (si éligible).

Retour au menu principal.
```

---

### 3.8 Répondre à une Évaluation

**Déclencheur** : Choix "5" dans menu principal ou depuis réputation

**Si aucune évaluation sans réponse** :
```
ℹ️ AUCUNE ÉVALUATION EN ATTENTE

Vous avez répondu à toutes vos évaluations.

1️⃣ Retour au menu
```

**Si évaluations sans réponse** :
```
💬 RÉPONDRE À UNE ÉVALUATION

Évaluations sans réponse :

1️⃣ [Date] - Score [X.X]/5 - "[commentaire_published]"
2️⃣ [Date] - Score [X.X]/5 - "[commentaire_published]"
3️⃣ Retour au menu
```

**État ConversationState** :
- `current_state` : "DRIVER_RESPOND_TO_REVIEW"
- `expected_input_type` : MENU_CHOICE
- `allowed_values` : [1, 2, ..., N, N+1]
- `behavior_on_invalid_input` : RETRY_SAME_STATE

**Après sélection** :
```
💬 RÉPONSE À L'ÉVALUATION

Évaluation du [date] :
⭐ [Score médian]/5
"[commentaire_published]"

───

Écrivez votre réponse (max 500 caractères) :

Votre réponse sera visible par tous les propriétaires.

Ou tapez 0 pour annuler.
```

**État ConversationState** :
- `current_state` : "DRIVER_ENTER_RESPONSE"
- `expected_input_type` : SANITIZED_TEXT
- `allowed_values` : null (validation longueur ≤500)
- `behavior_on_invalid_input` : RETRY_SAME_STATE
- `temp_data` : {"rating_id": X, "response": null}

**Validation** :
- Longueur ≤ 500 caractères
- Si valide → Stocker dans `temp_data.response`, envoyer à LLM pour sanitisation (optionnel, ou validation simple)

**Après sanitisation (si appliquée)** :
```
✅ RÉPONSE VÉRIFIÉE

Votre réponse originale :
"[response_raw]"

───

Version vérifiée (sera publiée) :
"[response_sanitized]"

───

Publier cette réponse ?

1️⃣ Oui, publier
2️⃣ Non, modifier
```

**Actions** :
- **1** → Mettre `driver_response` et `driver_response_at` dans Rating, invalider cache réputation, message confirmation
- **2** → Retour "DRIVER_ENTER_RESPONSE"

**Message confirmation** :
```
✅ RÉPONSE PUBLIÉE

Votre réponse a été publiée avec succès.

Elle sera visible par tous les propriétaires consultant votre profil.

Retour au menu principal.
```

---

## 4. GESTION DES ERREURS ET TIMEOUTS

### 4.1 Entrée Invalide

**Comportement général** :
- Incrémenter `invalid_input_count`
- Si `invalid_input_count < 3` : Réafficher message avec indication erreur
- Si `invalid_input_count >= 3` : Retour au menu principal avec message

**Message après 3 erreurs** :
```
⚠️ Trop de tentatives invalides.

Retour au menu principal.
```

---

### 4.2 Session Expirée

**Déclencheur** : `expires_at < now()`

**Message envoyé** :
```
⏰ SESSION EXPIRÉE

Votre session a expiré par inactivité.

Retour au menu principal.
```

**Action** : Transition vers menu principal selon rôle

---

### 4.3 Action Non Autorisée

**Exemples** :
- Owner essaie de noter une collaboration non éligible
- Driver essaie de verrouiller identité sans profil
- Tentative de modification après verrouillage

**Message générique** :
```
❌ ACTION NON AUTORISÉE

Cette action n'est pas possible dans l'état actuel.

Retour au menu principal.
```

---

## 6bis. ÉTAT SPÉCIAL (Compte désactivé)

### 6bis.1 Compte désactivé

**Déclencheur** : `User.is_active == False`

**Message envoyé** :
```
🚫 COMPTE DÉSACTIVÉ

Votre profil a été désactivé.

1️⃣ Réactiver
2️⃣ Quitter

Vous pouvez aussi taper /start.
```

**Actions** :
- **1** → `User.is_active = True` puis reset vers message de bienvenue
- **2** → Fin

---

## 5. NOTIFICATIONS AUTOMATIQUES

### 5.1 Invitation à Créer Profil

**Déclencheur** : Owner invite driver non-inscrit

**Message envoyé au driver** :
```
🔔 INVITATION

Un propriétaire souhaite déclarer une collaboration avec vous.

Pour créer votre profil et recevoir des évaluations :

1️⃣ Créer mon profil
2️⃣ Plus tard
```

---

### 5.2 Demande de Confirmation Collaboration

**Déclencheur** : Owner déclare collaboration

**Message envoyé au driver** :
```
🔔 NOUVELLE COLLABORATION

Un propriétaire a déclaré une collaboration avec vous.

Période : [start_date] au [end_date]

Confirmer cette collaboration ?

1️⃣ Confirmer
2️⃣ Voir les détails
```

---

### 5.3 Rappel Rating (Owner)

**Déclencheur** : Collaboration ELIGIBLE_FOR_RATING (≥7 jours après confirmation)

**Message envoyé à l'owner** :
```
🔔 RAPPEL - ÉVALUATION

Votre collaboration avec [display_name] est éligible pour évaluation.

Période : [start_date] au [end_date]

Souhaitez-vous évaluer cette collaboration ?

1️⃣ Évaluer maintenant
2️⃣ Plus tard
```

---

### 5.4 Nouvelle Évaluation (Driver)

**Déclencheur** : Rating publié par owner

**Message envoyé au driver** :
```
🔔 NOUVELLE ÉVALUATION

Vous avez reçu une nouvelle évaluation.

Score : [X.X]/5
"[commentaire_published]"

1️⃣ Voir ma réputation
2️⃣ Répondre
3️⃣ Plus tard
```

---

### 5.5 Droit de Réponse (Driver)

**Note** : Intégré dans notification nouvelle évaluation (option "2")

---

## 6. MESSAGES SPÉCIAUX

### 6.1 Message de Fin de Session

**Déclencheur** : Choix "Quitter" dans menu

**Message envoyé** :
```
👋 À BIENTÔT

Merci d'avoir utilisé Driver Verification Bot.

Revenez quand vous voulez pour gérer vos collaborations et votre réputation.
```

**Action** : Session terminée, ConversationState supprimé ou marqué comme terminé

---

### 6.2 Message d'Erreur Système

**Déclencheur** : Erreur serveur inattendue

**Message envoyé** :
```
❌ ERREUR SYSTÈME

Une erreur s'est produite. Veuillez réessayer plus tard.

Retour au menu principal.
```

**Action** : Logging détaillé côté serveur, retour menu principal

---

## PROCHAINES ÉTAPES

Après validation de ce document :
1. Implémentation incrémentale (identity → collaboration → rating → reputation → notifications)
