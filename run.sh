#!/usr/bin/env bash
# Lance Claude Control Web localement
set -e
cd "$(dirname "$0")"

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
