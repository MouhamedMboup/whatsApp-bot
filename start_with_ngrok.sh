#!/bin/bash
# Start both Django and ngrok together with proper health checks

DJANGO_PORT=${1:-8000}
MAX_RETRIES=30
RETRY_DELAY=1

echo "🚀 Starting Django + ngrok setup..."
echo ""

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Stopping services..."
    kill $DJANGO_PID 2>/dev/null
    kill $NGROK_PID 2>/dev/null
    exit 0
}

trap cleanup SIGINT SIGTERM

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Check if manage.py exists
if [ ! -f "manage.py" ]; then
    echo "❌ Error: manage.py not found. Are you in the project root?"
    exit 1
fi

# Check if port is already in use
if lsof -i :$DJANGO_PORT > /dev/null 2>&1 || netstat -tlnp 2>/dev/null | grep -q ":$DJANGO_PORT " || ss -tlnp 2>/dev/null | grep -q ":$DJANGO_PORT "; then
    echo "⚠️  Port $DJANGO_PORT is already in use"
    echo "   Please stop the existing service or use a different port"
    exit 1
fi

# Start Django server in background
echo "📦 Starting Django server on 0.0.0.0:$DJANGO_PORT..."
python manage.py runserver 0.0.0.0:$DJANGO_PORT > /tmp/django.log 2>&1 &
DJANGO_PID=$!

# Wait for Django to be ready with retry logic
echo "⏳ Waiting for Django to be ready..."
RETRY_COUNT=0
DJANGO_READY=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    # Check if process is still running
    if ! kill -0 $DJANGO_PID 2>/dev/null; then
        echo "❌ Django process died. Check /tmp/django.log for errors:"
        tail -20 /tmp/django.log
        exit 1
    fi
    
    # Check if Django is responding
    if curl -s -f http://127.0.0.1:$DJANGO_PORT/api/health > /dev/null 2>&1; then
        DJANGO_READY=true
        break
    fi
    
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $((RETRY_COUNT % 5)) -eq 0 ]; then
        echo "   Still waiting... ($RETRY_COUNT/$MAX_RETRIES)"
    fi
    sleep $RETRY_DELAY
done

if [ "$DJANGO_READY" = false ]; then
    echo "❌ Django failed to start after $MAX_RETRIES attempts"
    echo "   Check /tmp/django.log for errors:"
    tail -20 /tmp/django.log
    kill $DJANGO_PID 2>/dev/null
    exit 1
fi

# Verify Django is listening on the correct interface
echo "🔍 Verifying Django is listening on 0.0.0.0:$DJANGO_PORT..."
LISTENING_ON=$(lsof -i :$DJANGO_PORT 2>/dev/null | grep LISTEN | awk '{print $9}' | head -1 || \
               netstat -tlnp 2>/dev/null | grep ":$DJANGO_PORT " | awk '{print $4}' | head -1 || \
               ss -tlnp 2>/dev/null | grep ":$DJANGO_PORT " | awk '{print $4}' | head -1)

if [ -z "$LISTENING_ON" ]; then
    echo "⚠️  Warning: Could not verify listening address"
else
    echo "   Django is listening on: $LISTENING_ON"
    if [[ "$LISTENING_ON" == *"127.0.0.1"* ]] && [[ "$LISTENING_ON" != *"0.0.0.0"* ]]; then
        echo "   ⚠️  Warning: Django is only listening on 127.0.0.1, not 0.0.0.0"
        echo "   ngrok may not be able to connect. Restart Django with:"
        echo "   python manage.py runserver 0.0.0.0:$DJANGO_PORT"
    fi
fi

# Test both localhost and 127.0.0.1
echo "🧪 Testing Django connectivity..."
if curl -s -f http://127.0.0.1:$DJANGO_PORT/api/health > /dev/null 2>&1; then
    echo "   ✅ 127.0.0.1:$DJANGO_PORT - OK"
else
    echo "   ❌ 127.0.0.1:$DJANGO_PORT - FAILED"
fi

if curl -s -f http://localhost:$DJANGO_PORT/api/health > /dev/null 2>&1; then
    echo "   ✅ localhost:$DJANGO_PORT - OK"
