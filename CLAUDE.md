# CLAUDE.md — AudioLecture

## Panoramica Progetto

AudioLecture è un bot Telegram completamente gratuito che trascrive registrazioni audio di lezioni universitarie in PDF. Il servizio è rivolto a studenti italiani, monetizzato esclusivamente tramite sponsorizzazioni dirette. Non esiste alcun piano a pagamento: il servizio è e resterà gratuito per tutti gli utenti. L'obiettivo è offrire trascrizioni di qualità a costo zero per l'utente, con un'architettura a costi operativi minimi.

Bot Telegram: `@AudioLectureBot`

---

## Architettura

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│   Telegram    │────▶│   Bot API Server  │────▶│        n8n           │
│   (utente)    │◀────│   (t3.micro/c6g)  │◀────│      (EC2 AWS)       │
└──────────────┘     └──────────────────┘     └──────────┬───────────┘
                                                         │
                                                         ▼
                                                ┌──────────────────────┐
                                                │  EC2 g4dn.xlarge     │
                                                │  On-Demand (GPU T4)  │
                                                │  Whisper + PDF       │
                                                └──────────────────────┘
```

### Componenti

| Componente | Ruolo | Hosting |
|---|---|---|
| **Bot API Server** | Proxy Telegram locale, elimina limite 20 MB download | EC2 t3.micro (lancio) → c6g.medium (scala) |
| **n8n** | Orchestratore: riceve webhook, gestisce coda, accende/spegne EC2, invia risultati | EC2 AWS (stessa region del worker) |
| **EC2 GPU On-Demand** | Trascrizione audio con Whisper + generazione PDF | EC2 g4dn.xlarge on-demand, accesa/spenta tramite n8n |

### Flusso Completo

1. L'utente invia un audio/file vocale al bot Telegram
2. Il Bot API Server locale riceve il file (nessun limite di dimensione)
3. n8n riceve il webhook e:
   - Invia immediatamente il **messaggio sponsor** all'utente
   - Invia conferma: "📝 Trascrizione in coda, riceverai il PDF appena pronta"
   - Salva in coda: `file_id` (Telegram) + `chat_id` + `timestamp`
4. Un processo schedulato in n8n controlla se l'istanza EC2 è attiva
5. Se istanza ferma e coda non vuota, n8n avvia l'istanza (start)
6. Quando attiva (~1-2 minuti startup), l'EC2:
   - Docker container si avvia automaticamente (--restart=always)
   - Scarica l'audio da Telegram usando il `file_id` (non scade mai)
   - Trascrive con Whisper
   - Genera il PDF formattato (con logo sponsor nel footer)
   - Invia il PDF all'utente via bot Telegram
7. Quando la coda è vuota, n8n ferma l'istanza (stop)

### Niente S3

L'audio non viene salvato su S3. Il `file_id` di Telegram non scade e permette il download in qualsiasi momento tramite il Bot API Server locale. Se l'utente cancella il messaggio prima della trascrizione, il bot notifica e chiede di reinviare.

---

## Primo Utilizzo / Onboarding

Al primo messaggio dell'utente al bot, PRIMA di qualsiasi trascrizione:

1. Messaggio di benvenuto con descrizione del servizio
2. Link all'informativa privacy (pagina web statica)
3. Pulsante inline: **"✅ Accetto le condizioni e acconsento al trattamento dei dati"**
4. Il bot registra: `user_id`, `username`, `timestamp_consenso`, `versione_informativa`
5. Da questo momento il servizio è attivo

### Dati raccolti (GDPR)

- **Raccolti:** user ID Telegram, username, nome, dati di utilizzo (timestamp, durata audio, frequenza)
- **NON raccolti:** numero di telefono (non necessario)
- **Base giuridica:** consenso esplicito (pubblicità), esecuzione contrattuale (servizio)
- **Conservazione:** fino a revoca del consenso o cancellazione account
- **Trasferimento extra-UE:** sì (AWS), con garanzie adeguate

---

## Componente 1: Bot API Server

### Setup

Il Bot API Server è il software ufficiale di Telegram per hostare l'API Bot in locale.

- **Repository:** https://github.com/tdlib/telegram-bot-api
- **Istanza:** EC2 t3.micro (lancio), c6g.medium (scala 10.000+ utenti)
- **Disco:** 30-50 GB EBS per file in transito
- **Porta:** 8081 (default)
- **Rete:** accessibile solo dall'IP di n8n (firewall/security group)

### Configurazione

```bash
# Build
git clone --recursive https://github.com/tdlib/telegram-bot-api.git
cd telegram-bot-api
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
cmake --build . --target install

