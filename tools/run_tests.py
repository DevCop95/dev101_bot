"""Run only maintained tests with no credentials, dotenv reads or network access."""

import logging
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    system_env = {key: value for key, value in os.environ.items()
                  if key.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL"}}
    os.environ.clear()
    os.environ.update(system_env)

    def guard(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
                     "socket.gethostbyaddr", "socket.sendto", "socket.sendmsg"}:
            raise RuntimeError("Network access is forbidden in offline tests")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if os.path.basename(os.fsdecode(args[0])).startswith(".env"):
                raise RuntimeError("Dotenv files are forbidden in offline tests")

    sys.addaudithook(guard)
    import dotenv

    logging.disable(logging.NOTSET)
    logging.basicConfig(handlers=[logging.NullHandler()], force=True)
    with patch.object(dotenv, "load_dotenv", return_value=False), patch(
        "requests.sessions.Session.request", side_effect=AssertionError("Unmocked HTTP request")
    ):
        suite = unittest.TestSuite([
            unittest.defaultTestLoader.loadTestsFromName("test_run_job"),
            unittest.defaultTestLoader.discover(str(root / "tests")),
        ])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
