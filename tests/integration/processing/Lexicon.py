# -*- coding: utf-8 -*-
import math
import re
import sys
from unittest.mock import MagicMock

from asyncy.Exceptions import StoryscriptError, StoryscriptRuntimeError, \
    TypeAssertionRuntimeError, TypeValueRuntimeError
from asyncy.Stories import Stories
from asyncy.processing import Story
from asyncy.processing.internal import File, Http, Json, Log

from pytest import mark

import storyscript

from .Assertions import ContextAssertion, IsANumberAssertion, \
    ListItemAssertion, MapValueAssertion, RuntimeExceptionAssertion


class TestCase:
    def __init__(self, append=None, prepend=None, assertion=None):
        self.append = append
        self.prepend = prepend
        self.assertion = assertion


class TestSuite:
    def __init__(self, cases, preparation_lines=''):
        self.cases = cases
        self.preparation_lines = preparation_lines


@mark.parametrize('suite', [  # See pydoc below for how this runs.
    TestSuite(
        preparation_lines='a = {}\n'
                          'b = 0\n',
        cases=[
            TestCase(append='if a["foo"] != null\n'
                            '    b = 1',
                     assertion=ContextAssertion(key='b', expected=0)),
            TestCase(append='if a["foo"] == null\n'
                            '    b = 1',
                     assertion=ContextAssertion(key='b', expected=1))
        ]
    ),
    TestSuite(
        preparation_lines='a = {"a": "b"}',
        cases=[
            TestCase(append='b = "{1} {a}"',
                     assertion=ContextAssertion(key='b',
                                                expected='1 {\'a\': \'b\'}'))
        ]
    ),
    TestSuite(
        preparation_lines='a = json stringify content: {"a": "b"}',
        cases=[
            TestCase(assertion=ContextAssertion(key='a',
                                                expected='{"a": "b"}'))
        ]
    ),
    TestSuite(
        preparation_lines='a = 1\n'
                          'if false and true\n'
                          '    a = 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=1))
        ]
    ),
    TestSuite(
        preparation_lines='a = 1\n'
                          'if true and false\n'
                          '    a = 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=1))
        ]
    ),
    TestSuite(
        preparation_lines='a = 1283',
        cases=[
            TestCase(append='b = a + ""',
                     assertion=ContextAssertion(key='b', expected='1283'))
        ]
    ),
    TestSuite(
        preparation_lines='function is_even n:int returns boolean\n'
                          '    if n % 2 == 0\n'
                          '        return true\n'
                          '    else\n'
                          '        return false\n'
                          '\n'
                          'even = is_even(n: a)',  # a is prepended.
        cases=[
            TestCase(prepend='a = 10',
                     assertion=ContextAssertion(key='even', expected=True)),
            TestCase(prepend='a = 11',
                     assertion=ContextAssertion(key='even', expected=False))
        ]
    ),
    TestSuite(
        preparation_lines='function echo i:int returns int\n'
                          '    return i\n'
                          '\n'
                          'x = 500\n'
                          'function add a:int b:int returns int\n'
                          '    return a + b\n'
                          '\n'
                          'function get_28 returns int\n'
                          '    return 28\n'
                          '\n'
                          'y=30*x\n'
                          'function do_nothing\n'
                          '    a = "nothing meaningful happened"\n',
        cases=[
            TestCase(append='a = echo(i: 200)',
                     assertion=ContextAssertion(key='a', expected=200)),
            TestCase(append='a = echo(i: -1)',
                     assertion=ContextAssertion(key='a', expected=-1)),
            TestCase(append='a = echo(i: 28)',
                     assertion=ContextAssertion(key='a', expected=28)),
            TestCase(append='echo(i: 28)',
                     assertion=[]),
            TestCase(append='a = add(a: 10 b: 20)',
                     assertion=ContextAssertion(key='a', expected=30)),
            TestCase(append='a = add(a: 10 b: 20) + get_28()',
                     assertion=ContextAssertion(key='a', expected=58)),
            TestCase(append='a = get_28()',
                     assertion=ContextAssertion(key='a', expected=28)),
            TestCase(append='do_nothing()',
                     assertion=ContextAssertion(key='a', expected=None)),
        ]
    ),
    TestSuite(
        cases=[
            TestCase(append='a = echo(i: 200)\n'
                            'function echo i:int returns int\n'
                            '    return i\n',
                     assertion=ContextAssertion(key='a', expected=200)),
        ]
    ),
    TestSuite(
        preparation_lines='my_list = [1, 2, 3]',
        cases=[
            TestCase(append='a = (my_list length) + 4',
                     assertion=ContextAssertion(key='a', expected=7)),
            TestCase(append='a = my_list[0]',
                     assertion=ContextAssertion(key='a', expected=1)),
            TestCase(append='a = my_list[-1]',
                     assertion=ContextAssertion(key='a', expected=3)),
        ]
    ),
    TestSuite(
        preparation_lines='status = "opened"\n'
                          'tag = "priority"\n'
                          'if status == "opened" and '
                          '(["important", "priority"] contains item: tag)\n'
                          '   a = 1',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=1))
        ]
    ),
    TestSuite(
        preparation_lines='hello = "hello"\n'
                          'world = "world"',
        cases=[
            TestCase(append='a = hello + world',
                     assertion=ContextAssertion(
                         key='a', expected='helloworld')),
            TestCase(append='a = hello + " " + world',
                     assertion=ContextAssertion(
                         key='a', expected='hello world')),
            TestCase(append='a = hello + " "',  # Test for no auto trim.
                     assertion=ContextAssertion(
                         key='a', expected='hello ')),
            TestCase(append='a = "{hello}"',
                     assertion=ContextAssertion(
                         key='a', expected='hello')),
            TestCase(append='a = "{hello} {world}"',
                     assertion=ContextAssertion(
                         key='a', expected='hello world')),
            TestCase(append='a = "{hello}{world}"',
                     assertion=ContextAssertion(
                         key='a', expected='helloworld'))
        ]
    ),
    TestSuite(
        preparation_lines='labels = [{"name": "a"}]\n'
                          'found = false',
        cases=[
            TestCase(
                append='foreach labels as label\n'
                       '   if label["name"] == "a" or label["name"] == "b"\n'
                       '        found = true\n'
                       'outside = true',
                assertion=[ContextAssertion(key='found', expected=True),
                           ContextAssertion(key='outside', expected=True)]
            )
        ]
    ),
    TestSuite(
        preparation_lines='a = 1\n'
                          'b = 5\n'
                          'c = null\n',
        cases=[
            TestCase(append='if true or false\n'
                            '   c = "true"',
                     assertion=ContextAssertion(key='c', expected='true')),
            TestCase(append='if false or true\n'
                            '   c = "true"',
                     assertion=ContextAssertion(key='c', expected='true')),
            TestCase(append='if true\n'
                            '   c = "true"',
                     assertion=ContextAssertion(key='c', expected='true')),
            TestCase(append='if false\n'
                            '   c = "wtf"',
                     assertion=ContextAssertion(key='c', expected=None)),
            TestCase(append='if a == 100 or b == 100\n'
                            '   c = "wtf"',
                     assertion=ContextAssertion(key='c', expected=None)),
            TestCase(append='if a == 100 or b == 5\n'
                            '   c = "b"',
                     assertion=ContextAssertion(key='c', expected='b')),
            TestCase(append='if a == 1 or b == 100\n'
                            '   c = "a"',
                     assertion=ContextAssertion(key='c', expected='a')),
            TestCase(append='if a == 1 or b == 5\n'
                            '   c = "a"',
                     assertion=ContextAssertion(key='c', expected='a')),
            TestCase(append='if a == 100 or b == 100 or true\n'
                            '   c = "true"',
                     assertion=ContextAssertion(key='c', expected='true'))
        ]
    ),
    TestSuite(
        preparation_lines='a = [1, 2, 3, 4, 5]\n'
                          'b = []\n'
                          'c = []\n',
        cases=[
            TestCase(append='foreach a as elem\n'
                            '   b append item: elem\n'
                            '   foreach b as elem2\n'
                            '       if elem2 > 1\n'
                            '           break\n'
                            '       c append item: elem2\n',
                     assertion=[
                         ContextAssertion(key='b', expected=[1, 2, 3, 4, 5]),
                         ContextAssertion(key='c', expected=[1, 1, 1, 1, 1])
                     ])
        ]
    ),
    TestSuite(
        preparation_lines='a = [1, 1, 1, 2, 3, 4, 5]\n'
                          'b = 0\n',
        cases=[
            TestCase(append='b = a[b]',
                     assertion=ContextAssertion(key='b', expected=1)),
            TestCase(append='foreach a as elem\n'
                            '   b = b + elem\n'
                            '   if b == 3\n'
                            '       break',
                     assertion=ContextAssertion(key='b', expected=3))
        ]
    ),
    TestSuite(
        preparation_lines='a = [0]',
        cases=[
            TestCase(append='b = a[0]',
                     assertion=ContextAssertion(key='b', expected=0)),
            TestCase(append='b = a[10]',
                     assertion=RuntimeExceptionAssertion(
                         exception_type=StoryscriptRuntimeError))
        ]
    ),
    TestSuite(
        preparation_lines='if colour == "blue"\n'
                          '  result = "blue"\n'
                          'else if colour == "red"\n'
                          '  result = "red"\n'
                          'else if colour == "yellow"\n'
                          '  result = "yellow"\n'
                          'else if colour == "green"\n'
                          '  result = "green"\n'
                          'else\n'
                          '  result = "unknown"\n'
                          'outside_var = "executed"\n',
        cases=[
            TestCase(prepend='colour = "blue"',
                     assertion=[ContextAssertion(key='result',
                                                 expected='blue'),
                                ContextAssertion(key='outside_var',
                                                 expected='executed')]),

            TestCase(prepend='colour = "red"',
                     assertion=[ContextAssertion(key='result',
                                                 expected='red'),
                                ContextAssertion(key='outside_var',
                                                 expected='executed')]),

            TestCase(prepend='colour = "yellow"',
                     assertion=[ContextAssertion(key='result',
                                                 expected='yellow'),
                                ContextAssertion(key='outside_var',
                                                 expected='executed')]),

            TestCase(prepend='colour = "green"',
                     assertion=[ContextAssertion(key='result',
                                                 expected='green'),
                                ContextAssertion(key='outside_var',
                                                 expected='executed')]),

            TestCase(prepend='colour = "pink"',
                     assertion=[ContextAssertion(key='result',
                                                 expected='unknown'),
                                ContextAssertion(key='outside_var',
                                                 expected='executed')])
        ]
    ),
    TestSuite(
        preparation_lines='str = "hello world!"',
        cases=[
            TestCase(append='len = str length',
                     assertion=ContextAssertion(key='len', expected=12)),

            TestCase(append='r = str contains item: "hello"',
                     assertion=ContextAssertion(key='r', expected=True)),

            TestCase(append='r = str contains item: "hello1"',
                     assertion=ContextAssertion(key='r', expected=False)),

            TestCase(append='r = str contains pattern: /llo/',
                     assertion=ContextAssertion(key='r', expected=True)),

            TestCase(append='r = str contains pattern: /f/',
                     assertion=ContextAssertion(key='r', expected=False)),

            TestCase(append='parts = str split by: " "',
                     assertion=ContextAssertion(
                         key='parts', expected=['hello', 'world!'])),

            TestCase(append='a = str uppercase',
                     assertion=ContextAssertion(
                         key='a', expected='HELLO WORLD!')),

            TestCase(append='a = str lowercase',
                     assertion=ContextAssertion(
                         key='a', expected='hello world!')),

            TestCase(append='a = str capitalize',
                     assertion=ContextAssertion(
                         key='a', expected='Hello World!')),

            TestCase(append='a = str substring start: 2',
                     assertion=ContextAssertion(
                         key='a', expected='llo world!')),

            TestCase(append='a = str substring end: 5',
                     assertion=ContextAssertion(
                         key='a', expected='hello')),

            TestCase(append='a = str substring start: 6 end: 11',
                     assertion=ContextAssertion(
                         key='a', expected='world')),

            TestCase(append='a = str substring start: 6 end: -2',
                     assertion=ContextAssertion(
                         key='a', expected='worl')),

            TestCase(append='a = str substring start: 6 end: -6',
                     assertion=ContextAssertion(
                         key='a', expected='')),

            TestCase(append='a = str substring start: 20',
                     assertion=ContextAssertion(
                         key='a', expected='')),

            TestCase(append='a = str substring start: 10 end:20',
                     assertion=ContextAssertion(
                         key='a', expected='d!')),

            TestCase(append='a = str substring start: -3',
                     assertion=ContextAssertion(
                         key='a', expected='ld!')),

            TestCase(append='a = str startswith prefix: "hello"',
                     assertion=ContextAssertion(
                         key='a', expected=True)),

            TestCase(append='a = str startswith prefix: "ello"',
                     assertion=ContextAssertion(
                         key='a', expected=False)),

            TestCase(append='a = str endswith suffix: "!"',
                     assertion=ContextAssertion(
                         key='a', expected=True)),

            TestCase(append='a = str endswith suffix: "."',
                     assertion=ContextAssertion(
                         key='a', expected=False)),
        ]
    ),
    TestSuite(
        preparation_lines='str = "hello."',
        cases=[
            TestCase(append='r = str replace item: "hello" by:"foo"',
                     assertion=ContextAssertion(key='r', expected='foo.')),

            TestCase(append='r = str replace item: "l" by:"o"',
                     assertion=ContextAssertion(key='r', expected='heooo.')),

            TestCase(append='r = str replace item: "k" by:"$"',
                     assertion=ContextAssertion(key='r', expected='hello.')),

            TestCase(append='r = str replace pattern: /hello/ by:"foo"',
                     assertion=ContextAssertion(key='r', expected='foo.')),

            TestCase(append='r = str replace pattern: /l/ by:"o"',
                     assertion=ContextAssertion(key='r', expected='heooo.')),

            TestCase(append='r = str replace pattern: /k/ by:"$"',
                     assertion=ContextAssertion(key='r', expected='hello.')),
        ]
    ),
    TestSuite(
        preparation_lines='str = " text "',
        cases=[
            TestCase(append='a = str trim',
                     assertion=ContextAssertion(
                         key='a', expected='text')),
        ],
    ),
    TestSuite(
        preparation_lines='e = 10\n'
                          'o = -3',
        cases=[
            TestCase(append='a = e is_odd',
                     assertion=ContextAssertion(key='a', expected=False)),

            TestCase(append='a = o is_odd',
                     assertion=ContextAssertion(key='a', expected=True)),

            TestCase(append='a = e is_even',
                     assertion=ContextAssertion(key='a', expected=True)),

            TestCase(append='a = o is_even',
                     assertion=ContextAssertion(key='a', expected=False)),

            TestCase(append='a = o absolute',
                     assertion=[
                         ContextAssertion(key='a', expected=3),
                         ContextAssertion(key='o', expected=-3)
                     ]),

            TestCase(append='a = e increment',
                     assertion=[
                         ContextAssertion(key='a', expected=11),
                         ContextAssertion(key='e', expected=10)
                     ]),

            TestCase(append='a = e decrement',
                     assertion=[
                         ContextAssertion(key='a', expected=9),
                         ContextAssertion(key='e', expected=10)
                     ]),

            TestCase(append='e decrement',
                     assertion=ContextAssertion(key='e', expected=10)),

            TestCase(append='e increment',
                     assertion=ContextAssertion(key='e', expected=10))
        ]
    ),
    TestSuite(
        preparation_lines='m = {"a": 1, "b": 2}',
        cases=[
            TestCase(append='s = m length',
                     assertion=ContextAssertion(key='s', expected=2)),

            TestCase(append='s = m keys',
                     assertion=ContextAssertion(key='s', expected=['a', 'b'])),

            TestCase(append='s = m values',
                     assertion=ContextAssertion(key='s', expected=[1, 2])),

            TestCase(append='s = m flatten',
                     assertion=ContextAssertion(
                         key='s', expected=[['a', 1], ['b', 2]])),

            TestCase(append='s = m pop key: "a"',
                     assertion=[
                         ContextAssertion(key='s', expected=1),
                         ContextAssertion(key='m', expected={'b': 2})
                     ]),

            TestCase(append='s = m get key: "a" default: 3',
                     assertion=[
                         ContextAssertion(key='s', expected=1),
                         ContextAssertion(key='m', expected={'a': 1, 'b': 2})
                     ]),

            TestCase(append='s = m get key: "c" default: 42',
                     assertion=ContextAssertion(key='s', expected=42)),

            TestCase(append='s = m contains key: "d"',
                     assertion=ContextAssertion(key='s', expected=False)),

            TestCase(append='s = m contains key: "a"',
                     assertion=ContextAssertion(key='s', expected=True)),

            TestCase(append='s = m contains value: 3',
                     assertion=ContextAssertion(key='s', expected=False)),

            TestCase(append='s = m contains value: 1',
                     assertion=ContextAssertion(key='s', expected=True)),

            TestCase(append='key = "a"\ns = m[key]',
                     assertion=ContextAssertion(key='s', expected=1)),
        ]
    ),
    TestSuite(
        preparation_lines=r'm = "\n\t"',
        cases=[
            TestCase(append='s = m',
                     assertion=ContextAssertion(key='s', expected='\n\t')),
            TestCase(append=r's = "{m}\n"',
                     assertion=ContextAssertion(key='s', expected='\n\t\n')),
        ]
    ),
    TestSuite(
        preparation_lines='arr = [1, 2, 2, 3, 4, 4, 5, 5]',
        cases=[
            TestCase(append='actual = arr index of: 5',
                     assertion=ContextAssertion(key='actual', expected=6)),

            TestCase(append='actual = arr index of: 500',
                     assertion=ContextAssertion(key='actual', expected=-1)),

            TestCase(append='actual = arr length',
                     assertion=ContextAssertion(key='actual', expected=8)),

            TestCase(append='arr append item: 6',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 2, 2, 3, 4, 4, 5, 5, 6])),

            TestCase(append='arr prepend item: 1',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 1, 2, 2, 3, 4, 4, 5, 5])),

            TestCase(append='r = arr random',
                     assertion=IsANumberAssertion(key='r')),

            TestCase(append='arr reverse',
                     assertion=ContextAssertion(
                         key='arr', expected=[5, 5, 4, 4, 3, 2, 2, 1])),

            TestCase(append='arr sort',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 2, 2, 3, 4, 4, 5, 5])),

            TestCase(append='min = arr min',
                     assertion=ContextAssertion(key='min', expected=1)),

            TestCase(append='max = arr max',
                     assertion=ContextAssertion(key='max', expected=5)),

            TestCase(append='sum = arr sum',
                     assertion=ContextAssertion(key='sum', expected=26)),

            TestCase(append='arr unique',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 2, 3, 4, 5])),

            TestCase(append='a = arr contains item: 1',
                     assertion=ContextAssertion(key='a', expected=True)),

            TestCase(append='a = arr contains item: 11000',
                     assertion=ContextAssertion(key='a', expected=False)),

            TestCase(append='arr remove item: 3',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 2, 2, 4, 4, 5, 5])),

            TestCase(append='arr remove item: 30',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 2, 2, 3, 4, 4, 5, 5])),

            TestCase(append='arr replace item: 3 by: 42',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 2, 2, 42, 4, 4, 5, 5])),

            TestCase(append='arr.replace(item: 6 by: 42)',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 2, 2, 3, 4, 4, 5, 5])),

            TestCase(append='arr replace item: 2 by: 42',
                     assertion=ContextAssertion(
                         key='arr', expected=[1, 42, 42, 3, 4, 4, 5, 5])),
        ])
])
@mark.asyncio
async def test_mutation(suite: TestSuite, logger):
    """
    How these test suites run:
    Each test suite contains a set of Storyscript lines. Each suite
    also contains multiple cases. For each of these cases, in a test
    suite, we combine the lines of the test suite (which are required
    for preparing context, etc) with the line in this test case. This
    produces a valid tree. Then, we just run this using Lexicon (no mocking).
    After the run completes, we check what kind of assertion we have
    (each test case can have a different kind of assertion).

    This way, we can test the actual execution of the story. This works very
    well for simple stories. Don't run services or anything here. Use it
    to test mutations, variable assignments, value resolution, and so on.
    """
    await run_suite(suite, logger)