else
    echo "   ❌ localhost:$DJANGO_PORT - FAILED"
fi

echo ""
echo "✅ Django server is ready and responding"
echo ""

# Check if ngrok is already running
if pgrep -f "ngrok http" > /dev/null; then
    echo "⚠️  ngrok is already running. Stopping it..."
    pkill -f "ngrok http"
    sleep 2
fi

# Check if ngrok port 4040 is in use
if lsof -i :4040 > /dev/null 2>&1 || netstat -tlnp 2>/dev/null | grep -q ":4040 " || ss -tlnp 2>/dev/null | grep -q ":4040 "; then
    echo "⚠️  Port 4040 is already in use (ngrok web interface)"
    echo "   This might be from a previous ngrok instance"
    echo "   Trying to continue anyway..."
fi

# Start ngrok
echo "🌐 Starting ngrok tunnel..."
ngrok http $DJANGO_PORT > /tmp/ngrok.log 2>&1 &
NGROK_PID=$!

# Give ngrok a moment to start
sleep 2

# Check if ngrok process is still running
if ! kill -0 $NGROK_PID 2>/dev/null; then
    echo "❌ ngrok process died immediately after starting"
    echo "   Check /tmp/ngrok.log for errors:"
    cat /tmp/ngrok.log
    kill $DJANGO_PID 2>/dev/null
    exit 1
fi

# Wait for ngrok to be ready with retry logic
echo "⏳ Waiting for ngrok to be ready..."
RETRY_COUNT=0
NGROK_READY=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    # Check if ngrok process is still running
    if ! kill -0 $NGROK_PID 2>/dev/null; then
        echo ""
        echo "❌ ngrok process died. Check /tmp/ngrok.log:"
        cat /tmp/ngrok.log
        kill $DJANGO_PID 2>/dev/null
        exit 1
    fi
    
    # Check if ngrok API is responding
    if curl -s http://localhost:4040/api/tunnels > /dev/null 2>&1; then
        # Verify we can actually get tunnel data
        API_RESPONSE=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null)
        if [ -n "$API_RESPONSE" ] && echo "$API_RESPONSE" | grep -q "tunnels"; then
            NGROK_READY=true
            break
        fi
    fi
    
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $((RETRY_COUNT % 5)) -eq 0 ]; then
        echo "   Still waiting... ($RETRY_COUNT/$MAX_RETRIES)"
        # Show last few lines of ngrok log for debugging
        if [ -f /tmp/ngrok.log ]; then
            echo "   Last ngrok log entries:"
            tail -3 /tmp/ngrok.log | sed 's/^/      /'
        fi
    fi
    sleep $RETRY_DELAY
done

if [ "$NGROK_READY" = false ]; then
    echo ""
    echo "❌ ngrok failed to start after $MAX_RETRIES attempts"
    echo ""
    echo "📋 Debugging information:"
    echo "   ngrok process running: $(kill -0 $NGROK_PID 2>/dev/null && echo 'Yes' || echo 'No')"
    echo "   ngrok API accessible: $(curl -s http://localhost:4040/api/tunnels > /dev/null 2>&1 && echo 'Yes' || echo 'No')"
    echo ""
    echo "   Full ngrok log:"
    cat /tmp/ngrok.log
    echo ""
    echo "   Try running ngrok manually to see errors:"
    echo "   ngrok http $DJANGO_PORT"
    kill $DJANGO_PID 2>/dev/null
    kill $NGROK_PID 2>/dev/null
    exit 1
fi

# Get the ngrok URL with better error handling
echo "📡 Fetching ngrok tunnel information..."
API_RESPONSE=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null)
NGROK_URL=""

# Debug: Show API response if verbose
if [ -z "$API_RESPONSE" ]; then
    echo "   ⚠️  ngrok API not responding. Checking ngrok status..."
    if ! kill -0 $NGROK_PID 2>/dev/null; then
        echo "   ❌ ngrok process is not running"
        echo "   Check /tmp/ngrok.log:"
        cat /tmp/ngrok.log
        kill $DJANGO_PID 2>/dev/null
        exit 1
    fi
    echo "   ngrok process is running, but API not ready yet"
    echo "   Try accessing http://localhost:4040 in your browser"
    echo "   Or check /tmp/ngrok.log for details"
    kill $DJANGO_PID 2>/dev/null
    kill $NGROK_PID 2>/dev/null
    exit 1
