#!/usr/bin/env python3
"""Herramienta CLI para inspeccionar y reconciliar entregas bloqueadas en bot-state.

Ejemplos:
  python tools/reconcile_outbox.py --list
  python tools/reconcile_outbox.py --id 2840 --action mark_sent --message-id 912
  python tools/reconcile_outbox.py --id 2840 --action retry
  python tools/reconcile_outbox.py --id 2840 --action drop
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_job  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Reconciliación segura (CAS) de bot-state/noticias.json")
    parser.add_argument("--list", action="store_true", help="Listar entregas no confirmadas en bot-state")
    parser.add_argument("--id", type=int, help="ID de la noticia a reconciliar")
    parser.add_argument("--action", choices=("mark_sent", "retry", "drop"), help="Acción a aplicar")
    parser.add_argument("--message-id", type=int, default=None, help="message_id de Telegram (opcional para mark_sent)")
    args = parser.parse_args(argv)

    noticias, sha = run_job.get_github_file(run_job.GITHUB_STATE_BRANCH)
    if noticias is None:
        raise SystemExit("Error: no se pudo leer bot-state/noticias.json")

    if args.list or (args.id is None and args.action is None):
        pendientes = [
            n for n in noticias
            if isinstance(n.get("telegram"), dict) and n["telegram"].get("status") != "sent"
        ]
        if not pendientes:
            print("Outbox limpio: no hay entregas pendientes ni inciertas.")
            return 0
        for n in pendientes:
            tg = n["telegram"]
            print(
                f"id={n.get('id')} | status={tg.get('status')} | attempts={tg.get('attempts', 0)} "
                f"| error={tg.get('error', '-') } | fecha={n.get('fecha', '-')} | titulo={n.get('titulo', '')!r}"
            )
        return 0

    if args.id is None or not args.action:
        parser.error("Se requieren ambos argumentos --id y --action (o --list)")

    run_job.reconcile_outbox_record(
        noticias, sha, args.id, args.action, message_id=args.message_id
    )
    print(f"OK: noticia id={args.id} reconciliada con acción '{args.action}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