async def run_suite(suite: TestSuite, logger):
    for case in suite.cases:
        await run_test_case_in_suite(suite, case, logger)


async def run_test_case_in_suite(suite: TestSuite, case: TestCase, logger):
    File.init()
    Log.init()
    Http.init()
    Json.init()
    story_name = 'dummy_name'

    # Combine the preparation lines with those of the test case.
    all_lines = suite.preparation_lines

    if case.append is not None:
        all_lines = all_lines + '\n' + case.append

    if case.prepend is not None:
        all_lines = case.prepend + '\n' + all_lines

    story = storyscript.Api.loads(all_lines, features={'globals': True})
    errors = story.errors()
    if len(errors) > 0:
        print(f'Failed to compile the following story:'
              f'\n\n{all_lines}', file=sys.stderr)
        raise errors[0]

    app = MagicMock()

    app.stories = {
        story_name: story.result()
    }
    app.environment = {}

    context = {}

    story = Stories(app, story_name, logger)
    story.prepare(context)
    try:
        await Story.execute(logger, story)
    except StoryscriptError as e:
        try:
            assert isinstance(case.assertion, RuntimeExceptionAssertion)
            case.assertion.verify(e)
        except BaseException as e:
            print(f'Failed to assert exception for the following story:'
                  f'\n\n{all_lines}', file=sys.stderr)
            raise e
        return
    except BaseException as e:
        print(f'Failed to run the following story:'
              f'\n\n{all_lines}', file=sys.stderr)
        raise e

    if type(case.assertion) == list:
        assertions = case.assertion
    else:
        assertions = [case.assertion]

    for a in assertions:
        try:
            a.verify(context)
        except BaseException as e:
            print(f'Assertion failure ({type(a)}) for story: \n{all_lines}')
            raise e


