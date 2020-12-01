from neutron.plugins.openvswitch.agent import ovs_lib

class TaasOVSAgent(object):

    def __init__(self):

        self.root_helper = "sudo"

    def get_bridge(self, br_name):
        pass

    def initialize(self, br_name):
        self.br_tap = ovs_lib.OVSBridge(br_name, self.root_helper)
        self.br_tap.create()