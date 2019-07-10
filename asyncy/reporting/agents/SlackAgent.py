import json

from tornado.httpclient import AsyncHTTPClient

from ..ReportingAgent import ReportingAgent
from ...Logger import Logger
from ...utils.HttpUtils import HttpUtils


class SlackAgent(ReportingAgent):

    def __init__(self, webhook: str, release: str, logger: Logger):
        self._webhook = webhook
        self._release = release
        self._logger = logger
        self._http_client = AsyncHTTPClient()

    async def publish_msg(self, message: str, agent_config: dict = None):
        if self._webhook is None and \
                agent_config is None:
            return

        webhook = self._webhook

        if agent_config is not None and \
                'webhook' in agent_config:
            webhook = agent_config['webhook']
        elif webhook is None:
            return

        await HttpUtils.fetch_with_retry(
            tries=3, logger=self._logger,
            url=webhook, http_client=self._http_client,
            kwargs={
                'method': 'POST',
                'body': json.dumps({'text': message}),
                'headers': {'Content-Type': 'application/json'}
            })

    async def publish_evt(
            self, evt_name: str, evt_data: dict, agent_config: dict = None):
        if self._webhook is None and \
                agent_config is None:
            return

        story_name = ''
        story_line = ''
        app_uuid = ''
        app_version = ''
        app_name = ''

        if 'app_name' in evt_data:
            app_name = f'*App Name*: {evt_data["app_name"]}\n'

        if 'app_uuid' in evt_data:
            app_uuid = f'*App UUID*: {evt_data["app_uuid"]}\n'

        if 'app_version' in evt_data:
            app_version = f'*App Version*: {evt_data["app_version"]}\n'

        if 'story_name' in evt_data:
            story_name = f'*Story Name*: {evt_data["story_name"]}\n'

        if 'story_line' in evt_data:
            story_line = f'*Story Line Number*: {evt_data["story_line"]}\n\n'

        evt_str = ''
        if evt_data['event_data'] is not None and \
                len(evt_data['event_data'].items()) > 0:
            evt_str = json.dumps(evt_data['event_data'])
            evt_str = f'\n\n```{evt_str}```'

        event_line = f'*Event*: {evt_name}{evt_str}'

        err_msg = f'An event was triggered with ' \
            f'the following information:\n\n' \
            f'*Platform Engine Release*: {self._release}\n' \
            f'{app_name}' \
            f'{app_uuid}' \
            f'{app_version}' \
            f'{story_name}' \
            f'{story_line}' \
            f'{event_line}'

        webhook = self._webhook

        # allow the webhook to be overridden for user based reporting
        if agent_config is not None and \
                'webhook' in agent_config:
            webhook = agent_config['webhook']
        elif webhook is None:
            return

        await HttpUtils.fetch_with_retry(
            tries=3, logger=self._logger,
            url=webhook, http_client=self._http_client,
            kwargs={
                'method': 'POST',
                'body': json.dumps({'text': err_msg}),
                'headers': {'Content-Type': 'application/json'}
            })

    async def publish_exc(self, exc_info: BaseException,
                          exc_data: dict, agent_config: dict = None):
        if self._webhook is None and \
                agent_config is None:
            return

        story_name = ''
        story_line = ''
        app_uuid = ''
        app_version = ''
        app_name = ''

        if 'app_name' in exc_data:
            app_name = f'*App Name*: {exc_data["app_name"]}\n'

        if 'app_uuid' in exc_data:
            app_uuid = f'*App UUID*: {exc_data["app_uuid"]}\n'

        if 'app_version' in exc_data:
            app_version = f'*App Version*: {exc_data["app_version"]}\n'

        if 'story_name' in exc_data:
            story_name = f'*Story Name*: {exc_data["story_name"]}\n'

        if 'story_line' in exc_data:
            story_line = f'*Story Line Number*: {exc_data["story_line"]}\n\n'

        full_stacktrace = True
        suppress_stacktrace = False

        # check if we are allowed to include the stacktrace in
        # this event. If not, let's just include the error messages
        if agent_config is not None:
            if agent_config.get('full_stacktrace', True) is False:
                full_stacktrace = False

            if agent_config.get('suppress_stacktrace', False) is True:
                suppress_stacktrace = True

        err_str = ReportingAgent.format_tb_error(
            exc_info=exc_info,
            full_stacktrace=full_stacktrace,
            suppress_stacktrace=suppress_stacktrace
        )

        if suppress_stacktrace is True:
            traceback_line = f'*Error*: {err_str}'
        else:
            traceback_line = f'```{err_str}```'

        err_msg = f'An exception occurred with' \
            f' the following information:\n\n' \
            f'*Platform Engine Release*: {self._release}\n' \
            f'{app_name}' \
            f'{app_uuid}' \
            f'{app_version}' \
            f'{story_name}' \
            f'{story_line}' \
            f'{traceback_line}'

        webhook = self._webhook

        # allow the webhook to be overridden for user based reporting
        if agent_config is not None and \
                'webhook' in agent_config:
            webhook = agent_config['webhook']
        elif webhook is None:
            return

        await HttpUtils.fetch_with_retry(
            tries=3, logger=self._logger,
            url=webhook, http_client=self._http_client,
            kwargs={
                'method': 'POST',
                'body': json.dumps({'text': err_msg}),
                'headers': {'Content-Type': 'application/json'}
            })
