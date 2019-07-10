import json
import time

from asyncy.Exceptions import StoryscriptError
from asyncy.reporting.ReportingAgent import ReportingAgent
from asyncy.reporting.agents.CleverTapAgent import CleverTapAgent
from asyncy.utils.HttpUtils import HttpUtils

from pytest import mark

from tornado.httpclient import AsyncHTTPClient


@mark.parametrize('suppress_stacktrace', [False, True])
@mark.parametrize('full_stacktrace', [False, True])
@mark.asyncio
async def test_publish_exc(patch, magic, async_mock,
                           suppress_stacktrace, full_stacktrace):
    logger = magic()

    _time = int(time.time())
    patch.object(time, 'time', return_value=_time)

    patch.init(AsyncHTTPClient)
    patch.object(HttpUtils, 'fetch_with_retry', new=async_mock())
    patch.object(ReportingAgent, 'format_tb_error',
                 return_value='traceback_err')

    clevertap_agent = CleverTapAgent(
        account_id='account_id',
        account_pass='account_pass',
        release='release',
        logger=logger
    )

    AsyncHTTPClient.__init__.assert_called()

    try:
        story = magic()
        story.app.app_name = 'app_name'
        story.app.app_id = 'app_id'
        story.app.version = 'app_version'
        story.name = 'story_name'
        line = magic()
        line['ln'] = '28'

        if full_stacktrace is True:
            try:
                raise BaseException('Something initially went wrong!')
            except BaseException as e1:
                raise StoryscriptError(
                    message='A Storyscript error happened',
                    root=e1
                )
        else:
            raise StoryscriptError(message='foo', story=story, line=line)

    except BaseException as e:
        exc_data = {
            'app_uuid': 'app_uuid',
            'app_name': 'app_name',
            'app_version': 'app_version',
            'story_line': '28',
            'story_name': 'story_name',
            'platform_release': 'release'
        }

        await clevertap_agent.publish_exc(
            exc_info=e,
            exc_data=exc_data,
            agent_config={
                'suppress_stacktrace': suppress_stacktrace,
                'full_stacktrace': full_stacktrace,
                'clever_ident': 'clever_ident',
                'clever_event': 'clever_event'
            })

        ReportingAgent.format_tb_error.assert_called_with(
            exc_info=e,
            full_stacktrace=full_stacktrace,
            suppress_stacktrace=suppress_stacktrace
        )

        evt_data = {
            'Stacktrace': 'traceback_err',
            'App name': 'app_name',
            'App version': 'app_version',
            'Story name': 'story_name',
            'Story line': '28'
        }

        event = {
            'ts': _time,
            'identity': 'clever_ident',
            'evtName': 'clever_event',
            'evtData': evt_data,
            'type': 'event'
        }

        HttpUtils.fetch_with_retry.mock.assert_called_with(
            tries=3, logger=logger,
            url='https://api.clevertap.com/1/upload',
            http_client=clevertap_agent._http_client,
            kwargs={
                'method': 'POST',
                'body': json.dumps({'d': [event]}),
                'headers': {
                    'X-CleverTap-Account-Id': 'account_id',
                    'X-CleverTap-Passcode': 'account_pass',
                    'Content-Type': 'application/json; charset=utf-8'
                }
            })
