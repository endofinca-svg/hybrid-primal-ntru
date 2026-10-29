"""Stage 3 durable observational records; never changes numerical defaults."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append(path, value):
    with Path(path).open('a') as stream:
        stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


class Journal:
    def __init__(self, output, request):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.request = request
        self.result = {'status': 'running', 'current_stage': None, 'timings': {},
                       'started_monotonic': time.monotonic(), 'request': request}
        self.save()

    def check(self):
        if time.monotonic() >= self.request['work_deadline']:
            raise TimeoutError('inherited work deadline reached')

    def save(self):
        write(self.output / 'result.json', self.result)

    def emit(self, event, **values):
        append(self.output / 'events.jsonl', {'event': event, 'monotonic': time.monotonic(), **values})

    @contextmanager
    def stage(self, name):
        self.check()
        self.result['current_stage'] = name
        self.result['timings'][name] = None
        self.emit('stage_started', stage=name)
        self.save()
        tick = time.monotonic()
        # A killed call keeps null and its durable stage-start evidence.
        yield
        self.result['timings'][name] = time.monotonic() - tick
        self.result['current_stage'] = None
        self.emit('stage_finished', stage=name, seconds=self.result['timings'][name])
        self.save()

