import json
import os
import datetime
import queue
import threading
import tkinter as tk
from tkinter import scrolledtext, simpledialog

from openai import AuthenticationError, OpenAI

MODEL = os.environ.get("MODEL", "openrouter/free")

# ---------- Window + API key ----------
root = tk.Tk()
root.withdraw()  # hide the window until we have a key

# The key is saved in your user folder (NOT in the project), so Git never sees it.
KEY_FILE = os.path.join(os.path.expanduser("~"), ".my_agent_key")


def clean(key):
    return (key or "").strip().strip('"').strip("'").strip()


def load_key():
    key = clean(os.environ.get("OPENROUTER_API_KEY"))
    if key:
        return key
    try:
        with open(KEY_FILE, encoding="utf-8") as f:
            return clean(f.read())
    except OSError:
        return ""


api_key = load_key()
if not api_key:
    api_key = clean(
        simpledialog.askstring(
            "API key", "Paste your OpenRouter API key:", show="*", parent=root
        )
    )
    if not api_key:
        raise SystemExit
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(api_key)

client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)


# ---------- Tools ----------
def get_time():
    return datetime.datetime.now().isoformat(timespec="seconds")


def calculate(expression):
    allowed = set("0123456789+-*/(). %")
    if not set(expression) <= allowed:
        return "error: invalid characters"
    try:
        return str(eval(expression, {"builtins": {}}, {}))
    except Exception as e:
        return f"error: {e}"


FUNCS = {
    "get_time": lambda **a: get_time(),
    "calculate": lambda a: calculate(a),
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current local date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a math expression like (12+3)*4.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
]

# ---------- Agent loop (runs in a background thread) ----------
messages = [
    {
        "role": "system",
        "content": "You are a helpful assistant. Use tools when needed. "
        "Reply in the same language as the user.",
    }
]
out = queue.Queue()  # the thread puts results here, the window reads them


def run_agent(user_text):
    start = len(messages)
    messages.append({"role": "user", "content": user_text})
    try:
        for _ in range(8):  # max 8 tool steps per question
            resp = client.chat.completions.create(
                model=MODEL, messages=messages, tools=TOOLS
            )
            msg = resp.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                out.put(("agent", msg.content or ""))
                return

            for call in msg.tool_calls:
                args = json.loads(call.function.arguments or "{}")
                out.put(("tool", f"{call.function.name}({args})"))
                result = FUNCS[call.function.name](**args)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
        out.put(("error", "Too many tool steps."))
    except AuthenticationError:
        del messages[start:]
        try:
            os.remove(KEY_FILE)  # forget the bad key
        except OSError:
            pass
        out.put(
            (
                "error",
                "Invalid API key. Close the app and open it again to enter a "
"valid key. (If you set an OPENROUTER_API_KEY variable in "
                "Windows, fix or remove it too.)",
            )
        )
    except Exception as e:
        del messages[start:]  # drop the broken turn so the chat can continue
        out.put(("error", str(e)))
    finally:
        out.put(("done", ""))


# ---------- Window ----------
root.title("My AI Agent")
root.geometry("720x560")

chat = scrolledtext.ScrolledText(
    root, wrap="word", state="disabled", font=("Segoe UI", 11)
)
chat.pack(fill="both", expand=True, padx=8, pady=(8, 4))
chat.tag_config("you", foreground="#1a5fb4")
chat.tag_config("agent", foreground="#000000")
chat.tag_config("tool", foreground="#888888", font=("Consolas", 9))
chat.tag_config("error", foreground="#c01c28")


def add(text, tag):
    chat.config(state="normal")
    chat.insert("end", text + "\n\n", tag)
    chat.config(state="disabled")
    chat.see("end")


def send(event=None):
    text = entry.get().strip()
    if not text:
        return
    entry.delete(0, "end")
    add("You: " + text, "you")
    send_btn.config(state="disabled")
    threading.Thread(target=run_agent, args=(text,), daemon=True).start()


bottom = tk.Frame(root)
bottom.pack(fill="x", padx=8, pady=(0, 8))
entry = tk.Entry(bottom, font=("Segoe UI", 11))
entry.pack(side="left", fill="x", expand=True, ipady=4)
entry.bind("<Return>", send)
send_btn = tk.Button(bottom, text="Send", width=10, command=send)
send_btn.pack(side="left", padx=(6, 0))


def poll():
    try:
        while True:
            kind, text = out.get_nowait()
            if kind == "agent":
                add("Agent: " + text, "agent")
            elif kind == "tool":
                add("[tool] " + text, "tool")
            elif kind == "error":
                add("Error: " + text, "error")
            elif kind == "done":
                send_btn.config(state="normal")
                entry.focus()
    except queue.Empty:
        pass
    root.after(100, poll)


add("Hi! Ask me anything. I can tell the time and do math.", "agent")
root.deiconify()
entry.focus()
poll()
root.mainloop()