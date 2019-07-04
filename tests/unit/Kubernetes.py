# -*- coding: utf-8 -*-
import asyncio
import base64
import json
import ssl
import time
import urllib.parse
from unittest import mock
from unittest.mock import MagicMock

from asyncy.AppConfig import AppConfig, Expose, KEY_EXPOSE
from asyncy.Exceptions import K8sError
from asyncy.Kubernetes import Kubernetes
from asyncy.constants.LineConstants import LineConstants
from asyncy.constants.ServiceConstants import ServiceConstants
from asyncy.entities.ContainerConfig import ContainerConfig
from asyncy.entities.Volume import Volume
from asyncy.utils.HttpUtils import HttpUtils

import pytest
from pytest import fixture, mark

from tornado.httpclient import AsyncHTTPClient


@fixture
def line():
    return MagicMock()


def test_find_all_ports():
    services = {
        'alpine': {
            'http': {
                'port': 8080
            }
        },
        'alpha': {
            'expose': {
                'console': {
                    'http': {
                        'port': 1882
                    }
                }
            },
            'http': {
                'port': 9092,
                'subscribe': {
                    'port': 9090
                },
                'unsubscribe': {
                    'port': 9091
                }
            }
        },
        'nested': {
            'a': {
                'b': {
                    'c': {
                        'd': {
                            'e': {
                                'http': {
                                    'subscribe': {
                                        'port': 1234
                                    },
                                    'unsubscribe': {
                                        'port': 1235
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    assert Kubernetes.find_all_ports(services['alpine']) == {8080}
    assert Kubernetes.find_all_ports(services['alpha']) == {1882, 9090, 9091,
                                                            9092}
    assert Kubernetes.find_all_ports(services['nested']) == {1234, 1235}


def test_raise_if_not_2xx(story, line):
    res = MagicMock()
    res.code = 401
    with pytest.raises(K8sError):
        Kubernetes.raise_if_not_2xx(res)

    res.code = 200
    assert Kubernetes.raise_if_not_2xx(res) is None


@mark.asyncio
async def test_create_namespace_if_required_existing(patch, app,
                                                     async_mock):
    res = MagicMock()
    res.code = 200
    patch.object(Kubernetes, 'make_k8s_call', new=async_mock(return_value=res))

    app.app_id = 'my_app'
    await Kubernetes.create_namespace(app)

    Kubernetes.make_k8s_call.mock.assert_called_once()
    Kubernetes.make_k8s_call.mock.assert_called_with(
        app.config, app.logger, '/api/v1/namespaces/my_app')


@mark.asyncio
@mark.parametrize('create_result', [200, 500])
async def test_create_namespace_if_required(patch, app,
                                            line, async_mock, create_result):
    res_check = MagicMock()
    res_check.code = 400

    res_create = MagicMock()
    res_create.code = create_result

    app.app_id = 'my_app'

    patch.object(Kubernetes, 'make_k8s_call',
                 new=async_mock(side_effect=[res_check, res_create]))
    if create_result != 200:
        with pytest.raises(K8sError):
            await Kubernetes.create_namespace(app)
        return
    else:
        await Kubernetes.create_namespace(app)

    expected_payload = {
        'apiVersion': 'v1',
        'kind': 'Namespace',
        'metadata': {
            'name': 'my_app'
        }
    }

    assert Kubernetes.make_k8s_call.mock.mock_calls == [
        mock.call(app.config, app.logger, '/api/v1/namespaces/my_app'),
        mock.call(app.config, app.logger, '/api/v1/namespaces',
                  payload=expected_payload)
    ]


@mark.asyncio
async def test_clean_namespace(patch, story, async_mock):
    patch.object(Kubernetes, '_list_resource_names',
                 new=async_mock(side_effect=[['service_1', 'service_2'],
                                             ['depl_1', 'depl_2'],
                                             ['pod_1', 'pod_2'],
                                             ['ing_1', 'ing_2'],
                                             ['secret_1', 'secret_2']]))

    patch.object(Kubernetes, '_delete_resource', new=async_mock())

    await Kubernetes.clean_namespace(story.app)

    assert Kubernetes._delete_resource.mock.mock_calls == [
        mock.call(story.app, 'services', 'service_1'),
        mock.call(story.app, 'services', 'service_2'),
        mock.call(story.app, 'deployments', 'depl_1'),
        mock.call(story.app, 'deployments', 'depl_2'),
        mock.call(story.app, 'pods', 'pod_1'),
        mock.call(story.app, 'pods', 'pod_2'),
        mock.call(story.app, 'ingresses', 'ing_1'),
        mock.call(story.app, 'ingresses', 'ing_2'),
        mock.call(story.app, 'secrets', 'secret_1'),
        mock.call(story.app, 'secrets', 'secret_2')
    ]


def test_get_hostname(story):
    story.app.app_id = 'my_app'
    container_name = 'alpine'
    ret = Kubernetes.get_hostname(story.app, container_name)
    assert ret == 'alpine.my_app.svc.cluster.local'


def _create_response(code: int, body: dict = None):
    res = MagicMock()
    res.code = code
    if body:
        res.body = json.dumps(body)
    return res


@mark.parametrize('first_res', [200, 409, 404])
@mark.parametrize('resource', ['deployments', 'services', 'secrets',
                               'persistentvolumeclaims', 'unknown', 'pods'])
@mark.asyncio
async def test_delete_resource(patch, story, async_mock, first_res, resource):
    story.app.app_id = 'my_app'
    api_responses = [
        _create_response(first_res),
        _create_response(200),
        _create_response(200),
        _create_response(404),
    ]
    patch.object(Kubernetes, 'make_k8s_call',
                 new=async_mock(side_effect=api_responses))
    patch.object(asyncio, 'sleep', new=async_mock())
    if resource == 'unknown':
        with pytest.raises(Exception):
            await Kubernetes._delete_resource(story.app, resource, 'foo')
        return
    else:
        await Kubernetes._delete_resource(story.app, resource, 'foo')

    if first_res == 404:
        assert Kubernetes.make_k8s_call.mock.call_count == 1
        return

    prefix = Kubernetes._get_api_path_prefix(resource)

    assert Kubernetes.make_k8s_call.mock.mock_calls == [
        mock.call(story.app.config, story.app.logger,
                  f'{prefix}/my_app/{resource}/foo'
                  f'?gracePeriodSeconds=0',
                  method='delete'),
        mock.call(story.app.config, story.app.logger,
                  f'{prefix}/my_app/{resource}/foo'),
        mock.call(story.app.config, story.app.logger,
                  f'{prefix}/my_app/{resource}/foo'),
        mock.call(story.app.config, story.app.logger,
                  f'{prefix}/my_app/{resource}/foo'),
    ]


@mark.parametrize('method', ['patch', 'post'])
@mark.asyncio
async def test_make_k8s_call(patch, story, async_mock, method):
    patch.object(HttpUtils, 'fetch_with_retry', new=async_mock())

    context = MagicMock()
    patch.object(Kubernetes, 'new_ssl_context', return_value=context)
    context.load_verify_locations = MagicMock()

    patch.init(AsyncHTTPClient)

    client = AsyncHTTPClient()

    story.app.config.CLUSTER_CERT = 'this_is\\nmy_cert'  # Notice the \\n.
    story.app.config.CLUSTER_AUTH_TOKEN = 'my_token'
    story.app.config.CLUSTER_HOST = 'k8s.local'

    path = '/hello_world'

    payload = {
        'foo': 'bar'
    }

    expected_kwargs = {
        'ssl_options': context,
        'headers': {
            'Authorization': 'bearer my_token',
            'Content-Type': 'application/json; charset=utf-8'
        },
        'method': method.upper(),
        'body': json.dumps(payload)
    }

    if method == 'patch':
        expected_kwargs['headers']['Content-Type'] = \
            'application/merge-patch+json; charset=utf-8'

    assert await Kubernetes.make_k8s_call(story.app.config, story.app.logger,
                                          path, payload, method=method) \
        == HttpUtils.fetch_with_retry.mock.return_value

    HttpUtils.fetch_with_retry.mock.assert_called_with(
        3, story.app.logger, 'https://k8s.local/hello_world', client,
        expected_kwargs)

    # Notice the \n. \\n MUST be converted to \n in Kubernetes#make_k8s_call.
    context.load_verify_locations.assert_called_with(cadata='this_is\nmy_cert')


@mark.asyncio
async def test_remove_volume(patch, story, async_mock):
    name = 'foo'
    patch.object(Kubernetes, '_delete_resource', new=async_mock())
    await Kubernetes.remove_volume(story.app, name)
    Kubernetes._delete_resource.mock.assert_called_with(
        story.app, 'persistentvolumeclaims', name)


@mark.parametrize('resource', ['persistentvolumeclaims', 'deployments',
                               'services', 'foo'])
@mark.parametrize('res_code', [404, 200, 500])
@mark.asyncio
async def test_does_resource_exist(patch, story, resource,
                                   async_mock, res_code):
    resp = MagicMock()
    resp.code = res_code
    patch.object(Kubernetes, 'make_k8s_call',
                 new=async_mock(return_value=resp))

    if res_code == 500 or resource == 'foo':
        with pytest.raises(Exception):
            await Kubernetes._does_resource_exist(story.app, resource, 'name')
        return

    ret = await Kubernetes._does_resource_exist(story.app, resource, 'name')

    if res_code == 200:
        assert ret is True
    else:
        assert ret is False

    expected_path = Kubernetes._get_api_path_prefix(resource) + \
        f'/{story.app.app_id}/{resource}/name'
    Kubernetes.make_k8s_call.mock.assert_called_with(story.app.config,
                                                     story.app.logger,
                                                     expected_path)


@mark.asyncio
async def test_list_resource_names(story, patch, async_mock):
    mock_res = MagicMock()
    mock_res.body = json.dumps({
        'items': [
            {'metadata': {'name': 'hello'}},
            {'metadata': {'name': 'world'}},
        ]
    })

    patch.object(Kubernetes, 'make_k8s_call',
                 new=async_mock(return_value=mock_res))
    patch.object(Kubernetes, '_get_api_path_prefix', return_value='prefix')
    ret = await Kubernetes._list_resource_names(story.app, 'services')
    Kubernetes.make_k8s_call.mock.assert_called_with(
        story.app.config, story.app.logger,
        f'prefix/{story.app.app_id}/services?includeUninitialized=true')

    assert ret == ['hello', 'world']


def test_new_ssl_context():
    assert isinstance(Kubernetes.new_ssl_context(), ssl.SSLContext)


@mark.parametrize('res_code', [200, 400])
@mark.asyncio
async def test_create_pod(patch, async_mock, story, line, res_code):
    res = MagicMock()
    res.code = res_code
    patch.object(Kubernetes, 'create_deployment', new=async_mock())
    patch.object(Kubernetes, 'create_service', new=async_mock())
    patch.object(Kubernetes, 'make_k8s_call', new=async_mock(return_value=res))

    image = 'alpine/alpine:latest'
    start_command = ['/bin/sleep', '1d']
    container_name = 'asyncy--alpine-1'
    env = {'token': 'foo'}

    story.app.app_id = 'my_app'

    await Kubernetes.create_pod(
        story.app, line[LineConstants.service], image,
        container_name, start_command, None, env, [], [])

    Kubernetes.make_k8s_call.mock.assert_called_with(
        story.app.config, story.app.logger,
        '/apis/apps/v1/namespaces/my_app/deployments/asyncy--alpine-1')

    if res_code == 200:
        assert Kubernetes.create_deployment.mock.called is False
        assert Kubernetes.create_service.mock.called is False
    else:
        Kubernetes.create_deployment.mock.assert_called_with(
            story.app, line[LineConstants.service],
            image, container_name, start_command, None, env, [], [])
        Kubernetes.create_service.mock.assert_called_with(
            story.app, line[LineConstants.service], container_name)


@mark.parametrize('persist', [True, False])
@mark.parametrize('resource_exists', [True, False])
@mark.asyncio
async def test_create_volume(story, patch, async_mock,
                             persist, resource_exists):
    name = 'foo'
    patch.object(Kubernetes, '_does_resource_exist',
                 new=async_mock(return_value=resource_exists))
    patch.object(Kubernetes, '_update_volume_label',
                 new=async_mock())
    patch.object(time, 'time', return_value=123)
    res = MagicMock()
    patch.object(Kubernetes, 'make_k8s_call', new=async_mock(return_value=res))
    patch.object(Kubernetes, 'raise_if_not_2xx')

    expected_path = f'/api/v1/namespaces/{story.app.app_id}' \
                    f'/persistentvolumeclaims'

    expected_payload = {
        'apiVersion': 'v1',
        'kind': 'PersistentVolumeClaim',
        'metadata': {
            'name': name,
            'namespace': story.app.app_id,
            'labels': {
                'last_referenced_on': '123',
                'omg_persist': f'{persist}'
            }
        },
        'spec': {
            'accessModes': ['ReadWriteOnce'],
            'resources': {
                'requests': {
                    'storage': '100Mi'
                }
            }
        }
    }

    await Kubernetes.create_volume(story.app, name, persist)
    if resource_exists:
        Kubernetes._update_volume_label.mock.assert_called_with(
            story.app, name)
        Kubernetes.make_k8s_call.mock.assert_not_called()
    else:
        Kubernetes._update_volume_label.mock.assert_not_called()
        Kubernetes.make_k8s_call.mock.assert_called_with(
            story.app.config, story.app.logger,
            expected_path, expected_payload)
        Kubernetes.raise_if_not_2xx.assert_called_with(res)


@mark.asyncio
async def test_create_imagepullsecret(story, patch, async_mock):
    res = MagicMock()
    res.code = 200
    patch.object(Kubernetes, 'make_k8s_call', new=async_mock(return_value=res))

    container_config = ContainerConfig(name='first', data={
        'auths': {
            'https://index.docker.io/v1/': {
                'auth': 'username_password_base64'
            }
        }
    })

    b64_container_config = base64.b64encode(
        json.dumps(container_config.data).encode()
    ).decode()

    expected_path = f'/api/v1/namespaces/{story.app.app_id}/secrets'

    expected_payload = {
        'apiVersion': 'v1',
        'kind': 'Secret',
        'type': 'kubernetes.io/dockerconfigjson',
        'metadata': {
            'name': container_config.name,
            'namespace': story.app.app_id
        },
        'data': {
            '.dockerconfigjson': b64_container_config
        }
    }

    await Kubernetes.create_imagepullsecret(story.app, container_config)

    Kubernetes.make_k8s_call.mock.assert_called_with(
        story.app.config, story.app.logger, expected_path, expected_payload)


@mark.asyncio
async def test_update_volume_label(story, patch, async_mock):
    res = MagicMock()
    patch.object(Kubernetes, 'make_k8s_call', new=async_mock(return_value=res))
    patch.object(Kubernetes, 'raise_if_not_2xx')
    patch.object(time, 'time', return_value=123)

    payload = {
        'metadata': {
            'labels': {
                'last_referenced_on': '123'
            }
        }
    }

    await Kubernetes._update_volume_label(story.app, 'db')

    path = f'/api/v1/namespaces/{story.app.app_id}/persistentvolumeclaims/db'

    Kubernetes.make_k8s_call.mock.assert_called_with(
        story.app.config, story.app.logger, path, payload, method='patch')
    Kubernetes.raise_if_not_2xx.assert_called_with(res)


@mark.parametrize('resource_exists', [True, False])
@mark.parametrize('k8s_api_returned_2xx', [True, False])
@mark.asyncio
async def test_create_ingress(patch, app, async_mock, resource_exists,
                              k8s_api_returned_2xx):
    if resource_exists and not k8s_api_returned_2xx:
        # Invalid combination, since if the ing resource exists already,
        # no additional call to the k8s API is made.
        return

    app.app_id = 'my_app_id'
    app.config.INGRESS_GLOBAL_STATIC_IP_NAME = 'ip-static-name-global'
    ingress_name = 'my_ingress_name'
    hostname = 'my_ingress_hostname'
    container_name = 'my_container_name'
    expose = Expose(service='service',
                    service_expose_name='expose_name',
                    http_path='expose_path')

    http_conf = {
        'path': '/my_app',
        'port': 6000
    }

    app.services = {
        expose.service: {
            ServiceConstants.config: {
                KEY_EXPOSE: {
                    expose.service_expose_name: {
                        'http': http_conf
                    }
                }
            }
        }
    }

    app.config.APP_DOMAIN = 'foo.com'

    patch.object(Kubernetes, '_does_resource_exist',
                 new=async_mock(return_value=resource_exists))

    expected_payload = {
        'apiVersion': 'extensions/v1beta1',
        'kind': 'Ingress',
        'metadata': {
            'name': ingress_name,
            'annotations': {
                'kubernetes.io/ingress.class': 'nginx',
                'kubernetes.io/ingress.global-static-ip-name':
                    app.config.INGRESS_GLOBAL_STATIC_IP_NAME,
                'ingress.kubernetes.io/rewrite-target': expose.http_path,
                'nginx.ingress.kubernetes.io/proxy-body-size': '1m',
                'nginx.ingress.kubernetes.io/proxy-read-timeout': '120'
            }
        },
        'spec': {
            'tls': [
                {
                    'hosts': [f'{hostname}.'
                              f'{app.config.APP_DOMAIN}']
                }
            ],
            'rules': [
                {
                    'host': f'{hostname}.{app.config.APP_DOMAIN}',
                    'http': {
                        'paths': [
                            {
                                'path': http_conf['path'],
                                'backend': {
                                    'serviceName': container_name,
                                    'servicePort': http_conf['port']
                                }
                            }
                        ]
                    }
                }
            ]
        }
    }

    patch.object(Kubernetes, 'make_k8s_call',
                 new=async_mock(return_value=314))
    patch.object(Kubernetes, 'is_2xx',
                 return_value=k8s_api_returned_2xx)

    if k8s_api_returned_2xx:
        await Kubernetes.create_ingress(ingress_name, app, expose,
                                        container_name,
                                        hostname)
    else:
        with pytest.raises(K8sError):
            await Kubernetes.create_ingress(ingress_name, app, expose,
                                            container_name,
                                            hostname)
        return

    if resource_exists:
        Kubernetes.make_k8s_call.mock.assert_not_called()
    else:
        prefix = Kubernetes._get_api_path_prefix('ingresses')
        prefix = f'{prefix}/{app.app_id}/ingresses'
        Kubernetes.make_k8s_call.mock.assert_called_with(
            app.config, app.logger, prefix, payload=expected_payload)

        Kubernetes.is_2xx.assert_called_with(314)


@mark.asyncio
@mark.parametrize('image_pull_policy', ['Always', 'IfNotPresent'])
async def test_create_deployment(patch, async_mock, story, image_pull_policy):
    container_name = 'asyncy--alpine-1'
    story.app.app_id = 'my_app'
    patch.object(story.app, 'image_pull_policy', return_value=image_pull_policy)
    image = 'alpine:latest'

    env = {'token': 'asyncy-19920', 'username': 'asyncy'}
    start_command = ['/bin/bash', 'sleep', '10000']
    shutdown_command = ['wall', 'Shutdown']

    volumes = [Volume(persist=False, name='tmp', mount_path='/tmp'),
               Volume(persist=True, name='db', mount_path='/db')]

    container_configs = [
        ContainerConfig(name='first', data={
            'auths': {
                'https://index.docker.io/v1/': {
                    'auth': 'username_password_base64'
                }
            }
        }),
        ContainerConfig(name='second', data={
            'auths': {
                'https://index.docker.io/v1/': {
                    'auth': 'new_username_password_base64'
                }
            }
        })
    ]

    liveness_probe = {
        'httpGet': {
            'path': '/healthz',
            'port': 8000
        },
        'initialDelaySeconds': 10,
        'timeoutSeconds': 30,
        'periodSeconds': 30,
        'successThreshold': 1,
        'failureThreshold': 5
    }

    patch.object(Kubernetes, 'remove_volume', new=async_mock())
    patch.object(Kubernetes, 'create_volume', new=async_mock())
    patch.object(Kubernetes, 'create_imagepullsecret', new=async_mock())
    patch.object(Kubernetes, 'get_liveness_probe', return_value=liveness_probe)

    b16_service_name = base64.b16encode('alpine'.encode()).decode()

    expected_payload = {
        'apiVersion': 'apps/v1',
        'kind': 'Deployment',
        'metadata': {
            'name': container_name,
            'namespace': story.app.app_id
        },
        'spec': {
            'replicas': 1,
            'strategy': {
                'type': 'RollingUpdate'
            },
            'selector': {
                'matchLabels': {
                    'app': container_name
                }
            },
            'template': {
                'metadata': {
                    'labels': {
                        'app': container_name,
                        'logstash-enabled': 'true',
                        'b16-service-name': b16_service_name
                    }
                },
                'spec': {
                    'containers': [
                        {
                            'name': container_name,
                            'image': image,
                            'resources': {
                                'limits': {
                                    'memory': '200Mi'
                                    # 'cpu': '500m'
                                }
                            },
                            'command': start_command,
                            'imagePullPolicy': image_pull_policy,
                            'env': [{'name': 'token', 'value': 'asyncy-19920'},
                                    {'name': 'username', 'value': 'asyncy'}],
                            'lifecycle': {
                                'preStop': {
                                    'exec': {
                                        'command': shutdown_command
                                    }
                                }
                            },
                            'volumeMounts': [
                                {
                                    'mountPath': volumes[0].mount_path,
                                    'name': volumes[0].name
                                },
                                {
                                    'mountPath': volumes[1].mount_path,
                                    'name': volumes[1].name
                                }
                            ],
                            'livenessProbe': liveness_probe
                        }
                    ],
                    'volumes': [
                        {
                            'name': volumes[0].name,
                            'persistentVolumeClaim': {
                                'claimName': volumes[0].name
                            }
                        },
                        {
                            'name': volumes[1].name,
                            'persistentVolumeClaim': {
                                'claimName': volumes[1].name
                            }
                        }
                    ],
                    'imagePullSecrets': [
                        {
                            'name': container_configs[0].name
                        },
                        {
                            'name': container_configs[1].name
                        }
                    ]
                }
            }
        }
    }

    patch.object(asyncio, 'sleep', new=async_mock())
    patch.object(Kubernetes, 'check_for_image_errors', new=async_mock())

    expected_create_path = f'/apis/apps/v1/namespaces/' \
                           f'{story.app.app_id}/deployments'
    expected_verify_path = f'/apis/apps/v1/namespaces/{story.app.app_id}' \
                           f'/deployments/{container_name}'

    patch.object(Kubernetes, 'make_k8s_call', new=async_mock(side_effect=[
        _create_response(404),
        _create_response(201),
        _create_response(200, {'status': {'readyReplicas': 0}}),
        _create_response(200, {'status': {'readyReplicas': 0}}),
        _create_response(200, {'status': {'readyReplicas': 1}})
    ]))

    await Kubernetes.create_deployment(story.app, 'alpine', image,
                                       container_name,
                                       start_command, shutdown_command, env,
                                       volumes, container_configs)

    Kubernetes.remove_volume.mock.assert_called_once()
    Kubernetes.remove_volume.mock.assert_called_with(
        story.app, volumes[0].name)

    assert Kubernetes.create_volume.mock.mock_calls == [
        mock.call(story.app, volumes[0].name, volumes[0].persist),
        mock.call(story.app, volumes[1].name, volumes[1].persist)
    ]

    assert Kubernetes.create_imagepullsecret.mock.mock_calls == [
        mock.call(story.app, container_configs[0]),
        mock.call(story.app, container_configs[1]),
    ]

    assert Kubernetes.make_k8s_call.mock.mock_calls == [
        mock.call(story.app.config, story.app.logger,
                  expected_create_path, expected_payload),
        mock.call(story.app.config, story.app.logger,
                  expected_create_path, expected_payload),
        mock.call(story.app.config, story.app.logger, expected_verify_path),
        mock.call(story.app.config, story.app.logger, expected_verify_path),
        mock.call(story.app.config, story.app.logger, expected_verify_path)
    ]

@mark.parametrize('unavailable', [True, False])
@mark.asyncio
async def test_wait_for_port(patch, magic, async_mock, unavailable):
    fut = magic()
    patch.object(asyncio, 'open_connection', return_value=fut)

    def exc(a, timeout=0):
        raise ConnectionRefusedError()

    if unavailable:
        patch.object(asyncio, 'wait_for', new=async_mock(side_effect=exc))
    else:
        patch.object(asyncio, 'wait_for', new=async_mock())

    patch.object(asyncio, 'sleep', new=async_mock())

    ret = await Kubernetes.wait_for_port('asyncy.com', 80)

    asyncio.wait_for.mock.assert_called_with(fut, timeout=2)

    if unavailable:
        assert ret is False
    else:
        assert ret is True


@mark.asyncio
async def test_create_service(patch, story, async_mock):
    container_name = 'asyncy--alpine-1'
    line = {
        LineConstants.service: 'alpine'
    }
    patch.object(Kubernetes, 'find_all_ports', return_value={10, 20, 30})
    patch.object(Kubernetes, 'raise_if_not_2xx')
    patch.object(Kubernetes, 'get_hostname', return_value=container_name)
    patch.object(Kubernetes, 'make_k8s_call', new=async_mock())
    patch.object(Kubernetes, 'wait_for_port',
                 new=async_mock(return_value=True))
    patch.object(asyncio, 'sleep', new=async_mock())
    story.app.app_id = 'my_app'

    expected_payload = {
        'apiVersion': 'v1',
        'kind': 'Service',
        'metadata': {
            'name': container_name,
            'namespace': story.app.app_id,
            'labels': {
                'app': container_name
            }
        },
        'spec': {
            'ports': [
                {'port': 10, 'protocol': 'TCP', 'targetPort': 10},
                {'port': 20, 'protocol': 'TCP', 'targetPort': 20},
                {'port': 30, 'protocol': 'TCP', 'targetPort': 30}
            ],
            'selector': {
                'app': container_name
            }
        }
    }

    expected_path = f'/api/v1/namespaces/{story.app.app_id}/services'
    await Kubernetes.create_service(story.app, line[LineConstants.service],
                                    container_name)
    Kubernetes.make_k8s_call.mock.assert_called_with(
        story.app.config, story.app.logger, expected_path, expected_payload)

    Kubernetes.raise_if_not_2xx.assert_called_with(
        Kubernetes.make_k8s_call.mock.return_value)
    assert Kubernetes.wait_for_port.mock.mock_calls == [
        mock.call(container_name, 10),
        mock.call(container_name, 20),
        mock.call(container_name, 30)
    ]


@mark.asyncio
async def test_check_for_image_errors(patch, app, async_mock):

    container_name = 'my_container'
    app.app_id = 'my_app'

    patch.object(Kubernetes, 'make_k8s_call', new=async_mock(side_effect=[
        _create_response(200, {
            'items': [{
                'status': {
                    'containerStatuses': [{
                        'image': 'test',
                        'state': {
                            'waiting': {
                                'reason': 'ContainerCreating'
                            }
                        }
                    }]
                }
            }]
        }),
        _create_response(200, {
            'items': [{
                'status': {
                    'containerStatuses': [{
                        'image': 'test',
                        'state': {
                            'waiting': {
                                'reason': 'ImagePullBackOff'
                            }
                        }
                    }]
                }
            }]
        }),
    ]))

    await Kubernetes.check_for_image_errors(app, container_name)
    with pytest.raises(K8sError) as exc:
        await Kubernetes.check_for_image_errors(app, container_name)
    assert exc.value.message == 'ImagePullBackOff - Failed to pull image test'

    prefix = Kubernetes._get_api_path_prefix('pods')
    qs = urllib.parse.urlencode({
        'labelSelector': f'app={container_name}'
    })
    Kubernetes.make_k8s_call.mock.assert_called()
    Kubernetes.make_k8s_call.mock.assert_called_with(app.config, app.logger,
                                                     f'{prefix}/{app.app_id}'
                                                     f'/pods?{qs}')


@mark.parametrize('service', [{
    'name': 'first',
    'configuration': {},
    'liveness_probe': None
}, {
    'name': 'second',
    'configuration': {
        'health': {
            'http': {
                'method': 'get',
                'path': '/healthz',
                'port': 8000
            }
        }
    },
    'liveness_probe': {
        'httpGet': {
            'path': '/healthz',
            'port': 8000
        },
        'initialDelaySeconds': 10,
        'timeoutSeconds': 30,
        'periodSeconds': 30,
        'successThreshold': 1,
        'failureThreshold': 5
    }
}])
def test_get_liveness_probe(app, service):
    app.services = {
        service['name']: {
            'configuration': service['configuration']
        }
    }
    liveness_probe = Kubernetes.get_liveness_probe(app, service['name'])
    assert liveness_probe == service['liveness_probe']


def test_is_2xx():
    res = MagicMock()
    res.code = 200
    assert Kubernetes.is_2xx(res) is True
    res.code = 210
    assert Kubernetes.is_2xx(res) is True
    res.code = 300
    assert Kubernetes.is_2xx(res) is False
    res.code = 400
    assert Kubernetes.is_2xx(res) is False
