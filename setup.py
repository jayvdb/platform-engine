#!/usr/bin/env python
import io
import os
import sys

from setuptools import find_packages, setup


if sys.argv[-1] == 'publish':
    os.system('python setup.py sdist upload')
    os.system('python setup.py bdist_wheel upload')
    sys.exit()


readme = io.open('README.md', 'r', encoding='utf-8').read()

setup(
    name='storyscript-platform-engine',
    description='The engine of the Storyscript platform',
    long_description=readme,
    author='Storyscript',
    author_email='noreply@storyscript.io',
    version='0.2.0',
    packages=find_packages(
        exclude=('build.*', 'bench', 'bench.*', 'tests', 'tests.*'),
    ),
    tests_require=[
        'pytest',
        'pytest-cov',
        'pytest-mock',
        'pytest-asyncio',
    ],
    setup_requires=['pytest-runner'],
    python_requires='>=3.7',
    install_requires=[
        'prometheus-client==0.2.0',
        'tornado==5.0.2',
        'click==7.0',
        'frustum==0.0.6',
        'sentry-sdk==0.10.2',
        'storyscript==0.24.0',
        'ujson==1.35',
        'certifi>=2018.8.24',
        'asyncpg==0.18.3',
        'numpy==1.16.4',
        'expiringdict==1.1.4',
        'requests==2.21.0'  # Used for structures like CaseInsensitiveDict.
    ],
    classifiers=[
        'Environment :: Console',
        'Intended Audience :: Developers',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3'
    ],
    entry_points="""
        [console_scripts]
        storyscript-server=storyruntime.Service:Service.main
    """
)
