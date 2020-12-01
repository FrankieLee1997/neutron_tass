from neutron.agent.neutron_tass.agents.ovs import taas_ovs_agent
from oslo_config import cfg

class TaasAgentExtension(object):

    def initialize(self, driver_type):
        """Initialize agent extension."""
        self.taas_agent = taas_ovs_agent.TaasOvsAgentRpcCallback(
            cfg.CONF, driver_type)
        self.taas_agent.consume_api(self.agent_api)
        self.taas_agent.initialize()

    def consume_api(self, agent_api):
        self.agent_api = agent_api