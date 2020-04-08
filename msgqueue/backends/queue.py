from dataclasses import dataclass, asdict
from datetime import datetime
import threading
from typing import Union, List


def to_dict(a):
    if isinstance(a, (Agent, Message)):
        return asdict(a)

    elif isinstance(a, datetime):
        return a.timestamp()

    raise TypeError(f'type {type(a)} not json serializable')


@dataclass
class Agent:
    uid: int             # Unique ID of the agent
    time: datetime       # Time the agent was created
    agent: str           # Name of the Agent (Names are not unique)
    heartbeat: datetime  # Last time we had a proof of life
    alive: bool          # Is the Agent Alive
    # Necessary to detect messages that are stuck
    message: int         # Message the Agent is processing
    queue: str           # Message queue the message belong to

    def to_dict(self):
        return asdict(self)


@dataclass
class Message:
    uid: int                        # Unique ID of the message
    time: datetime                  # Time that message was created
    mtype: int                      # type of message
    read: bool                      # Was that message read
    read_time: datetime             # Time when that message was read
    actioned: bool                  # Was that message processed
    actioned_time: datetime         # Time when that message was done being processed
    replying_to: int                # Message ID this message relies to
    message: str                    # User data
    retry: int                      # Number of time it has been retried
    error: str                      # Error if any

    def __repr__(self):
        return f"""Message({self.uid}, {self.time}, {self.mtype}, {self.read}, """ +\
            f"""{self.read_time}, {self.actioned}, {self.actioned_time}, {self.message})"""

    def to_dict(self):
        return asdict(self)


class _Buffer:
    def __init__(self, file, pacemaker, ltype=0):
        self.file = file
        self.pacemaker = pacemaker
        self.ltype = ltype

    def flush(self):
        if self.file is not None:
            self.file.flush()

    def write(self, data):
        import traceback
        try:
            self.pacemaker.insert_log_line(data, ltype=self.ltype)
        except Exception as e:
            print(f'`{data}`', file=self.file)
            print(traceback.format_exc(), file=self.file)

        if self.file is not None:
            self.file.write(data)


class QueueServer:
    pass


class QueuePacemaker(threading.Thread):
    def __init__(self, agent, namespace, wait_time, capture):
        threading.Thread.__init__(self)
        self.namespace = namespace
        self.stopped = threading.Event()
        self.wait_time = wait_time
        self.agent = agent
        self.agent_id = None
        self.capture = capture
        if capture:
            self.capture_output()

    def capture_output(self):
        # capture std output
        import sys
        sys.stdout = _Buffer(sys.stdout, self, ltype=0)
        sys.stderr = _Buffer(sys.stderr, self, ltype=0)

        # capture logging output
        import logging

        stream = _Buffer(None, self, ltype=0)

        root = logging.getLogger()
        root.propagate = False
        ch = logging.StreamHandler(stream)
        formatter = logging.Formatter(
            '%(relativeCreated)8d [%(levelname)8s] %(name)s [%(process)d] %(pathname)s:%(lineno)d %(message)s')
        ch.setFormatter(formatter)
        root.addHandler(ch)

    def register_agent(self, agent_name):
        raise NotImplementedError()

    def run(self):
        """Run the trial monitoring every given interval."""
        while not self.stopped.wait(self.wait_time):
            self.update_heartbeat()

    def update_heartbeat(self):
        raise NotImplementedError()

    def register_message(self, name, message):
        raise NotImplementedError()

    def unregister_message(self, uid):
        raise NotImplementedError()

    def stop(self):
        """Stop monitoring."""
        self.stopped.set()
        self.join()

        if self.capture:
            import sys
            sys.stdout = sys.stdout.file
            sys.stderr = sys.stderr.file

    def unregister_agent(self):
        raise NotImplementedError()

    def insert_log_line(self, line, ltype=0):
        raise NotImplementedError()


