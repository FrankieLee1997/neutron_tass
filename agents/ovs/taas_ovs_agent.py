from neutron import manager
from neutron.common import topics
from neutron.openstack.common import rpc
from neutron.openstack.common.rpc import dispatcher
from neutron.agent.linux import interface
from neutron.openstack.common.rpc import proxy
from neutron.agent.neutron_tass.agents import tcpdump
from neutron.agent.neutron_tass.services.taas.drivers.linux import ovs_constants as taas_ovs_consts
from neutron.agent.linux import ip_lib
from neutron.agent.linux import utils

from neutron.openstack.common import log as logging
LOG = logging.getLogger(__name__)

def getInterfaceDriver(*args, **kwargs):
    return interface.OVSInterfaceDriver(*args, **kwargs)

class TaasAgentRpcCallbackMinxin(object):

    def consume_api(self, agent_api):
        self.agent_api = agent_api

class TaasOvsAgentRpcDispatcher(dispatcher.RpcDispatcher):
    def __init__(self, callbacks):
        super(TaasOvsAgentRpcDispatcher, self).__init__(callbacks)

class TaasOvsPluginApi(proxy.RpcProxy):

    BASE_RPC_API_VERSION = '1.0'

    def __init__(self, topic=topics.TAAS_AGENT):
        super(TaasOvsPluginApi, self).__init__(topic=topic,
            default_version=self.BASE_RPC_API_VERSION)

    def delete_port_after(self, context, port_id):
        self.cast(context, self.make_msg('delete_port_after',
                                         port_id=port_id))

class TaasOvsAgentRpcCallback(TaasAgentRpcCallbackMinxin):

    def __init__(self, conf, driver_type):
        self.conf = conf
        self.driver_type = driver_type
        self.process = None
        self.interface_driver = interface.OVSInterfaceDriver(self.conf)

    def initialize(self):
        self.taas_driver = manager.NeutronManager.load_class_for_provider(
            'neutron_taas.taas.agent.drivers', self.driver_type)(self.conf)
        self.taas_driver.consume_api(self.agent_api)
        self.taas_driver.initialize()
        self.taas_rpc_setup()
        LOG.info("taas ovs agent start")


    def taas_rpc_setup(self):

        self.taas_plugin_rpc = TaasOvsPluginApi(topics.TAAS_PLUGIN)

        self.topic = topics.TAAS_AGENT
        self.conn = rpc.create_connection(new=True)
        self.dispatcher = TaasOvsAgentRpcDispatcher([self])
        self.conn.create_consumer(self.topic, self.dispatcher,
                                  fanout=False, queue_name=self._get_queue_name(self.topic))
        self.conn.consume_in_thread()


    def _get_queue_name(self, topic):
        return "%s-%s" % (self.conf.host, topic)

    def _invoke_driver_api(self, args, func_name):
        try:
            self.taas_driver.__getattribute__(func_name)(args)
        except Exception as e :
            LOG.error(e, exc_info=True)

    def plug_new_device(self, port, interface_name, prefix=None):
        self.interface_driver.plug(port['network_id'],
                         port['id'],
                         interface_name,
                         port['mac_address'],
                         bridge="br-int",
                         prefix=prefix,
                         mtu=port.get('mtu'))
        ip_cidrs = utils.fixed_ip_cidrs(port["fixed_ips"])
        self.interface_driver.init_router_port(
            interface_name,
            ip_cidrs,
            namespace=None)

    def unplug_device(self, port, interface_name, prefix=None):
        self.interface_driver.unplug(interface_name,
                                     bridge="br-int")

    def _is_device_exists(self, device_name):
        return ip_lib.IPDevice(device_name).exists()

    def create_tap_service(self, context, tap_service, host):

        LOG.info('Agent create_tap_service, %s %s' % (tap_service, host))
        if host != self.conf.host:
            return

        port = tap_service['port']
        # port_id = port["id"]

        device_name = taas_ovs_consts.TAP_SERVICE_PREFIX + tap_service["tap_service"]["name"][:9]
        if not self._is_device_exists(device_name):
            self.plug_new_device(port, device_name,
                                 taas_ovs_consts.TAP_SERVICE_PREFIX)

        self._invoke_driver_api(tap_service, 'create_tap_service')

        self.process = tcpdump.Tcpdump.get_process_by_tap_service(tap_service)
        self.process.start()

    def delete_tap_service(self, context, tap_service, host):

        LOG.info('Agent delete_tap_service, %s %s' % (tap_service, host))
        if host != self.conf.host:
            return

        self._invoke_driver_api(tap_service, 'delete_tap_service')

        try:
            self.process.stop()
        except Exception as e:
            LOG.info("tcpdump process stopped failed, %s" % e)

        port = tap_service['port']
        port_id = port["id"]
        # device_name = taas_ovs_consts.TAP_SERVICE_PREFIX + port_id[:8]
        device_name = taas_ovs_consts.TAP_SERVICE_PREFIX + tap_service["tap_service"]["name"][:9]
        if self._is_device_exists(device_name):
            self.unplug_device(port, device_name,
                                 taas_ovs_consts.TAP_SERVICE_PREFIX)
            self.taas_plugin_rpc.delete_port_after(context, port_id)

    def create_tap_flow(self, context, tap_flow, host):

        LOG.info('Agent create_tap_flow, %s %s' % (tap_flow, host))

        if host != self.conf.host:
            return
        self._invoke_driver_api(tap_flow, 'create_tap_flow')

    def delete_tap_flow(self, context, tap_flow, host):

        LOG.info('Agent delete_tap_flow %s %s' % (tap_flow, host))

        if host != self.conf.host:
            return
        self._invoke_driver_api(tap_flow, 'delete_tap_flow')