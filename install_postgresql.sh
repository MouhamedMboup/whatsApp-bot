#!/bin/bash
# Script d'installation et configuration PostgreSQL

echo "=== Installation PostgreSQL pour WhatsApp Bot ==="
echo ""

# Vérifier si le serveur PostgreSQL est installé
if dpkg -l | grep -q "postgresql-[0-9]" && ! dpkg -l | grep -q "postgresql-server"; then
    echo "⚠️  Seuls les clients PostgreSQL sont installés."
    echo "Installation du serveur PostgreSQL..."
    echo ""
    
    # Installer le serveur PostgreSQL
    sudo apt-get update
    sudo apt-get install -y postgresql postgresql-contrib
    
    echo ""
    echo "✅ Serveur PostgreSQL installé"
    echo ""
    
    # Attendre que le service démarre
    sleep 3
fi

# Vérifier si PostgreSQL est maintenant disponible
if systemctl list-units --type=service | grep -q postgresql; then
    echo "✅ Service PostgreSQL détecté"
    
    # Démarrer le service
    echo "Démarrage du service PostgreSQL..."
    sudo systemctl start postgresql
    sudo systemctl enable postgresql
    
    # Attendre que le service démarre
    sleep 3
    
    if sudo systemctl is-active --quiet postgresql; then
        echo "✅ Service PostgreSQL démarré"
    else
        echo "⚠️  Vérification du service..."
        # Essayer avec le nom de service spécifique à la version
        POSTGRES_VERSION=$(psql --version | grep -oP '\d+' | head -1)
        if [ -n "$POSTGRES_VERSION" ]; then
            echo "Tentative avec postgresql@${POSTGRES_VERSION}-main..."
            sudo systemctl start postgresql@${POSTGRES_VERSION}-main 2>/dev/null || true
            sudo systemctl enable postgresql@${POSTGRES_VERSION}-main 2>/dev/null || true
        fi
    fi
else
    echo "❌ PostgreSQL n'est toujours pas installé comme service"
    echo "Installation du serveur PostgreSQL..."
    sudo apt-get update
    sudo apt-get install -y postgresql postgresql-contrib
    sleep 3
    sudo systemctl start postgresql
    sudo systemctl enable postgresql
fi

echo ""

# Vérifier la connexion
echo "Vérification de la connexion PostgreSQL..."
if sudo -u postgres psql -c "SELECT version();" > /dev/null 2>&1; then
    echo "✅ PostgreSQL fonctionne correctement"
    echo ""
    
    # Continuer avec la création de la base de données
    echo "=== Création de la base de données ==="
    
    DB_NAME="whatsapp_bot"
    DB_USER="whatsapp_user"
    DB_PASSWORD=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-25)
    
    echo "Création de l'utilisateur et de la base de données..."
    
    sudo -u postgres psql << EOF
-- Créer l'utilisateur (ignore l'erreur si existe déjà)
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_user WHERE usename = '${DB_USER}') THEN
        CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';
    END IF;
END
\$\$;

-- Créer la base de données (ignore l'erreur si existe déjà)
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec

-- Donner les privilèges
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
            SECRET_KEY=$(python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())" 2>/dev/null || openssl rand -base64 50 | tr -d "=+/" | cut -c1-50)
            
            cat > "$ENV_FILE" << ENVEOF
# Django Settings
SECRET_KEY=${SECRET_KEY}
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
else
    echo "❌ Impossible de se connecter à PostgreSQL"
    echo ""
    echo "Essayez manuellement:"
    echo "1. sudo apt-get install postgresql postgresql-contrib"
    echo "2. sudo systemctl start postgresql"
    echo "3. sudo -u postgres psql"
    exit 1
fi