fi

# Use Python to parse JSON
if command -v python3 &> /dev/null && [ -n "$API_RESPONSE" ]; then
    NGROK_URL=$(echo "$API_RESPONSE" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if data.get('tunnels') and len(data['tunnels']) > 0:
        print(data['tunnels'][0]['public_url'])
    else:
        print('')
except Exception as e:
    print('')
    sys.stderr.write(f'Error parsing ngrok response: {e}\n')
" 2>/dev/null)
fi

# Fallback: try jq if Python didn't work
if [ -z "$NGROK_URL" ] && command -v jq &> /dev/null; then
    NGROK_URL=$(echo "$API_RESPONSE" | jq -r '.tunnels[0].public_url // empty' 2>/dev/null)
fi

# Fallback: try grep/sed
if [ -z "$NGROK_URL" ]; then
    NGROK_URL=$(echo "$API_RESPONSE" | grep -o '"public_url":"https://[^"]*"' | head -1 | sed 's/"public_url":"\(.*\)"/\1/')
fi

if [ -z "$NGROK_URL" ] || [ "$NGROK_URL" = "null" ] || [ "$NGROK_URL" = "" ]; then
    echo ""
    echo "❌ Failed to get ngrok URL from API response"
    echo ""
    echo "📋 Debugging information:"
    echo "   ngrok process running: $(kill -0 $NGROK_PID 2>/dev/null && echo 'Yes' || echo 'No')"
    echo "   ngrok API response length: ${#API_RESPONSE} characters"
    echo ""
    echo "   API Response preview:"
    echo "$API_RESPONSE" | head -10
    echo ""
    echo "   Full ngrok log:"
    cat /tmp/ngrok.log
    echo ""
    echo "   Try accessing http://localhost:4040 in your browser to see ngrok dashboard"
    kill $DJANGO_PID 2>/dev/null
    kill $NGROK_PID 2>/dev/null
    exit 1
fi

# Extract domain for ALLOWED_HOSTS
NGROK_DOMAIN=$(echo $NGROK_URL | sed 's|https://||')

# Test ngrok -> Django connection
echo "🧪 Testing ngrok -> Django connection..."
sleep 2  # Give ngrok a moment to establish connection
if curl -s -f "$NGROK_URL/api/health" > /dev/null 2>&1; then
    echo "   ✅ ngrok -> Django connection successful!"
else
    echo "   ⚠️  ngrok -> Django connection test failed"
    echo "   This might be normal if Django hasn't fully started"
    echo "   Try accessing $NGROK_URL/api/health in your browser"
fi

echo ""
echo "✅ ngrok tunnel active!"
echo ""
echo "🌐 Public URL: $NGROK_URL"
echo ""
echo "📋 Webhook URLs for Meta Developer Console:"
echo "   Callback:  $NGROK_URL/api/whatsapp/webhook"
echo "   Verify:    $NGROK_URL/api/whatsapp/verify"
echo ""
echo "📝 Note: ALLOWED_HOSTS is automatically handled by NgrokHostMiddleware"
echo "   (No need to manually update .env)"
echo ""
echo "📊 Dashboards:"
echo "   Django:    http://localhost:$DJANGO_PORT/admin"
echo "   ngrok:     http://localhost:4040"
echo ""
echo "🛑 Press Ctrl+C to stop both services"
echo ""

# Keep running and monitor processes
while true; do
    # Check if Django is still running
    if ! kill -0 $DJANGO_PID 2>/dev/null; then
        echo ""
        echo "❌ Django process died. Stopping ngrok..."
        kill $NGROK_PID 2>/dev/null
        exit 1
    fi
    
    # Check if ngrok is still running
    if ! kill -0 $NGROK_PID 2>/dev/null; then
        echo ""
        echo "❌ ngrok process died. Stopping Django..."
        kill $DJANGO_PID 2>/dev/null
        exit 1
    fi
    
    sleep 5
done
