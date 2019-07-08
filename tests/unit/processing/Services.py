# -*- coding: utf-8 -*-
import json
import uuid
from collections import deque
from io import StringIO
from unittest.mock import MagicMock, Mock

from asyncy.Containers import Containers
from asyncy.Exceptions import ArgumentTypeMismatchError, StoryscriptError
from asyncy.Types import StreamingService
from asyncy.constants import ContextConstants
from asyncy.constants.LineConstants import LineConstants as Line, LineConstants
from asyncy.constants.ServiceConstants import ServiceConstants
from asyncy.entities.Multipart import FileFormField, FormField
from asyncy.omg.ServiceOutputValidator import ServiceOutputValidator
from asyncy.processing.Services import Command, Event, \
    Service, Services
from asyncy.utils.HttpUtils import HttpUtils

import pytest
from pytest import mark

from tornado.gen import coroutine
from tornado.httpclient import AsyncHTTPClient, HTTPRequest, HTTPResponse

import ujson


@mark.asyncio
async def test_services_execute_execute_internal(story, async_mock):
    handler = async_mock(return_value='output')

    Services.register_internal('my_service', 'my_command', {}, 'any', handler)

    assert Services.is_internal('my_service', 'my_command') is True
    line = {
        Line.service: 'my_service',
        Line.command: 'my_command',
        Line.method: 'execute'
    }

    assert await Services.execute(story, line) == 'output'


@mark.asyncio
async def test_services_execute_execute_external(patch, story, async_mock):
    patch.object(Services, 'execute_external', new=async_mock())
    assert Services.is_internal('foo_service', 'blah') is False
    line = {
        Line.service: 'foo_service',
        Line.command: 'foo_command',
        Line.method: 'execute'
    }

    assert await Services.execute(story, line) \
        == await Services.execute_external()


@mark.asyncio
async def test_services_execute_execute_external_inline(patch, story,
                                                        async_mock):
    patch.object(Services, 'execute_inline', new=async_mock())
    patch.object(Services, 'start_container', new=async_mock())
    patch.object(Services, 'get_command_conf',
                 return_value={'http': {'use_event_conn': True}})
    line = {
        Line.service: 'foo_service',
        Line.command: 'foo_command',
        Line.method: 'execute'
    }

    assert await Services.execute(story, line) \
        == await Services.execute_inline()


@mark.asyncio
async def test_services_execute_args(story, async_mock):
    handler = async_mock(return_value='output')

    Services.register_internal('my_service', 'my_command',
                               {'arg1': {'type': 'string'}},
                               'any', handler)

    assert Services.is_internal('my_service', 'my_command') is True
    line = {
        Line.service: 'my_service',
        Line.command: 'my_command',
        Line.method: 'execute',
        'args': [
            {
                '$OBJECT': 'argument',
                'name': 'arg1',
                'argument': {
                    '$OBJECT': 'string',
                    'string': 'Hello world!'
                }
            }
        ]
    }

    assert await Services.execute(story, line) == 'output'
    handler.mock.assert_called_with(story=story, line=line,
                                    resolved_args={'arg1': 'Hello world!'})


def test_services_log_registry(logger):
    Services.init(logger)
    Services.register_internal('my_service', 'my_command', {}, 'any', None)
    Services.log_internal()
    logger.info.assert_called_with(
        'Discovered internal service my_service - [\'my_command\']')


def test_resolve_chain(story):
    """
    The story tested here is:
    alpine echo as client
        when client foo as echo_helper
            alpine echo
                echo_helper sonar  # This isn't possible, but OK.
            echo_helper sonar

    """
    story.app.services = {
        'alpine': {}
    }

    story.tree = {
        '1': {
            Line.method: 'execute',
            Line.service: 'alpine',
            Line.command: 'echo',
            Line.enter: '2',
            Line.output: ['client']
        },
        '2': {
            Line.method: 'when',
            Line.service: 'client',
            Line.command: 'foo',
            Line.parent: '1',
            Line.output: ['echo_helper']
        },
        '3': {
            Line.method: 'execute',
            Line.service: 'alpine',
            Line.command: 'echo',
            Line.parent: '2',
            Line.enter: '4'
        },
        '4': {
            Line.method: 'execute',
            Line.service: 'echo_helper',
            Line.command: 'sonar',
            Line.parent: '3'
        },
        '5': {
            Line.method: 'execute',
            Line.service: 'echo_helper',
            Line.command: 'sonar',
            Line.parent: '2'
        }
    }

    assert Services.resolve_chain(story, story.tree['1']) \
        == deque([Service(name='alpine'), Command(name='echo')])

    assert Services.resolve_chain(story, story.tree['2']) \
        == deque([Service(name='alpine'),
                  Command(name='echo'), Event(name='foo')])

    assert Services.resolve_chain(story, story.tree['3']) \
        == deque([Service(name='alpine'), Command(name='echo')])

    assert Services.resolve_chain(story, story.tree['4']) \
        == deque([Service(name='alpine'), Command(name='echo'),
                  Event(name='foo'), Command(name='sonar')])

    assert Services.resolve_chain(story, story.tree['5']) \
        == deque([Service(name='alpine'), Command(name='echo'),
                  Event(name='foo'), Command(name='sonar')])