# Run
telegram-bot-api \
  --api-id=<APP_API_ID> \
  --api-hash=<APP_API_HASH> \
  --http-port=8081 \
  --local
```

Per ottenere `api-id` e `api-hash`: https://my.telegram.org/apps

### Note

- Il flag `--local` abilita il download di file fino a 2 GB
- I file scaricati vengono salvati temporaneamente su disco, pulire periodicamente
- Configurare un systemd service per il restart automatico

---

## Componente 2: n8n Workflow

### Infrastruttura n8n

- **Hosting:** EC2 AWS (stessa VPC del worker GPU)
- **Istanza:** t3.medium o t3.large (2-4 vCPU, 4-8 GB RAM)
- **Requisiti:** Docker
- **Security Group:** sg-0ced7b87340173ff9 (condiviso con worker GPU)
- **Credenziali necessarie:**
  - Token Bot Telegram
  - AWS Access Key (per gestione EC2 worker)
  - URL del Bot API Server locale

### Credenziale Telegram in n8n

Nelle credenziali Telegram di n8n, impostare:
- **API URL:** `http://<IP-BOT-API-SERVER>:8081` (invece del default `https://api.telegram.org`)

### Workflow Principale

**Trigger:** Telegram Trigger (webhook su messaggi audio/vocali)

**Nodi:**

1. **Telegram Trigger** — riceve il messaggio
2. **Switch** — controlla se l'utente ha già dato il consenso (query al DB)
   - Se NO → flusso onboarding (punto 3a)
   - Se SÌ → flusso trascrizione (punto 3b)
3a. **Onboarding** — invia informativa + pulsante consenso → salva consenso nel DB
3b. **Sponsor Message** — invia il messaggio pubblicitario dello sponsor attivo
4. **Conferma** — invia "📝 Trascrizione in coda..."
5. **Salva in coda** — inserisce nel DB: `file_id`, `chat_id`, `user_id`, `timestamp`, `status: pending`
6. **Cron (ogni 2-5 min)** — workflow separato che:
   - Controlla se ci sono job `pending` in coda
   - Se sì, verifica stato istanza EC2 worker (AWS DescribeInstances)
   - Se stopped → avvia istanza (AWS StartInstances)
   - Se running → verifica health endpoint `/health`
   - Quando ready, invia i job alla API FastAPI sull'EC2 (POST /transcribe)
7. **Callback** — l'EC2 chiama un webhook n8n quando la trascrizione è completa
8. **Invio PDF** — n8n invia il PDF all'utente via Telegram
9. **Cleanup** — aggiorna status in coda a `completed`
10. **Shutdown check** — se la coda è vuota per >10 min, ferma l'istanza EC2 (AWS StopInstances)

### Workflow Attuale (Implementato)

**Workflow ID:** `s-W_XN6Sv06HcbUHf-mMC`
**Nome:** AudioLectureBot
**Stato:** Inattivo (da attivare dopo setup worker)

**Nodi implementati:**

