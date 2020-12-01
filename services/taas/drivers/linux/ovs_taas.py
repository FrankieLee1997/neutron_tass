# coding=utf-8
from neutron.plugins.openvswitch.agent import ovs_lib
from neutron.agent.neutron_tass.services.taas.drivers.linux import ovs_constants as taas_ovs_consts
from neutron.openstack.common import shellutils
from neutron.openstack.common import log as logging
LOG = logging.getLogger(__name__)


class OvsTaasDriver(object):
    def __init__(self, conf):
        self.agent_conf = conf
        self.root_helper = conf.AGENT.root_helper

    def initialize(self):
        self.int_br = self.agent_api.request_int_br()
        self.tun_br = self.agent_api.request_tun_br()
        self.tap_br = ovs_lib.OVSBridge('br-tap', self.root_helper)
        self.setup_ovs_bridges()


    def consume_api(self, agent_api):
        self.agent_api = agent_api

    def setup_ovs_bridges(self):

        self.tap_br.create()
        self.int_br.add_patch_port('patch-int-tap', 'patch-tap-int')
        self.tap_br.add_patch_port('patch-tap-int', 'patch-int-tap')
        self.tun_br.add_patch_port('patch-tun-tap', 'patch-tap-tun')
        self.tap_br.add_patch_port('patch-tap-tun', 'patch-tun-tap')

        patch_tap_int_id = self.tap_br.get_port_ofport('patch-tap-int')
        patch_tap_tun_id = self.tap_br.get_port_ofport('patch-tap-tun')
        patch_tun_tap_id = self.tun_br.get_port_ofport('patch-tun-tap')

        self.tap_br.delete_flows(table=0)
        self.tap_br.delete_flows(table=taas_ovs_consts.TAAS_RECV_LOC)
        self.tap_br.delete_flows(table=taas_ovs_consts.TAAS_RECV_REM)

        self.tun_br.delete_flows(table=0, in_port=patch_tun_tap_id)
        self.tun_br.delete_flows(table=taas_ovs_consts.TAAS_SEND_UCAST)
        self.tun_br.delete_flows(table=taas_ovs_consts.TAAS_SEND_FLOOD)
        self.tun_br.delete_flows(table=taas_ovs_consts.TAAS_CLASSIFY)
        self.tun_br.delete_flows(table=taas_ovs_consts.TAAS_DST_CHECK)
        self.tun_br.delete_flows(table=taas_ovs_consts.TAAS_SRC_CHECK)
        self.tun_br.delete_flows(table=taas_ovs_consts.TAAS_DST_RESPOND)
        self.tun_br.delete_flows(table=taas_ovs_consts.TAAS_SRC_RESPOND)

        self.tap_br.add_flow(table=0,
                             priority=1,
                             in_port=patch_tap_int_id,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_RECV_LOC)

        self.tap_br.add_flow(table=0,
                             priority=1,
                             in_port=patch_tap_tun_id,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_RECV_REM)

        self.tap_br.add_flow(table=0,
                             priority=0,
                             actions="drop")

        self.tap_br.add_flow(table=taas_ovs_consts.TAAS_RECV_LOC,
                             priority=0,
                             actions="drop")

        self.tap_br.add_flow(table=taas_ovs_consts.TAAS_RECV_REM,
                             priority=0,
                             actions="drop")


        # Configure standard Taas flows in br-tun,
        # 来自tap的镜像流量，单播出去
        self.tun_br.add_flow(table=0,
                             priority=1,
                             in_port=patch_tun_tap_id,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_SEND_UCAST)
        # 在学习到相对应的单播表项之前，先广播泛洪出去
        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_SEND_UCAST,
                             priority=0,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_SEND_FLOOD)

        # 添加泛洪列表
        flow_action = self._create_tunnel_flood_flow_action()
        if flow_action != "":
            self.tun_br.add_flow(table=taas_ovs_consts.TAAS_SEND_FLOOD,
                                 priority=0,
                                 actions=flow_action)

        # 对于接受到的单播报文
        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_CLASSIFY,
                             priority=2,
                             reg0=0,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_DST_CHECK)
        # 泛洪广播报文
        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_CLASSIFY,
                             priority=1,
                             reg0=1,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_DST_CHECK)
        # 泛洪反射报文
        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_CLASSIFY,
                             priority=1,
                             reg0=2,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_SRC_CHECK)

        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_DST_CHECK,
                             priority=0,
                             actions="drop")

        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_SRC_CHECK,
                             priority=0,
                             actions="drop")

        # 下面这两段代码本来可以放到创建tap-service里面再执行，不过为了删除与更新的方便，只是
        # 把所有动态添加的代码放到了tap-service里面


        # 对于单播报文，传送到tap上
        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_DST_RESPOND,
                             priority=2,
                             reg0=0,
                             actions="output:%s" % str(patch_tun_tap_id))
        # 对于泛洪广播报文,送一份给br-tap,然后再反射一份回去，目的是让发出洪泛报文的主机学习本台主机的mac地址
        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_DST_RESPOND,
                             priority=1,
                             reg0=1,
                             actions=(
                                 "output:%s,"
                                 "move:NXM_OF_VLAN_TCI[0..11]->NXM_NX_TUN_ID"
                                 "[0..11],mod_vlan_vid:2,output:in_port" %
                                 str(patch_tun_tap_id)))


        # 这一段代码本来可以放到创建tap-flow里面再执行，不过为了删除与更新的方便，只是
        # 把所有动态添加的代码放到了tap-flow里面
        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_SRC_RESPOND,
                             priority=1,
                             actions=(
                                 "learn(table=%s,hard_timeout=60,"
                                 "priority=1,NXM_OF_VLAN_TCI[0..11],"
                                 "load:NXM_OF_VLAN_TCI[0..11]->NXM_NX_TUN_ID"
                                 "[0..11],load:0->NXM_OF_VLAN_TCI[0..11],"
                                 "output:NXM_OF_IN_PORT[])" %
                                 taas_ovs_consts.TAAS_SEND_UCAST))

    def create_tap_service(self, tap_service):
        taas_id = tap_service['taas_id']
        port = tap_service['port']
        try:
            ovs_port = self.int_br.get_vif_port_by_id(port['id'])
            ovs_port_id = ovs_port.ofport
            if str(ovs_port_id) == "-1":
                return

        except Exception as e:
            raise e

        patch_int_tap_id = self.int_br.get_port_ofport('patch-int-tap')
        patch_tap_int_id = self.tap_br.get_port_ofport('patch-tap-int')

        self.int_br.add_flow(table=0,
                             priority=25,
                             in_port=patch_int_tap_id,
                             dl_vlan=taas_id,
                             actions="output:%s" % ovs_port_id)

        # Add flow(s) in br-tap
        self.tap_br.add_flow(table=taas_ovs_consts.TAAS_RECV_LOC,
                             priority=1,
                             dl_vlan=taas_id,
                             actions="output:in_port")

        self.tap_br.add_flow(table=taas_ovs_consts.TAAS_RECV_REM,
                             priority=1,
                             dl_vlan=taas_id,
                             actions="output:%s" % patch_tap_int_id)

        # Add flow(s) in br-tun
        # for tunnel_type in taas_ovs_consts.TUNNEL_NETWORK_TYPES:
        self.tun_br.add_flow(table=0,
                             priority=10,
                             tun_id=taas_id,
                             actions=(
                                 "move:NXM_OF_VLAN_TCI[0..11]->"
                                 "NXM_NX_REG0[0..11],move:NXM_NX_TUN_ID"
                                 "[0..11]->NXM_OF_VLAN_TCI[0..11],"
                                 "resubmit(,%s)" %
                                 taas_ovs_consts.TAAS_CLASSIFY))

        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_DST_CHECK,
                             priority=1,
                             tun_id=taas_id,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_DST_RESPOND)


    def delete_tap_service(self, tap_service):

        taas_id = tap_service['taas_id']
        patch_int_tap_id = self.int_br.get_port_ofport('patch-int-tap')

        self.int_br.delete_flows(table=0,
                                 in_port=patch_int_tap_id,
                                 dl_vlan=taas_id)

        self.tap_br.delete_flows(table=taas_ovs_consts.TAAS_RECV_LOC,
                                 dl_vlan=taas_id)
        self.tap_br.delete_flows(table=taas_ovs_consts.TAAS_RECV_REM,
                                 dl_vlan=taas_id)

        for tunnel_type in taas_ovs_consts.TUNNEL_NETWORK_TYPES:
            self.tun_br.delete_flows(table=taas_ovs_consts.TUN_TABLE[tunnel_type],
                                     tun_id=taas_id)

        self.tun_br.delete_flows(table=taas_ovs_consts.TAAS_DST_CHECK,
                                 tun_id=taas_id)


    def create_tap_flow(self, tap_flow):
        taas_id = tap_flow['taas_id']
        port = tap_flow['port']
        direction = tap_flow['tap_flow']['direction']

        ovs_port = self.int_br.get_vif_port_by_id(port['id'])
        ovs_port_id = ovs_port.ofport
        patch_int_tap_id = self.int_br.get_port_ofport('patch-int-tap')
        patch_tap_tun_id = self.tun_br.get_port_ofport('patch-tap-tun')

        if direction == 'OUT' or direction == 'BOTH':
            self.int_br.add_flow(table=0,
                                 priority=20,
                                 in_port=ovs_port_id,
                                 actions="normal,mod_vlan_vid:%s,output:%s" %
                                 (str(taas_id), str(patch_int_tap_id)))

        if direction == 'IN' or direction == 'BOTH':
            port_mac = tap_flow['port_mac']
            self.int_br.add_flow(table=0,
                                 priority=20,
                                 dl_dst=port_mac,
                                 actions="normal,mod_vlan_vid:%s,output:%s" %
                                 (str(taas_id), str(patch_int_tap_id)))

        self.tap_br.add_flow(table=taas_ovs_consts.TAAS_RECV_LOC,
                             priority=0,
                             actions="output:%s" % str(patch_tap_tun_id))


        for tunnel_type in taas_ovs_consts.TUNNEL_NETWORK_TYPES:
            self.tun_br.add_flow(table=taas_ovs_consts.TUN_TABLE[tunnel_type],
                                 priority=25,
                                 tun_id=taas_id,
                                 actions=(
                                     "move:NXM_OF_VLAN_TCI[0..11]->"
                                     "NXM_NX_REG0[0..11],move:NXM_NX_TUN_ID"
                                     "[0..11]->NXM_OF_VLAN_TCI[0..11],"
                                     "resubmit(,%s)" %
                                     taas_ovs_consts.TAAS_CLASSIFY))


    def delete_tap_flow(self, tap_flow):
        port = tap_flow['port']
        taas_id = tap_flow['taas_id']
        direction = tap_flow['tap_flow']['direction']

        # Get OVS port id for tap flow port
        ovs_port = self.int_br.get_vif_port_by_id(port['id'])
        ovs_port_id = ovs_port.ofport

        # Delete flow(s) from br-int
        if direction == 'OUT' or direction == 'BOTH':
            self.int_br.delete_flows(table=0,
                                     in_port=ovs_port_id)

        if direction == 'IN' or direction == 'BOTH':
            port_mac = tap_flow['port_mac']

            self.int_br.delete_flows(table=0, dl_dst=port_mac)

        self.tun_br.add_flow(table=taas_ovs_consts.TAAS_SRC_CHECK,
                             priority=1,
                             tun_id=taas_id,
                             actions="resubmit(,%s)" %
                             taas_ovs_consts.TAAS_SRC_RESPOND)

        self.tap_br.add_flow(table=taas_ovs_consts.TAAS_RECV_LOC,
                             priority=0,
                             actions="drop")

        for tunnel_type in taas_ovs_consts.TUNNEL_NETWORK_TYPES:
            self.tun_br.delete_flows(table=taas_ovs_consts.TUN_TABLE[tunnel_type],
                                     tun_id=taas_id)

    def update_tunnel_flood_flow(self):
        pass

    def _create_tunnel_flood_flow_action(self):
        args = ["ovs-vsctl", "list-ports", "br-tun"]
        res = shellutils.execute(args, root_helper=self.root_helper)
        port_list = res.splitlines()
        flow_action = ""

        for port_name in port_list:
            if port_name != 'patch-int' and port_name != "patch-tun-tap":
                flow_action += ",output:%s" % self.tun_br.get_port_ofport(port_name)

        if flow_action:
            flow_action = "move:NXM_OF_VLAN_TCI[0..11]->NXM_NX_TUN_ID[0..11],"\
                       "mod_vlan_vid:1" + flow_action

        return flow_action


# vlan_vid = 2: 这个表示对于泛洪报文所反射回来的报文
# vlan_vid = 1: 泛洪广播报文
# vlan_vid = 0: 表示单播报文