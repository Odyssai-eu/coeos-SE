"""`coeos-se` / `python -m coeos_se` — run the server."""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="coeos-se",
        description="CoeOS SE — benchmark-composed LLM router (OpenAI-compatible).")
    parser.add_argument("--host", default=os.environ.get("COEOS_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("COEOS_PORT", "4600")))
    args = parser.parse_args()

    import uvicorn
    uvicorn.run("coeos_se.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
