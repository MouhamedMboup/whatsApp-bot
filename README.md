# WhatsApp Driver Verification Bot (MVP)

Système de vérification d'identité et de réputation de chauffeurs via WhatsApp pour la Côte d'Ivoire.

## Structure du Projet

```
whatsapp-bot/
├── whatsapp_bot/          # Configuration Django
│   ├── settings.py        # Paramètres du projet
│   ├── urls.py            # URLs principales
│   └── wsgi.py            # WSGI config
├── core/                  # Application principale
│   ├── models.py          # Modèles de données
│   ├── utils.py           # Utilitaires
│   ├── services/          # Services métier
│   │   └── identity_service.py
│   └── urls.py            # URLs de l'API
├── requirements.txt       # Dépendances Python
└── manage.py             # Script Django
```

## Installation

1. Créer un environnement virtuel :
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows
```

2. Installer les dépendances :
```bash
pip install -r requirements.txt
```

3. Configurer la base de données PostgreSQL :
- Créer une base de données nommée `whatsapp_bot`
- Configurer les variables d'environnement dans `.env`

4. Appliquer les migrations :
```bash
python manage.py makemigrations
python manage.py migrate
```

## Configuration

Créer un fichier `.env` à la racine avec :
```
SECRET_KEY=your-secret-key
DEBUG=True
DB_NAME=whatsapp_bot
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
WHATSAPP_API_TOKEN=your-token
WHATSAPP_PHONE_NUMBER_ID=your-id
WHATSAPP_VERIFY_TOKEN=your-verify-token
OPENAI_API_KEY=your-key
```

## Modèles de Données

- **User** : Utilisateurs (Owner/Driver)
- **OwnerProfile** : Profil propriétaire
- **DriverProfile** : Profil chauffeur avec permis hashé
- **Collaboration** : Relation owner-driver
- **Rating** : Évaluation d'une collaboration
- **ConversationState** : État de conversation WhatsApp
- **ConsentLog** : Journal des consentements

## Statut d'Implémentation

- ✅ Structure Django de base
- ✅ Modèles de données
- ✅ Service d'identité (normalisation, hashage, verrouillage)
- ⏳ Gestion conversations WhatsApp
- ⏳ Collaborations
- ⏳ Système de rating
- ⏳ Calcul réputation
- ⏳ Notifications
- ⏳ Intégration API WhatsApp

## Documentation

Voir les fichiers :
- `ARCHITECTURE.md` : Architecture système
- `MODELS_AND_STATES.md` : Modèles et états
- `CONVERSATION_FLOWS.md` : Flux conversationnels
