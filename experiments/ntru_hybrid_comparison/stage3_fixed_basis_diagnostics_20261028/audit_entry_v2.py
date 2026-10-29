"""Isolated corrective v2 guard. Original frozen guard remains unchanged."""
import argparse
import json
import os
import runpy
import sys
from access_v2 import classify_path


def install(log_path):
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    def audit(event, arguments):
        if event != 'open' or not arguments or isinstance(arguments[0], int):
            return
        record = {'event': 'open', **classify_path(arguments[0], cwd=os.getcwd())}
        os.write(log_fd, (json.dumps(record, sort_keys=True) + '\n').encode())
        if record['denied']:
            raise PermissionError('stage3 v2 blocked secret content before open: ' + record['path'])

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