@mark.parametrize('suite', [
    TestSuite(preparation_lines='a = [20, 12, 23]', cases=[
        TestCase(append='a[0] = 100', assertion=ContextAssertion(
            key='a', expected=[100, 12, 23]))
    ]),

    TestSuite(preparation_lines='a = [[20, 12, 23], [-1]]', cases=[
        TestCase(append='a[0][1] = 100\na[1][0] = 10',
                 assertion=ContextAssertion(
                     key='a', expected=[[20, 100, 23], [10]]))
    ])
])
@mark.asyncio
async def test_arrays(suite, logger):
    await run_suite(suite, logger)


@mark.parametrize('suite', [
    TestSuite(
        preparation_lines='a = 2 + 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=4))
        ]
    ),
    TestSuite(
        preparation_lines='a = 0 + 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=2))
        ]
    ),
    TestSuite(
        preparation_lines='a = 2 - 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=0))
        ]
    ),
    TestSuite(
        preparation_lines='a = 2 / 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=1))
        ]
    ),
    TestSuite(
        preparation_lines='a = "a" + "b"',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected='ab'))
        ]
    ),
    TestSuite(
        preparation_lines='a = "a" + "b" + ("c" + "d")',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected='abcd'))
        ]
    ),
    TestSuite(
        preparation_lines='a = 2 + 10 / 5',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=4))
        ]
    ),
    TestSuite(
        preparation_lines='a = 20 * 100',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=2000))
        ]
    ),
    TestSuite(
        preparation_lines='a = 10 % 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=0))
        ]
    ),
    TestSuite(
        preparation_lines='a = 11 % 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=1))
        ]
    ),
    TestSuite(
        preparation_lines='a = 2.5 ^ 2',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=6.25))
        ]
    ),
    TestSuite(
        preparation_lines='a = 20 / 1000',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=0.02))
        ]
    ),
    TestSuite(
        preparation_lines='a = 2 * 4 / 4',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=2))
        ]
    ),
    TestSuite(
        preparation_lines='a = 2 * 4 / (4 * 2)',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=1))
        ]
    ),
    TestSuite(
        preparation_lines='a = 2 * 4 / (4 * 2) + 1',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=2))
        ]
    ),
    TestSuite(
        preparation_lines='a = 2 * 4 / (4 * 2) + 1 * 20',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=21))
        ]
    ),
    TestSuite(
        preparation_lines='foo = 2\n'
                          'zero = 0\n'
                          'a = 2 * 4 / (4 * foo) + 1 * 20 + zero',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=21))
        ]
    ),
    TestSuite(
        preparation_lines='a = {}\n',
        cases=[
            TestCase(append='a["b"] = 1',
                     assertion=MapValueAssertion(key='a',
                                                 map_key='b',
                                                 expected=1)),
            TestCase(append='a[0] = 2',
                     assertion=MapValueAssertion(key='a',
                                                 map_key=0,
                                                 expected=2)),
            TestCase(append='a[0.5] = 3',
                     assertion=MapValueAssertion(key='a',
                                                 map_key=0.5,
                                                 expected=3)),
            TestCase(append='b = "key"\n'
                            'a[b] = 4',
                     assertion=MapValueAssertion(key='a',
                                                 map_key='key',
                                                 expected=4))
        ]
    )
])
@mark.asyncio
async def test_resolve_expressions(suite: TestSuite, logger):
    await run_suite(suite, logger)


