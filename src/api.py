import requests
import json
from config import API_URL, MODEL_NAME, API_KEY

def llm_chat(messages: list, temperature=0.7):
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": temperature
    }
    try:
        resp = requests.post(API_URL, headers=headers, json=payload)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}