# -*- coding: utf-8 -*-
from asyncy.db.Database import Database
from asyncy.entities.ContainerConfig import ContainerConfig
from asyncy.entities.Release import Release
from asyncy.enums.ReleaseState import ReleaseState

from pytest import fixture


@fixture
def database(magic, patch):
    db = magic()
    patch.object(Database, 'new_pg_cur')
    Database.new_pg_cur.return_value.__enter__.return_value = db
    return db


def test_update_release_state(logger, config, database):
    expected_query = 'update releases ' \
                     'set state = %s ' \
                     'where app_uuid = %s and id = %s;'

    Database.update_release_state(logger, config, 'app_id', 'version',
                                  ReleaseState.DEPLOYED)

    database.cur.execute.assert_called_with(
        expected_query, (ReleaseState.DEPLOYED.value, 'app_id', 'version'))

    database.conn.commit.assert_called()


def test_get_all_app_uuids_for_deployment(config, database):
    query = 'select app_uuid uuid from releases group by app_uuid;'
    ret = Database.get_all_app_uuids_for_deployment(config)
    database.cur.execute.assert_called_with(query)
    assert ret == database.cur.fetchall()


def test_get_container_configs(patch, magic, config, database):
    expected_query = """
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

    patch.object(database.cur, 'fetchall', return_value=[
        {'name': 'n1', 'containerconfig': 'config'}
    ])
    app = magic()
    app.config = config
    app.owner_uuid = 'my_owner_uuid'
    registry_url = 'my_registry_url_here'
    ret = Database.get_container_configs(app, registry_url)

    assert ret == [
        ContainerConfig(name='n1', data='config')
    ]

    database.cur.execute.assert_called_with(expected_query,
                                            (app.owner_uuid, registry_url))


def test_get_release_for_deployment(patch, config, database):
    app_id = 'my_app_id'
    expected_query = """
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

    patch.object(database.cur, 'fetchone', return_value={
        'app_uuid': 'my_app_uuid',
        'app_name': 'my_app_name',
        'version': 'my_version',
        'environment': 'my_environment',
        'stories': 'my_stories',
        'maintenance': 'my_maintenance',
        'always_pull_images': 'my_always_pull_images',
        'app_dns': 'my_app_dns',
        'state': 'my_state',
        'deleted': 'my_deleted',
        'owner_uuid': 'my_owner_uuid',
        'owner_email': 'my_owner_email'
    })

    ret = Database.get_release_for_deployment(config, app_id)

    assert ret == Release(
        app_uuid='my_app_uuid',
        app_name='my_app_name',
        version='my_version',
        environment='my_environment',
        stories='my_stories',
        maintenance='my_maintenance',
        always_pull_images='my_always_pull_images',
        app_dns='my_app_dns',
        state='my_state',
        deleted='my_deleted',
        owner_uuid='my_owner_uuid',
        owner_email='my_owner_email'
    )

    database.cur.execute.assert_called_with(expected_query, (app_id,))
