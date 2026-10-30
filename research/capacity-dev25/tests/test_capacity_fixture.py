import hashlib
from pathlib import Path
import socket
import sys
import threading
import time

import httpx
import uvicorn

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(KIT/'source'))
sys.path.insert(0,str(KIT))
from capacity_fixture import warm_fixture, verify_fixture
from parallel_replay import replay_search, replay_history_search
from heguang_code.api import create_app
from heguang_code.config import Settings
from heguang_code.selection import source_record


class Scorer:
    def scores(self, query, documents):
        return [9. if 'required_revision):' in d else 0. for d in documents]


def test_capacity_input_has_exact_bounds_and_actual_priority_signal():
    assert verify_fixture()


def test_actual_socket_parallel_search_preserves_compatible_response_and_isolation(tmp_path):
    app = create_app(Settings(data_dir=tmp_path,api_key='synthetic',source_selection='coalesce',max_concurrency=1),scorer=Scorer())
    reserved = socket.socket();reserved.bind(('127.0.0.1',0))
    port = reserved.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning',access_log=False))
    thread = threading.Thread(target=lambda:server.run(sockets=[reserved]),daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+10
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.02)
        url = f'http://127.0.0.1:{port}'
        payload, query = warm_fixture()
        with httpx.Client(base_url=url,headers={'X-Api-Key':'synthetic'},timeout=30,trust_env=False) as client:
            assert client.post('/add',json=payload).status_code == 200
            before = client.post('/search',json=query)
            assert before.status_code == 200
            assert 'required_revision=None' in source_record(before.json()['data'][0])[1]
            users = []
            for i in range(16):
                user = 'history-'+str(i)
                users.append({'user_id':user})
                text = 'Qiskit QuantumCircuit circuit append\n'+str(i)
                assert client.post('/add',json={'user_id':user,'session_id':'history','request_id':'r',
                    'messages':[{'role':'user','content':text}]}).status_code == 200
            assert client.post('/search',json={**query,'user_id':'missing'}).json()['data'] == []
        warm = replay_search(url,'synthetic',query,before.content)
        actual = replay_history_search(url,'synthetic',users,tmp_path)
        assert warm['clients_peak'] == actual['clients_peak'] == 16
        assert warm['all_responses_byte_equal'] and actual['returned_source_spans_audited'] == 16
        assert len({r['response_sha256'] for r in warm['requests']}) == 1
        assert len({r['response_sha256'] for r in actual['requests']}) == 16
    finally:
        server.should_exit = True
        thread.join(10)
        reserved.close()
        assert not thread.is_alive()
