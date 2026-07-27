"""Customer simulator — a tiny CLI that lets you 'be the customer' on WhatsApp
without the real API. It POSTs Meta-shaped payloads to the running webhook, so it
exercises the exact production path, and prints the agent's reply.

Usage (with the API running: `uvicorn app.main:app --reload`):

    python -m app.tools.sim --phone 923001234567
    python -m app.tools.sim --phone 923001234567 --message "TRK12345 status?"

Interactive mode (default) reads messages until you type 'quit'. Uses only the
stdlib (urllib) so it adds no dependency.
"""
import argparse
import json
import urllib.error
import urllib.request

from app.routes.whatsapp_routes import build_text_message_payload

DEFAULT_URL = "http://localhost:8000/webhook/whatsapp"


def send(url: str, phone: str, message: str) -> None:
    payload = build_text_message_payload(phone, message)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"[sim] could not reach {url}: {e}. Is the API running?")
        return

    replies = body.get("replies", [])
    if not replies:
        print("[sim] (no reply)")
    for r in replies:
        print(f"[bot] {r.get('reply')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Zentric WhatsApp customer simulator")
    parser.add_argument("--phone", default="923001234567", help="customer WhatsApp number")
    parser.add_argument("--message", help="send one message and exit (non-interactive)")
    parser.add_argument("--url", default=DEFAULT_URL, help="webhook URL")
    args = parser.parse_args()

    if args.message:
        print(f"[you] {args.message}")
        send(args.url, args.phone, args.message)
        return

    print(f"Zentric simulator — you are {args.phone}. Type a message ('quit' to exit).")
    while True:
        try:
            message = input("[you] ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if message.lower() in {"quit", "exit"}:
            break
        if message:
            send(args.url, args.phone, message)


if __name__ == "__main__":
    main()
