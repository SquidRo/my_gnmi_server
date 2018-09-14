#
# util_sys.py
#
# APIs for processing system info.
#

import subprocess
import json
import pdb
import util_utl

GET_NTP_SVR_LST_CMD = util_utl.GET_VAR_LST_CMD_TMPL.format("NTP_SERVER")
GET_CUR_DATE_CMD    = 'date +"%Y-%m-%dT%H:%M:%SZ%:z"'
GET_NTPQ_STAUS_CMD  = 'ntpq -pn'

CFG_NTP_SVR_CMD_TMPL = 'sonic-cfggen -a \'{"NTP_SERVER": {"%s" : %s}}\' --write-to-db'

# ntp server list needed to check existence
OLD_NTP_SVR_LST = []

def fill_ntpq_info(oc_svr, svr, ntpq_output):
    for ldata in ntpq_output:
        # ex: ['*103.18.128.60', '140.112.2.189', '2', 'u', '472', '1024', '377', '23.490', '-0.583', '11.320']
        if svr in ldata[0]:
            oc_svr.state._set_root_delay(int(float(ldata[7])))
            oc_svr.state._set_poll_interval(int(ldata[5]))
            oc_svr.state._set_offset(abs(int((float(ldata[8])))))

def sys_get_info(root_yph, path_ar, key_ar):
    global OLD_NTP_SVR_LST
    new_ntp_svr_lst = []
    #pdb.set_trace()
    (is_ok, ntpq_output) = util_utl.utl_get_execute_cmd_output(GET_NTPQ_STAUS_CMD)
    if is_ok:
        """
        root@switch1:/home/admin# ntpq -pn
             remote           refid      st t when poll reach   delay   offset  jitter
        ==============================================================================
        *103.18.128.60   140.112.2.189    2 u  247 1024  377   22.842   -0.318   2.747
        """
        ntpq_output = ntpq_output.splitlines()[2:]
        ntpq_output = [ oline.split() for oline in ntpq_output if oline ]

    (is_ok, output) = util_utl.utl_get_execute_cmd_output(GET_NTP_SVR_LST_CMD)
    if is_ok:
        oc_sys = root_yph.get("/system")[0]
        ntp_lst = [] if output.strip('\n') =='' else eval(output)
        for svr in ntp_lst:
            if svr not in oc_sys.ntp.servers.server:
                oc_svr = oc_sys.ntp.servers.server.add(svr)
                oc_svr.config.iburst = True
            else:
                oc_svr = oc_sys.ntp.servers.server[svr]

            fill_ntpq_info(oc_svr, svr, ntpq_output)
            new_ntp_svr_lst.append(svr)

    # remove no existing entry
    for svr in OLD_NTP_SVR_LST:
        if svr not in new_ntp_svr_lst:
            oc_sys.ntp.servers.server.delete(svr)
    OLD_NTP_SVR_LST = new_ntp_svr_lst

    (is_ok, output) = util_utl.utl_get_execute_cmd_output(GET_CUR_DATE_CMD)
    if is_ok:
        oc_sys.state._set_current_datetime(output.strip('\n'))

    return True

# ex:    pkey_ar = [u'103.18.128.60']
#   val for del  = '' or '{}'
#   val for add  = '{"address":"103.18.128.60"}'
#
# To add/remove a ntp server
def sys_set_ntp_server(root_yph, pkey_ar, val, is_create):
    #pdb.set_trace()
    try:
        ntp_cfg = {"address":""} if val == "" else eval(val)

        if ntp_cfg["address"] == "":
            cfg_str = "null"
        else:
            cfg_str = "{}"

    except:
        return False

    exec_cmd = CFG_NTP_SVR_CMD_TMPL % (pkey_ar[0], cfg_str)
    ret_val = util_utl.utl_execute_cmd(exec_cmd)

    return ret_val
