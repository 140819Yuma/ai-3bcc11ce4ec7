import os
from google import genai

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def run(prompt: str) -> str:
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    return response.text
