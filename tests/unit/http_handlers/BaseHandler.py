# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

from asyncy.Exceptions import StoryscriptError
from asyncy.Sentry import Sentry
from asyncy.http_handlers.BaseHandler import BaseHandler

from pytest import mark


def test_handle_init(magic, logger):
    app = magic()
    req = magic()
    handler = BaseHandler(app, req, logger=logger)
    assert handler.logger == logger


def test_finished(magic, logger):
    handler = BaseHandler(magic(), magic(), logger=logger)
    assert handler.is_finished() is False
    assert handler.is_not_finished() is True

    handler._finished = True
    assert handler.is_finished() is True
    assert handler.is_not_finished() is False


@mark.parametrize('exception', [StoryscriptError(story=MagicMock(), line={'ln': 1}),
                                Exception()])
@mark.parametrize('story_name', [None, 'super_story'])
def test_handle_story_exc(patch, magic, logger, exception, story_name):
    handler = BaseHandler(magic(), magic(), logger=logger)
    patch.object(Sentry, 'capture_exc')
    patch.many(handler, ['set_status', 'finish'])
    handler.handle_story_exc('app_id', story_name, exception)
    handler.set_status.assert_called_with(500, 'Story execution failed')
    handler.finish.assert_called()
    logger.error.assert_called()
    if isinstance(exception, StoryscriptError):
        Sentry.capture_exc.assert_called_with(
            exception, exception.story, exception.line)
    elif story_name is not None:
        Sentry.capture_exc.assert_called_with(exception, extra={
            'story_name': story_name
        })
    else:
        Sentry.capture_exc.assert_called_with(exception)
