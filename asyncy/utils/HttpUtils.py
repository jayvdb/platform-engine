# -*- coding: utf-8 -*-
import asyncio
from urllib.parse import urlencode

from tornado.httpclient import HTTPError


class HttpUtils:

    @staticmethod
    def read_response_body_quietly(response):
        try:
            return response.body.decode('utf-8')
        except BaseException:
            return None

    @staticmethod
    async def fetch_with_retry(tries, logger, url, http_client, kwargs):
        kwargs['raise_error'] = False
        attempts = 0
        last_exception = None
        while attempts < tries:
            attempts = attempts + 1
            try:
                res = await http_client.fetch(url, **kwargs)
                if res.code == 599:  # Network connectivity issues.
                    raise HTTPError(res.code, message=str(res.error),
                                    response=res)
                return res
            except HTTPError as e:
                last_exception = e
                if logger is not None:
                    logger.error(
                        f'Failed to call {url}; attempt={attempts}; '
                        f'err={str(e)}'
                    )
                await asyncio.sleep(0.5)

        assert last_exception is not None  # Impossible.
        raise HTTPError(500, message=f'Failed to call {url}!') \
            from last_exception

    @staticmethod
    def add_params_to_url(url, params: dict):
        if len(params) == 0:
            return url

        return f'{url}?{urlencode(params)}'
