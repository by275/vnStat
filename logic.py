import subprocess

# third-party
from flask import render_template, jsonify

# pylint: disable=import-error
from plugin import PluginModuleBase

# local
from .setup import P
from .data import check_output, vnStatDataJson

plugin = P
logger = plugin.logger
package_name = plugin.package_name
ModelSetting = plugin.ModelSetting
plugin_info = plugin.plugin_info


class LogicMain(PluginModuleBase):
    db_default = {
        "default_interface_id": "",
        "default_traffic_view": "months",
        "traffic_unit": "iec",
        "traffic_list": "24,24,30,12,0,10",
        "vnstat_bin": "vnstat",
        "vnstat_db": "",
    }

    def __init__(self, PM):
        super().__init__(PM, None)

    def plugin_load(self):
        pass

    def process_menu(self, sub, req):
        _ = req
        try:
            arg = ModelSetting.to_dict()
            return render_template(f"{package_name}_{sub}.html", arg=arg)
        except Exception:
            return render_template("sample.html", title=f"{package_name} - {sub}")

    def process_ajax(self, sub, req):
        p = req.form.to_dict() if req.method == "POST" else req.args.to_dict()
        try:
            if sub == "check_vnstat_bin":
                path = p.get("path", "vnstat")
                return {"success": True, "data": check_output(f"{path} -v")}
            if sub == "get_vnstat_data":
                vnstat_bin = ModelSetting.get("vnstat_bin")
                ifname = p.get("ifname", ModelSetting.get("default_interface_id"))
                limits = ModelSetting.get("traffic_list").split(",")
                limits = map(int, map(str.strip, limits))
                data = vnStatDataJson.from_bin(vnstat_bin, ifname, limits).export()
                return {"success": True, "data": data}
            if sub == "save_current_view":
                traffic_view = p.get("traffic_view", "")
                if traffic_view:
                    ModelSetting.set("default_traffic_view", traffic_view)
                interface_id = p.get("interface_id", "")
                if interface_id:
                    ModelSetting.set("default_interface_id", interface_id)
                return jsonify({"success": True})
            raise NotImplementedError(f"Unknown sub for ajax request: {sub}")
        except subprocess.CalledProcessError as e:
            # vnStat 바이너리가 없을때
            logger.exception("Exception while calling vnStat process:")
            return {"success": False, "log": e.output.strip().decode("utf-8")}
        except Exception as e:
            logger.exception("Exception while processing ajax request:")
            return jsonify({"success": False, "log": str(e)})