@mark.parametrize('value', [{'a': 'b'}, [0, 2, 'hello'], 'a'])
def test_smart_insert(patch, story, value):
    patch.object(Services, 'raise_for_type_mismatch')

    command_conf = {
        'type': 'string'
    }

    m = {}

    key = 'my_key'

    if isinstance(value, dict) or isinstance(value, list):
        expected = json.dumps(value)
    else:
        expected = value

    Services.smart_insert(story, {}, command_conf, key, value, m)
    Services.raise_for_type_mismatch.assert_called_with(
        story, {}, key, expected, command_conf)

    assert m[key] == expected


@mark.parametrize('val', ['a', 'b', 'c', 'd'])
def test_raise_for_type_mismatch_enum(story, val):
    command_conf = {
        'type': 'enum',
        'enum': ['a', 'b', 'c']
    }

    if val in command_conf['enum']:
        Services.raise_for_type_mismatch(story, {}, 'arg_name',
                                         val, command_conf)
    else:
        with pytest.raises(ArgumentTypeMismatchError):
            Services.raise_for_type_mismatch(story, {}, 'arg_name',
                                             val, command_conf)


@mark.parametrize('typ', ['int', 'float', 'string', 'list', 'map',
                          'boolean', 'any'])
@mark.parametrize('val', [1, 0.9, 'hello', [0, 1], {'a': 'b'}, True, False])
def test_raise_for_type_mismatch(story, typ, val):
    command_conf = {
        'type': typ
    }

    line = {'ln': '10'}

    valid = False
    if typ == 'string' and isinstance(val, str):
        valid = True
    elif typ == 'int' and isinstance(val, int):
        valid = True
    elif typ == 'float' and isinstance(val, float):
        valid = True
    elif typ == 'list' and isinstance(val, list):
        valid = True
    elif typ == 'map' and isinstance(val, dict):
        valid = True
    elif typ == 'boolean' and isinstance(val, bool):
        valid = True
    elif typ == 'any':
        valid = True

    if valid:
        Services.raise_for_type_mismatch(story, line, 'arg_name',
                                         val, command_conf)
    else:
        with pytest.raises(ArgumentTypeMismatchError):
            Services.raise_for_type_mismatch(story, line, 'arg_name',
                                             val, command_conf)


@mark.parametrize('location', ['requestBody', 'query', 'path',
                               'invalid_loc', 'formBody', None])
