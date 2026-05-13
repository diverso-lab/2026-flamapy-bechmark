#!/usr/bin/env python3
"""
Flamapy Benchmark — command-line interface

Commands
--------
  run     Run the solver benchmark across UVL feature models
  plots   Generate paper figures from a results CSV

Usage
-----
  python main.py <command> [options]
  python main.py <command> --help
"""

import importlib
import sys


COMMANDS = {
    'run':   ('Run the solver benchmark',            'scripts.benchmark'),
    'plots': ('Generate figures from a results CSV', 'scripts.generate_plots'),
}


def _print_help() -> None:
    print(__doc__)
    print("Available commands:\n")
    for name, (desc, _) in COMMANDS.items():
        print(f"  {name:<8}  {desc}")
    print()


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        _print_help()
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Error: unknown command '{cmd}'\n")
        _print_help()
        sys.exit(1)

    # Strip the subcommand name and hand off to the module's own argparse.
    sys.argv = [f"main.py {cmd}"] + sys.argv[2:]
    _, module_path = COMMANDS[cmd]
    importlib.import_module(module_path).main()


if __name__ == '__main__':
    main()
