"""Install a deny-before-open audit hook before importing a numerical worker.

The companion strace records native opens independently. This guard addresses
future access and cannot reconstruct the unknown preparation search scan set.
"""
import argparse
import json
import os
import runpy
import sys

FORBIDDEN = ('private', 'truth', 'reference')


def install(log_path):
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    def audit(event, arguments):
        if event != 'open' or not arguments or isinstance(arguments[0], int):
            return
        path = os.fsdecode(arguments[0])
        real = os.path.realpath(path)
        denied = any(word in path.lower() or word in real.lower() for word in FORBIDDEN)
        os.write(log_fd, (json.dumps({'event': 'open', 'path': path, 'realpath': real,
                                     'denied': denied}, sort_keys=True) + '\n').encode())
        if denied:
            raise PermissionError('stage3 blocked forbidden content path before open: ' + path)

    sys.addaudithook(audit)
    return log_fd


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--audit-log', required=True)
    parser.add_argument('module')
    args, rest = parser.parse_known_args()
    install(args.audit_log)
    sys.argv = [args.module, *rest]
    runpy.run_module(args.module, run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    main()
