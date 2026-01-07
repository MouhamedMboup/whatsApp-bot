#!/bin/bash
# Script de configuration PostgreSQL pour WhatsApp Bot

echo "=== Configuration PostgreSQL pour WhatsApp Bot ==="
echo ""

# Vérifier si PostgreSQL est installé
if ! command -v psql &> /dev/null; then
    echo "❌ PostgreSQL n'est pas installé."
    echo "Installez-le avec: sudo apt-get install postgresql postgresql-contrib"
    exit 1
fi

echo "✅ PostgreSQL est installé"
echo ""

# Démarrer le service PostgreSQL
echo "Démarrage du service PostgreSQL..."
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Attendre que le service démarre
sleep 3

# Vérifier si le service est actif
if sudo systemctl is-active --quiet postgresql; then
    echo "✅ Service PostgreSQL démarré"
else
    echo "❌ Impossible de démarrer PostgreSQL. Vérifiez les logs: sudo journalctl -u postgresql"
    exit 1
fi

echo ""

# Créer la base de données
DB_NAME="whatsapp_bot"
DB_USER="whatsapp_user"
DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)

echo "Création de la base de données et de l'utilisateur..."
echo ""

# Créer l'utilisateur et la base de données
sudo -u postgres psql << EOF
-- Créer l'utilisateur
CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';

-- Créer la base de données
CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};

-- Donner tous les privilèges
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};

-- Se connecter à la base et donner les privilèges sur le schéma public
\c ${DB_NAME}
GRANT ALL ON SCHEMA public TO ${DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${DB_USER};
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${DB_USER};

\q
EOF

if [ $? -eq 0 ]; then
    echo "✅ Base de données créée avec succès"
    echo ""
    echo "=== Informations de connexion ==="
    echo "Nom de la base: ${DB_NAME}"
    echo "Utilisateur: ${DB_USER}"
    echo "Mot de passe: ${DB_PASSWORD}"
    echo ""
    echo "⚠️  IMPORTANT: Sauvegardez ces informations !"
    echo ""
    
    # Créer le fichier .env
    ENV_FILE=".env"
    if [ ! -f "$ENV_FILE" ]; then
        cat > "$ENV_FILE" << ENVEOF
# Django Settings
SECRET_KEY=$(openssl rand -base64 50 | tr -d "=+/" | cut -c1-50)
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=localhost
DB_PORT=5432

# WhatsApp API (Meta)
WHATSAPP_API_TOKEN=your-whatsapp-api-token
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_VERIFY_TOKEN=your-verify-token

# OpenAI (for comment sanitization)
OPENAI_API_KEY=your-openai-api-key

# Security
PERMIT_SALT_LENGTH=32
ENVEOF
        echo "✅ Fichier .env créé avec les informations de connexion"
    else
        echo "⚠️  Le fichier .env existe déjà. Mettez à jour manuellement:"
        echo "   DB_NAME=${DB_NAME}"
        echo "   DB_USER=${DB_USER}"
        echo "   DB_PASSWORD=${DB_PASSWORD}"
    fi
    
    echo ""
    echo "=== Prochaines étapes ==="
    echo "1. Vérifiez le fichier .env"
    echo "2. Activez l'environnement virtuel: source venv/bin/activate"
    echo "3. Appliquez les migrations: python manage.py migrate"
    echo ""
else
    echo "❌ Erreur lors de la création de la base de données"
    exit 1
fi
