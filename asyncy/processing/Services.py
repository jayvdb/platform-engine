# -*- coding: utf-8 -*-
import json
import urllib
import uuid
from collections import deque, namedtuple
from functools import partial
from urllib import parse

from tornado.gen import coroutine
from tornado.httpclient import AsyncHTTPClient

import ujson

from ..Containers import Containers
from ..Exceptions import ArgumentTypeMismatchError, StoryscriptError
from ..Logger import Logger
from ..Types import Command, Event, InternalCommand, \
    InternalService, Service, StreamingService
from ..constants.ContextConstants import ContextConstants
from ..constants.LineConstants import LineConstants
from ..constants.ServiceConstants import ServiceConstants
from ..entities.Multipart import FileFormField, FormField
from ..omg.ServiceOutputValidator import ServiceOutputValidator
from ..utils import Dict
from ..utils.HttpUtils import HttpUtils
from ..utils.StringUtils import StringUtils


class Services:
    internal_services = {}
    logger = None

    @classmethod
    def set_logger(cls, logger: Logger):
        cls.logger = logger

    @classmethod
    def register_internal(cls, name, command, arguments, output_type, handler):
        service = cls.internal_services.get(name)
        if service is None:
            service = InternalService(commands={})
            cls.internal_services[name] = service

        service.commands[command] = InternalCommand(arguments=arguments,
                                                    output_type=output_type,
                                                    handler=handler)

    @classmethod
    def is_internal(cls, service, command):
        service = cls.internal_services.get(service)
        if service is not None:
            return service.commands.get(command) is not None

        return False

    @classmethod
    def last(cls, chain):
        return chain[len(chain) - 1]

    @classmethod
    async def execute(cls, story, line):
        chain = cls.resolve_chain(story, line)
        assert isinstance(chain, deque)
        assert isinstance(chain[0], Service)

        if cls.is_internal(chain[0].name, cls.last(chain).name):
            return await cls.execute_internal(story, line)
        else:
            return await cls.execute_external(story, line)

    @classmethod
    async def execute_internal(cls, story, line):
        service = cls.internal_services[line['service']]
        command = service.commands.get(line['command'])

        resolved_args = {}

        if command.arguments:
            for arg in command.arguments:
                actual = story.argument_by_name(line=line, argument_name=arg)
                resolved_args[arg] = actual

        return await command.handler(
            story=story, line=line,
            resolved_args=resolved_args
        )

    @classmethod
    async def execute_external(cls, story, line):
        """
        Executes external services via HTTP or a docker exec.
        :return: The output of docker exec or the HTTP call.

        Note: If the Content-Type of an output from an HTTP call
        is application/json, this method will parse the response
        and return a dict.
        """
        service = line[LineConstants.service]
        chain = cls.resolve_chain(story, line)
        command_conf = cls.get_command_conf(story, chain)
        await cls.start_container(story, line)
        if command_conf.get('format') is not None:
            return await Containers.exec(story.logger, story, line,
                                         service, line['command'])
        elif command_conf.get('http') is not None:
            if command_conf['http'].get('use_event_conn', False):
                return await cls.execute_inline(
                    story, line,
                    chain, command_conf
                )
            else:
                return await cls.execute_http(
                    story, line, chain, command_conf
                )
        else:
            raise StoryscriptError(
                message=f'Service {service}/{line["command"]} '
                f'has neither http nor format sections!',
                story=story, line=line)

    @classmethod
    def resolve_chain(cls, story, line):
        """
        resolve_chain returns a path (chain) to the current command.
        The command or service in 'line' might be the result of
        an event output, deeply nested. This method returns the
        path to the command described in line.

        Example:
        [Service(slack), Command(bot), Event(hears), Command(reply)]

        In most cases, the output would be:
        [Service(alpine), Command(echo)]

        The first entry in the chain will always be a concrete service,
        and the last entry will always be a command.
        """

        def get_owner(line):
            service = line[LineConstants.service]
            while True:
                parent = line.get(LineConstants.parent)
                assert parent is not None

                line = story.line(parent)
                output = line.get(LineConstants.output)
                if output is not None \
                        and len(output) == 1 \
                        and service == output[0]:
                    return line

        chain = deque()
        parent_line = line

        while True:
            service = parent_line[LineConstants.service]

            if parent_line[LineConstants.method] == 'when':
                chain.appendleft(Event(parent_line[LineConstants.command]))
            else:
                chain.appendleft(Command(parent_line[LineConstants.command]))

            # Is this a concrete service?
            if story.app.services.get(service) is not None or \
                    cls.is_internal(service, parent_line['command']):
                chain.appendleft(Service(service))
                break

            assert parent_line.get(LineConstants.parent) is not None
            parent_line = get_owner(parent_line)
            assert parent_line is not None

        story.logger.debug(f'Chain resolved - {chain}')
        return chain

    @classmethod
    def get_command_conf(cls, story, chain):
        """
        Returns the conf for the command specified by 'chain'.
        """
        next = story.app.services
        for entry in chain:
            if isinstance(entry, Service):
                next = next[entry.name]['configuration']['actions']
            elif isinstance(entry, Command):
                next = next[entry.name]
            elif isinstance(entry, Event):
                next = next['events'][entry.name]['output']['actions']

        return next or {}

    @classmethod
    async def execute_inline(cls, story, line, chain, command_conf):
        assert isinstance(chain, deque)
        command = cls.last(chain)
        assert isinstance(command, Command)

        args = command_conf.get('arguments', {})
        body = {'command': command.name, 'data': {}}

        for arg in args:
            arg_val = story.argument_by_name(line, arg)
            body['data'][arg] = arg_val

        req = story.context[ContextConstants.server_request]
        io_loop = story.context[ContextConstants.server_io_loop]

        if req.is_finished():
            raise StoryscriptError(
                message='No more actions can be executed for'
                        ' this service as it\'s already closed.',
                story=story, line=line)

        # BEGIN hack for writing a binary response to the gateway
        # How we write binary response to the gateway right now:
        # 1. If the method is command is write,
        # and the content is an instance of bytes, write it directly
        # 2. Set the content-type to "application/octet-stream"
        # 3. Dump the bytes directly in the response
        if chain[0].name == 'http' and command.name == 'write' \
                and isinstance(body['data']['content'], bytes):
            req.set_header(
                name='Content-Type',
                value='application/octet-stream'
            )
            req.write(body['data']['content'])
            # Close this connection immediately,
            # as no more data can be written to it.
            story.app.logger.info('Connection has been closed '
                                  'for service http implicitly, '
                                  'as binary data was written to it.')
            io_loop.add_callback(req.finish)
            return

        # END hack for writing a binary response to the gateway

        # Set the header for the first time to something we know.
        req.set_header('Content-Type', 'application/stream+json')

        req.write(ujson.dumps(body) + '\n')

        # HTTP hack
        if chain[0].name == 'http' and command.name == 'finish':
            io_loop.add_callback(req.finish)
        # HTTP hack

    @classmethod
    def _fill_http_req_body(cls, http_res_kwargs, content_type, body):
        if content_type.startswith('application/json'):
            http_res_kwargs['body'] = json.dumps(body)
            http_res_kwargs['headers'] = {
                'Content-Type': 'application/json; charset=utf-8'
            }
        elif content_type.startswith('multipart/form-data'):
            boundary = uuid.uuid4().hex
            headers = {
                'Content-Type': f'multipart/form-data; boundary={boundary}'
            }
            producer = partial(cls._multipart_producer, body, boundary)
            http_res_kwargs['headers'] = headers
            http_res_kwargs['body_producer'] = producer

    @classmethod
    @coroutine
    def _multipart_producer(cls, body, boundary, write):
        """
        Writes files as well as regular form fields.

        Inspired directly from here:
        https://git.io/fjorx
        """

        for _, field in body.items():
            assert isinstance(field, FormField) or \
                isinstance(field, FileFormField)

            buf = f'--{boundary}\r\n' \
                  f'Content-Disposition: form-data; '

            if isinstance(field, FileFormField):
                buf += f'name="{field.name}"; filename="{field.filename}"\r\n'
                buf += f'Content-Type: {field.content_type}\r\n'
            else:
                buf += f'name="{field.name}"\r\n'

            buf += f'\r\n'

            yield write(buf.encode())

            if isinstance(field.body, bytes):
                yield write(field.body)
            elif not isinstance(field.body, str):
                yield write(f'{field.body}'.encode())
            else:
                yield write(field.body.encode())

            yield write(b'\r\n')

        yield write(b'--%s--\r\n' % (boundary.encode(),))

    @classmethod
    def raise_for_type_mismatch(cls, story, line, name, value, command_conf):
        """
        Validates for types listed on
        https://microservice.guide/schema/actions/#arguments.

        Supported types: int, float, string, list, map, boolean, enum, or any
        """
        t = command_conf.get('type', 'any')

        if t == 'string' and isinstance(value, str):
            return
        elif t == 'int' and isinstance(value, int):
            return
        elif t == 'float' and isinstance(value, float):
            return
        elif t == 'list' and isinstance(value, list):
            return
        elif t == 'map' and isinstance(value, dict):
            return
        elif t == 'boolean' and isinstance(value, bool):
            return
        elif t == 'enum' and isinstance(value, str):
            valid_values = command_conf.get('enum', [])
            if value in valid_values:
                return
        elif t == 'any':
            return

        raise ArgumentTypeMismatchError(name, t, story=story, line=line)

    @classmethod
    def smart_insert(cls, story, line, command_conf: dict, key: str, value,
                     m: dict):
        """
        Validates type, and sets the key in the map m.
        Additionally, it performs a "smart" cast - if the value is of type
        dict, or an array, and the command_conf indicates that the value
        expected by the service is a string, it will convert the value
        into a JSON representation, and insert it.

        :param line: The line
        :param story: The story
        :param command_conf: The command config, as seen in the OMG
        :param key: The key to insert this value as
        :param value: The value, which might be "smartly" stringified to JSON
        :param m: The map to insert the value in
        """
        t = command_conf.get('type', 'any')
        if t == 'string':
            if isinstance(value, dict) or isinstance(value, list):
                value = json.dumps(value)

        cls.raise_for_type_mismatch(story, line, key, value, command_conf)

        m[key] = value

    @classmethod
    async def execute_http(cls, story, line, chain, command_conf):
        assert isinstance(chain, deque)
        assert isinstance(chain[0], Service)
        hostname = await Containers.get_hostname(story, line, chain[0].name)
        args = command_conf.get('arguments', {})
        body = {}
        query_params = {}
        path_params = {}

        form_fields_count = 0
        request_body_fields_count = 0

        for arg in args:
            value = story.argument_by_name(line, arg)
            location = args[arg].get('in', 'requestBody')
            if location == 'query':
                cls.smart_insert(story, line, command_conf,
                                 arg, value, query_params)
            elif location == 'path':
                cls.smart_insert(story, line, command_conf,
                                 arg, value, path_params)
            elif location == 'requestBody':
                cls.smart_insert(story, line, command_conf,
                                 arg, value, body)
                request_body_fields_count += 1
            elif location == 'formBody':
                # Created in StoryEventHandler.
                if isinstance(value, FileFormField):
                    body[arg] = FileFormField(arg, value.body, value.filename,
                                              value.content_type)
                else:
                    body[arg] = FormField(arg, value)
                form_fields_count += 1
            else:
                raise StoryscriptError(
                    f'Invalid location for'
                    f' argument "{arg}" specified: {location}',
                    story=story, line=line
                )

        if form_fields_count > 0 and request_body_fields_count > 0:
            raise StoryscriptError(f'Mixed locations are not permitted. '
                                   f'Found {request_body_fields_count}'
                                   f' fields of which '
                                   f'{form_fields_count}'
                                   f' were in the form body',
                                   story=story, line=line)

        method = command_conf['http'].get('method', 'post')
        kwargs = {
            'method': method.upper()
        }

        content_type = command_conf['http'] \
            .get('contentType', 'application/json')

        if method.lower() == 'post':
            cls._fill_http_req_body(kwargs, content_type, body)
        elif len(body) > 0:
            raise StoryscriptError(
                message=f'Parameters found in the request body, '
                f'but the method is {method}', story=story, line=line)

        port = command_conf['http'].get('port', 5000)
        path = HttpUtils.add_params_to_url(
            command_conf['http']['path'].format(**path_params), query_params)
        url = f'http://{hostname}:{port}{path}'

        story.logger.debug(f'Invoking service on {url} with payload {kwargs}')

        client = AsyncHTTPClient()
        response = await HttpUtils.fetch_with_retry(
            3, story.logger, url, client, kwargs
        )

        story.logger.debug(f'HTTP response code is {response.code}')
        if int(response.code / 100) == 2:
            content_type = response.headers.get('Content-Type')
            if content_type and 'application/json' in content_type:
                try:
                    body = ujson.loads(response.body)
                except TypeError:
                    raise StoryscriptError(
                        message=f'Failed to parse service output as JSON!'
                        f' Response body is {body}.',
                        story=story, line=line)

                expected_service_output = command_conf.get('output')
                if expected_service_output is not None:
                    ServiceOutputValidator.raise_if_invalid(
                        expected_service_output, body, chain)
                return body
            else:
                return cls.parse_output(command_conf, response.body,
                                        story, line, content_type)
        else:
            response_body = HttpUtils.read_response_body_quietly(response)
            raise StoryscriptError(
                message=f'Failed to invoke service! '
                f'Status code: {response.code}; '
                f'response body: {response_body}',
                story=story, line=line
            )

    @classmethod
    def parse_output(cls, command_conf: dict, raw_output, story,
                     line, content_type: str):
        output = command_conf.get('output', None)
        if output is None:
            return raw_output
        t = output.get('type', None)
        if t is None or t == 'any':
            return raw_output  # We don't know what it is, return raw bytes.

        try:
            if t == 'string':
                return cls._convert_bytes_to_string(raw_output)
            elif t == 'int':
                return int(cls._convert_bytes_to_string(raw_output))
            elif t == 'float':
                return float(cls._convert_bytes_to_string(raw_output))
            elif t == 'boolean':
                raw_output = cls._convert_bytes_to_string(raw_output).lower()
                return raw_output == 'true'

            raise Exception(f'Unsupported type {t}')
        except BaseException:
            truncated_output = StringUtils.truncate(raw_output, 160)
            raise StoryscriptError(
                message=f'Failed to parse output as type {t}. '
                f'Content-Type received: "{content_type}". '
                f'Output received {truncated_output}.',
                story=story, line=line)

    @classmethod
    def _convert_bytes_to_string(cls, raw):
        if isinstance(raw, bytes):
            return raw.decode()
        return raw

    @classmethod
    async def start_container(cls, story, line):
        chain = cls.resolve_chain(story, line)
        assert isinstance(chain[0], Service)
        if chain[0].name == 'http':
            return StreamingService(
                name='http',
                command=line[LineConstants.command],
                container_name='gateway',
                hostname=story.app.config.ASYNCY_HTTP_GW_HOST)

        return await Containers.start(story, line)

    @classmethod
    def init(cls, logger):
        cls.logger = logger

    @classmethod
    async def when(cls, s: StreamingService, story, line: dict):
        service = line[LineConstants.service]
        command = line[LineConstants.command]
        conf = story.app.services[s.name][ServiceConstants.config]
        conf_event = Dict.find(
            conf, f'actions.{s.command}.events.{command}')

        port = Dict.find(conf_event, f'http.port', 80)
        subscribe_path = Dict.find(conf_event, 'http.subscribe.path')
        subscribe_method = Dict.find(conf_event,
                                     'http.subscribe.method', 'post')

        event_args = Dict.find(conf_event, 'arguments', {})

        data = {}
        for key in event_args:
            data[key] = story.argument_by_name(line, key)

        # HACK for http - send the DNS name of the app.
        if s.name == 'http':
            data['host'] = story.app.app_dns
        # END HACK for http.

        sub_url = f'http://{s.hostname}:{port}{subscribe_path}'

        story.logger.debug(f'Subscription URL - {sub_url}')

        engine = f'{story.app.config.ENGINE_HOST}:' \
            f'{story.app.config.ENGINE_PORT}'

        query_params = urllib.parse.urlencode({
            'story': story.name,
            'block': line['ln'],
            'app': story.app.app_id
        })

        sub_id = str(uuid.uuid4())

        sub_body = {
            'endpoint': f'http://{engine}/story/event?{query_params}',
            'data': data,
            'event': command,
            'id': sub_id
        }

        body = {
            'sub_id': sub_id,
            'sub_url': sub_url,
            'sub_method': subscribe_method.upper(),
            'sub_body': sub_body,
            'pod_name': s.container_name,
            'app_id': story.app.app_id
        }

        # Why request_timeout is set to 120 seconds:
        # Since this is the Synapse, Synapse does multiple internal retries,
        # so we must set this to a really high value.
        kwargs = {
            'method': subscribe_method.upper(),
            'body': json.dumps(body),
            'headers': {
                'Content-Type': 'application/json; charset=utf-8'
            },
            'request_timeout': 120
        }

        client = AsyncHTTPClient()
        story.logger.debug(f'Subscribing to {service} '
                           f'from {s.command} via Synapse...')

        url = f'http://{story.app.config.ASYNCY_SYNAPSE_HOST}:' \
            f'{story.app.config.ASYNCY_SYNAPSE_PORT}' \
            f'/subscribe'

        # Okay to retry a request to the Synapse a hundred times.
        response = await HttpUtils.fetch_with_retry(100, story.logger, url,
                                                    client, kwargs)
        if int(response.code / 100) == 2:
            story.logger.debug(f'Subscribed!')
            story.app.add_subscription(sub_id, s, command, body)
        else:
            raise StoryscriptError(
                message=f'Failed to subscribe to {service} from '
                f'{s.command} in {s.container_name}! '
                f'http err={response.error}; code={response.code}',
                story=story, line=line)

    @classmethod
    def log_internal(cls):
        for key in cls.internal_services:
            commands = []
            for command in cls.internal_services[key].commands:
                commands.append(command)

            cls.logger.info(f'Discovered internal service {key} - {commands}')
