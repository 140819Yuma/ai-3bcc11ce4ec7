import os
import google.generativeai as genai

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel("gemini-2.0-flash")

def run(prompt: str) -> str:
    response = model.generate_content(prompt)
    return response.text
