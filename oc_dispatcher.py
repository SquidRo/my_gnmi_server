#
# oc_dispatcher.py
#
# OpenConfig Dispatcher
#

from oc_binding.oc_if_binding import openconfig_interfaces
from oc_binding.oc_lldp_binding import openconfig_lldp
from oc_binding.oc_platform_binding import openconfig_platform
from oc_binding.oc_nwi_binding import openconfig_network_instance
from oc_binding.oc_lr_binding import openconfig_local_routing
from oc_binding.oc_acl_binding import openconfig_acl
from oc_binding.oc_sys_binding import openconfig_system
from oc_binding.oc_qos_binding import openconfig_qos

from pyangbind.lib.xpathhelper import YANGPathHelper
from grpc import StatusCode

from util import util_lldp, util_interface, util_platform, util_utl, \
                 util_nwi, util_lr, util_acl, util_sys, util_qos, util_bcm, \
                 util_sonic

import logging, re, pdb, swsssdk, time

# Dispatch table for openconfig class and info function
ocTable = {
    "interfaces" : { "cls"   : openconfig_interfaces,
                     "info_f": "util_interface.interface_get_info"  },
    "lldp"       : { "cls"   : openconfig_lldp,
                     "info_f": "util_lldp.lldp_get_info"            },
    "components" : { "cls"   : openconfig_platform,
                     "info_f": "util_platform.platform_get_info"    },
    "network-instances" : {
                     "cls"   : openconfig_network_instance,
                     "info_f": "util_nwi.nwi_get_info"              },
    "local-routes" : { "cls" : openconfig_local_routing,
                     "info_f": "util_lr.lr_get_info"                },
    "acl"        : { "cls"   : openconfig_acl,
                     "info_f": "util_acl.acl_get_info"              },
    "system"     : { "cls"   : openconfig_system,
                     "info_f": "util_sys.sys_get_info"              },
    "qos"        : { "cls"   : openconfig_qos,
                     "info_f": "util_qos.qos_get_info"              },
    "sonic"      : { "cls"   : util_sonic.openconfig_sonic,
                     "info_f": "util_sonic.sonic_get_sonic_db_info" },
    "vesta"      : { "cls"   : util_bcm.openconfig_vesta,
                     "info_f": "util_bcm.bcm_get_info"              },
    }

# Dispatch table for registered path and set function
setPathTable = {
    # path : set function
    # [xxx] means key
    '/interfaces/interface[name]/ethernet/config/aggregate-id' :
            "util_interface.interface_set_aggregate_id",
    '/interfaces/interface[name]/config/name' :
            "util_interface.interface_set_cfg_name",
    '/interfaces/interface[name]/config/enabled' :
            "util_interface.interface_set_cfg_enabled",
    '/interfaces/interface[name]/ethernet/switched-vlan/config/trunk-vlans' :
            "util_interface.interface_set_trunk_vlans",
    '/interfaces/interface[name]/ethernet/switched-vlan/config/native-vlan' :
            "util_interface.interface_set_native_vlan",
    '/interfaces/interface[name]/routed-vlan/ipv4/addresses/address[ip]/config' :
            "util_interface.interface_set_ip_v4",
    '/interfaces/interface[name]/routed-vlan/ipv4/neighbors/neighbor[ip]/config' :
            "util_interface.interface_set_nbr_v4",
    '/local-routes/static-routes/static[prefix]/next-hops/next-hop' :
            "util_lr.lr_set_route_v4",
    # multiple keys must be in alphabet order
    '/acl/acl-sets/acl-set[name][type]/config' :
            "util_acl.acl_set_acl_set",
    '/acl/acl-sets/acl-set[name][type]/acl-entries/acl-entry' :
            "util_acl.acl_set_acl_entry",
    '/acl/interfaces/interface[id]/ingress-acl-sets/ingress-acl-set[set-name][type]/config' :
            "util_acl.acl_set_interface",
    '/system/ntp/servers/server[address]/config' :
            "util_sys.sys_set_ntp_server",
    '/sonic' :
            "util_sonic.sonic_set_sonic_db",
    '/network-instances/network-instance[name]/policy-forwarding/interfaces/interface[interface-id]/config' :
            "util_nwi.nwi_pf_set_interface",
    '/network-instances/network-instance[name]/policy-forwarding/policies/policy[policy-id]/config' :
            "util_nwi.nwi_pf_set_policy",
    '/network-instances/network-instance[name]/policy-forwarding/policies/policy[policy-id]/rules/rule' :
            "util_nwi.nwi_pf_set_rule",
    '/vesta/mac' :
            "util_sonic.sonic_set_mac",
    '/vesta/mirror' :
            "util_bcm.bcm_set_port_mirror",
    '/vesta/traffic-seg' :
            "util_bcm.bcm_set_traffic_seg",
    }

