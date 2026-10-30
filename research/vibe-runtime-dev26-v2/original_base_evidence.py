"""Capture complete native events before generation; Python3.7-compatible."""
import hashlib
import json
from pathlib import Path
import subprocess

MAX_EVENTS = 4*1024*1024


def capture_native_events(commands, case, recorder, working_dir, output, *, timeout=180, environment=None):
    commands, recorder, output = Path(commands), Path(recorder), Path(output)
    contract = json.loads(commands.read_text(encoding='utf-8'))
    if contract.get('frozen_before_oracle_access') is not True:
        raise ValueError('Pre-oracle native command contract required')
    argv = contract['commands'][case]['argv']
    if (not isinstance(argv,list) or len(argv)<2 or argv[0]!='pytest'
            or any(not isinstance(v,str) or '\x00' in v for v in argv)):
        raise ValueError('Explicit native pytest argv required')
    output.mkdir(parents=True,exist_ok=False)
    command = [__import__('sys').executable,'-B',str(recorder.resolve()),'--commands',str(commands.resolve()),
               '--case',case,'--output',str((output/'run').resolve())]
    timed_out, exit_code = False, None
    with (output/'stdout.log').open('xb') as log:
        try:
            result = subprocess.run(command,cwd=str(working_dir),env=environment,
                                    stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        log.seek(0,2)
        size = log.tell()
    with (output/'stdout.log').open('rb') as log:
        first = log.read(18000).decode('utf-8',errors='replace')
        log.seek(max(0,size-12000))
        tail = log.read(12000).decode('utf-8',errors='replace')
    events = output/'run/native-events.json'
    raw, record = None, None
    if events.exists():
        if events.stat().st_size > MAX_EVENTS:
            raise ValueError('Complete original BASE events exceed frozen transport limit')
        raw = events.read_bytes()
        record = json.loads(raw.decode('utf-8'))
        if record['native_command'] != argv or record.get('test_selection_changed') is not False:
            raise ValueError('Recorded native selection differs')
        if not timed_out and record['exit_code'] != exit_code:
            raise ValueError('Recorded pytest and child exit codes differ')
    return {'record':record,'record_sha256':hashlib.sha256(raw).hexdigest() if raw is not None else None,
            'record_bytes':len(raw) if raw is not None else None,'exit_code':exit_code,'timed_out':timed_out,
            'native_argv':argv,'native_argv_changed':False,'environment_overridden':environment is not None,
            'diagnostics':first if size<=18000 else first+'\n[bounded original BASE tail]\n'+tail,
            'full_stdout_bytes':size,'full_events_retained':raw is not None and not timed_out}
