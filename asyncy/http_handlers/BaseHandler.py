# -*- coding: utf-8 -*-
from tornado.web import RequestHandler

from ..Apps import Apps
from ..reporting.ReportingAgent import ReportingAgentOptions


class BaseHandler(RequestHandler):
    logger = None

    # noinspection PyMethodOverriding
    def initialize(self, logger):
        self.logger = logger

    def handle_story_exc(self, app_id, story_name, e):
        # Always prefer the app logger if the app is available.
        try:
            logger = Apps.get(app_id).logger
        except BaseException:
            logger = self.logger

        app = Apps.get(app_id)

        logger.error(f'Story execution failed; cause={str(e)}', exc=e,
                     reporting_agent_options=ReportingAgentOptions(
                         story_name=story_name,
                         app_uuid=app_id,
                         app_name=app.app_name,
                         app_version=app.version,
                         agent_config={
                             'clever_ident': app.owner_email,
                             'clever_event': 'App Request Failure'
                         },
                         allow_user_events=True
                     ))

        self.set_status(500, 'Story execution failed')
        if not self.is_finished():
            self.finish()

    def is_finished(self):
        return self._finished

    def is_not_finished(self):
        return self.is_finished() is False
