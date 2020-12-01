from neutron.plugins.openvswitch.agent import async_process
from neutron.openstack.common import log as logging
from neutron.agent.neutron_tass.common import constants as taas_constants
LOG = logging.getLogger(__name__)
import os
from neutron.openstack.common import timeutils

class Tcpdump(object):

    def __init__(self, dev, filename, proto=None):
        self.dev = dev
        self.filename = filename
        self.proto = proto
        self.process = None

    def start(self):
        cmd = ["tcpdump", "-i", self.dev, "ip", "-w", self.filename]
        # cmd = ["tcpdump", "-i", self.dev, "-C", "1", "ip", "-w", self.filename]

        self.process = async_process.AsyncProcess(cmd, run_as_root=True)
        try:
            self.process.start()
        except Exception as e:
            LOG.error("start tcpdump process failed", exc_info=True)

    @classmethod
    def get_process_by_tap_service(cls, tap_service):
        utc_now = str(timeutils.strtime())
        utc_now = utc_now[:19]
        tap_service = tap_service["tap_service"]
        dev_name = taas_constants.TAP_SERVICE_PREFIX + tap_service["name"][:9]
        if not os.path.exists("/var/pcaps/"):
            os.makedirs("/var/pcaps/")
        filename = "/var/pcaps/%s.pcap" % (tap_service["name"] + "_" + utc_now.replace("-", "").replace(":", ""))
        LOG.info("device_name is %s, filename is %s" % (dev_name, filename))
        return cls(dev_name, filename)

    def stop(self):
        self.process.stop()
