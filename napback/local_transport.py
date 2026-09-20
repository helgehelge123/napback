"""A local process transport for rsync's remote-shell protocol.

No network or shell is involved. Running sender and receiver as separate peers
keeps fake-super scoped to the correct side, avoiding old rsync local -M bugs.
"""

import os
import sys


def main():
    args = sys.argv[1:]
    if len(args) < 3 or args[:3] != ["napback-local", "rsync", "--server"]:
        print("napback local transport: expected an rsync server invocation", file=sys.stderr)
        return 2
    os.execvp("rsync", args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