@mark.parametrize('suite', [
    TestSuite(
        preparation_lines='a = "yoda"',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected='yoda'))
        ]
    ),
    TestSuite(
        preparation_lines='planet = "mars"',
        cases=[
            TestCase(assertion=ContextAssertion(key='planet',
                                                expected='mars')),
            TestCase(append='a = {"planet": planet, "element": "air"}',
                     assertion=ContextAssertion(key='a',
                                                expected={
                                                    'planet': 'mars',
                                                    'element': 'air'
                                                }))
        ]
    ),
    TestSuite(
        preparation_lines='planet = ["mars", "earth"]',
        cases=[
            TestCase(assertion=ListItemAssertion(key='planet',
                                                 index=0,
                                                 expected='mars')),
            TestCase(assertion=ListItemAssertion(key='planet',
                                                 index=1,
                                                 expected='earth'))
        ]
    ),
    TestSuite(
        preparation_lines='planet = {"name": "mars"}',
        cases=[
            TestCase(assertion=MapValueAssertion(key='planet',
                                                 map_key='name',
                                                 expected='mars'))
        ]
    ),
    TestSuite(
        preparation_lines='a = [0, 1, 2]',
        cases=[
            TestCase(assertion=ContextAssertion(key='a', expected=[0, 1, 2]))
        ]
    ),
    TestSuite(
        preparation_lines='a = /foo/',
        cases=[
            TestCase(assertion=ContextAssertion(key='a',
                                                expected=re.compile('foo')))
        ]
    ),
    TestSuite(
        preparation_lines='a = [true]',
        cases=[
            TestCase(assertion=ContextAssertion(key='a',
                                                expected=[True]))
        ]
    ),
    TestSuite(
        preparation_lines='i = 1\n'
                          'success = false\n'
                          'if i == 1\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 2\n'
                          'success = true\n'
                          'if i == 1\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 2\n'
                          'success = false\n'
                          'if i != 1\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 1\n'
                          'success = true\n'
                          'if i != 1\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = false\n'
                          'if i >= 1\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = false\n'
                          'if i >= 5\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = true\n'
                          'if i >= 6\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = true\n'
                          'if i > 5\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = false\n'
                          'if i > 4\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = true\n'
                          'if i < 5\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = false\n'
                          'if i < 6\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = false\n'
                          'if i <= 5\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = true\n'
                          'if i <= 4\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = 5\n'
                          'success = false\n'
                          'if i <= 6\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = false\n'
                          'success = true\n'
                          'if i\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = true\n'
                          'success = false\n'
                          'if i\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = true\n'
                          'success = true\n'
                          'if !i\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = false\n'
                          'success = false\n'
                          'if !i\n'
                          '    success = true',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=True))
        ]
    ),
    TestSuite(
        preparation_lines='i = null\n'
                          'success = true\n'
                          'if !i\n'
                          '    success = false',
        cases=[
            TestCase(assertion=ContextAssertion(key='success', expected=False))
        ]
    ),
])
@mark.asyncio
async def test_resolve_all_objects(suite: TestSuite, logger):
    await run_suite(suite, logger)