class MessageQueue:
    def pacemaker(self, namespace, wait_time, capture):
        raise NotImplementedError()

    def __enter__(self):
        self.heartbeat_monitor = self.pacemaker(self.namespace, self.timeout, self.capture)
        self.heartbeat_monitor.register_agent(self.name)
        self.heartbeat_monitor.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.heartbeat_monitor.stop()
        self.heartbeat_monitor.unregister_agent()
        self.heartbeat_monitor.client = None

    def join(self):
        return self.heartbeat_monitor.join()

    def enqueue(self, name, message, mtype=0, replying_to=None):
        """Insert a new message inside the queue

        Parameters
        ----------
        name: str
            Message queue namespace

        message: str
            message to insert

        mtype: int
            message type

        replying_to: int
            message id this message replies to
        """
        raise NotImplementedError()

    def dequeue(self, name, mtype: Union[int, List[int]] = None):
        """Remove oldest message from the queue i.e mark is as read

        Parameters
        ----------
        name: str
            Queue namespace to pop message from

        mtype: Union[int, List[int]
            type of message to look for (default: none)
        """
        raise NotImplementedError()

    def mark_actioned(self, name, message: Union[Message, int]):
        """Mark a message as actioned

        Parameters
        ----------
        name: str
            Message queue namespace

        message: Union[Message, int]
            message object to update or uid of the message

        """
        raise NotImplementedError()

    def mark_error(self, name, message, error):
        raise NotImplementedError()

    def push(self, name, message, mtype=0, replying_to=None):
        return self.enqueue(name, message, mtype, replying_to)

    def pop(self, name, mtype: Union[int, List[int]] = None):
        return self.dequeue(name, mtype)

    def get_reply(self, name, message: Union[int, List[int]]):
        raise NotImplementedError()

    def _register_message(self, name, msg):
        if self.heartbeat_monitor:
            return self.heartbeat_monitor.register_message(name, msg)

        return msg

    def _unregister_message(self, uid):
        if self.heartbeat_monitor:
            return self.heartbeat_monitor.unregister_message(uid)

    def _queue_exist(self, queue):
        return self.namespace in self.monitor().namespaces() and queue in self.monitor().queues(self.namespace)


class QueueMonitor:
    def archive(self, namespace, archive_name, namespace_out=None, format='json'):
        """Archive a namespace into a zipfile and delete the namespace from the database"""
        raise NotImplementedError()

    def namespaces(self):
        raise NotImplementedError()

    def queues(self, namespace):
        raise NotImplementedError()

    def agents(self, namespace):
        raise NotImplementedError()

    def clear(self, namespace, name):
        """Clear the queue by removing all messages"""
        raise NotImplementedError()

    def messages(self, namespace, name, limit=100):
        raise NotImplementedError()

    def unread_messages(self, namespace, name):
        raise NotImplementedError()

    def unactioned_messages(self, namespace, name):
        raise NotImplementedError()

    def unread_count(self, namespace, name):
        raise NotImplementedError()

    def unactioned_count(self, namespace, name):
        raise NotImplementedError()

    def actioned_count(self, namespace, name):
        raise NotImplementedError()

    def read_count(self, namespace, name):
        raise NotImplementedError()

    def reset_queue(self, namespace, name):
        """Hard reset the queue, putting all unactioned messages into an unread state"""
        raise NotImplementedError()

    def dead_agents(self, namespace, timeout_s=60):
        """Return a list of unresponsive agent"""
        raise NotImplementedError()

    def lost_messages(self, namespace, timeout_s=60):
        """Return the list of messages that were assigned to worker that died"""
        raise NotImplementedError()

    def requeue_lost_messages(self, namespace):
        raise NotImplementedError()

    def failed_messages(self, namespace, queue):
        """Return the list of messages that failed because of an exception was raised"""
        raise NotImplementedError()

    def requeue_failed_messages(self, namespace, queue, max_retry):
        raise NotImplementedError()

    def log(self, namespace, agent: Union[Agent, int], ltype: int = 0):
        """Return the log of an agent"""
        raise NotImplementedError()

    def reply(self, namespace, name, uid):
        """Return the reply of a message"""
        raise NotImplementedError

    def _make_archive(self, namespace, archive_name, namespace_out, format, remove_db, log_types, lock):
        """Archive a namespace into a zipfile and delete the namespace from the database"""
        import zipfile
        import json
        import bson

        if format == 'bson':
            dumper = lambda m, fp: fp.write(bson.encode({'data': [asdict(i) for i in m]}))
        elif format == 'json':
            dumper = lambda m, fp: json.dump(m, fp=_Wrapper(fp), default=to_dict)
        else:
            raise RuntimeError('Format must be json or bson')

        if namespace_out is None:
            namespace_out = namespace

        # transform strings to bytes
        class _Wrapper:
            def __init__(self, buffer):
                self.buffer = buffer

            def write(self, data):
                self.buffer.write(data.encode('utf-8'))

        # archive logic
        with lock:
            with zipfile.ZipFile(archive_name, 'w') as archive:
                queues = self.queues(namespace)

                for queue in set(queues):
                    with archive.open(f'{namespace_out}/{queue}.{format}', 'w') as queue_archive:
                        messages = self.messages(namespace, queue)
                        dumper(messages, queue_archive)

                with archive.open(f'{namespace_out}/system.{format}', 'w') as system_archive:
                    agents = self.agents(namespace)
                    dumper(agents, system_archive)

                for agent in agents:
                    for type in log_types(namespace, agent):
                        with archive.open(f'{namespace_out}/logs/{agent.uid}_{type}.txt', 'w') as logs_archive:
                            log = self.log(namespace, agent, type)
                            _Wrapper(logs_archive).write(log)

            if remove_db:
                remove_db(namespace)

        print('Archiving is done')
        return None

