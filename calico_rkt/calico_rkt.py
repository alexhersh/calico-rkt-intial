#!/usr/bin/env python
from __future__ import print_function
import socket
from netaddr import IPAddress

from pycalico import datastore, netns
import functools
import json
import os
import sys
from subprocess import check_output, CalledProcessError
from pycalico.datastore_datatypes import Rules
from pycalico.netns import Namespace

print_stderr = functools.partial(print, file=sys.stderr)

# Append to existing env, to avoid losing PATH etc.
# TODO-PAT: This shouldn't be hardcoded
# env = os.environ.copy()
# env['ETCD_AUTHORITY'] = 'localhost:2379'

# ETCD_AUTHORITY_ENV = "ETCD_AUTHORITY"
# PROFILE_LABEL = 'CALICO_PROFILE'
# ETCD_PROFILE_PATH = '/calico/'
RKT_ORCHESTRATOR = 'rkt'

def main():
    print_stderr('Args: ', sys.argv)
    print_stderr('Env: ', os.environ)
    input_ = ''.join(sys.stdin.readlines()).replace('\n', '')
    print_stderr('Input: ', input_)
    input_json = json.loads(input_)

    mode = os.environ['CNI_COMMAND']

    if mode == 'init':
        print_stderr('No initialization work to perform')
    elif mode == 'ADD':
        print_stderr('Executing Calico pod-creation plugin')
        NetworkPlugin().add()
    elif mode == 'teardown':
        print_stderr('No pod-deletion work to perform')

class NetworkPlugin(object):

    def __init__(self):
        self._datastore_client = datastore.DatastoreClient()
        self.pod_id=os.environ['CNI_CONTAINERID']
        self.netns_path=os.environ['CNI_NETNS']
        self.interface=os.environ['CNI_IFNAME']
        self.ip='192.168.0.111'

    def add(self):
        """"Handle rkt pod-add event."""
        print_stderr('Configuring pod %s' % self.pod_id, file=sys.stderr)

        try:
            endpoint = self._create_calico_endpoint()
            self._create_profile(endpoint=endpoint, profile_name=self.pod_id)
        except CalledProcessError as e:
            print_stderr('Error code %d creating pod networking: %s\n%s' % (
                e.returncode, e.output, e))
            sys.exit(1)

    def _create_calico_endpoint(self):
        """Configure the Calico interface for a pod."""
        print_stderr('Configuring Calico networking.', file=sys.stderr)
        endpoint = self._datastore_client.create_endpoint(socket.gethostname(), RKT_ORCHESTRATOR,
                                          self.pod_id, [IPAddress(self.ip)])
        endpoint.provision_veth(Namespace(self.netns_path), self.interface)
        self._datastore_client.set_endpoint(endpoint)
        print_stderr('Finished configuring network interface', file=sys.stderr)
        return endpoint

    def _create_profile(self, endpoint, profile_name):
        """
        Configure the calico profile for a pod.

        Currently assumes one pod with each name.
        """
        print_stderr('Configuring Pod Profile: %s' % profile_name)

        if self._datastore_client.profile_exists(profile_name):
            print_stderr("Error: Profile with name %s already exists, exiting." % profile_name)
            sys.exit(1)

        rules = self._create_rules(profile_name)
        self._datastore_client.create_profile(profile_name, rules)

        # Also set the profile for the workload.
        print_stderr('Setting profile %s on endpoint %s' %
                     (profile_name, endpoint.endpoint_id))
        self._datastore_client.set_profiles_on_endpoint(
            profile_name, endpoint_id=endpoint.endpoint_id
        )
        print_stderr('Finished configuring profile.')
        print(json.dumps(
            {
                'ip4': {
                    'ip': '%s/24' % self.ip
                }
            }))

    def _create_rules(self, id_):
        rules_dict = {
            'id': id_,
            'inbound_rules': [
                {
                    'action': 'allow',
                },
            ],
            'outbound_rules': [
                {
                    'action': 'allow',
                },
            ],
        }
        rules_json = json.dumps(rules_dict, indent=2)
        rules = Rules.from_json(rules_json)
        return rules

if __name__ == '__main__':
    main()