1. **Telegram Trigger** → riceve messaggi e callback
2. **È un Callback?** (IF) → distingue callback da messaggi normali
3. **Consenso accettato?** (IF) → verifica callback consenso
4. **Salva Consenso** (Google Sheets) → registra utente
5. **Conferma Consenso** (Telegram) → callback query answer
6. **Messaggio Post-Consenso** (Telegram) → conferma all'utente
7. **Cerca Utente** (Google Sheets) → query utente per user_id
8. **Ha dato consenso?** (IF) → verifica se utente esiste
9. **Messaggio Onboarding** (Telegram) → invia informativa + pulsante
10. **È un audio?** (IF) → verifica presenza audio/voice/document
11. **Conferma in Coda** (Telegram) → "📝 Trascrizione in coda"
12. **Salva in Coda** (Google Sheets) → inserisce job pending
13. **Messaggio Benvenuto** (Telegram) → se non è audio

**Database Google Sheets:**
- **Sheet ID:** `1h3zVHs8WkwHaLiewgug0CPnm9SjMDBXQmw18vtnNTVw`
- **Foglio "users":** user_id, username, first_name, consent_timestamp, consent_version
- **Foglio "transcription_queue":** id, user_id, chat_id, file_id, file_size, duration_seconds, status, created_at, started_at, completed_at, error_message

### Workflow da Implementare

1. **queue-processor.json** (cron ogni 2-5 min)
   - Query Google Sheets: job con status=pending
   - Se presenti: check EC2 instance state
   - Se stopped: StartInstances
   - Polling fino a running + health check OK
   - POST job a worker `/transcribe`
   - Update status=processing in Google Sheets

2. **transcription-callback.json** (webhook)
   - Riceve POST da worker quando trascrizione completa
   - Download PDF da URL fornito dal worker
   - Invia PDF all'utente via Telegram
   - Update status=completed in Google Sheets
   - Se coda vuota da >10 min: StopInstances

### Database Coda

**Attualmente:** Google Sheets (MVP)
**Futuro:** SQLite o PostgreSQL (su stesso EC2 di n8n) con queste tabelle:

```sql
-- Utenti e consenso
CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    consent_timestamp TIMESTAMP NOT NULL,
    consent_version TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Coda trascrizioni
CREATE TABLE transcription_queue (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(user_id),
    chat_id BIGINT NOT NULL,
    file_id TEXT NOT NULL,
    file_size INTEGER,
    duration_seconds INTEGER,
    status TEXT DEFAULT 'pending',  -- pending, processing, completed, failed
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT
);

-- Sponsor e impression
CREATE TABLE sponsor_impressions (
    id SERIAL PRIMARY KEY,
    sponsor_id TEXT NOT NULL,
    user_id BIGINT,
    impression_type TEXT,  -- 'in_transcription', 'broadcast', 'pdf_footer'
    clicked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Sponsor attivi
CREATE TABLE sponsors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan TEXT NOT NULL,  -- 'base', 'pro', 'premium'
    message_text TEXT,
    message_url TEXT,
    pdf_logo_url TEXT,
    cpc DECIMAL(4,2),
    monthly_min DECIMAL(6,2),
    active BOOLEAN DEFAULT TRUE,
    start_date DATE,
    end_date DATE
);
```

---

## Componente 3: EC2 GPU — Servizio di Trascrizione

### Istanza

- **Tipo:** g4dn.xlarge **On-Demand** (non spot)
- **GPU:** NVIDIA T4 (16 GB VRAM)
- **vCPU:** 4
- **RAM:** 16 GB
- **AMI:** Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.9 (Ubuntu 24.04)
  - AMI ID: `ami-0f3d7b789119ccbfa`
- **Region:** eu-west-1 (Irlanda)
- **Storage:** 50 GB EBS gp3, **DeleteOnTermination: false** (persiste quando ferma)
- **Shutdown Behavior:** Stop (non Terminate)
- **Security Group:** sg-0ced7b87340173ff9 (condiviso con n8n)
- **Key Pair:** EvolviDigitalWebApp
- **Startup Time:** ~1-2 minuti (da stopped a running)

### Costi Operativi

