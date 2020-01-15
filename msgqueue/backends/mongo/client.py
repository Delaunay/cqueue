import datetime
import pymongo

from msgqueue.uri import parse_uri
from msgqueue.backends.queue import Message, MessageQueue, QueuePacemaker

from .util import _parse
from .server import new_queue


class MongoQueuePacemaker(QueuePacemaker):
    def __init__(self, agent, namespace, wait_time, capture):
        self.client = agent.client
        super(MongoQueuePacemaker, self).__init__(agent, namespace, wait_time, capture)

    def register_agent(self, agent_name):
        self.agent_id = self.client[self.namespace].system.insert_one({
            'time': datetime.datetime.utcnow(),
            'agent': agent_name,
            'heartbeat': datetime.datetime.utcnow(),
            'alive': True,
            'message': None,
            'queue': None
        }).inserted_id
        return self.agent_id

    def update_heartbeat(self):
        self.client[self.namespace].system.update_one(
            {'_id': self.agent_id},
            {'$set': {
                'heartbeat': datetime.datetime.utcnow()}
            })

    def register_message(self, name, message):
        if message is None:
            return None

        self.client[self.namespace].system.update_one({'_id': self.agent_id}, {
            '$set': {
                'message': message.uid,
                'queue': name
            }
        })
        return message

    def unregister_message(self, uid=None):
        self.client[self.namespace].system.update_one({
            '_id': self.agent_id,
            'message': uid}, {
            '$set': {
                'message': None,
                'queue': None}})

    def unregister_agent(self):
        self.client[self.namespace].system.update_one({'_id': self.agent_id}, {
            '$set': {'alive': False}
        })

    def insert_log_line(self, line, ltype=0):
        if self.agent_id is None or self.client is None:
            return

        self.client[self.namespace].logs.insert_one({
            'agent': self.agent_id,
            'ltype': ltype,
            'line': line
        })


class MongoClient(MessageQueue):
    """Simple cockroach db queue client

    Parameters
    ----------
    uri: str
        mongodb://192.168.0.10:8123
    """

    def __init__(self, uri, namespace, name='worker', log_capture=True, timeout=60):
        uri = parse_uri(uri)
        self.name = name
        self.namespace = namespace
        self.client = pymongo.MongoClient(host=uri['address'], port=int(uri['port']))
        self.heartbeat_monitor = None
        self.capture = log_capture
        self.timeout = timeout

    def join(self):
        return self.heartbeat_monitor.join()

    def pacemaker(self, namespace, wait_time, capture):
        return MongoQueuePacemaker(self, namespace, wait_time, capture)

    def enqueue(self, name, message, mtype=0, replying_to=None):
        """See `~mlbaselines.distributed.queue.MessageQueue`"""
        if not self._queue_exist(name):
            new_queue(self.client, self.namespace, name)

        return self.client[self.namespace][name].insert_one({
            'time': datetime.datetime.utcnow(),
            'mtype': mtype,
            'read': False,
            'read_time': None,
            'actioned': False,
            'actioned_time': None,
            'replying_to': replying_to,
            'message': message,
            'retry': 0,
            'error': None
        }).inserted_id

    def dequeue(self, name, mtype=None):
        """See `~mlbaselines.distributed.queue.MessageQueue`"""
        query = {'read': False}

        if isinstance(mtype, (list, tuple)):
            query['mtype'] = {'$in': list(mtype)}

        elif isinstance(mtype, int):
            query['mtype'] = mtype

        msg = self.client[self.namespace][name].find_one_and_update(
            query,
            {'$set': {
                'read': True, 'read_time': datetime.datetime.utcnow()}
            },
            return_document=pymongo.ReturnDocument.AFTER
        )
        return self._register_message(name, _parse(msg))

    def mark_actioned(self, name, uid: Message = None):
        """See `~mlbaselines.distributed.queue.MessageQueue`"""
        if isinstance(uid, Message):
            uid = uid.uid

        self.client[self.namespace][name].find_one_and_update(
            {'_id': uid},
            {'$set': {
                'actioned': True,
                'actioned_time': datetime.datetime.utcnow()}
            }
        )
        self._unregister_message(uid)
        return uid

    def mark_error(self, name, uid, error):
        if isinstance(uid, Message):
            uid = uid.uid

        self.client[self.namespace][name].find_one_and_update(
            {'_id': uid},
            {'$set': {
                'error': error}
            }
        )
        self._unregister_message(uid)
        return uid

    def reply(self, name, uid):
        if isinstance(uid, Message):
            uid = uid.uid

        return self.monitor().reply(self.namespace, name, uid)

    def monitor(self):
        from .monitor import MongoQueueMonitor
        return MongoQueueMonitor(cursor=self.client)


def new_client(*args, **kwargs):
    return MongoClient(*args, **kwargs)