@mark.parametrize('suite', [
    TestSuite(
        cases=[
            TestCase(append='a = [0] as List[int]',
                     assertion=ContextAssertion(key='a', expected=[0])),
            TestCase(append='a = [] as List[int]',
                     assertion=ContextAssertion(key='a', expected=[])),
            TestCase(append='a = ["3", "2"] as List[int]',
                     assertion=ContextAssertion(key='a', expected=[3, 2])),
            TestCase(append='a = 2 as float',
                     assertion=ContextAssertion(key='a', expected=2.)),
            TestCase(append='a = 2.5 as int',
                     assertion=ContextAssertion(key='a', expected=2)),
            TestCase(append='a = 2 as boolean',
                     assertion=ContextAssertion(key='a', expected=True)),
            TestCase(append='a = 2 as string',
                     assertion=ContextAssertion(key='a', expected='2')),
            TestCase(append='a = [1, 2] as boolean',
                     assertion=ContextAssertion(key='a', expected=True)),
            TestCase(append='a = [] as boolean',
                     assertion=ContextAssertion(key='a', expected=False)),
            TestCase(append='a = 2 as boolean',
                     assertion=ContextAssertion(key='a', expected=True)),
            TestCase(append='a = {2: "42"} as Map[float,float]',
                     assertion=ContextAssertion(key='a', expected={2.: 42.})),
            TestCase(append='a = "foo" as regex',
                     assertion=ContextAssertion(key='a',
                                                expected=re.compile('foo'))),
            TestCase(append='a = /foo/ as regex',
                     assertion=ContextAssertion(key='a',
                                                expected=re.compile('foo'))),
            TestCase(append='a = 2 as any',
                     assertion=ContextAssertion(key='a', expected=2)),
            TestCase(append='a = true as int',
                     assertion=ContextAssertion(key='a', expected=1)),
            TestCase(append='a = false as int',
                     assertion=ContextAssertion(key='a', expected=0)),
            TestCase(append='a = {"foo": 42} as Map[string,int]',
                     assertion=ContextAssertion(key='a',
                                                expected={'foo': 42})),
            TestCase(append='a = {} as Map[int,boolean]',
                     assertion=ContextAssertion(key='a',
                                                expected={}))
        ]
    ),
    TestSuite(
        preparation_lines='arr = []\narr append item: 42\nb = arr[0]',
        cases=[
            TestCase(append='c = b as List[int]',
                     assertion=RuntimeExceptionAssertion(
                         TypeAssertionRuntimeError,
                         message='Incompatible type assertion: Received 42 '
                                 '(int), but expected List[int]')),
            TestCase(append='c = b as Map[int,string]',
                     assertion=RuntimeExceptionAssertion(
                         TypeAssertionRuntimeError,
                         message='Incompatible type assertion: Received 42 '
                                 '(int), but expected Map[int,string]')),
            TestCase(append='b=/foo/\nc = b as List[int]',
                     assertion=RuntimeExceptionAssertion(
                         TypeAssertionRuntimeError,
                         message='Incompatible type assertion: Received /foo/ '
                                 '(regexp), but expected List[int]')),
        ]
    ),
    TestSuite(
        preparation_lines='a = {}',
        cases=[
            TestCase(append='c = "foo" as float',
                     assertion=RuntimeExceptionAssertion(
                         TypeValueRuntimeError,
                         message='Type conversion failed from str to float '
                                 'with `foo`')),
            TestCase(append='c = "foo" as int',
                     assertion=RuntimeExceptionAssertion(
                         TypeValueRuntimeError,
                         message='Type conversion failed from str to int '
                                 'with `foo`')),
            TestCase(append='c = a[0]["id"] as string',
                     assertion=ContextAssertion(key='c', expected=None)),
        ]
    )
])
@mark.asyncio
async def test_type_casts(suite: TestSuite, logger):
    await run_suite(suite, logger)