| Scenario | Compute | Storage | Totale/mese |
|---|---|---|---|
| Sempre accesa 24/7 | $380 | $5 | **~$385** |
| 100 trascrizioni/mese (50h totali) | $26 | $5 | **~$31** |
| Istanza ferma | $0 | $5 | **$5** |

**Nota:** On-Demand costa ~$0.526/ora running, ma garantisce:
- ✅ Startup rapido (1-2 min vs 3-4 min spot)
- ✅ Persistenza dati (volume EBS)
- ✅ Gestione semplificata (start/stop via n8n)
- ✅ Nessun rischio interruzione
- ✅ Docker container con --restart=always

### Software Stack

- **Docker** (container orchestration)
- **Python 3.11+**
- **OpenAI Whisper** (modello: `small`)
- **FastAPI** (server HTTP per ricevere job da n8n, porta 8000)
- **ReportLab** (generazione PDF)
- **requests** (per interazione con Telegram API)
- **PyTorch** con supporto GPU CUDA (già incluso in AMI)

### API FastAPI

```
POST /transcribe
Body: {
    "file_id": "...",
    "chat_id": 123456,
    "user_id": 789,
    "sponsor": {
        "name": "Sponsor Name",
        "logo_url": "https://...",
        "footer_text": "Offerto da Sponsor Name"
    },
    "callback_url": "https://n8n-host/webhook/transcription-complete"
}

Response: { "status": "queued", "job_id": "..." }
```

### Flusso interno EC2

1. Riceve job via FastAPI
2. Scarica audio da Telegram: `GET http://<BOT-API-SERVER>:8081/file/bot<TOKEN>/<file_path>`
   - Prima chiama `getFile` per ottenere il `file_path` dal `file_id`
3. Trascrive con Whisper (modello `small`)
4. Genera PDF con ReportLab:
   - Header: "AudioLecture — Trascrizione Audio"
   - Corpo: testo trascritto con timestamp ogni paragrafo
   - Footer: logo e testo sponsor
5. Invia PDF all'utente via Telegram Bot API
6. Chiama il callback_url di n8n per confermare il completamento
7. Cancella file temporanei (audio + PDF)

### Deployment con Docker

Il worker gira in un container Docker con auto-restart:

```bash
# Setup una volta sola (dopo lancio istanza)
docker build -t audiolecture-worker /opt/audiolecture
docker run -d \
  --name audiolecture-worker \
  --restart=always \
  --gpus all \
  -p 8000:8000 \
  -e TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
  -e BOT_API_SERVER_URL=$BOT_API_SERVER_URL \
  audiolecture-worker:latest
```

**Vantaggi --restart=always:**
- Docker riavvia il container automaticamente ad ogni boot dell'istanza
- Startup rapido: 30-60 secondi da "istanza running" a "worker ready"
- Nessun user data script necessario dopo setup iniziale

Il container espone:
- **Porta 8000:** FastAPI server
- **Endpoint `/transcribe`:** riceve job da n8n
- **Endpoint `/health`:** health check per n8n
- **Processing:** sequenziale (un job alla volta, GPU bottleneck)

---

## Pubblicità

### Tre canali

| Canale | Momento | CTR atteso | CPC | Come |
|---|---|---|---|---|
| **In-Trascrizione** | Subito dopo l'invio audio | 3-4% | €0.25 | Messaggio Telegram con testo + link inline |
| **Broadcast** | Schedulato (max 2/settimana) | 0.5-1% | €0.10 | Messaggio Telegram a tutti gli utenti |
| **Banner PDF** | Nel documento consegnato | N/A | Fee fissa | Logo + testo nel footer del PDF |

### Piani inserzionisti

| Piano | Include | CPC | Minimo/mese |
|---|---|---|---|
| **Base** | Broadcast (2/settimana) | €0.10 | €50 |
| **Pro** | In-Trascrizione (slot esclusivo) | €0.25 | €100 |
| **Premium** | Broadcast + In-Trascrizione + Banner PDF + Report | €0.20 | €200 |

