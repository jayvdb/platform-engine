import json
import time

from tornado.httpclient import AsyncHTTPClient

from ..ReportingAgent import ReportingAgent
from ...Logger import Logger
from ...utils.HttpUtils import HttpUtils


class CleverTapAgent(ReportingAgent):

    def __init__(self, account_id: str, account_pass: str,
                 release: str, logger: Logger):
        self._account_id = account_id
        self._account_pass = account_pass
        self._release = release
        self._logger = logger
        self._http_client = AsyncHTTPClient()

    async def publish_msg(self, message: str, agent_config: dict = None):
        pass

    async def publish_evt(
            self, evt_name: str, evt_data: dict, agent_config: dict = None):
        if agent_config is None or \
                'clever_ident' not in agent_config or \
                'clever_event' not in agent_config:
            return

        _evt_data = {}

        if 'app_name' in evt_data:
            _evt_data['App name'] = evt_data['app_name']

        if 'app_version' in evt_data:
            _evt_data['App version'] = evt_data['app_version']

        if 'story_name' in evt_data:
            _evt_data['Story name'] = evt_data['story_name']

        if 'story_line' in evt_data:
            _evt_data['Story line'] = evt_data['story_line']

        event = {
            'ts': int(time.time()),
            'identity': agent_config['clever_ident'],
            'evtName': agent_config['clever_event'],
            'evtData': _evt_data,
            'type': 'event'
        }

        await HttpUtils.fetch_with_retry(
            tries=3, logger=self._logger,
            url='https://api.clevertap.com/1/upload',
            http_client=self._http_client,
            kwargs={
                'method': 'POST',
                'body': json.dumps({'d': [event]}),
                'headers': {
                    'X-CleverTap-Account-Id': self._account_id,
                    'X-CleverTap-Passcode': self._account_pass,
                    'Content-Type': 'application/json; charset=utf-8'
                }
            })

    async def publish_exc(self, exc_info: BaseException,
                          exc_data: dict, agent_config: dict = None):
        if agent_config is None or \
                'clever_ident' not in agent_config or \
                'clever_event' not in agent_config:
            return

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

        evt_data = {
            'Stacktrace': err_str
        }

        if 'app_name' in exc_data:
            evt_data['App name'] = exc_data['app_name']

        if 'app_version' in exc_data:
            evt_data['App version'] = exc_data['app_version']

        if 'story_name' in exc_data:
            evt_data['Story name'] = exc_data['story_name']

        if 'story_line' in exc_data:
            evt_data['Story line'] = exc_data['story_line']

        event = {
            'ts': int(time.time()),
            'identity': agent_config['clever_ident'],
            'evtName': agent_config['clever_event'],
            'evtData': evt_data,
            'type': 'event'
        }

        await HttpUtils.fetch_with_retry(
            tries=3, logger=self._logger,
            url='https://api.clevertap.com/1/upload',
            http_client=self._http_client,
            kwargs={
                'method': 'POST',
                'body': json.dumps({'d': [event]}),
                'headers': {
                    'X-CleverTap-Account-Id': self._account_id,
                    'X-CleverTap-Passcode': self._account_pass,
                    'Content-Type': 'application/json; charset=utf-8'
                }
            })
