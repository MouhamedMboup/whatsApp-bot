# Guide de Configuration PostgreSQL

## Option 1 : Script Automatique (Recommandé)

Exécutez le script de configuration :

```bash
chmod +x setup_postgresql.sh
./setup_postgresql.sh
```

Le script va :
- Démarrer PostgreSQL
- Créer la base de données `whatsapp_bot`
- Créer un utilisateur `whatsapp_user`
- Générer un mot de passe sécurisé
- Créer le fichier `.env` avec les informations

## Option 2 : Configuration Manuelle

### 1. Démarrer PostgreSQL

```bash
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

### 2. Se connecter à PostgreSQL

```bash
sudo -u postgres psql
```

### 3. Créer la base de données et l'utilisateur

Dans le shell PostgreSQL :

```sql
-- Créer l'utilisateur
CREATE USER whatsapp_user WITH PASSWORD 'votre_mot_de_passe_securise';

-- Créer la base de données
CREATE DATABASE whatsapp_bot OWNER whatsapp_user;

-- Donner les privilèges
GRANT ALL PRIVILEGES ON DATABASE whatsapp_bot TO whatsapp_user;

-- Se connecter à la base
\c whatsapp_bot

-- Donner les privilèges sur le schéma public
GRANT ALL ON SCHEMA public TO whatsapp_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO whatsapp_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO whatsapp_user;

-- Quitter
\q
```

### 4. Créer le fichier .env

Créez un fichier `.env` à la racine du projet :

```env
# Django Settings
SECRET_KEY=votre-secret-key-genere-aleatoirement
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DB_NAME=whatsapp_bot
DB_USER=whatsapp_user
DB_PASSWORD=votre_mot_de_passe_securise
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
```

### 5. Générer un SECRET_KEY sécurisé

```bash
python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

## Vérification

Testez la connexion :

```bash
psql -U whatsapp_user -d whatsapp_bot -h localhost
```

Si vous êtes invité à entrer le mot de passe, c'est que la connexion fonctionne.

## Appliquer les Migrations

Une fois PostgreSQL configuré :

```bash
source venv/bin/activate
python manage.py migrate
```

## Dépannage

### Erreur : "connection to server failed"

1. Vérifiez que PostgreSQL est démarré :
   ```bash
   sudo systemctl status postgresql
   ```

2. Si le service n'est pas actif :
   ```bash
   sudo systemctl start postgresql
   ```

### Erreur : "password authentication failed"

1. Vérifiez les identifiants dans `.env`
2. Vérifiez que l'utilisateur existe :
   ```bash
   sudo -u postgres psql -c "\du"
   ```

### Erreur : "database does not exist"

1. Créez la base de données (voir Option 2, étape 3)

### Erreur : "permission denied"

1. Vérifiez que l'utilisateur a les bons privilèges :
   ```sql
   GRANT ALL PRIVILEGES ON DATABASE whatsapp_bot TO whatsapp_user;
   ```

## Configuration de pg_hba.conf (si nécessaire)

Si vous avez des problèmes d'authentification, vous pouvez modifier `/etc/postgresql/16/main/pg_hba.conf` :

```
# Ajouter cette ligne pour permettre les connexions locales avec mot de passe
local   all             all                                     md5
host    all             all             127.0.0.1/32            md5
```

Puis redémarrer PostgreSQL :
```bash
sudo systemctl restart postgresql
```
