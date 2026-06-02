#!/usr/bin/env python3
import os
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: ia_kissing_dispatch.py '<message>'", file=sys.stderr)
        return 2

    message = sys.argv[1]
    script_dir = os.path.dirname(os.path.abspath(__file__))
    shell_script = os.path.join(script_dir, "review_dispatch.sh")
    result = subprocess.run([shell_script, message], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