### Tracking

Ogni link sponsor deve passare per un redirect tracciato (es. `https://audiolecture.it/go/<sponsor_id>/<impression_id>`) che:
1. Registra il click nel DB (`sponsor_impressions.clicked = true`)
2. Redirige all'URL destinazione dello sponsor

---

## Modello di Servizio

Il servizio è **completamente gratuito** per tutti gli utenti. Non esiste un piano premium. La monetizzazione avviene esclusivamente tramite sponsorizzazioni.

---

## Struttura File del Progetto

```
audiolecture/
├── CLAUDE.md                  # Questo file
├── README.md                  # Documentazione pubblica
├── ec2-worker-config.txt      # Comando AWS CLI per lanciare worker
│
├── bot-api-server/
│   ├── Dockerfile             # Container per Bot API Server
│   ├── docker-compose.yml
│   └── setup.sh               # Script di setup su EC2
│
├── n8n/
│   ├── workflows/
│   │   ├── main-webhook.json          # Workflow principale (trigger Telegram)
│   │   ├── onboarding.json            # Flusso primo utilizzo
│   │   ├── queue-processor.json       # Cron che gestisce la coda e EC2
│   │   ├── transcription-callback.json # Riceve risultato da EC2
│   │   ├── broadcast-sponsor.json     # Invio messaggi sponsor schedulati
│   │   └── ec2-manager.json           # Accensione/spegnimento EC2
│   ├── db/
│   │   └── schema.sql                 # Schema database PostgreSQL
│   └── setup.md                       # Istruzioni setup n8n
│
├── worker/
│   ├── requirements.txt       # Dipendenze Python
│   ├── worker.py              # FastAPI server principale
│   ├── transcriber.py         # Wrapper Whisper
│   ├── pdf_generator.py       # Generazione PDF con ReportLab
│   ├── telegram_client.py     # Download audio + invio PDF via Telegram
│   ├── config.py              # Configurazione (env vars)
│   ├── Dockerfile             # Container per EC2 GPU
│   └── startup.sh             # User data script per EC2
│
├── infra/
│   ├── ec2-spot-request.json  # Template richiesta spot
│   ├── security-groups.json   # Regole firewall
│   ├── iam-policy.json        # Policy IAM per n8n
│   └── setup-guide.md         # Guida setup infrastruttura AWS
│
├── legal/
│   ├── privacy-policy.md      # Informativa privacy (GDPR)
│   ├── terms-of-service.md    # Termini di servizio
│   └── sponsor-contract.md    # Template contratto sponsor
│
└── docs/
    ├── business-plan.md       # Piano economico
    ├── sponsor-media-kit.md   # Media kit per inserzionisti
    └── onboarding-flow.md     # Documentazione flusso utente
```

---

## Variabili d'Ambiente

```env
# Telegram
TELEGRAM_BOT_TOKEN=<token dal BotFather>
TELEGRAM_API_ID=<da my.telegram.org>
TELEGRAM_API_HASH=<da my.telegram.org>
BOT_API_SERVER_URL=http://<ip>:8081

# AWS
AWS_ACCESS_KEY_ID=<key>
AWS_SECRET_ACCESS_KEY=<secret>
AWS_REGION=eu-west-1
EC2_WORKER_INSTANCE_ID=<instance-id dopo lancio>
EC2_WORKER_AMI=ami-0f3d7b789119ccbfa
EC2_INSTANCE_TYPE=g4dn.xlarge
EC2_SECURITY_GROUP=sg-0ced7b87340173ff9
EC2_KEY_PAIR=EvolviDigitalWebApp
EC2_WORKER_PRIVATE_IP=<ip-privato dopo lancio>

# Database (Google Sheets per MVP)
GOOGLE_SHEET_ID=<id-del-foglio>

# Whisper
WHISPER_MODEL=small

# App
SPONSOR_REDIRECT_BASE_URL=https://audiolecture.it/go
```

