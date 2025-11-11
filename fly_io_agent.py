from openai import OpenAI
from pydantic import BaseModel
from typing import List, Optional

class Message(BaseModel):
    content: str
    role: str
    tool_calls: Optional[List[str]] = None
    
    @classmethod
    def from_message(cls, m):
        return cls(
            content=m.content,
            role=m.role,
            tool_calls=m.tool_calls if m.tool_calls else getattr(m, 'function_call', None)
        )



# point to local llama-cpp-python OpenAI-compatible server
client = OpenAI(base_url="http://localhost:8080/v1", api_key="sk-local")

context = []

def call():
    # use chat completions API
    response = client.chat.completions.create(
        model="model-set-by-llama-server",
        messages=context,
        # max_tokens=512
    )

    # unwrap response
    m = Message.from_message(response.choices[0].message)
    return m

def process(line):
    context.append({"role": "user", "content": line})
    response = call()
    context.append({"role": "assistant", "content": response.content})
    return response.content

def main():
    while True:
        line = input("> ")
        result = process(line)
        print(f">>> {result}\n")

if __name__ == "__main__":
    main()