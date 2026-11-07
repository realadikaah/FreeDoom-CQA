"""Показывает модели, доступные твоему ключу. Запуск: python3 list_models.py"""
import sys, os, json, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))
import llm  # подхватывает .env
key = os.environ.get("GEMINI_API_KEY")
if not key: sys.exit("GEMINI_API_KEY не найден в .env")
req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", headers={"x-goog-api-key": key})
try:
    data = json.load(urllib.request.urlopen(req, timeout=30))
except Exception as e:
    sys.exit(f"Ошибка запроса: {e}")
names = sorted(m["name"].split("/")[-1] for m in data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", []))
print("Модели, которые можно вписать в GEMINI_MODEL:\n")
print("\n".join(n for n in names if "flash" in n or "pro" in n))
