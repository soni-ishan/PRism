import os
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

_client = AzureOpenAI(
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview"),
)

_DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT"]


def call_llm(system_prompt: str, user_input: str) -> str:
    resp = _client.chat.completions.create(
        model=_DEPLOYMENT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        temperature=0,
    )
    return resp.choices[0].message.content