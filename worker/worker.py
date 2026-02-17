import os
import uuid
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel

from config import TEMP_DIR
import transcriber
import pdf_generator
import telegram_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Coda in-memory (sequenziale, un job alla volta)
_queue: asyncio.Queue = None
_processing = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _queue
    _queue = asyncio.Queue()
    Path(TEMP_DIR).mkdir(parents=True, exist_ok=True)
    # Precarica il modello Whisper all'avvio
    log.info("Caricamento modello Whisper...")
    transcriber.load_model()
    log.info("Modello pronto.")
    # Avvia il worker loop
    task = asyncio.create_task(_worker_loop())
    yield
    task.cancel()


app = FastAPI(title="AudioLecture Worker", lifespan=lifespan)


class SponsorInfo(BaseModel):
    name: str | None = None
    footer_text: str | None = None
    logo_url: str | None = None


class TranscribeRequest(BaseModel):
    file_id: str
    chat_id: int
    user_id: int
    callback_url: str | None = None
    sponsor: SponsorInfo | None = None


class TranscribeResponse(BaseModel):
    status: str
    job_id: str


@app.get("/health")
def health():
    try:
        import torch
        gpu = torch.cuda.is_available()
    except Exception:
        gpu = False
    return {"status": "healthy", "gpu": gpu, "queue_size": _queue.qsize() if _queue else 0}


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_endpoint(req: TranscribeRequest):
    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "file_id": req.file_id,
        "chat_id": req.chat_id,
        "user_id": req.user_id,
        "callback_url": req.callback_url,
        "sponsor": req.sponsor.model_dump() if req.sponsor else None,
    }
    await _queue.put(job)
    log.info(f"Job {job_id} in coda (chat_id={req.chat_id})")
    return TranscribeResponse(status="queued", job_id=job_id)


async def _worker_loop():
    """Elabora i job uno alla volta."""
    while True:
        job = await _queue.get()
        try:
            await asyncio.get_event_loop().run_in_executor(None, _process_job, job)
        except Exception as e:
            log.error(f"Errore job {job['job_id']}: {e}")
            _notify_error(job)
        finally:
            _queue.task_done()


def _process_job(job: dict):
    job_id = job["job_id"]
    chat_id = job["chat_id"]
    file_id = job["file_id"]
    sponsor = job.get("sponsor")
    callback_url = job.get("callback_url")

    audio_path = os.path.join(TEMP_DIR, f"{job_id}.ogg")
    pdf_path = os.path.join(TEMP_DIR, f"{job_id}.pdf")

    try:
        # 1. Download audio
        log.info(f"[{job_id}] Download audio file_id={file_id}")
        telegram_client.download_audio(file_id, audio_path)

        # 2. Trascrizione
        log.info(f"[{job_id}] Trascrizione con Whisper...")
        text = transcriber.transcribe(audio_path)
        log.info(f"[{job_id}] Trascrizione completata ({len(text)} caratteri)")

        # 3. Generazione PDF
        log.info(f"[{job_id}] Generazione PDF...")
        pdf_generator.generate_pdf(text, pdf_path, sponsor=sponsor)

        # 4. Invio PDF all'utente
        log.info(f"[{job_id}] Invio PDF a chat_id={chat_id}")
        telegram_client.send_pdf(
            chat_id,
            pdf_path,
            caption="✅ Ecco la tua trascrizione!",
        )

        # 5. Callback a n8n
        if callback_url:
            _send_callback(callback_url, job_id, "completed")

        log.info(f"[{job_id}] Completato con successo.")

    except Exception as e:
        log.error(f"[{job_id}] Errore durante l'elaborazione: {e}")
        telegram_client.send_message(
            chat_id,
            "❌ Si è verificato un errore durante la trascrizione. Riprova più tardi.",
        )
        if callback_url:
            _send_callback(callback_url, job_id, "failed", error=str(e))
        raise

    finally:
        # Pulizia file temporanei
        for path in [audio_path, pdf_path]:
            if os.path.exists(path):
                os.remove(path)


def _send_callback(url: str, job_id: str, status: str, error: str = None):
    payload = {"job_id": job_id, "status": status}
    if error:
        payload["error"] = error
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        log.warning(f"Callback fallita per job {job_id}: {e}")


def _notify_error(job: dict):
    try:
        telegram_client.send_message(
            job["chat_id"],
            "❌ Si è verificato un errore durante la trascrizione. Riprova più tardi.",
        )
    except Exception:
        pass
