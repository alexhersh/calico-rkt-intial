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
from pycalico.util import generate_cali_interface_name, get_host_ips

print_stderr = functools.partial(print, file=sys.stderr)

# Append to existing env, to avoid losing PATH etc.
# TODO-PAT: This shouldn't be hardcoded
# env = os.environ.copy()
# env['ETCD_AUTHORITY'] = 'localhost:2379'

# ETCD_AUTHORITY_ENV = "ETCD_AUTHORITY"
# PROFILE_LABEL = 'CALICO_PROFILE'
# ETCD_PROFILE_PATH = '/calico/'

ORCHESTRATOR_ID = "rkt"
HOSTNAME = socket.gethostname()
NETNS_ROOT= '/var/lib/rkt/pods/run'

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
        NetworkPlugin().create()
    elif mode == 'DELETE':
        print_stderr('Executing Calico pod-deletion plugin')
        NetworkPlugin().delete()

class NetworkPlugin(object):

    def __init__(self):
        self._datastore_client = datastore.DatastoreClient()
        self.pod_id=os.environ['CNI_CONTAINERID']
        self.netns_path='%s/%s/%s' % (NETNS_ROOT, self.pod_id, os.environ['CNI_NETNS'])
        self.interface=os.environ['CNI_IFNAME']
        self.ip='192.168.0.111'

    def create(self):
        """"Handle rkt pod-create event."""
        print_stderr('Configuring pod %s' % self.pod_id, file=sys.stderr)

        try:
            endpoint = self._create_calico_endpoint()
            self._create_profile(endpoint=endpoint, profile_name=self.pod_id)
        except CalledProcessError as e:
            print_stderr('Error code %d creating pod networking: %s\n%s' % (
                e.returncode, e.output, e))
            sys.exit(1)
        print_stderr('Finished Creating pod %s' % self.pod_id)    

    def delete(self):
        """Cleanup after a pod."""
        print_stderr('Deleting pod %s' % self.pod_id, file=sys.stderr)

        # Remove the profile for the workload.
        self._container_remove(HOSTNAME, ORCHESTRATOR_ID)

        # Delete profile
        try:
            self._datastore_client.remove_profile(self.pod_id)
        except:
            print_stderr("Cannot remove profile %s; Profile cannot be found." % self.profile_name)

    def _create_calico_endpoint(self):
        """Configure the Calico interface for a pod."""
        print_stderr('Configuring Calico networking.', file=sys.stderr)
        endpoint = self._datastore_client.create_endpoint(HOSTNAME, ORCHESTRATOR_ID,
                                          self.pod_id, [IPAddress(self.ip)])
        endpoint.provision_veth(Namespace(self.netns_path), self.interface)
        self._datastore_client.set_endpoint(endpoint)
        print_stderr('Finished configuring network interface', file=sys.stderr)
        return endpoint

    def _container_remove(self, hostname, orchestrator_id):
        """
        Remove the indicated container on this host from Calico networking
        """
        # Find the endpoint ID. We need this to find any ACL rules
        try:
            endpoint = self._datastore_client.get_endpoint(
                hostname=hostname,
                orchestrator_id=orchestrator_id,
                workload_id=self.pod_id
            )
        except KeyError:
            print_stderr("Container %s doesn't contain any endpoints" % self.pod_id)
            sys.exit(1)

        # Remove any IP address assignments that this endpoint has
        for net in endpoint.ipv4_nets | endpoint.ipv6_nets:
            assert(net.size == 1)
            self._datastore_client.unassign_address(None, net.ip)

        # Remove the endpoint
        netns.remove_veth(endpoint.name)

        # Remove the container from the datastore.
        self._datastore_client.remove_workload(hostname, orchestrator_id, self.pod_id)

        print_stderr("Removed Calico interface from %s" % self.pod_id)

    def _create_profile(self, endpoint, profile_name):
        """
        Configure the calico profile for a pod.

        Currently assumes one pod with each name.
        """
        print_stderr('Configuring Pod Profile: %s' % profile_name)

        if self._datastore_client.profile_exists(profile_name):
            print_stderr("Error: Profile with name %s already exists, exiting." % profile_name)
            sys.exit(1)

        self._datastore_client.create_profile(profile_name)
        self._apply_rules(profile_name)

        # Also set the profile for the workload.
        print_stderr('Setting profile %s on endpoint %s' %
                     (profile_name, endpoint.endpoint_id))
        self._datastore_client.set_profiles_on_endpoint(
            [profile_name], endpoint_id=endpoint.endpoint_id
        )
        print_stderr("Finished configuring profile.")
        print_stderr(json.dumps(
            {
                "ip4": {
                    "ip": "%s/24" % self.ip
                }
            }))

    def _create_rules(self, id_):
        rules_dict = {
            "id": id_,
            "inbound_rules": [
                {
                    "action": "allow",
                },
            ],
            "outbound_rules": [
                {
                    "action": "allow",
                },
            ],
        }
        rules_json = json.dumps(rules_dict, indent=2)
        rules = Rules.from_json(rules_json)
        return rules

    def _apply_rules(self, profile_name):
        """
        Generate a new profile with the default "allow all" rules.
        :param profile_name: The profile to update
        :type profile_name: string
        :return:
        """
        try:
            profile = self._datastore_client.get_profile(profile_name)
        except:
            print_stderr("Error: Could not apply rules. Profile not found: %s, exiting" % profile_name)
            sys.exit(1)

        profile.rules = self._create_rules(profile_name)
        self._datastore_client.profile_update_rules(profile)
        print_stderr("Finished applying rules.")

if __name__ == '__main__':
    main()
