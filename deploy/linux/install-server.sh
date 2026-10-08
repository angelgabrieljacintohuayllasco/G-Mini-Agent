#!/usr/bin/env bash
# Instala o actualiza G-Mini en modo servidor (sin escritorio) como servicio de
# usuario de systemd. No necesita sudo: Python y las dependencias van en tu home.
#
#   bash install-server.sh                 # escucha en 127.0.0.1:8765
#   bash install-server.sh --tailscale     # escucha en la IP de Tailscale
#   bash install-server.sh --host 0.0.0.0  # toda la red (todas las rutas piden token)
#   bash install-server.sh --voz           # además Whisper, para dispositivos con micrófono
#
# Volver a correrlo actualiza el código y reinicia el servicio.
set -euo pipefail

REPO_URL="${GMINI_REPO_URL:-https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent.git}"
BRANCH="${GMINI_BRANCH:-main}"
APP_DIR="${GMINI_APP_DIR:-$HOME/g-mini-agent}"
DATA_DIR="${GMINI_HOME:-$HOME/.local/share/g-mini}"
CONF_DIR="$HOME/.config/g-mini"
ENV_FILE="$CONF_DIR/env"
UNIT_DIR="$HOME/.config/systemd/user"
PYTHON_VERSION="${GMINI_PYTHON:-3.13}"
HOST="127.0.0.1"
PORT="8765"
VOICE="${GMINI_VOICE:-0}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?falta la IP}"; shift 2 ;;
    --port) PORT="${2:?falta el puerto}"; shift 2 ;;
    --tailscale)
      command -v tailscale >/dev/null || fail "tailscale no está instalado"
      HOST="$(tailscale ip -4 | head -n1)"
      [ -n "$HOST" ] || fail "Tailscale no tiene IP; ¿está conectado?"
      shift ;;
    --voz|--voice) VOICE=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) fail "opción desconocida: $1" ;;
  esac
done

command -v git >/dev/null || fail "falta git (sudo apt install git)"
command -v systemctl >/dev/null || fail "este instalador usa systemd"

UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  UV="$HOME/.local/bin/uv"
  if [ ! -x "$UV" ]; then
    say "Instalando uv en ~/.local/bin"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  fi
fi

if [ -d "$APP_DIR/.git" ]; then
  say "Actualizando el código en $APP_DIR"
  git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  git -C "$APP_DIR" checkout --quiet "$BRANCH"
  git -C "$APP_DIR" merge --quiet --ff-only "origin/$BRANCH"
else
  say "Descargando G-Mini en $APP_DIR"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  say "Creando el entorno de Python $PYTHON_VERSION"
  "$UV" venv --quiet --python "$PYTHON_VERSION" "$APP_DIR/.venv"
fi
say "Instalando dependencias del modo servidor"
VIRTUAL_ENV="$APP_DIR/.venv" "$UV" pip install --quiet -r "$APP_DIR/backend/requirements-server.txt"
if [ "$VOICE" = "1" ]; then
  say "Instalando Whisper para reconocer voz (dispositivos y palabra de activación)"
  VIRTUAL_ENV="$APP_DIR/.venv" "$UV" pip install --quiet "faster-whisper>=1.0.0"
fi

mkdir -p "$DATA_DIR" "$CONF_DIR" "$UNIT_DIR"
chmod 700 "$CONF_DIR"
if [ ! -f "$ENV_FILE" ]; then
  say "Creando $ENV_FILE"
  umask 077
  cat > "$ENV_FILE" <<EOF
# Variables del servicio G-Mini. Tras cambiarlas: systemctl --user restart g-mini
GMINI_HOME=$DATA_DIR

# API keys (también se pueden guardar desde la app). Ejemplos:
# GMINI_KEY_GOOGLE_API=...
# GMINI_KEY_OPENAI_API=...
# GMINI_KEY_ANTHROPIC_API=...
# Vertex AI con una cuenta de servicio:
# GOOGLE_APPLICATION_CREDENTIALS=$CONF_DIR/vertex-sa.json
EOF
fi
chmod 600 "$ENV_FILE"

say "Escribiendo el servicio g-mini ($HOST:$PORT)"
cat > "$UNIT_DIR/g-mini.service" <<EOF
[Unit]
Description=G-Mini Agent (modo servidor)
Documentation=https://github.com/angelgabrieljacintohuayllasco/G-Mini-Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python -m backend.main --headless --host $HOST --port $PORT
Restart=always
RestartSec=5
NoNewPrivileges=yes
UMask=0077

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --quiet g-mini.service
systemctl --user restart g-mini.service

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo
  echo "El servicio se detiene al cerrar tu sesión. Para que siga corriendo 24/7:"
  echo "  sudo loginctl enable-linger $USER"
fi

say "Esperando a que responda"
for _ in $(seq 1 60); do
  if curl -fsS "http://$HOST:$PORT/api/v1/health" >/dev/null 2>&1; then
    echo
    echo "G-Mini está corriendo en http://$HOST:$PORT"
    echo "  Estado:   systemctl --user status g-mini"
    echo "  Logs:     journalctl --user -u g-mini -f"
    echo "  Keys:     $ENV_FILE"
    echo "  Datos:    $DATA_DIR"
    echo
    echo "Para emparejar otra PC o un dispositivo, pide un código de 6 dígitos:"
    echo "  curl -s -X POST http://$HOST:$PORT/api/v1/pairing \\"
    echo "    -H \"Authorization: Bearer \$(cat $DATA_DIR/data/runtime/session_token)\" \\"
    echo "    -H 'Content-Type: application/json' -d '{\"label\": \"Mi PC\", \"scopes\": [\"chat\", \"voice\", \"tasks\"]}'"
    exit 0
  fi
  sleep 1
done
fail "no respondió en 60 s; revisa: journalctl --user -u g-mini -n 50"
