import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def call_llm(system_prompt: str, user_input: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4.1-mini",  # lightweight + fast
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        temperature=0
    )

    return response.choices[0].message.content