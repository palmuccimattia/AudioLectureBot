from faster_whisper import WhisperModel
from config import WHISPER_MODEL

_model = None


def load_model():
    global _model
    if _model is None:
        # INT8 su GPU: massima velocità, minima VRAM
        _model = WhisperModel(WHISPER_MODEL, device="cuda", compute_type="int8")
    return _model


def transcribe(audio_path: str) -> str:
    """Trascrive un file audio e restituisce il testo con timestamp."""
    model = load_model()
    segments, info = model.transcribe(audio_path, beam_size=5)
    lines = []
    for seg in segments:
        start = _format_time(seg.start)
        text = seg.text.strip()
        if text:
            lines.append(f"[{start}] {text}")
    return "\n".join(lines)


def _format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
