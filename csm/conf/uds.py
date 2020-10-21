from salt.client import Caller, LocalClient
from pathlib import Path
from pwd import getpwnam
from shutil import copyfile, rmtree
from tempfile import NamedTemporaryFile

import json
import os


class UDSConfigGenerator:
    HAPROXY_BEGIN_UDS = '\n# BEGIN UDS\n'
    HAPROXY_END_UDS = '\n# END UDS\n'
    HAPROXY_UDS_WARNING = """\
# (The following HAproxy configuration entries, as well as the ``# BEGIN UDS``
# and ``# END UDS`` comment lines surrounding it, were automatically generated
# by ``csm_setup``. Please *do not edit these manually*.)
"""
    HAPROXY_CONFIG_PATH = '/etc/haproxy/haproxy.cfg.debug'
    HAPROXY_TEMP_CONFIG_PREFIX = 'haproxy.cfg.'
    UDS_HOME_DIR = '/var/lib/uds'
    UDS_CONFIG_DIR = f'{UDS_HOME_DIR}/.uds'
    UDS_CONFIG_PATH = f'{UDS_CONFIG_DIR}/uds-config.json.debug'
    UDS_USERNAME = 'uds'

    def __init__(self):
        self.salt_caller = Caller()
        self.salt_local_client = LocalClient()

    def get_private_data_ip(self, minion_id):
        return self.salt_caller.cmd(
            'pillar.get', f'cluster:{minion_id}:network:data_nw:pvt_ip_addr')

    def get_all_minions(self):
        minions = list(self.salt_local_client.cmd('*', 'grains.get', ['id']).values())
        assert len(minions) == 2
        minions.sort()
        return minions

    def generate_haproxy_config(self):
        cluster_ip = self.salt_caller.cmd('pillar.get', 'cluster:cluster_ip')
        private_data_ips = list(map(self.get_private_data_ip, self.get_all_minions()))
        return f"""\
frontend uds-frontend
    mode tcp
    bind 127.0.0.1:5000
    bind {cluster_ip}:5000
    acl udsbackendacl dst_port 5000
    use_backend uds-backend if udsbackendacl

backend uds-backend
    mode tcp
    balance static-rr
    server uds-1 {private_data_ips[0]}:5000 check
    server uds-2 {private_data_ips[1]}:5000 check"""

    def generate_uds_config(self):
        local_salt_minion_id = self.salt_caller.cmd('grains.get', 'id')
        local_private_data_ip = self.get_private_data_ip(local_salt_minion_id)
        d = {
            "version": "2.0",
            "service_config": {
                "RESTServer": {
                    "host": f'{local_private_data_ip}',
                },
            },
        }
        body = json.dumps(d, indent=4)
        return body

    @classmethod
    def write_haproxy_config(cls, config, requires_existing_uds_config=False):
        old_umask = os.umask(0o077)
        try:
            with NamedTemporaryFile(prefix=cls.HAPROXY_TEMP_CONFIG_PREFIX, mode='w') as outfile:
                with open(cls.HAPROXY_CONFIG_PATH, 'r') as infile:
                    begin_uds_pos = infile.read().find(cls.HAPROXY_BEGIN_UDS)
                    has_old_config = begin_uds_pos != -1
                    if requires_existing_uds_config and not has_old_config:
                        raise Exception("UDS configuration not found")
                    infile.seek(0)
                    contents_before = infile.read(begin_uds_pos)
                    outfile.write(contents_before)
                    if config is not None:
                        outfile.write(
                            f'{cls.HAPROXY_BEGIN_UDS}'
                            f'{cls.HAPROXY_UDS_WARNING}'
                            f'{config}'
                            f'{cls.HAPROXY_END_UDS}'
                        )
                    if has_old_config:
                        infile.seek(0)
                        end_uds_pos = infile.read().find(cls.HAPROXY_END_UDS)
                        if end_uds_pos < begin_uds_pos:
                            raise RuntimeError("Dangling UDS configuration block")
                        after_uds_pos = end_uds_pos + len(cls.HAPROXY_END_UDS)
                        infile.seek(after_uds_pos)
                        contents_after = infile.read()
                        outfile.write(contents_after)
                    outfile.flush()
                copyfile(outfile.name, infile.name)
        finally:
            os.umask(old_umask)

    def update_haproxy_config(self):
        cls = self.__class__
        config = self.generate_haproxy_config()
        cls.write_haproxy_config(config)

    def remove_haproxy_config(self):
        cls = self.__class__
        cls.write_haproxy_config(None)

    @classmethod
    def write_uds_config(cls, config):
        uds_pwnam = getpwnam(cls.UDS_USERNAME)
        (uds_uid, uds_gid) = uds_pwnam.pw_uid, uds_pwnam.pw_gid
        old_umask = os.umask(0o077)
        try:
            Path(cls.UDS_CONFIG_DIR).mkdir(exist_ok=True)
            os.chown(cls.UDS_CONFIG_DIR, uds_uid, uds_gid)
            with open(cls.UDS_CONFIG_PATH, 'w') as f:
                f.truncate(0)
                f.write(config)
            os.chown(cls.UDS_CONFIG_PATH, uds_uid, uds_gid)
        finally:
            os.umask(old_umask)

    def update_uds_config(self):
        cls = self.__class__
        config = self.generate_uds_config()
        cls.write_uds_config(f'{config}\n')

    def remove_uds_config(self):
        cls = self.__class__
        rmtree(cls.UDS_CONFIG_DIR)

    @staticmethod
    def apply():
        generator = UDSConfigGenerator()
        generator.update_haproxy_config()
        generator.update_uds_config()

    @staticmethod
    def delete():
        generator = UDSConfigGenerator()
        generator.remove_haproxy_config()
        generator.remove_uds_config()


if __name__ == '__main__':
    UDSConfigGenerator.apply()
