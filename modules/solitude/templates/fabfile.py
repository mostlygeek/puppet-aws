import os
from functools import partial

from fabric.api import execute, lcd, local, task

from mozawsdeploy import ec2
from mozawsdeploy.fabfile import aws, web


PROJECT_DIR = os.path.normpath(os.path.dirname(__file__))

AMAZON_AMI = 'ami-2a31bf1a'
SUBNET_ID = '<%= subnet_id %>'
ENV = '<%= site %>'

SERVER_TYPES = ['syslog', 'celery', 'sentry', 'rabbitmq', 'graphite']

create_server = partial(aws.create_server, app='solitude', ami=AMAZON_AMI,
                        subnet_id=SUBNET_ID, env=ENV)


@task
def create_web(instance_type='m1.small', count=1):
    """
    args: instance_type, count
    This function will create the "golden master" ami for solitude web servers.
    """

    instances = create_server(server_type='web', instance_type=instance_type,
                              count=count)

    return instances


@task
def create_instance(server_type, instance_type='m1.small'):
    """
    args: server_type, instance_type.
          Valid server_types are listed in SERVER_TYPES
    """
    assert server_type in SERVER_TYPES

    instances = create_server(server_type=server_type,
                              instance_type=instance_type)
    return instances


@task
def create_security_groups(env=ENV):
    """
    This function will create security groups for the specified env
    """
    security_groups = []
    admin = ec2.SecurityGroup('admin',
                              [ec2.SecurityGroupInbound('tcp',
                                                        873, 873, ['web',
                                                                   'web-proxy',
                                                                   'celery']),
                               ec2.SecurityGroupInbound('tcp',
                                                        8140, 8140, ['base'])])

    base = ec2.SecurityGroup('base',
                             [ec2.SecurityGroupInbound('tcp',
                                                       22, 22, ['admin'])])

    rabbit_elb = ec2.SecurityGroup('rabbitmq-elb',
                                   [ec2.SecurityGroupInbound('tcp',
                                                             5672, 5672,
                                                             ['web',
                                                              'admin',
                                                              'celery'])])

    syslog = ec2.SecurityGroup('syslog',
                               [ec2.SecurityGroupInbound('udp',
                                                         514, 514, ['base'])])

    security_groups.append(admin)
    security_groups.append(base)
    security_groups.append(rabbit_elb)
    security_groups.append(syslog)

    security_groups += [ec2.SecurityGroup('celery'),
                        ec2.SecurityGroup('graphite'),
                        ec2.SecurityGroup('graphite-elb'),
                        ec2.SecurityGroup('rabbitmq'),
                        ec2.SecurityGroup('sentry'),
                        ec2.SecurityGroup('sentry-elb'),
                        ec2.SecurityGroup('web-proxy'),
                        ec2.SecurityGroup('web'),
                        ec2.SecurityGroup('web-elb')]

    ec2.create_security_groups(security_groups, 'solitude', env)


@task
def deploy(ref):
    """Deploy a new version"""
    execute(build_release, ref)
    r_id = build_release(ref)
    venv = os.path.join(PROJECT_DIR, 'venv')
    python = os.path.join(venv, 'bin',  'python')
    app = os.path.join(PROJECT_DIR, 'solitude')
    with lcd(app):
        local('%s %s/bin/schematic migrations' % (python, venv))

    instances = create_web(count=4)
    for i in instances:
        i.add_tag('Release', r_id)

    elb_conn = ec2.get_elb_connection()
    elb_conn.register_instances('solitude-%s' % ENV, [i.id for i in instances])
    # TODO: wait for servers to become healthy, tear down old servers


@task
def build_release(ref):
    """Build release. This assumes puppet has placed settings in /settings"""
    def extra(release_dir):
        local('rsync -av %s/aeskeys/ %s/aeskeys/' % (PROJECT_DIR, release_dir))

    r_id = web.build_release('solitude', PROJECT_DIR,
                             repo='git://github.com/mozilla/solitude.git',
                             ref=ref,
                             requirements='requirements/prod.txt',
                             settings_dir='solitude/settings', extra=extra)

    return r_id


@task
def remove_old_releases():
    web.remove_old_releases(PROJECT_DIR, keep=4)