class dispArgs: pass

class openconfig_root_dpt_1(object):
    def __init__(self, path_helper):
        path_helper.register([''], self)
        self.data = {}
        for k in ocTable.keys():
            self.data [k] = {}

    def get(self, filter = True):
        return self.data

    def _yang_path(self):
        return '/'

class ocDispatcher:
    """ Open Config Dispatcher that dispatch requests to
        other openconfig binding modules """
    def __init__(self, is_dbg_test):
        self.my_args = dispArgs()
        self.my_args.cfgdb = swsssdk.ConfigDBConnector()
        self.my_args.cfgdb.connect()

        self.my_args.appdb = swsssdk.SonicV2Connector(host='127.0.0.1')
        self.my_args.appdb.connect(self.my_args.appdb.APPL_DB)
        self.my_args.appdb.connect(self.my_args.appdb.COUNTERS_DB)
        self.my_args.appdb.connect(self.my_args.appdb.ASIC_DB)

        # check if port table is ready
        is_pcfg_done = False
        chk_cnt = 0
        while True:
            pcfg_val = self.my_args.appdb.get_all(self.my_args.appdb.APPL_DB, "PORT_TABLE:PortConfigDone")

            is_pcfg_done = pcfg_val != None
            chk_cnt += 1

            if is_pcfg_done or chk_cnt % 3 == 1:
                util_utl.utl_log(
                    "PORT TABLE was%sready...(%s)" % ([" not ", " "][is_pcfg_done], chk_cnt),
                    logging.CRITICAL)

            if is_pcfg_done: break

            time.sleep(10)

        # create the full yang tree
        # for performance, only update the tree node requested
        self.oc_yph = YANGPathHelper()
        for k in ocTable.keys():
            if ocTable[k]["cls"]:
                ocTable[k]["cls"](path_helper = self.oc_yph)

        # create obj for "/" to only return subtree of depth 1
        openconfig_root_dpt_1(self.oc_yph)

        # create all interfaces to speed up processing request for interfaces later
        util_interface.interface_create_all_infs(self.oc_yph, is_dbg_test, self.my_args)

        # create default network instance
        util_nwi.nwi_create_dflt_nwi(self.oc_yph, is_dbg_test)

        # create default objects
        util_qos.qos_create_dflt_obj(self.oc_yph, is_dbg_test)

        # check if new teammgrd is used
        test_cmd = 'docker run --rm=true --privileged=true --entrypoint="/bin/bash" "docker-teamd" -c "[ -f /usr/bin/teammgrd ]"'
        util_interface.IS_NEW_TEAMMGRD = util_utl.utl_execute_cmd(test_cmd)



    #def CreateAllInterfaces(self, is_dbg_test):
    #    return util_interface.interface_create_all_infs(self.oc_yph, is_dbg_test)

    @util_utl.utl_timeit
    @util_utl.utl_log_outer
    def GetRequestYph(self, path_ar, key_ar):
        # only return subtree of depth 1
        if len(path_ar) < 1:
            return self.oc_yph

        if path_ar[0] not in ocTable:
            oc_yph = StatusCode.INVALID_ARGUMENT
        else:
            oc_yph = self.oc_yph

            # suppose key_ar [0] is interface name e.g. "eth0"
            try:
                ret_val = eval(ocTable[path_ar[0]]["info_f"])(oc_yph, path_ar, key_ar, self.my_args)
                if not ret_val: oc_yph = StatusCode.INTERNAL
            except Exception as e:
                logging.fatal(e, exc_info=True)
                oc_yph = StatusCode.INTERNAL

        return oc_yph

    @util_utl.utl_timeit
    @util_utl.utl_log_outer
    def SetValByPath(self, yp_str, pkey_ar, val):

        try:
            tmp_obj = self.oc_yph.get(yp_str)

            # replace key [xxx=yyy] with [xxx]
            reg_path = re.sub(r'\[([\w-]*)=[^]]*\]', r"[\1]", yp_str)

            ret_val = eval(setPathTable[reg_path])(self.oc_yph, pkey_ar, val.strip('"'), len(tmp_obj) == 0, self.my_args) \
                    if reg_path in setPathTable else False
        except Exception as e:
            logging.fatal(e, exc_info=True)
            ret_val = False

        return ret_val
