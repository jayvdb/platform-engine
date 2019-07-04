# -*- coding: utf-8 -*-
import asyncio
import base64
import json
import ssl
import time
import typing
import urllib.parse
from asyncio import TimeoutError

from tornado.httpclient import AsyncHTTPClient, HTTPResponse

from . import AppConfig
from .AppConfig import Expose
from .Exceptions import K8sError
from .constants.ServiceConstants import ServiceConstants
from .entities.ContainerConfig import ContainerConfig, ContainerConfigs
from .entities.Volume import Volumes
from .utils.Dict import Dict
from .utils.HttpUtils import HttpUtils


class Kubernetes:

    @classmethod
    def is_2xx(cls, res: HTTPResponse):
        return int(res.code / 100) == 2

    @classmethod
    def raise_if_not_2xx(cls, res: HTTPResponse):
        if cls.is_2xx(res):
            return

        path = res.request.url
        raise K8sError(message=f'Failed to call {path}! '
                               f'code={res.code}; body={res.body}; '
                               f'error={res.error}')

    @classmethod
    async def create_ingress(cls, ingress_name, app, expose: Expose,
                             container_name: str, hostname: str):
        if await cls._does_resource_exist(app, 'ingresses', ingress_name):
            app.logger.debug(f'Kubernetes ingress for {expose} exists')
            return

        expose_conf = app.services[expose.service][
            ServiceConstants.config][AppConfig.KEY_EXPOSE][
            expose.service_expose_name]
        http_conf = expose_conf['http']

        payload = {
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

        prefix = cls._get_api_path_prefix('ingresses')
        res = await cls.make_k8s_call(app.config, app.logger,
                                      f'{prefix}/{app.app_id}/ingresses',
                                      payload=payload)

        if not cls.is_2xx(res):
            raise K8sError(
                message=f'Failed to create ingress for expose {expose}!')

        app.logger.debug(f'Kubernetes ingress created')

    @classmethod
    async def create_namespace(cls, app):
        res = await cls.make_k8s_call(app.config, app.logger,
                                      f'/api/v1/namespaces/{app.app_id}')

        if res.code == 200:
            app.logger.debug(f'Kubernetes namespace exists')
            return

        app.logger.debug(f'Kubernetes namespace does not exist')
        payload = {
            'apiVersion': 'v1',
            'kind': 'Namespace',
            'metadata': {
                'name': app.app_id
            }
        }

        res = await cls.make_k8s_call(app.config, app.logger,
                                      '/api/v1/namespaces', payload=payload)

        if not cls.is_2xx(res):
            raise K8sError(message='Failed to create namespace!')

        app.logger.debug(f'Kubernetes namespace created')

    @classmethod
    def new_ssl_context(cls):
        return ssl.SSLContext()

    @classmethod
    async def make_k8s_call(cls, config, logger, path: str,
                            payload: dict = None,
                            method: str = 'get') -> HTTPResponse:
        context = cls.new_ssl_context()

        cert = config.CLUSTER_CERT
        cert = cert.replace('\\n', '\n')
        context.load_verify_locations(cadata=cert)

        kwargs = {
            'ssl_options': context,
            'headers': {
                'Authorization': f'bearer {config.CLUSTER_AUTH_TOKEN}',
                'Content-Type': 'application/json; charset=utf-8'
            },
            'method': method.upper()
        }

        if method.lower() == 'patch':
            kwargs['headers']['Content-Type'] = \
                'application/merge-patch+json; charset=utf-8'

        if payload is not None:
            kwargs['body'] = json.dumps(payload)

            if method == 'get':  # Default value.
                kwargs['method'] = 'POST'

        client = AsyncHTTPClient()
        return await HttpUtils.fetch_with_retry(
            3, logger, f'https://{config.CLUSTER_HOST}{path}',
            client, kwargs)

    @classmethod
    async def remove_volume(cls, app, name):
        await cls._delete_resource(app, 'persistentvolumeclaims', name)

    @classmethod
    async def _does_resource_exist(cls, app, resource, name):
        prefix = cls._get_api_path_prefix(resource)
        path = f'{prefix}/{app.app_id}' \
            f'/{resource}/{name}'

        res = await cls.make_k8s_call(app.config, app.logger, path)
        if res.code == 404:
            return False
        elif res.code == 200:
            return True

        raise K8sError(
            message=f'Failed to check if {resource}/{name} exists! '
            f'Kubernetes API returned {res.code}.')

    @classmethod
    async def _update_volume_label(cls, app, name):
        path = f'/api/v1/namespaces/{app.app_id}/persistentvolumeclaims/{name}'
        payload = {
            'metadata': {
                'labels': {
                    'last_referenced_on': f'{int(time.time())}'
                }
            }
        }
        res = await cls.make_k8s_call(app.config, app.logger,
                                      path, payload, method='patch')
        cls.raise_if_not_2xx(res)
        app.logger.debug(
            f'Updated reference time for volume {name}')

    @classmethod
    async def create_volume(cls, app, name, persist):
        if await cls._does_resource_exist(
                app, 'persistentvolumeclaims', name):
            app.logger.debug(f'Kubernetes volume {name} already exists')
            # Update the last_referenced_on label
            await cls._update_volume_label(app, name)
            return

        path = f'/api/v1/namespaces/{app.app_id}/persistentvolumeclaims'
        payload = {
            'apiVersion': 'v1',
            'kind': 'PersistentVolumeClaim',
            'metadata': {
                'name': name,
                'namespace': app.app_id,
                'labels': {
                    'last_referenced_on': f'{int(time.time())}',
                    'omg_persist': f'{persist}'
                }
            },
            'spec': {
                'accessModes': ['ReadWriteOnce'],
                'resources': {
                    'requests': {
                        'storage': '100Mi'  # For now, during beta.
                    }
                }
            }
        }

        res = await cls.make_k8s_call(app.config, app.logger, path, payload)
        cls.raise_if_not_2xx(res)
        app.logger.debug(f'Created a Kubernetes volume - {name}')

    @classmethod
    def _get_api_path_prefix(cls, resource):
        if resource == 'deployments':
            return '/apis/apps/v1/namespaces'
        elif resource == 'ingresses':
            return '/apis/extensions/v1beta1/namespaces'
        elif resource == 'services' or \
                resource == 'persistentvolumeclaims' or \
                resource == 'pods' or \
                resource == 'secrets':
            return '/api/v1/namespaces'
        else:
            raise Exception(f'Unsupported resource type {resource}')

    @classmethod
    async def _list_resource_names(cls, app, resource) -> typing.List[str]:
        prefix = cls._get_api_path_prefix(resource)
        res = await cls.make_k8s_call(
            app.config, app.logger, f'{prefix}/{app.app_id}/{resource}'
            f'?includeUninitialized=true')

        body = json.loads(res.body, encoding='utf-8')
        out = []

        for i in body['items']:
            out.append(i['metadata']['name'])

        return out

    @classmethod
    async def _delete_resource(cls, app, resource, name):
        """
        Deletes a resource immediately.
        :param app: An instance of App
        :param resource: "services"/"deployments"/etc.
        :param name: The resource name
        """
        prefix = cls._get_api_path_prefix(resource)
        res = await cls.make_k8s_call(
            app.config, app.logger,
            f'{prefix}/{app.app_id}/{resource}/{name}'
            f'?gracePeriodSeconds=0',
            method='delete')

        if res.code == 404:
            app.logger.debug(f'Resource {resource}/{name} not found')
            return

        # Sometimes, the API will throw a 409, indicating that a
        # deletion is in progress. Don't assert that the status code
        # is 2xx in this case.
        if res.code != 409:
            cls.raise_if_not_2xx(res)

        # Wait until the resource has actually been killed.
        while True:
            res = await cls.make_k8s_call(
                app.config, app.logger,
                f'{prefix}/{app.app_id}/{resource}/{name}')

            if res.code == 404:
                break

            app.logger.debug(f'{resource}/{name} is still terminating...')

            await asyncio.sleep(0.7)

        app.logger.debug(f'Deleted {resource}/{name} successfully!')

    @classmethod
    async def clean_namespace(cls, app):
        app.logger.debug(f'Clearing namespace contents...')
        # Things to delete:
        # 1. Services
        # 2. Deployments (should delete all pods internally too)
        # 3. Volumes which are marked with persist as false
        # 4. Ingresses

        for i in await cls._list_resource_names(app, 'services'):
            await cls._delete_resource(app, 'services', i)

        for i in await cls._list_resource_names(app, 'deployments'):
            await cls._delete_resource(app, 'deployments', i)

        for i in await cls._list_resource_names(app, 'pods'):
            await cls._delete_resource(app, 'pods', i)

        for i in await cls._list_resource_names(app, 'ingresses'):
            await cls._delete_resource(app, 'ingresses', i)

        for i in await cls._list_resource_names(app, 'secrets'):
            await cls._delete_resource(app, 'secrets', i)

        # Volumes are not deleted at this moment.
        # See https://github.com/asyncy/platform-engine/issues/189

    @classmethod
    def get_hostname(cls, app, container_name):
        return f'{container_name}.' \
            f'{app.app_id}.svc.cluster.local'

    @classmethod
    def find_all_ports(cls, service_config: dict, inside_http=False) -> set:
        ports = set()
        for key, value in service_config.items():
            if isinstance(value, dict):
                http = key == 'http' or inside_http
                ports.update(cls.find_all_ports(value, inside_http=http))
            elif inside_http and key == 'port':
                assert isinstance(value, int)
                ports.add(value)

        if not inside_http:
            expose = service_config.get('expose', {})
            for name, expose_conf in expose.items():
                expose_port = Dict.find(expose_conf, 'http.port')
                if expose_port is not None:
                    ports.add(expose_port)

        return ports

    @classmethod
    def format_ports(cls, ports: {int}):
        port_list = []
        for port in ports:
            port_list.append({
                'port': port,
                'protocol': 'TCP',
                'targetPort': port
            })
        return port_list

    @classmethod
    async def create_service(cls, app, service: str,
                             container_name: str):
        # Note: We don't check if this service exists because if it did,
        # then we'd not get here. create_pod checks it. During beta, we tie
        # 1:1 between a pod and a service.
        ports = cls.find_all_ports(app.services[service])
        port_list = cls.format_ports(ports)

        payload = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'name': container_name,
                'namespace': app.app_id,
                'labels': {
                    'app': container_name
                }
            },
            'spec': {
                'ports': port_list,
                'selector': {
                    'app': container_name
                }
            }
        }

        path = f'/api/v1/namespaces/{app.app_id}/services'
        res = await cls.make_k8s_call(app.config, app.logger, path, payload)
        cls.raise_if_not_2xx(res)

        # Wait until the ports of the destination pod are open.
        hostname = cls.get_hostname(app, container_name)
        app.logger.info(f'Waiting for ports to open: {ports}')
        for port in ports:
            success = await cls.wait_for_port(hostname, port)
            if not success:
                app.logger.warn(
                    f'Timed out waiting for {hostname}:{port} to open. '
                    f'Some actions of {service} might fail!')

    @classmethod
    async def create_imagepullsecret(cls, app, config: ContainerConfig):

        b64_container_config = base64.b64encode(
            json.dumps(config.data).encode()
        ).decode()

        payload = {
            'apiVersion': 'v1',
            'kind': 'Secret',
            'type': 'kubernetes.io/dockerconfigjson',
            'metadata': {
                'name': config.name,
                'namespace': app.app_id
            },
            'data': {
                '.dockerconfigjson': b64_container_config
            }
        }

        path = f'/api/v1/namespaces/{app.app_id}/secrets'
        res = await cls.make_k8s_call(app.config, app.logger, path, payload)
        if not cls.is_2xx(res):
            raise K8sError(
                message=f'Failed to create imagePullSecret {config["name"]} '
                        f'in namespace {app.app_id}!')

    @classmethod
    async def wait_for_port(cls, host, port):
        attempts = 0
        timeout_secs = 2
        while attempts < 60:  # Max wait time = attempts * timeout_secs = 120
            attempts += 1
            try:
                fut = asyncio.open_connection(host, port)
                await asyncio.wait_for(fut, timeout=timeout_secs)
                return True
            except (TimeoutError, ConnectionRefusedError):
                await asyncio.sleep(timeout_secs)

        return False

    @classmethod
    async def check_for_image_errors(cls, app, container_name):
        # List of image pull errors taken from the kubernetes source code
        # github/kubernetes/kubernetes/blob/master/pkg/kubelet/images/types.go
        image_errors = [
            'ImagePullBackOff',
            'ImageInspectError',
            'ErrImagePull',
            'ErrImageNeverPull',
            'RegistryUnavailable',
            'InvalidImageName'
        ]
        prefix = cls._get_api_path_prefix('pods')
        qs = urllib.parse.urlencode({
            'labelSelector': f'app={container_name}'
        })
        res = await cls.make_k8s_call(app.config, app.logger,
                                      f'{prefix}/{app.app_id}/pods?{qs}')
        cls.raise_if_not_2xx(res)
        body = json.loads(res.body, encoding='utf-8')
        for pod in body['items']:
            for container_status in pod['status'].get('containerStatuses', []):
                is_waiting = Dict.find(container_status,
                                       'state.waiting', False)
                if is_waiting and is_waiting['reason'] in image_errors:
                    raise K8sError(
                        message=f'{is_waiting["reason"]} - '
                        f'Failed to pull image {container_status["image"]}'
                    )

    @classmethod
    def get_liveness_probe(cls, app, service: str):
        """
        livenessProbe: Indicates whether the Container is running.
        If the liveness probe fails, the kubelet kills the Container,
        and the Container is subjected to its restart policy.
        If a Container does not provide a liveness probe,
        the default state is Success.
        """
        omg = app.services[service][ServiceConstants.config]
        health_check = Dict.find(omg, 'health.http')
        if health_check is None:
            return None
        assert health_check.get('method', 'get') == 'get'
        return {
            'httpGet': {
                'path': health_check['path'],
                'port': health_check['port']
            },
            'initialDelaySeconds': 10,
            'timeoutSeconds': 30,
            'periodSeconds': 30,
            'successThreshold': 1,
            'failureThreshold': 5
        }

    @classmethod
    async def create_deployment(cls, app, service_name: str, image: str,
                                container_name: str, start_command: [] or str,
                                shutdown_command: [] or str, env: dict,
                                volumes: Volumes,
                                container_configs: ContainerConfigs):
        # Note: We don't check if this deployment exists because if it did,
        # then we'd not get here. create_pod checks it. During beta, we tie
        # 1:1 between a pod and a deployment.

        env_k8s = []  # Must container {name:'foo', value:'bar'}.

        if env:
            for k, v in env.items():
                if isinstance(v, bool):
                    if v:
                        v = 'true'
                    else:
                        v = 'false'

                v = str(v)  # In case it was a number.
                env_k8s.append({
                    'name': k,
                    'value': v
                })

        volume_mounts = []
        volumes_k8s = []
        for vol in volumes:
            volume_mounts.append({
                'mountPath': vol.mount_path,
                'name': vol.name
            })

            volumes_k8s.append({
                'name': vol.name,
                'persistentVolumeClaim': {
                    'claimName': vol.name
                }
            })

            if not vol.persist:
                await cls.remove_volume(app, vol.name)

            await cls.create_volume(app, vol.name, vol.persist)

        image_pull_secrets = []
        for config in container_configs:
            await cls.create_imagepullsecret(app, config)
            image_pull_secrets.append({
                'name': config.name
            })

        b16_service_name = base64.b16encode(service_name.encode()).decode()

        app.logger.debug(f'imagePullPolicy set to {app.image_pull_policy()}')

        liveness_probe = cls.get_liveness_probe(app, service_name)

        payload = {
            'apiVersion': 'apps/v1',
            'kind': 'Deployment',
            'metadata': {
                'name': container_name,
                'namespace': app.app_id
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
                            'b16-service-name': b16_service_name,
                            'logstash-enabled': 'true'
                        }
                    },
                    'spec': {
                        'containers': [
                            {
                                'name': container_name,
                                'image': image,
                                'resources': {
                                    'limits': {
                                        'memory': '200Mi',  # During beta.
                                        # 'cpu': '500m',  # During beta.
                                    }
                                },
                                'command': start_command,
                                'imagePullPolicy': app.image_pull_policy(),
                                'env': env_k8s,
                                'lifecycle': {
                                },
                                'volumeMounts': volume_mounts
                            }
                        ],
                        'volumes': volumes_k8s,
                        'imagePullSecrets': image_pull_secrets
                    }
                }
            }
        }

        container = payload['spec']['template']['spec']['containers'][0]

        if liveness_probe is not None:
            container['livenessProbe'] = liveness_probe

        if shutdown_command is not None:
            container['lifecycle']['preStop'] = {
                'exec': {
                    'command': shutdown_command
                }
            }

        path = f'/apis/apps/v1/namespaces/{app.app_id}/deployments'

        # When a namespace is created for the first time, K8s needs to perform
        # some sort of preparation. Pods creation fails sporadically for new
        # namespaces. Check the status and retry.
        tries = 0
        res = None
        while tries < 10:
            tries = tries + 1
            res = await cls.make_k8s_call(app.config, app.logger,
                                          path, payload)
            if cls.is_2xx(res):
                break

            app.logger.debug(f'Failed to create deployment, retrying...')
            await asyncio.sleep(1)

        cls.raise_if_not_2xx(res)

        path = f'/apis/apps/v1/namespaces/{app.app_id}' \
            f'/deployments/{container_name}'

        # Wait until the deployment is ready.
        app.logger.debug('Waiting for deployment to be ready...')
        while True:
            res = await cls.make_k8s_call(app.config, app.logger, path)
            cls.raise_if_not_2xx(res)
            body = json.loads(res.body, encoding='utf-8')
            if body['status'].get('readyReplicas', 0) > 0:
                break

            await cls.check_for_image_errors(app, container_name)
            await asyncio.sleep(1)

        app.logger.debug('Deployment is ready')

    @classmethod
    async def create_pod(cls, app, service: str, image: str,
                         container_name: str, start_command: [] or str,
                         shutdown_command: [] or str, env: dict,
                         volumes: Volumes,
                         container_configs: ContainerConfigs):
        res = await cls.make_k8s_call(
            app.config, app.logger,
            f'/apis/apps/v1/namespaces/{app.app_id}'
            f'/deployments/{container_name}')

        if res.code == 200:
            app.logger.debug(f'Deployment {container_name} '
                             f'already exists, reusing')
            return

        await cls.create_deployment(app, service, image, container_name,
                                    start_command, shutdown_command, env,
                                    volumes, container_configs)

        await cls.create_service(app, service, container_name)