@mark.parametrize('method', ['POST', 'GET'])
@mark.parametrize('service_output', [{
    'properties': {
        'foo': {
            'type': 'string'
        }
    }
}, None])
@mark.asyncio
async def test_services_execute_http(patch, story, async_mock,
                                     location, method, service_output):
    if location == 'formBody' and method == 'GET':
        return  # Invalid case.

    chain = deque([Service(name='service'), Command(name='cmd')])
    patch.object(Containers, 'get_hostname',
                 new=async_mock(return_value='container_host'))

    patch.object(uuid, 'uuid4')

    patch.object(ServiceOutputValidator, 'raise_if_invalid')

    command_conf = {
        'http': {
            'method': method.lower(),
            'port': 2771,
            'path': '/invoke'
        },
        'arguments': {
            'foo': {
                'in': location
            }
        }
    }

    if service_output is not None:
        command_conf['output'] = service_output

    if location is None:
        del command_conf['arguments']

    if location == 'formBody':
        command_conf['http']['contentType'] = 'multipart/form-data'

    patch.object(story, 'argument_by_name', return_value='bar')

    if location == 'path':
        command_conf['http']['path'] = '/invoke/{foo}'
        expected_url = 'http://container_host:2771/invoke/bar'
    elif location == 'query':
        expected_url = 'http://container_host:2771/invoke?foo=bar'
    else:  # requestBody
        expected_url = 'http://container_host:2771/invoke'

    expected_kwargs = {
        'method': method
    }

    if method == 'POST':
        expected_kwargs['headers'] = {
            'Content-Type': 'application/json; charset=utf-8'
        }

        if location == 'requestBody':
            expected_kwargs['body'] = '{"foo": "bar"}'
        elif location == 'formBody':
            expected_kwargs['headers']['Content-Type'] = \
                f'multipart/form-data; boundary={uuid.uuid4().hex}'
        else:
            expected_kwargs['body'] = '{}'

    line = {
        'ln': '1'
    }

    patch.init(AsyncHTTPClient)
    client = AsyncHTTPClient()
    response = HTTPResponse(HTTPRequest(url=expected_url), 200,
                            buffer=StringIO('{"foo": "\U0001f44d"}'),
                            headers={'Content-Type': 'application/json'})

    patch.object(HttpUtils, 'fetch_with_retry',
                 new=async_mock(return_value=response))

    if location == 'invalid_loc' or \
            (location == 'requestBody' and method == 'GET'):
        with pytest.raises(StoryscriptError):
            await Services.execute_http(story, line, chain, command_conf)
        return
    else:
        ret = await Services.execute_http(story, line, chain, command_conf)

    assert ret == {'foo': '\U0001f44d'}

    if location == 'formBody':
        call = HttpUtils.fetch_with_retry.mock.mock_calls[0][1]
        assert call[0] == 3
        assert call[1] == story.logger
        assert call[2] == expected_url
        assert call[3] == client

        # Since we can't mock the partial, we must inspect it.
        actual_body_producer = call[4].pop('body_producer')
        assert call[4] == expected_kwargs

        assert actual_body_producer.func == Services._multipart_producer
    else:
        HttpUtils.fetch_with_retry.mock.assert_called_with(
            3, story.logger, expected_url, client, expected_kwargs)

    if service_output is not None:
        ServiceOutputValidator.raise_if_invalid.assert_called_with(
            command_conf['output'], ret, chain)
    else:
        ServiceOutputValidator.raise_if_invalid.assert_not_called()

    # Additionally, test for other scenarios.
    response = HTTPResponse(HTTPRequest(url=expected_url), 200,
                            buffer=StringIO('foo'), headers={})

    patch.object(HttpUtils, 'fetch_with_retry',
                 new=async_mock(return_value=response))

    ret = await Services.execute_http(story, line, chain, command_conf)
    assert ret == 'foo'

    response = HTTPResponse(HTTPRequest(url=expected_url), 500)

    patch.object(HttpUtils, 'fetch_with_retry',
                 new=async_mock(return_value=response))

    with pytest.raises(StoryscriptError):
        await Services.execute_http(story, line, chain, command_conf)


@mark.parametrize('output_type',
                  ['string', 'any', 'int', 'float', 'boolean', None])
def test_parse_output(output_type, story):
    line = {}
    command_conf = {
        'output': {
            'type': output_type
        }
    }

    expected_output = None
    actual_input = None

    if output_type == 'string':
        actual_input = 'hello'
        expected_output = 'hello'
    elif output_type == 'int':
        actual_input = b'10'
        expected_output = 10
    elif output_type == 'float':
        actual_input = b'7.0'
        expected_output = 7.0
    elif output_type == 'boolean':
        actual_input = f'true'
        expected_output = True
    elif output_type is None:
        actual_input = None
        expected_output = None
    elif output_type == 'any':
        actual_input = b'empty'
        expected_output = b'empty'

    assert Services.parse_output(
        command_conf, actual_input, story, line, '') == expected_output


def test_parse_output_invalid_cast(story):
    command_conf = {
        'output': {
            'type': 'int'
        }
    }

    with pytest.raises(StoryscriptError):
        Services.parse_output(command_conf, 'not_an_int', story, {}, '')


def test_parse_output_invalid_type(story):
    command_conf = {
        'output': {
            'type': 'foo'
        }
    }

    with pytest.raises(StoryscriptError):
        Services.parse_output(command_conf, 'blah', story, {}, '')


def test_convert_bytes_to_string():
    assert Services._convert_bytes_to_string(b'hello') == 'hello'
    assert Services._convert_bytes_to_string('hello') == 'hello'


@mark.asyncio
async def test_services_start_container(patch, story, async_mock):
    line = {
        'ln': '10',
        Line.service: 'alpine',
        Line.method: 'execute',
        Line.command: 'echo'
    }
    patch.object(Containers, 'start', new=async_mock())
    ret = await Services.start_container(story, line)
    Containers.start.mock.assert_called_with(story, line)
    assert ret == Containers.start.mock.return_value