@mark.parametrize('suite', [
    TestSuite(
        preparation_lines='a = [1, 2, 3, 4, 5]',
        cases=[
            TestCase(append='c=a[0]\nb = a[:2]',
                     assertion=ContextAssertion(key='b', expected=[1, 2])),
            TestCase(append='b = a[1:2]',
                     assertion=ContextAssertion(key='b', expected=[2])),
            TestCase(append='b = a[3:]',
                     assertion=ContextAssertion(key='b', expected=[4, 5])),
            TestCase(append='b = a[10:]',
                     assertion=ContextAssertion(key='b', expected=[])),
            TestCase(append='b = a[10:20]',
                     assertion=ContextAssertion(key='b', expected=[])),
            TestCase(append='b = a[:-2]',
                     assertion=ContextAssertion(key='b', expected=[1, 2, 3])),
            TestCase(append='b = a[-2:5]',
                     assertion=ContextAssertion(key='b', expected=[4, 5])),
            TestCase(append='c=1\nd=3\nb = a[c:d]',
                     assertion=ContextAssertion(key='b', expected=[2, 3])),
        ]
    ),
    TestSuite(
        preparation_lines='a = "abcde"',
        cases=[
            TestCase(append='b = a[:2]',
                     assertion=ContextAssertion(key='b', expected='ab')),
            TestCase(append='b = a[1:2]',
                     assertion=ContextAssertion(key='b', expected='b')),
            TestCase(append='b = a[3:]',
                     assertion=ContextAssertion(key='b', expected='de')),
            TestCase(append='b = a[10:]',
                     assertion=ContextAssertion(key='b', expected='')),
            TestCase(append='b = a[10:20]',
                     assertion=ContextAssertion(key='b', expected='')),
            TestCase(append='b = a[:-2]',
                     assertion=ContextAssertion(key='b', expected='abc')),
            TestCase(append='b = a[-2:5]',
                     assertion=ContextAssertion(key='b', expected='de')),
            TestCase(append='c=1\nd=3\nb = a[c:d]',
                     assertion=ContextAssertion(key='b', expected='bc')),
        ]
    )
])
@mark.asyncio
async def test_range_mutations(suite: TestSuite, logger):
    await run_suite(suite, logger)


