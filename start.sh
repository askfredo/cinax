#!/bin/bash
set -e

echo "→ Iniciando dashboard en puerto ${PORT:-8080}..."
python dashboard.py &
DASH_PID=$!

# Dale 5 segundos al dashboard para que Railway detecte el puerto
sleep 5

echo "→ Iniciando bot CINAX..."
python cinax_v02.py &
BOT_PID=$!

# Si cualquiera de los dos muere, matar al otro y salir
wait -n $DASH_PID $BOT_PID
EXIT_CODE=$?
kill $DASH_PID $BOT_PID 2>/dev/null
exit $EXIT_CODE
