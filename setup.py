#!/usr/bin/env python
import io
import os
import sys

from setuptools import find_packages, setup
from setuptools.command.test import test as TestCommand  # noqa: N812


if sys.argv[-1] == 'publish':
    os.system('python setup.py sdist upload')
    os.system('python setup.py bdist_wheel upload')
    sys.exit()


readme = io.open('README.md', 'r', encoding='utf-8').read()


class PyTestCommand(TestCommand):
    """
    From https://pytest.org/latest/goodpractices.html
    """
    user_options = [('pytest-args=', 'a', 'Arguments to pass to py.test')]

    def initialize_options(self):
        TestCommand.initialize_options(self)
        self.pytest_args = []

    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        # import here, cause outside the eggs aren't loaded
        import pytest
        errno = pytest.main(self.pytest_args)
        sys.exit(errno)


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
    cmdclass={'test': PyTestCommand},
    entry_points="""
        [console_scripts]
        storyscript-server=storyruntime.Service:Service.main
    """
)