@mark.parametrize('suite', [
    TestSuite(
        cases=[
            TestCase(
                append='a = 1.23\nb = a.round()',
                assertion=ContextAssertion(key='b', expected=1)
            ),
            TestCase(
                append='a = 1.56\nb = a.round()',
                assertion=ContextAssertion(key='b', expected=2)
            ),
            TestCase(
                append='a = 2.22\nb = a.ceil()',
                assertion=ContextAssertion(key='b', expected=3)
            ),
            TestCase(
                append='a = 4.00\nb = a.ceil()',
                assertion=ContextAssertion(key='b', expected=4)
            ),
            TestCase(
                append='a = 5.01\nb = a.floor()',
                assertion=ContextAssertion(key='b', expected=5)
            ),
            TestCase(
                append=f'a = ({math.pi}/2)\nb = a.sin()',
                assertion=ContextAssertion(key='b', expected=1)
            ),
            TestCase(
                append=f'a = {math.pi}\nb = a.cos()',
                assertion=ContextAssertion(key='b', expected=-1)
            ),
            TestCase(
                append='a = 0.0\nb = a.tan()',
                assertion=ContextAssertion(key='b', expected=0)
            ),
            TestCase(
                append='a = 1.0\nb = a.asin()',
                assertion=ContextAssertion(key='b', expected=math.pi / 2)
            ),
            TestCase(
                append='a = 0.0\nb = a.acos()',
                assertion=ContextAssertion(key='b', expected=math.pi / 2)
            ),
            TestCase(
                append='a = 0.0\nb = a.atan()',
                assertion=ContextAssertion(key='b', expected=0)
            ),
            TestCase(
                append=f'a = {math.e}\nb = a.log()',
                assertion=ContextAssertion(key='b', expected=1)
            ),
            TestCase(
                append=f'a = 4.0\nb = a.log2()',
                assertion=ContextAssertion(key='b', expected=2)
            ),
            TestCase(
                append=f'a = 1000.0\nb = a.log10()',
                assertion=ContextAssertion(key='b', expected=3)
            ),
            TestCase(
                append=f'a = 1.0\nb = a.exp()',
                assertion=ContextAssertion(key='b', expected=math.e)
            ),
            TestCase(
                append=f'a = -1.0\nb = a.abs()',
                assertion=ContextAssertion(key='b', expected=1)
            ),
            TestCase(
                append=f'a = "nan" as float\nb = a.is_nan()',
                assertion=ContextAssertion(key='b', expected=True)
            ),
            TestCase(
                append=f'a = "inf" as float\nb = a.is_infinity()',
                assertion=ContextAssertion(key='b', expected=True)
            ),
            TestCase(
                append=f'a = 4.0\nb = a.sqrt()',
                assertion=ContextAssertion(key='b', expected=2)
            ),
        ]
    )
])
@mark.asyncio
async def test_float_mutations(suite: TestSuite, logger):
    await run_suite(suite, logger)
