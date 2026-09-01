import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
load_dotenv(".keys.env")

# Ensure client initializes with correct key
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

try:
    # Try gemini-2.5-flash or gemini-2.0-flash or gemini-1.5-flash
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='Hello, respond with: API is working!'
    )
    print("Success with gemini-2.5-flash:")
    print(response.text)
except Exception as e:
    print("Failed with gemini-2.5-flash:", e)
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents='Hello, respond with: API is working!'
        )
        print("Success with gemini-2.0-flash:")
        print(response.text)
    except Exception as e2:
        print("Failed with gemini-2.0-flash:", e2)
