#!/usr/bin/env python
from setuptools import setup
import subprocess
import os


if __name__ == '__main__':
    _base = os.path.dirname(os.path.realpath(__file__))
    data = []

    subprocess.call(f'./{_base}/install_cockroach.sh', shell=True)
    cockroach_path = f'{_base}/msgqueue/backends/cockroach/bin/cockroach'

    if os.path.exists(cockroach_path):
        data = [('msgqueue', [cockroach_path])]
    else:
        print('cockroach db is not going to be installed')

    setup(
        name='msgqueue',
        version='0.1.0',
        description='Message Queue Primitives',
        author='Pierre Delaunay',
        packages=[
            'msgqueue',
            'msgqueue.backends',
            'msgqueue.backends.cockroach',
            'msgqueue.backends.mongo',
        ],
        data_files=data,
        setup_requires=['setuptools'],
        tests_require=['pytest', 'flake8', 'codecov', 'pytest-cov'],
    )
