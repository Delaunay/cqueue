import signal
import time
import os

from msgqueue.backends import new_client
from msgqueue.backends.queue import RecordQueue, MessageQueue

from multiprocessing import Process
import pytest


QUEUE = 'TESTING_QUEUE'
NAMESPACE = 'TESTING_NAMESPACE'
DATABASE = 'test'
URI = 'mongo://127.0.0.1:27017'


class FakeClient:
    def __init__(self, client: MessageQueue):
        self.client = client
        self.m = None

    def do_something_1(self):
        print('do_something_1')
        time.sleep(5)
        self.m = self.client.pop(QUEUE, NAMESPACE)
        print(self.m)

    def do_something_2(self):
        print('do_something_2')
        time.sleep(5)
        self.client.mark_actioned(QUEUE, self.m)
        print('DONE')


def my_important_transaction():
    # Nothing is done there, everything is lazy
    client = RecordQueue()
    client.do_something_1()
    time.sleep(5)
    client.do_something_2()

    # Our Transaction is running there
    client.execute(FakeClient(new_client(URI, DATABASE)))


kill_early_signals = [
    signal.SIGTERM,
    signal.SIGINT,
    signal.SIGKILL
]


@pytest.mark.parametrize('signal', kill_early_signals)
def test_check_sigkill_nothing_happen(signal):
    client = new_client(URI, DATABASE)
    client.db[QUEUE].drop()
    client.push(QUEUE, NAMESPACE, {'my_work': 0})

    p = Process(target=my_important_transaction)

    p.start()
    time.sleep(1)
    os.kill(p.pid, signal)

    # Nothing was done, the process died before the transaction
    assert not client.monitor().messages(QUEUE, NAMESPACE)[0].read
    assert not client.monitor().messages(QUEUE, NAMESPACE)[0].actioned


signals = [
    signal.SIGTERM,
    signal.SIGINT
]


@pytest.mark.parametrize('signal', signals)
def test_check_sigterm_everything_finished(signal):
    client = new_client(URI, DATABASE)
    client.db[QUEUE].drop()
    client.push(QUEUE, NAMESPACE, {'my_work': 0})

    p = Process(target=my_important_transaction)

    p.start()
    time.sleep(10)
    # Kill during the transaction
    os.kill(p.pid, signal)

    # Should not be able to kill it until the end of the thread
    p.join()

    # Nothing was done, the process died before the transaction
    assert client.monitor().messages(QUEUE, NAMESPACE)[0].read
    assert client.monitor().messages(QUEUE, NAMESPACE)[0].actioned


if __name__ == '__main__':
    test_check_sigterm_everything_finished(signal.SIGTERM)