---

## Priorità di Sviluppo

### Stato Attuale

**✅ Fase 2 — Onboarding e Compliance** (COMPLETATA)
- ✅ Workflow n8n base: Telegram Trigger + onboarding
- ✅ Flusso consenso GDPR con pulsante inline
- ✅ Database Google Sheets (users + transcription_queue)
- ✅ Gestione utenti e verifica consenso
- ✅ Accodamento trascrizioni con `file_id`
- ⏸️ Informativa privacy (pagina web) - da fare
- ⏸️ Limite trascrizioni/giorno - da implementare

**🚧 Fase 1 — MVP Core** (IN CORSO)
- ✅ EC2 Worker configurato (g4dn.xlarge on-demand)
- ✅ AMI Deep Learning con GPU T4 selezionata
- ✅ Security Group configurato
- ⏳ Quota vCPU GPU richiesta (in attesa approvazione)
- ⏸️ Docker worker: FastAPI + Whisper + PDF
- ⏸️ Workflow n8n: queue processor (start/stop EC2)
- ⏸️ Workflow n8n: transcription callback
- ⏸️ Test end-to-end
- ⏸️ Bot API Server setup (opzionale per MVP)

### Prossimi Step

1. **Approvazione quota GPU** (wait)
2. **Lancio istanza EC2 worker**
3. **Setup Docker container** con worker Python
4. **Sviluppo worker.py** (FastAPI + Whisper + PDF)
5. **Workflow n8n queue processor**
6. **Test trascrizione end-to-end**

### Fase 3 — Pubblicità (settimana 4)
1. Sistema sponsor con messaggio in-trascrizione
2. Tracking click con redirect
3. Banner sponsor nel footer PDF
4. Dashboard basic per conteggio impression/click

### Fase 4 — Scaling (settimane 5-6)
1. Gestione automatica EC2 spot (accensione/spegnimento)
2. Sistema broadcast schedulato

---

## Comandi Utili

```bash
# Test Whisper locale
whisper audio.mp3 --model small --language it --output_format txt

# Test Bot API Server
curl http://localhost:8081/bot<TOKEN>/getMe

# Test download file via Bot API locale
curl http://localhost:8081/bot<TOKEN>/getFile?file_id=<FILE_ID>

# Lanciare istanza worker (prima volta)
bash ec2-worker-config.txt

# Verificare stato istanza
aws ec2 describe-instances \
  --instance-ids <instance-id> \
  --query 'Reservations[0].Instances[0].State.Name'

# Avviare istanza (se stopped)
aws ec2 start-instances --instance-ids <instance-id>

# Fermare istanza (se running)
aws ec2 stop-instances --instance-ids <instance-id>

# Test health endpoint worker
curl http://<worker-ip>:8000/health

# Test trascrizione (da n8n o locale)
curl -X POST http://<worker-ip>:8000/transcribe \
  -H "Content-Type: application/json" \
  -d '{
    "file_id": "...",
    "chat_id": 123456,
    "user_id": 789,
    "callback_url": "https://n8n-url/webhook/callback"
  }'

# SSH nell'istanza
ssh -i ~/.ssh/EvolviDigitalWebApp.pem ubuntu@<instance-public-ip>

# Verificare quota vCPU GPU
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-DB2E81BA
```

---

## Setup EC2 Worker - Checklist

### Pre-requisiti

1. **Quota vCPU GPU richiesta** ⚠️
   - Gli account AWS nuovi hanno quota 0 per istanze GPU
   - Richiedere aumento quota: https://console.aws.amazon.com/servicequotas/
   - Servizio: "EC2"
   - Quota: "Running On-Demand G and VT instances"
   - Valore richiesto: 4 vCPU (per 1x g4dn.xlarge)
   - Tempo approvazione: poche ore (se account verificato) o 24-48h

