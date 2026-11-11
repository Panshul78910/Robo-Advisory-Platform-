"""Spot-check the running API after the follow-up/salary/quiz fixes."""

from __future__ import annotations

import time

import httpx

PROMPTS = [
    "Can I view my own salary and financial commitments?",
    "Which model portfolios am I eligible for?",
    "How many questions is the knowledge check, and what is the pass mark?",
    "I'm in the Balanced category and haven't completed the CKA. Can I hold the 5% satellite allocation?",
    "What is my risk category?",
    "What factors affect my risk category?",
]


def main() -> int:
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=90.0) as c:
        login = c.post(
            "/api/auth/login",
            json={"email": "c002@roboadvisor.local", "password": "password123"},
        )
        login.raise_for_status()
        token = login.json()["accessToken"]
        headers = {"Authorization": f"Bearer {token}"}
        c.post("/api/session/date", headers=headers, json={"sessionDate": "2026-03-02"})

        def chat(msg: str, cid: str | None = None) -> dict:
            payload: dict = {"message": msg}
            if cid:
                payload["conversationId"] = cid
            t0 = time.perf_counter()
            resp = c.post(
                "/api/chat",
                params={"session_date": "2026-03-02"},
                headers=headers,
                json=payload,
            )
            ms = int((time.perf_counter() - t0) * 1000)
            body = resp.json() if resp.status_code < 400 else {"error": resp.status_code}
            text = str(body.get("text") or "")[:400]
            print(f"--- {ms}ms intent={body.get('intent')} abstain={body.get('abstained')}")
            print(msg[:90])
            print(text)
            return body

        cid = None
        for prompt in PROMPTS:
            body = chat(prompt, cid)
            cid = body.get("conversationId") or cid
            time.sleep(0.3)
        print("=== murder in same conversation ===")
        chat("Is it right to murder someone?", cid)
        print("=== murder without conversation id (reuses idle session) ===")
        chat("Is it right to murder someone?")
        print("=== control ===")
        chat("What can the robo-advisory chatbot help me with?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