@mark.asyncio
async def test_services_execute_external_format(patch, story, async_mock):
    line = {
        Line.service: 'cups',
        Line.command: 'print',
        Line.method: 'execute'
    }

    story.app.services = {
        'cups': {
            ServiceConstants.config: {
                'actions': {
                    'print': {
                        'format': {}
                    }
                }
            }
        }
    }

    patch.object(Containers, 'exec', new=async_mock())
    patch.object(Services, 'start_container', new=async_mock())

    ret = await Services.execute_external(story, line)
    Containers.exec.mock.assert_called_with(
        story.logger, story, line, 'cups', 'print')
    assert ret == await Containers.exec()
    Services.start_container.mock.assert_called()


@mark.asyncio
async def test_services_execute_external_http(patch, story, async_mock):
    line = {
        Line.service: 'cups',
        Line.command: 'print',
        Line.method: 'execute'
    }

    story.app.services = {
        'cups': {
            ServiceConstants.config: {
                'actions': {
                    'print': {
                        'http': {}
                    }
                }
            }
        }
    }

    patch.object(Services, 'execute_http', new=async_mock())
    patch.object(Services, 'start_container', new=async_mock())

    ret = await Services.execute_external(story, line)
    Services.execute_http.mock.assert_called_with(
        story, line,
        deque([Service(name='cups'), Command(name='print')]),
        {'http': {}})
    assert ret == await Services.execute_http()
    Services.start_container.mock.assert_called()


class Writer:
    out = ''

    @coroutine
    def write(self, content_bytes):
        assert isinstance(content_bytes, bytes)
        self.out += content_bytes.decode()
        return len(content_bytes)


def test_multipart_producer():
    w = Writer()
    boundary = str(uuid.uuid4())
    body = {
        'simple_arg': FormField('simple_arg', 10),
        'simple_arg2': FormField('simple_arg2', 'hello'),
        'hello_file': FileFormField('f1', 'hello world'.encode(),
                                    'hello.txt', 'text/plain')
    }
    list(Services._multipart_producer(body, boundary, w.write))
    expected = (
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="simple_arg"\r\n'
        '\r\n'
        '10'
        '\r\n'
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="simple_arg2"\r\n'
        '\r\n'
        'hello'
        '\r\n'
        f'--{boundary}\r\n'
        'Content-Disposition: form-data; name="f1"; filename="hello.txt"\r\n'
        'Content-Type: text/plain\r\n'
        '\r\n'
        'hello world'
        '\r\n'
        f'--{boundary}--\r\n'
    )

    assert w.out == expected


@mark.asyncio
async def test_services_execute_external_unknown(patch, story, async_mock):
    line = {
        Line.service: 'cups',
        Line.command: 'print',
        Line.method: 'execute'
    }

    story.app.services = {
        'cups': {
            ServiceConstants.config: {
                'actions': {
                    'print': {
                        'unix': {}
                    }
                }
            }
        }
    }

    patch.object(Services, 'start_container', new=async_mock())

    with pytest.raises(StoryscriptError):
        await Services.execute_external(story, line)


def test_service_get_command_conf_simple(story):
    chain = deque([Service('service'), Command('cmd')])
    story.app.services = {
        'service': {
            'configuration': {
                'actions': {
                    'cmd': {'x': 'y'}
                }
            }
        }
    }
    assert Services.get_command_conf(story, chain) == {'x': 'y'}


@mark.asyncio
async def test_start_container_http(story):
    line = {
        Line.command: 'server',
        Line.service: 'http',
        Line.method: 'execute'
    }
    ret = await Services.start_container(story, line)
    assert ret.name == 'http'
    assert ret.command == 'server'
    assert ret.container_name == 'gateway'
    assert ret.hostname == story.app.config.ASYNCY_HTTP_GW_HOST


@mark.parametrize('command', ['write', 'finish'])
@mark.parametrize('simulate_finished', [True, False])
@mark.parametrize('bin_content', [True, False])
@mark.asyncio
async def test_execute_inline(patch, story, command, simulate_finished,
                              bin_content):
    # Not a valid combination.
    if bin_content and command != 'write':
        return

    chain = deque([Service('http'), Event('server'), Command(command)])
    req = MagicMock()
    req._finished = simulate_finished

    def is_finished():
        return req._finished

    req.is_finished = is_finished
    io_loop = MagicMock()
    story.context = {
        ContextConstants.server_request: req,
        ContextConstants.server_io_loop: io_loop
    }

    command_conf = {
        'arguments': {
            'content': {
                'type': 'string',
                'in': 'responseBody',
                'required': True
            }
        }
    }

    if bin_content:
        patch.object(story, 'argument_by_name', return_value=b'bin world!')
    else:
        patch.object(story, 'argument_by_name', return_value='hello world!')

    expected_body = {
        'command': command,
        'data': {
            'content': 'hello world!'
        }
    }

    line = {}

    if simulate_finished:
        with pytest.raises(StoryscriptError):
            await Services.execute_inline(story, line, chain, command_conf)
        return
    else:
        await Services.execute_inline(story, line, chain, command_conf)

    if bin_content:
        req.write.assert_called_with(b'bin world!')
    else:
        req.write.assert_called_with(ujson.dumps(expected_body) + '\n')

    if command == 'finish' or bin_content:
        io_loop.add_callback.assert_called_with(req.finish)
    else:
        io_loop.add_callback.assert_not_called()


