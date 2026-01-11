#!/bin/bash
# Start Django development server for ngrok

echo "🚀 Starting Django development server..."
echo ""

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo "📦 Activating virtual environment..."
    source venv/bin/activate
fi

# Check if we're in the right directory
if [ ! -f "manage.py" ]; then
    echo "❌ Error: manage.py not found. Are you in the project root?"
    exit 1
fi

# Get port from argument or use default
PORT=${1:-8000}

echo "🌐 Starting server on port $PORT..."
echo ""
echo "📋 Server will be available at:"
echo "   Local:  http://localhost:$PORT"
echo "   Health: http://localhost:$PORT/api/health"
echo ""
echo "🛑 Press Ctrl+C to stop the server"
echo ""

# Start Django server
python manage.py runserver 0.0.0.0:$PORT
