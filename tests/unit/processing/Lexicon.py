# -*- coding: utf-8 -*-
from unittest.mock import MagicMock, Mock


from asyncy import Exceptions
from asyncy.Containers import Containers
from asyncy.constants.ContextConstants import ContextConstants
from asyncy.processing import Lexicon
from asyncy.processing.internal.HttpEndpoint import HttpEndpoint

import pytest
from pytest import fixture, mark


@fixture
def line():
    return {'enter': '2', 'exit': '25', 'ln': '1', 'container': 'alpine',
            'args': ['args'], 'next': '26'}


@fixture
def story(patch, story):
    patch.many(story, ['end_line', 'resolve', 'resolve_command',
                       'context', 'next_block', 'line'])
    return story


@mark.asyncio
async def test_lexicon_run(patch, logger, story, line, async_mock):
    output = MagicMock()
    patch.object(Containers, 'exec', new=async_mock(return_value=output))
    patch.object(Lexicon, 'next_line_or_none')
    result = await Lexicon.run(logger, story, line)
    story.resolve_command.assert_called_with(line)
    c = line['container']
    Containers.exec.mock.assert_called_with(logger, story, line, c,
                                            story.resolve_command())
    story.end_line.assert_called_with(line['ln'],
                                      output=output,
                                      assign=None)
    story.line.assert_called_with(line['next'])
    assert result == Lexicon.next_line_or_none()


@mark.asyncio
async def test_lexicon_run_none(patch, logger, story, line, async_mock):
    story.line.return_value = None
    patch.object(Containers, 'exec', new=async_mock())
    result = await Lexicon.run(logger, story, line)
    assert result is None


@mark.asyncio
async def test_lexicon_run_log(patch, logger, story, line):
    patch.object(Lexicon, 'next_line_or_none')
    story.resolve_command.return_value = 'log'
    result = await Lexicon.run(logger, story, line)
    story.resolve_command.assert_called_with(line)
    story.end_line.assert_called_with(line['ln'])
    story.line.assert_called_with(line['next'])
    assert result == Lexicon.next_line_or_none()


@mark.asyncio
async def test_lexicon_run_log_none(patch, logger, story, line):
    story.resolve_command.return_value = 'log'
    patch.object(Lexicon, 'next_line_or_none', return_value=None)
    result = await Lexicon.run(logger, story, line)
    assert result is None


def test_lexicon_set(patch, logger, story):
    story.context = {}
    patch.object(Lexicon, 'next_line_or_none')
    line = {'ln': '1', 'args': [{'paths': ['name']}, 'values'], 'next': '2'}
    story.resolve.return_value = 'resolved'
    result = Lexicon.set(logger, story, line)
    story.resolve.assert_called_with(line['args'][1])
    story.end_line.assert_called_with(line['ln'],
                                      assign={'paths': ['name']},
                                      output='resolved')
    story.line.assert_called_with(line['next'])
    assert result == Lexicon.next_line_or_none()


def test_lexicon_function(patch, logger, story, line):
    patch.object(story, 'next_block')
    patch.object(Lexicon, 'next_line_or_none', return_value='1')
    assert Lexicon.function(logger, story, line) == '1'
    story.next_block.assert_called_with(line)


def test_lexicon_if(logger, story, line):
    story.context = {}
    result = Lexicon.if_condition(logger, story, line)
    logger.log.assert_called_with('lexicon-if', line, story.context)
    story.resolve.assert_called_with(line['args'][0], encode=False)
    assert result == line['enter']


def test_lexicon_if_false(logger, story, line):
    story.context = {}
    story.resolve.return_value = False
    assert Lexicon.if_condition(logger, story, line) == line['exit']


def test_lexicon_unless(logger, story, line):
    story.context = {}
    result = Lexicon.unless_condition(logger, story, line)
    logger.log.assert_called_with('lexicon-unless', line, story.context)
    story.resolve.assert_called_with(line['args'][0], encode=False)
    assert result == line['exit']


def test_lexicon_unless_false(logger, story, line):
    story.context = {}
    story.resolve.return_value = False
    assert Lexicon.unless_condition(logger, story, line) == line['enter']


@mark.asyncio
async def test_lexicon_for_loop(patch, logger, story, line, async_mock):
    patch.object(Lexicon, 'run', new=async_mock())
    line['args'] = [
        'element',
        {'$OBJECT': 'path', 'paths': ['elements']}
    ]
    story.context = {'elements': ['one']}
    story.resolve.return_value = ['one']
    story.environment = {}
    result = await Lexicon.for_loop(logger, story, line)
    Lexicon.run.mock.assert_called_with(logger, story, line['ln'])
    assert result == line['exit']


@mark.asyncio
async def test_lexicon_run_http_endpoint(patch, logger, story,
                                         http_line, async_mock):
    return_values = Mock()
    return_values.side_effect = ['get', '/']
    patch.object(HttpEndpoint, 'register_http_endpoint', new=async_mock())
    story.resolve.side_effect = return_values

    await Lexicon.run(logger, story, http_line)

    HttpEndpoint.register_http_endpoint.mock.assert_called_with(
        block=http_line['ln'], line=http_line, method='get', path='/',
        story=story)


@mark.parametrize('args', [[None, '/'], ['get', None]])
@mark.asyncio
async def test_lexicon_run_http_endpoint_no_method(patch, logger, story,
                                                   http_line, args):
    with pytest.raises(Exceptions.ArgumentNotFoundError):
        return_values = Mock()
        return_values.side_effect = args
        story.resolve.side_effect = return_values

        await Lexicon.run(logger, story, http_line)


@mark.parametrize('http_object', ['request', 'response'])
@mark.asyncio
async def test_lexicon_run_http_functions(patch, logger, story, http_object):
    http_object_line = {
        'ln': '1',
        'container': http_object
    }

    story.context.return_value = {
        ContextConstants.server_request: 'foo'
    }

    story.container = http_object
    patch.object(HttpEndpoint, 'run')

    await Lexicon.run(logger, story, http_object_line)

    HttpEndpoint.run.assert_called_with(story, http_object_line)
    story.end_line.assert_called()