def test_set_logger(logger):
    Services.set_logger(logger)
    assert Services.logger == logger


@mark.parametrize('service_name', ['http', 'time-client'])
@mark.asyncio
async def test_when(patch, story, async_mock, service_name):
    line = {
        'ln': '10',
        LineConstants.service: service_name,
        LineConstants.command: 'updates',
        'args': [
            {
                '$OBJECT': 'argument',
                'name': 'foo',
                'argument': {
                    '$OBJECT': 'string',
                    'string': 'bar'
                }
            }
        ]
    }

    story.app.services = {
        service_name: {
            ServiceConstants.config: {
                'actions': {
                    'time-server': {
                        'events': {
                            'updates': {
                                'http': {
                                    'port': 2000,
                                    'subscribe': {
                                        'method': 'post',
                                        'path': '/sub'
                                    }
                                },
                                'arguments': {
                                    'foo': {'required': True}
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    story.name = 'my_event_driven_story.story'
    story.app.config.ENGINE_HOST = 'localhost'
    story.app.config.ENGINE_PORT = 8000
    story.app.config.ASYNCY_SYNAPSE_HOST = 'localhost'
    story.app.config.ASYNCY_SYNAPSE_PORT = 9000
    story.app.app_id = 'my_fav_app'
    story.app.app_dns = 'my_apps_hostname'

    streaming_service = StreamingService(service_name, 'time-server',
                                         'asyncy--foo-1', 'foo.com')
    story.context = {
        service_name: streaming_service
    }

    expected_sub_url = 'http://foo.com:2000/sub'
    expected_url = f'http://{story.app.config.ASYNCY_SYNAPSE_HOST}:' \
                   f'{story.app.config.ASYNCY_SYNAPSE_PORT}' \
                   f'/subscribe'

    expected_body = {
        'sub_id': 'my_guid_here',
        'sub_url': expected_sub_url,
        'sub_method': 'POST',
        'sub_body': {
            'endpoint': f'http://localhost:8000/story/event?'
                        f'story={story.name}&block={line["ln"]}'
                        f'&app=my_fav_app',
            'data': {
                'foo': 'bar'
            },
            'event': 'updates',
            'id': 'my_guid_here'
        },
        'pod_name': streaming_service.container_name,
        'app_id': story.app.app_id
    }

    if service_name == 'http':
        expected_body['sub_body']['data']['host'] = story.app.app_dns

    patch.object(uuid, 'uuid4', return_value='my_guid_here')

    expected_kwargs = {
        'method': 'POST',
        'body': json.dumps(expected_body),
        'headers': {'Content-Type': 'application/json; charset=utf-8'},
        'request_timeout': 120
    }

    patch.init(AsyncHTTPClient)
    patch.object(story, 'next_block')
    patch.object(story.app, 'add_subscription')
    patch.object(story, 'argument_by_name', return_value='bar')
    http_res = Mock()
    http_res.code = 204
    patch.object(HttpUtils, 'fetch_with_retry',
                 new=async_mock(return_value=http_res))
    ret = await Services.when(streaming_service, story, line)

    client = AsyncHTTPClient()

    HttpUtils.fetch_with_retry.mock.assert_called_with(
        100, story.logger, expected_url, client, expected_kwargs)

    story.app.add_subscription.assert_called_with(
        'my_guid_here', story.context[service_name],
        'updates', expected_body)

    assert ret is None

    http_res.code = 400
    with pytest.raises(StoryscriptError):
        await Services.when(streaming_service, story, line)


def test_service_get_command_conf_events(story):
    chain = deque(
        [Service('service'), Command('cmd'), Event('foo'), Command('bar')])
    story.app.services = {
        'service': {
            'configuration': {
                'actions': {
                    'cmd': {
                        'events': {
                            'foo': {
                                'output': {
                                    'actions': {
                                        'bar': {'a': 'b'}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    assert Services.get_command_conf(story, chain) == {'a': 'b'}
