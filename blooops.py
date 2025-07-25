import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("PRINTIFY_API_KEY")

resp = requests.get(
    "https://api.printify.com/v1/catalog/blueprints.json",
    headers={"Authorization": f"Bearer {API_KEY}"}
)

if resp.status_code == 200:
    blueprints = resp.json()
    for bp in blueprints:
        print(f"ID: {bp['id']} | Title: {bp['title']}")
else:
    print("Failed to fetch blueprints:", resp.text)