2. **Credenziali AWS configurate**
   ```bash
   aws configure
   # Access Key ID
   # Secret Access Key
   # Region: eu-west-1
   ```

3. **Key pair SSH creata**
   - Nome: EvolviDigitalWebApp
   - File: `~/.ssh/EvolviDigitalWebApp.pem`

### Lancio Istanza

```bash
# Verifica quota approvata
aws service-quotas get-service-quota \
  --service-code ec2 \
  --quota-code L-DB2E81BA

# Lancia istanza (configurazione salvata)
bash ec2-worker-config.txt

# Salva instance ID per riferimenti futuri
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=AudioLectureBot" \
  --query 'Reservations[0].Instances[0].InstanceId' \
  --output text)
echo "INSTANCE_ID=$INSTANCE_ID" >> .env
```

### Setup Docker Worker

```bash
# 1. SSH nell'istanza (aspetta 2-3 min che si avvii)
ssh -i ~/.ssh/EvolviDigitalWebApp.pem ubuntu@<instance-public-ip>

# 2. Installa Docker (se non presente in AMI)
sudo apt update
sudo apt install -y docker.io
sudo usermod -aG docker ubuntu
# Logout e re-login per applicare gruppo docker

# 3. Installa NVIDIA Container Toolkit
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
sudo apt update
sudo apt install -y nvidia-container-toolkit
sudo systemctl restart docker

# 4. Crea directory progetto
sudo mkdir -p /opt/audiolecture
sudo chown ubuntu:ubuntu /opt/audiolecture
cd /opt/audiolecture

# 5. Copia file worker (Dockerfile, requirements.txt, worker.py, etc.)
# Usa scp o git clone per trasferire i file

# 6. Build immagine Docker
docker build -t audiolecture-worker .

# 7. Run container con auto-restart
docker run -d \
  --name audiolecture-worker \
  --restart=always \
  --gpus all \
  -p 8000:8000 \
  -e TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN \
  -e BOT_API_SERVER_URL=$BOT_API_SERVER_URL \
  audiolecture-worker:latest

# 8. Verifica container running
docker ps
curl http://localhost:8000/health
```

### Test Worker

```bash
# Dall'esterno (sostituisci <worker-ip>)
curl http://<worker-ip>:8000/health

# Dovrebbe rispondere:
# {"status": "healthy", "gpu": "available"}
```

### Gestione Istanza da n8n

n8n controlla periodicamente la coda e gestisce l'istanza:

```javascript
// Workflow n8n - Queue Processor (ogni 2-5 min)

// 1. Query Google Sheets per job pending
const pendingJobs = $node["Query Queue"].json;

if (pendingJobs.length > 0) {
  // 2. Controlla stato istanza
  const state = await aws.describeInstances(instanceId);

  if (state === 'stopped') {
    // 3. Avvia istanza
    await aws.startInstances(instanceId);

    // 4. Aspetta che sia running (polling ogni 10s)
    await waitForState('running');

    // 5. Aspetta health check (max 2 min)
    await waitForHealth('http://<worker-ip>:8000/health');
  }

  // 6. Invia job a worker
  for (const job of pendingJobs) {
    await fetch('http://<worker-ip>:8000/transcribe', {
      method: 'POST',
      body: JSON.stringify(job)
    });
  }
} else {
  // Coda vuota
  const state = await aws.describeInstances(instanceId);

  if (state === 'running') {
    // Aspetta 10 min di idle prima di fermare
    const lastJob = await getLastCompletedJobTime();
    const idleMinutes = (Date.now() - lastJob) / 1000 / 60;

    if (idleMinutes > 10) {
      await aws.stopInstances(instanceId);
    }
  }
}
```

### Configurazione Security Group

Il security group `sg-0ced7b87340173ff9` è condiviso tra n8n e worker:

