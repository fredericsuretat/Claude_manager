#!/usr/bin/env bash
# Lance Claude Control Web localement (mode dev / hors Docker)
# En production, préférer : docker compose up -d
set -e
cd "$(dirname "$0")"

# Bloquer si le container Docker tourne déjà sur le port 8765
if docker ps --filter "name=claude-control-web" --filter "status=running" --format '{{.Names}}' 2>/dev/null | grep -q claude-control-web; then
  echo "⚠️  Le container Docker 'claude-control-web' tourne déjà sur le port 8765."
  echo "   Utilise 'docker compose logs -f' pour voir les logs."
  echo "   Pour forcer le mode natif : docker stop claude-control-web && ./run.sh"
  exit 1
fi

unset ANTHROPIC_API_KEY

VENV="venv"

if [ ! -d "$VENV" ]; then
    echo "Création de l'environnement virtuel..."
    python3 -m venv "$VENV"
fi

source "$VENV/bin/activate"

if ! python -c "import fastapi" 2>/dev/null; then
    echo "Installation des dépendances..."
    pip install -r requirements.txt
fi

echo ""
echo "  Claude Control Web — http://localhost:8765"
echo "  Réseau local      — http://$(hostname -I | awk '{print $1}'):8765"
echo ""
python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --reload
