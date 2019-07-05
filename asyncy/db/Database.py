# -*- coding: utf-8 -*-
from asyncy.Config import Config
from asyncy.db.SimpleConnCursor import SimpleConnCursor
from asyncy.entities.ContainerConfig import ContainerConfig
from asyncy.entities.Release import Release
from asyncy.enums.ReleaseState import ReleaseState

import psycopg2


class Database:

    @classmethod
    def new_pg_conn(cls, config: Config):
        conn = psycopg2.connect(config.POSTGRES)
        return conn

    @classmethod
    def new_pg_cur(cls, config: Config) -> SimpleConnCursor:
        return SimpleConnCursor(cls.new_pg_conn(config))

    @classmethod
    def get_all_app_uuids_for_deployment(cls, config: Config):
        with cls.new_pg_cur(config) as db:
            query = 'select app_uuid uuid from releases group by app_uuid;'
            db.cur.execute(query)
            return db.cur.fetchall()

    @classmethod
    def update_release_state(cls, glogger, config, app_id, version,
                             state: ReleaseState):
        with cls.new_pg_cur(config) as db:
            query = 'update releases ' \
                    'set state = %s ' \
                    'where app_uuid = %s and id = %s;'
            db.cur.execute(query, (state.value, app_id, version))
            db.conn.commit()

        glogger.info(f'Updated state for {app_id}@{version} to {state.name}')

    @classmethod
    def get_container_configs(cls, app, registry_url):
        with cls.new_pg_cur(app.config) as db:
            query = """
            with containerconfigs as (
            select name,
            owner_uuid, containerconfig,
            json_object_keys(
                (containerconfig->>'auths')::json
            ) registry
            from app_public.owner_containerconfigs
            )
            select name, containerconfig
            from containerconfigs
            where owner_uuid = %s and registry = %s
            """
            db.cur.execute(query, (app.owner_uuid, registry_url))
            data = db.cur.fetchall()
            result = []
            for config in data:
                result.append(ContainerConfig(
                    name=config['name'],
                    data=config['containerconfig'])
                )
            return result

    @classmethod
    def get_release_for_deployment(cls, config, app_id):
        with cls.new_pg_cur(config) as db:
            query = """
            with latest as (select app_uuid, max(id) as id
                            from releases
                            where state != 'NO_DEPLOY'::release_state
                            group by app_uuid)
            select app_uuid, id as version, config environment,
                   payload stories, apps.name as app_name,
                   maintenance, always_pull_images,
                   hostname app_dns, state, deleted,
                   apps.owner_uuid, owner_emails.email as owner_email
            from latest
                   inner join releases using (app_uuid, id)
                   inner join apps on (latest.app_uuid = apps.uuid)
                   inner join app_dns using (app_uuid)
                   left join app_public.owner_emails on
                    (apps.owner_uuid = owner_emails.owner_uuid)
            where app_uuid = %s;
            """
            db.cur.execute(query, (app_id,))
            data = db.cur.fetchone()
            return Release(
                app_uuid=data['app_uuid'],
                app_name=data['app_name'],
                version=data['version'],
                environment=data['environment'],
                stories=data['stories'],
                maintenance=data['maintenance'],
                always_pull_images=data['always_pull_images'],
                app_dns=data['app_dns'],
                state=data['state'],
                deleted=data['deleted'],
                owner_uuid=data['owner_uuid'],
                owner_email=data['owner_email']
            )
