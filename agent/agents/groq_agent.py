import os
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])

def run(prompt: str, model: str = "llama-3.3-70b-versatile") -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
    )
    return response.choices[0].message.content
