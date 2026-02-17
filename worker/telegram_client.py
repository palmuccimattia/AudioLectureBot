import os
import requests
from config import TELEGRAM_BOT_TOKEN, BOT_API_SERVER_URL


def get_file_path(file_id: str) -> str:
    """Chiama getFile e restituisce il file_path da usare per il download."""
    url = f"{BOT_API_SERVER_URL}/bot{TELEGRAM_BOT_TOKEN}/getFile"
    resp = requests.get(url, params={"file_id": file_id}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"getFile fallito: {data}")
    return data["result"]["file_path"]


def download_audio(file_id: str, dest_path: str) -> str:
    """
    Scarica l'audio dal Bot API Server locale.
    Restituisce il percorso del file salvato.
    """
    file_path = get_file_path(file_id)
    download_url = f"{BOT_API_SERVER_URL}/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    resp = requests.get(download_url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest_path


def send_pdf(chat_id: int, pdf_path: str, caption: str = "") -> None:
    """Invia il PDF via Telegram al chat_id specificato."""
    url = f"{BOT_API_SERVER_URL}/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    with open(pdf_path, "rb") as f:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption},
            files={"document": ("trascrizione.pdf", f, "application/pdf")},
            timeout=60,
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"sendDocument fallito: {data}")


def send_message(chat_id: int, text: str) -> None:
    """Invia un messaggio testuale all'utente."""
    url = f"{BOT_API_SERVER_URL}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    resp.raise_for_status()
