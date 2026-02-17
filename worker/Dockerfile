# Base image: PyTorch con CUDA (compatibile AMI Deep Learning Ubuntu 24.04)
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

# Evita prompt interattivi durante apt
ENV DEBIAN_FRONTEND=noninteractive

# Dipendenze sistema (ffmpeg necessario per Whisper)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia e installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Scarica il modello faster-whisper durante il build (evita download al primo avvio)
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

# Copia sorgenti
COPY config.py transcriber.py pdf_generator.py telegram_client.py worker.py ./

# Variabili d'ambiente (override con docker run -e)
ENV WHISPER_MODEL=small
ENV TEMP_DIR=/tmp/audiolecture

EXPOSE 8000

CMD ["uvicorn", "worker:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
