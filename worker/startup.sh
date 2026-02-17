#!/bin/bash
# startup.sh — Da eseguire una volta sola sull'istanza EC2 dopo il primo boot
# Configura Docker, NVIDIA Container Toolkit, e lancia il worker AudioLecture

set -e

echo "=== AudioLecture Worker Setup ==="

# 1. Installa Docker se non presente
if ! command -v docker &> /dev/null; then
    echo "[1/5] Installazione Docker..."
    sudo apt-get update -q
    sudo apt-get install -y docker.io
    sudo systemctl enable docker
    sudo systemctl start docker
    sudo usermod -aG docker ubuntu
    echo "    Docker installato."
else
    echo "[1/5] Docker già presente."
fi

# 2. Installa NVIDIA Container Toolkit
if ! dpkg -l | grep -q nvidia-container-toolkit; then
    echo "[2/5] Installazione NVIDIA Container Toolkit..."
    distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L "https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list" \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update -q
    sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    echo "    NVIDIA Container Toolkit installato."
else
    echo "[2/5] NVIDIA Container Toolkit già presente."
fi

# 3. Crea directory progetto
echo "[3/5] Setup directory..."
sudo mkdir -p /opt/audiolecture
sudo chown ubuntu:ubuntu /opt/audiolecture

# 4. Build immagine Docker
echo "[4/5] Build immagine Docker (può richiedere qualche minuto)..."
cd /opt/audiolecture
docker build -t audiolecture-worker:latest .
echo "    Immagine costruita."

# 5. Avvia container con auto-restart
echo "[5/5] Avvio container..."

# Ferma eventuale container precedente
docker stop audiolecture-worker 2>/dev/null || true
docker rm audiolecture-worker 2>/dev/null || true

docker run -d \
    --name audiolecture-worker \
    --restart=always \
    --gpus all \
    -p 8000:8000 \
    -e TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:?Variabile TELEGRAM_BOT_TOKEN non impostata}" \
    -e BOT_API_SERVER_URL="${BOT_API_SERVER_URL:?Variabile BOT_API_SERVER_URL non impostata}" \
    -e WHISPER_MODEL="${WHISPER_MODEL:-small}" \
    audiolecture-worker:latest

echo ""
echo "=== Setup completato! ==="
echo "Attendi 30-60 secondi per il caricamento del modello Whisper, poi:"
echo "  curl http://localhost:8000/health"
echo ""
echo "Per vedere i log:"
echo "  docker logs -f audiolecture-worker"
