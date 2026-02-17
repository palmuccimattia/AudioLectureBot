# 🎙️ AudioLecture — Bot Telegram per Trascrizione Audio di Lezioni

> **Progetto esplorativo** · Febbraio 2026
> Bot Telegram gratuito che trascrive registrazioni audio universitarie in PDF, monetizzato tramite sponsorizzazioni.

---

## Indice

- [Idea e obiettivo](#idea-e-obiettivo)
- [Architettura](#architettura)
- [Stack tecnologico](#stack-tecnologico)
- [Flusso operativo](#flusso-operativo)
- [Worker EC2 — Componente Core](#worker-ec2--componente-core)
- [Workflow n8n](#workflow-n8n)
- [Modello economico](#modello-economico)
- [Confronto con Turboscribe.ai](#confronto-con-turboscribeai)
- [Perché il progetto si è fermato](#perché-il-progetto-si-è-fermato)
- [Struttura del repository](#struttura-del-repository)

---

## Idea e obiettivo

AudioLecture nasce dall'osservazione che migliaia di studenti italiani registrano le lezioni universitarie ma poi faticano a rielaborarle. L'obiettivo era costruire un bot Telegram **completamente gratuito** che riceve un file audio e restituisce un PDF con la trascrizione completa e timestamp.

La monetizzazione sarebbe avvenuta esclusivamente tramite **sponsorizzazioni** (messaggio pubblicitario inviato prima di ogni trascrizione), senza alcun piano a pagamento.

---

## Architettura

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│   Telegram    │────▶│   Bot API Server  │────▶│        n8n           │
│   (utente)    │◀────│   (proxy locale)  │◀────│   (orchestratore)    │
└──────────────┘     └──────────────────┘     └──────────┬───────────┘
                                                         │
                                              Start/Stop EC2
                                                         │
                                                         ▼
                                                ┌──────────────────────┐
                                                │  EC2 g4dn.xlarge     │
                                                │  NVIDIA T4 GPU       │
                                                │  faster-whisper      │
                                                │  FastAPI + ReportLab │
                                                └──────────────────────┘
```

### Componenti

| Componente | Ruolo | Hosting |
|---|---|---|
| **Bot API Server** | Proxy Telegram locale (nessun limite 20 MB) | EC2 t3.micro |
| **n8n** | Orchestratore: webhook, coda, start/stop EC2 | EC2 t3.medium (sempre acceso) |
| **Worker GPU** | Trascrizione audio + generazione PDF | EC2 g4dn.xlarge **on-demand** (acceso solo quando serve) |

La scelta **on-demand** (invece di spot) garantisce startup in 1–2 minuti e zero rischio di interruzioni. Il costo fisso del worker è ~$5/mese quando fermo (solo storage EBS).

---

## Stack tecnologico

### Worker (Python)

| Libreria | Versione | Scopo |
|---|---|---|
| `faster-whisper` | 1.1.0 | Trascrizione audio con CTranslate2 (INT8 GPU) |
| `fastapi` | 0.115.0 | Server HTTP per ricevere job da n8n |
| `uvicorn` | 0.30.6 | ASGI server |
| `reportlab` | 4.2.2 | Generazione PDF |
| `requests` | 2.32.3 | Download audio da Telegram, callback a n8n |

**Base image Docker:** `pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime`
**GPU:** NVIDIA T4 — compute type INT8 via CTranslate2

### Infrastruttura

- **AWS EC2 g4dn.xlarge** — 4 vCPU, 16 GB RAM, GPU T4 16 GB VRAM
- **AMI:** Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.9 (Ubuntu 24.04)
- **n8n** — orchestrazione workflow via self-hosted
- **Google Sheets** — database MVP (users + transcription_queue)
- **Docker** con `--restart=always` per auto-start al boot dell'istanza

---

## Flusso operativo

```
1. Utente invia audio al bot Telegram
         │
         ▼
2. n8n riceve il webhook
   ├─ Se primo utilizzo → flusso GDPR (consenso inline)
   └─ Se utente registrato:
      ├─ Invia messaggio sponsor
      ├─ Conferma "📝 Trascrizione in coda..."
      └─ Salva job su Google Sheets (status: pending)
         │
         ▼
3. Cron n8n (ogni 3 min)
   ├─ Legge job pending da Sheets
   ├─ Controlla stato EC2 → se stopped: StartInstances
   ├─ Polling health check su /health
   └─ POST /transcribe al worker
         │
         ▼
4. Worker EC2
   ├─ Download audio da Telegram (via file_id)
   ├─ Trascrizione con faster-whisper (modello small, INT8, GPU)
   ├─ Generazione PDF con timestamp (ReportLab)
   ├─ Invio PDF all'utente via Telegram
   └─ Callback a n8n → update status completed
         │
         ▼
5. n8n verifica coda vuota da >10 min → StopInstances
```

---

## Worker EC2 — Componente Core

### API FastAPI

```
GET  /health       → {"status": "healthy", "gpu": true, "queue_size": 0}

POST /transcribe   → {"status": "queued", "job_id": "..."}
Body: {
    "file_id": "...",
    "chat_id": 123456,
    "user_id": 789,
    "callback_url": "https://n8n-host/webhook/callback",
    "sponsor": {
        "name": "Sponsor",
        "footer_text": "Offerto da Sponsor"
    }
}
```

### Performance faster-whisper

Il modello `small` con compute type `int8` su GPU T4 ha raggiunto un **rapporto di elaborazione di 26.5x** rispetto al tempo reale.

| Audio | Tempo elaborazione |
|---|---|
| 1 ora | ~2 min 15 sec |
| 30 min | ~1 min 7 sec |
| 5 min | ~11 sec |

### Docker build

Il modello viene scaricato durante il `docker build` (non al primo avvio), rendendo il container pronto immediatamente.

```dockerfile
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime
RUN apt-get update && apt-get install -y ffmpeg git
RUN pip install -r requirements.txt
# Pre-download modello durante build
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
CMD ["uvicorn", "worker:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

---

## Workflow n8n

Il progetto include due workflow n8n principali (in `n8n/workflows/`):

### 1. Queue Processor (`queue-processor.json`)
Cron ogni 3 minuti. Gestisce l'intera pipeline:
- Lettura job pending da Google Sheets
- Verifica e avvio istanza EC2
- Health check worker
- Invio job al worker via HTTP POST
- Aggiornamento status in Sheets
- Shutdown EC2 quando coda vuota

### 2. Transcription Callback (`transcription-callback.json`)
Webhook ricevuto dal worker al completamento:
- Aggiorna status `completed` o `failed` su Sheets
- Verifica se la coda è vuota
- Se inattiva da >10 min → Stop EC2

### Bot principale (n8n.evolvidigital.it)
Il workflow principale gestisce:
- Onboarding GDPR con pulsante inline di consenso
- Distinzione callback / messaggi audio / altri messaggi
- Accodamento trascrizioni su Google Sheets

---

## Modello economico

### Costi variabili per trascrizione

Con faster-whisper su EC2 g4dn.xlarge ($0.526/ora), il costo variabile per trascrizione dipende dalla durata dell'audio:

```
Costo per trascrizione = (durata_audio_ore / 26.5) × $0.526

Esempi:
  Audio 30 min  → 0.5 / 26.5 × $0.526 ≈ $0.010
  Audio 60 min  → 1.0 / 26.5 × $0.526 ≈ $0.020
  Audio 90 min  → 1.5 / 26.5 × $0.526 ≈ $0.030
```

**Costo medio stimato per trascrizione: ~$0.01–0.03**

### Costi fissi

I costi fissi dell'infrastruttura (n8n su t3.medium ~$30/mese, Bot API Server ~$8/mese, storage EBS ~$5/mese) erano **già sostenuti da un'altra attività** preesistente sullo stesso account AWS. Questo significa che il costo incrementale reale del progetto era composto quasi interamente dai costi variabili di compute GPU — rendendo il modello economico particolarmente favorevole nella fase iniziale.

### Ricavi per trascrizione (stima)

Con un solo sponsor in modalità *In-Trascrizione* (CPC €0.25, CTR 3%):

```
Revenue per trascrizione = CPC × CTR = €0.25 × 3% = €0.0075
```

Con 1.000 trascrizioni/mese:
- Costo variabile GPU: ~$15 (~€14)
- Revenue sponsor: €7.50
- Break-even stimato: ~2.000 trascrizioni/mese con CTR 3%

Il modello diventa profittevole quando si raggiunge un volume sufficiente di utenti attivi settimanali (coda che mantiene l'EC2 accesa in modo efficiente) combinato con più slot sponsor.

---

## Confronto con Turboscribe.ai

Turboscribe.ai è il competitor diretto più noto: trascrizione audio illimitata a $10/mese (annuale) o $20/mese, basata su Whisper Large v3 su GPU dedicate.

| | **Turboscribe** | **AudioLecture** |
|---|---|---|
| Prezzo utente | $10–20/mese | €0 (sempre gratis) |
| Modello revenue | Freemium SaaS | Sponsor-based |
| Target | Globale, qualsiasi utente | Studenti universitari italiani |
| Costo server | Always-on (GPU dedicata) | On-demand (acceso solo quando lavora) |
| Limite free | 3 file/giorno × 30 min | Nessuno |
| Accuratezza modello | Whisper Large v3 | Whisper Small (qualità inferiore) |
| LTV utente | Alto ($120–240/anno) | €0 (gratuito) |
| Scalabilità revenue | Lineare con utenti paganti | Legata ai CPM sponsor |

### Osservazioni

Turboscribe si basa su un classico modello freemium: il piano gratuito (3 file/giorno) serve come esca per la conversione al piano pagamento. I costi marginali per trascrizione su GPU sono bassissimi (~$0.007/ora audio con faster-whisper), quindi anche utenti con uso elevato restano ampiamente profittevoli a $10/mese.

AudioLecture sceglie una strada opposta: **zero barriera all'adozione** (massimizza il numero di utenti) e monetizzazione tramite attenzione pubblicitaria. Questo funziona meglio in una nicchia verticale dove il passaparola è forte (studenti universitari) ma è più difficile da scalare in termini di revenue rispetto a un SaaS.

---

## Perché il progetto si è fermato

Il progetto è stato interrotto nella fase MVP funzionante (end-to-end pipeline operativa per audio <20 MB) per le seguenti considerazioni:

1. **Unit economics sfavorevoli a basso volume** — il break-even richiede un volume di trascrizioni difficile da raggiungere con un singolo sponsor all'inizio
2. **Dipendenza da sponsor** — trovare sponsor prima di avere utenti è il classico problema dell'uovo e della gallina
3. **Competizione con servizi gratuiti esistenti** — Whisper è open source e molti studenti possono usarlo localmente o tramite tool gratuiti
4. **Complessità operativa** — gestire EC2 on/off, Bot API Server, n8n, Google Sheets introduce molti punti di failure per un MVP

L'esperienza ha comunque permesso di esplorare in profondità: infrastruttura AWS GPU on-demand, faster-whisper con ottimizzazioni INT8, orchestrazione con n8n, workflow Telegram bot con consenso GDPR.

---

## Struttura del repository

```
AudioLectureBot/
├── README.md                          # Questo file
├── CLAUDE.md                          # Note architetturali dettagliate
├── ec2-worker-config.txt              # Comando AWS CLI per lanciare il worker
│
├── worker/
│   ├── Dockerfile                     # Container PyTorch + faster-whisper
│   ├── requirements.txt               # Dipendenze Python
│   ├── config.py                      # Variabili d'ambiente
│   ├── worker.py                      # FastAPI server + asyncio job queue
│   ├── transcriber.py                 # Wrapper faster-whisper
│   ├── pdf_generator.py               # Generazione PDF con ReportLab
│   ├── telegram_client.py             # Download audio + invio PDF
│   └── startup.sh                     # Setup one-shot EC2 (Docker + NVIDIA)
│
├── n8n/
│   └── workflows/
│       ├── queue-processor.json       # Cron: gestione coda + EC2 start/stop
│       └── transcription-callback.json # Webhook: ricezione risultato da worker
│
└── screenshots/                       # Screenshot del progetto durante lo sviluppo
```

### File da escludere dal repository pubblico

- `connect_istance.bat` — contiene IP dell'istanza EC2
- `.env` — variabili d'ambiente con token e credenziali
- `.claude/` — configurazione locale Claude Code

---

## Variabili d'ambiente necessarie

```env
TELEGRAM_BOT_TOKEN=      # Token da BotFather
BOT_API_SERVER_URL=      # http://<ip>:8081 (Bot API Server locale)
WHISPER_MODEL=small      # small | medium | large-v3
TEMP_DIR=/tmp/audiolecture
```

---

*Progetto esplorativo — Febbraio 2026*
