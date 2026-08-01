"""`coeos-se` / `python -m coeos_se` — run the server."""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys

# Le plan de controle de l'edition complete vit sur ce port. Un client agent
# (CodeOS) prend l'URL du moteur, y force ce port et demande /api/state — il
# ne sait pas, et ne doit pas savoir, quelle edition repond. SE ecoute donc
# AUSSI dessus : c'est a SE de se presenter comme CoeOS, pas au client de
# gerer deux cas (Sophie, 2026-08-01 : "codeos doit juste voir coeos se comme
# coeos, il ne doit rien changer").
CONSOLE_PORT = 4800


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("" if host == "0.0.0.0" else host, port))
            return True
        except OSError:
            return False


async def _serve_all(host: str, ports: list) -> None:
    import uvicorn
    servers = [uvicorn.Server(uvicorn.Config("coeos_se.app:app", host=host, port=p))
               for p in ports]
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="coeos-se",
        description="CoeOS SE — benchmark-composed LLM router (OpenAI-compatible).")
    parser.add_argument("--host", default=os.environ.get("COEOS_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("COEOS_PORT", "4600")))
    parser.add_argument(
        "--console-port", type=int,
        default=int(os.environ.get("COEOS_CONSOLE_PORT", CONSOLE_PORT)),
        help=f"second port answering the control-plane surface agent clients "
             f"expect (default {CONSOLE_PORT}); 0 disables it")
    args = parser.parse_args()

    ports = [args.port]
    cp = args.console_port
    if cp and cp != args.port:
        # Deja pris = une edition complete tourne probablement la. Les deux ne
        # sont pas censees tourner ensemble ; on le signale et on continue sur
        # le port principal plutot que de refuser de demarrer.
        if _port_free(args.host, cp):
            ports.append(cp)
        else:
            sys.stderr.write(
                f"[coeos-se] port {cp} busy — serving on {args.port} only; an "
                f"agent client looking for the control plane there will not "
                f"find this instance\n")

    import uvicorn
    if len(ports) == 1:
        uvicorn.run("coeos_se.app:app", host=args.host, port=ports[0])
    else:
        sys.stderr.write(f"[coeos-se] listening on {', '.join(map(str, ports))}\n")
        asyncio.run(_serve_all(args.host, ports))


if __name__ == "__main__":
    main()