**Regole Inbound:**
- SSH (22): Il tuo IP
- HTTPS (443): 0.0.0.0/0 (webhook Telegram → n8n)
- HTTP (80): 0.0.0.0/0 (webhook Telegram → n8n)
- Custom TCP (8000): sg-0ced7b87340173ff9 (comunicazione interna n8n ↔ worker)

**Vantaggi:**
- Comunicazione interna via IP privato VPC (no costi traffico)
- Worker non esposto a internet pubblico
- Solo n8n può chiamare worker

---

## Note per Claude Code

### Tecnologie

- Il progetto usa **Python 3.11+** per il worker e **n8n** (Node.js) per l'orchestrazione
- Worker gira in **Docker container** con `--restart=always` per auto-start
- I workflow n8n sono file JSON esportabili/importabili
- Database MVP: **Google Sheets** (users + transcription_queue)

### Infrastruttura

- Worker: **Ubuntu 24.04** con GPU NVIDIA T4 (Deep Learning OSS AMI)
- AMI include: PyTorch 2.9, NVIDIA drivers, CUDA toolkit
- Istanza: **On-Demand** (non spot) per startup rapido e gestione semplice
- Storage: **EBS persistente** (50GB, non cancellato quando istanza ferma)
- Security: **VPC privato**, worker accessibile solo da n8n via IP privato
- Configurazione salvata in: `ec2-worker-config.txt`

### Comportamento

- Tutti i file temporanei (audio scaricati, PDF generati) vanno cancellati dopo l'invio
- Il bot deve rispondere in **italiano**
- I messaggi del bot devono essere concisi e usare emoji con moderazione
- La generazione PDF usa **ReportLab** (no WeasyPrint, no wkhtmltopdf)
- Per i test, usare audio MP3 di durata variabile (5 min, 30 min, 60 min, 90 min)
- Il `file_id` di Telegram **non scade mai**, non serve salvare l'audio su S3
- Ogni interazione Telegram passa dal Bot API Server locale, mai da `api.telegram.org`

### Costi Operativi (stimati)

| Componente | Utilizzo | Costo/mese |
|---|---|---|
| n8n (t3.medium sempre on) | 730h/mese | ~$30 |
| Worker GPU (100 trascrizioni, ~50h) | ~50h/mese | ~$26 |
| Storage EBS Worker (50GB) | Sempre | ~$5 |
| Bot API Server (t3.micro) | 730h/mese | ~$8 |
| Traffico dati | Trascurabile | ~$2 |
| **Totale** | | **~$71/mese** |

Con scaling (500 trascrizioni/mese): ~$100-120/mese

---

## Changelog

### 2026-02-17 - Configurazione EC2 Worker
- ✅ Scelta architettura: **On-Demand** invece di Spot per semplicità e user experience
- ✅ AMI selezionata: Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.9 (Ubuntu 24.04)
- ✅ Configurazione istanza: g4dn.xlarge, 50GB EBS persistente, shutdown=stop
- ✅ Security Group configurato: sg-0ced7b87340173ff9 (condiviso n8n + worker)
- ✅ Deploy strategy: Docker con --restart=always
- ✅ Comando lancio salvato: `ec2-worker-config.txt`
- ⏳ Quota vCPU GPU richiesta (in attesa approvazione AWS)
- 📝 Workflow n8n base completato e testato (onboarding + coda)

**Decisioni architetturali:**
- On-Demand vs Spot: +$23/mese ma startup 1-2 min (vs 3-4 min) e gestione semplificata
- Docker: containerizzazione worker per portabilità e auto-restart
- VPC interno: comunicazione n8n ↔ worker via IP privato (sicurezza + no costi traffico)
- EBS persistente: volume non cancellato per permettere stop/start rapido

**Prossimi step:**
1. Aspettare approvazione quota GPU
2. Lanciare istanza con comando salvato
3. Setup Docker + worker Python (FastAPI + Whisper + PDF)
4. Implementare workflow n8n queue processor
5. Test end-to-end
