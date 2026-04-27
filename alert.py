import requests
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN") #"8763326208:AAEwyN62L2VV1h0UXSuENkIEBFXIdzphe8Q"
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") #"Odssvck_bot"

def enviar_alerta(msg):

    if not TOKEN:
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": msg
        })
    except:
        pass