import datetime
from typing import List

import pymongo

from msgqueue.logs import warning
from msgqueue.uri import parse_uri
from msgqueue.backends.queue import Message, MessageQueue, QueuePacemaker, Reply

from .util import _parse
from .server import new_queue


class MongoQueuePacemaker(QueuePacemaker):
    def __init__(self, agent, wait_time, capture):
        self.client = agent.db
        super(MongoQueuePacemaker, self).__init__(agent, wait_time, capture)

    def register_agent(self, agent_name):
        self.agent_id = self.client.system.insert_one({
            'time': datetime.datetime.utcnow(),
            'agent': agent_name,
            'heartbeat': datetime.datetime.utcnow(),
            'alive': True,
            'message': None,
            'namespace': None
        }).inserted_id
        return self.agent_id

    def update_heartbeat(self):
        if self.message is not None:
            self.client[self.name].update_one({
                '_id': self.message.uid
            }, {
                '$set': {
                    'heartbeat': datetime.datetime.utcnow()
                }
            })

    def register_message(self, name, message: Message):
        if message is None:
            return None

        self.client.system.update_one(
            {'_id': self.agent_id},
            {
                '$set': {
                    'message': message.uid,
                    'namespace': message.namespace,
                    'heartbeat': datetime.datetime.utcnow()
                }
            }
        )

        self.name = name
        self.message = message
        self.update_heartbeat()

        return message

    def unregister_message(self, uid=None):
        self.client.system.update_one(
            {'_id': self.agent_id},
            {
                '$set': {
                    'message': None,
                    'heartbeat': datetime.datetime.utcnow()
                }
            }
        )

    def unregister_agent(self):
        self.client.system.update_one({'_id': self.agent_id}, {
            '$set': {'alive': False}
        })

    def insert_log_line(self, line, ltype=0):
        if self.agent_id is None or self.client is None:
            return

        self.client.logs.insert_one({
            'agent': self.agent_id,
            'ltype': ltype,
            'line': line
        })


class MongoClient(MessageQueue):
    """Simple cockroach db queue client

    Parameters
    ----------
    uri: str
        mongo://192.168.0.10:8123
    """

    def __init__(self, uri, database, name='worker', log_capture=True, timeout=60):
        mongodb_uri = uri.replace('mongo', 'mongodb')
        uri = parse_uri(uri)
        self.name = name

        if uri.get('username') is not None:
            self.client = pymongo.MongoClient(mongodb_uri)
        else:
            self.client = pymongo.MongoClient(host=uri['address'], port=int(uri['port']))

        self.heartbeat_monitor = None
        self.capture = log_capture
        self.timeout = timeout
        self.database = database
        self.db = self.client[self.database]

    def join(self):
        return self.heartbeat_monitor.join()

    def pacemaker(self, wait_time, capture):
        return MongoQueuePacemaker(self, wait_time, capture)

    def _queue_exist(self, queue):
        return queue in self.monitor().queues()

    def enqueue(self, queue, namespace, message, mtype=0, replying_to=None):
        """See `~mlbaselines.distributed.queue.MessageQueue`"""
        if not self._queue_exist(queue):
            new_queue(self.db, namespace, queue)

        message = {
            'namespace': namespace,
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
        }

        return self.db[queue].insert_one(message).inserted_id

    def dequeue(self, queue, namespace, mtype=None):
        """See `~mlbaselines.distributed.queue.MessageQueue`"""
        query = {
            'read': False,
        }

        if namespace is not None:
            query['namespace'] = namespace

        if isinstance(mtype, (list, tuple)):
            query['mtype'] = {'$in': list(mtype)}

        elif isinstance(mtype, int):
            query['mtype'] = mtype

        msg = self.db[queue].find_one_and_update(
            query, {
                '$set': {
                    'read': True, 'read_time': datetime.datetime.utcnow()}
            },
            sort=[
                ('time', pymongo.ASCENDING),
            ],
            return_document=pymongo.ReturnDocument.AFTER
        )
        return self._register_message(queue, _parse(msg))

    def mark_actioned(self, queue, uid: Message = None):
        """See `~mlbaselines.distributed.queue.MessageQueue`"""
        if isinstance(uid, Message):
            uid = uid.uid

        self.db[queue].find_one_and_update({
            '_id': uid}, {
            '$set': {
                'actioned': True,
                'actioned_time': datetime.datetime.utcnow()}
        }
        )
        self._unregister_message(uid)
        return uid

    def _rollback_actioned_all(self, queue, messages):
        self.db[queue].find_and_modify({
            '_id': {
                '$in': list(map(lambda m: m.uid, messages))
            }}, {
            '$set': {
                'actioned': False
            }
        })

    def reply(self, queue, namespace, work_messages: dict, work_reply, mtype=None):
        # TODO: add transaction here if mongo>=4.2
        try:
            for mqueue, messages in work_messages.items():
                self.mark_actioned_all(mqueue, messages)

            self.enqueue(queue, namespace, work_reply, mtype=mtype)
        except Exception as e:
            warning(f'Rolling back because of {e}')

            for mqueue, messages in work_messages.items():
                self._rollback_actioned_all(mqueue, messages)

            raise e

    def mark_actioned_all(self, queue, messages: List[Message]):
        """See `~mlbaselines.distributed.queue.MessageQueue`"""
        self.db[queue].find_and_modify({
            '_id': {
                '$in': list(map(lambda m: m.uid, messages))
            }}, {
            '$set': {
                'actioned': True,
                'actioned_time': datetime.datetime.utcnow()}
            }
        )
        self._unregister_message(messages[-1].uid)

    def mark_error(self, queue, uid, error):
        if isinstance(uid, Message):
            uid = uid.uid

        self.db[queue].find_one_and_update({
            '_id': uid}, {
                '$set': {
                    'error': error}
            }
        )
        self._unregister_message(uid)
        return uid

    def monitor(self):
        from .monitor import MongoQueueMonitor
        return MongoQueueMonitor(uri=None, database=self.database, cursor=self.client)

    def aggregate_monitor(self):
        from .aggregate_monitor import AggregateMonitor
        return AggregateMonitor(uri=None, database=self.database, cursor=self.client)


def new_client(*args, **kwargs):
    return MongoClient(*args, **kwargs)
