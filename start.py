"""Run: python start.py, then browse http://127.0.0.1:8000."""
import uvicorn
import os
from server import llm  # reads .env before app startup

if __name__ == '__main__':
    print('FreeDoom CQA: http://127.0.0.1:8000')
    if not llm.api_key_configured():
        print('Gemini: ключ пока не настроен. Старые результаты и интерфейс работают; новые AI-оценки потребуют .env.')
    uvicorn.run('server.app:app',host='127.0.0.1',port=int(os.getenv('PORT','8000')),reload=False)
