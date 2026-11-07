import json, os, time, urllib.request, urllib.error, re

def load_env(path=None):
    path = path or os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"'))
load_env()

class LLMError(Exception): pass


def api_key_configured():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return bool(key and key not in ("твой_ключ", "твой_настоящий_ключ", "сюда_вставь_ключ", "your-key", "YOUR_API_KEY"))

def model_name():
    return "mock-heuristic" if os.environ.get("MOCK") == "1" else os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

import threading
_lock = threading.Lock(); _last = [0.0]

def _pace():
    """Минимальная пауза между запросами (MIN_INTERVAL, сек), общая для всех потоков."""
    gap = float(os.environ.get("MIN_INTERVAL", "0"))
    if gap <= 0: return
    with _lock:
        wait = _last[0] + gap - time.time()
        if wait > 0: time.sleep(wait)
        _last[0] = time.time()

def _retry_delay(body):
    m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body or "")
    return float(m.group(1)) if m else None

def call_json(system, user, retries=7):
    """Возвращает dict из JSON-ответа модели. Повторы с паузой при 429/5xx и таймаутах."""
    key = os.environ.get("GEMINI_API_KEY")
    if not api_key_configured(): raise LLMError("GEMINI_API_KEY не задан в .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name()}:generateContent"
    timeout = float(os.environ.get("TIMEOUT", "180"))
    gen = {"temperature": float(os.environ.get("TEMPERATURE", "0")), "responseMimeType": "application/json"}
    think = os.environ.get("THINKING_LEVEL", "").strip()      # minimal | low | medium | high (необязательно)
    if think: gen["thinkingConfig"] = {"thinkingLevel": think}
    last = None
    for i in range(retries):
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen}
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        _pace()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.load(r)
            txt = out["candidates"][0]["content"]["parts"][0]["text"]
            txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
            return json.loads(txt)
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", "replace"); last = f"HTTP {e.code}"
            if e.code == 400 and "thinkingConfig" in gen and "think" in err.lower():
                print("  [модель] thinkingLevel не поддерживается, отключаю", flush=True); gen.pop("thinkingConfig"); continue
            if e.code in (429, 500, 502, 503, 504):
                w = _retry_delay(err) or min(60, 4 * 2 ** i); w = min(90, w + 1)
                print(f"  [модель] {last}, пауза {w:.0f}с (попытка {i+1}/{retries})", flush=True); time.sleep(w); continue
            # Keys and request headers must never be printed to logs or returned to users.
            raise LLMError(f"{last}: проверьте доступность модели и ключ Gemini (см. list_models.py)")
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError) as e:
            last = type(e).__name__; print(f"  [модель] сбой: {last}, повтор (попытка {i+1}/{retries})", flush=True); time.sleep(3 + i)
    raise LLMError(f"Модель не ответила: {last}")
