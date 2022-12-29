import json
import os
import subprocess
from datetime import datetime
from typing import List, Union


def check_output(command: Union[str, list], shell: bool = True):
    stdout = subprocess.check_output(command, shell=shell, stderr=subprocess.STDOUT)
    return os.linesep.join(stdout.decode(errors="ignore").splitlines())


class vnStatException(Exception):
    """Base class for all vnStat exceptions."""


class Unsupported(vnStatException):
    """Unsupported data version."""


class NotFound(vnStatException):
    """Data not found."""


class vnStatData:
    def __init__(self):
        self.vnstatversion: str = None
        self.iflist: List[str] = None
        self.interface: dict = None  # for selected interface
        self.traffic: dict = None  # for selected interface

    def export_traffic(self) -> dict:
        if self.traffic is None:
            return
        traffic = {}
        for dtype, items in self.traffic.items():
            labels, rxs, txs = [], [], []
            for item in items:
                dt = item["dt"]
                # fiveminute, hour, day, month, year, top
                if dtype == "fiveminute":
                    label = dt.strftime("%H:%M")
                    if label == "00:00":
                        label = dt.strftime("%-d일 ") + label
                elif dtype == "hour":
                    label = dt.strftime("%-H시")
                    if label == "0시":
                        label = dt.strftime("%-d일 ") + label
                elif dtype == "day":
                    label = dt.strftime("%-d일")
                    if label == "1일":
                        label = dt.strftime("%-m월 ") + label
                elif dtype == "month":
                    label = dt.strftime("%-m월")
                    if label == "1월":
                        label = dt.strftime("%y년 ") + label
                elif dtype == "year":
                    label = dt.strftime("%Y년")
                elif dtype == "top":
                    label = dt.strftime("%Y-%m-%d")
                labels.append(label)
                rxs.append(item["rx"])
                txs.append(item["tx"])
            traffic[dtype] = {
                "labels": labels,
                "rxs": rxs,
                "txs": txs,
            }
        return traffic

    def export(self):
        traffic = self.export_traffic()

        # summary
        labels, rxs, txs = [], [], []

        labels.append("오늘")
        try:
            rxs.append(traffic["day"]["rxs"][-1])
            txs.append(traffic["day"]["txs"][-1])
        except IndexError:
            rxs.append(0)
            txs.append(0)

        labels.append("이번달")
        try:
            rxs.append(traffic["month"]["rxs"][-1])
            txs.append(traffic["month"]["txs"][-1])
        except IndexError:
            rxs.append(0)
            txs.append(0)

        labels.append("전체기간")
        rxs.append(self.interface["total"]["rx"])  # pylint: disable=unsubscriptable-object
        txs.append(self.interface["total"]["tx"])  # pylint: disable=unsubscriptable-object

        summary = {
            "labels": labels,
            "rxs": rxs,
            "txs": txs,
        }

        return {
            "vnstatversion": self.vnstatversion,
            "iflist": self.iflist,
            "interface": self.interface,
            "traffic": traffic,
            "summary": summary,
        }


class vnStatDataJson(vnStatData):
    @staticmethod
    def strptime(item: dict) -> datetime:
        date = item.get("date", {})
        year = date.get("year", 1971)
        month = date.get("month", 1)
        day = date.get("day", 1)
        time = item.get("time", {})
        hour = time.get("hour", 0)
        minute = time.get("minute", 0)
        return datetime(year, month, day, hour, minute)

    @staticmethod
    def parse_traffic(traffic: dict, datatype: str):
        data = []
        for item in traffic[datatype]:
            data.append(
                {
                    "dt": vnStatDataJson.strptime(item),
                    "rx": item["rx"],
                    "tx": item["tx"],
                }
            )
        return data

    @classmethod
    def from_bin(cls, vnstatbin: str, ifname: str, limits: List[int]):
        """data structure: json v2
        vnstatversion: str
        jsonversion: "2"
        interfaces[].name: str
        interfaces[].alias: str
        interfaces[].created: {date: {year: , month: , day:}}
        interfaces[].updated: {date: {year: , month: , day: }, {time: {hour: , minute: }}}
        interfaces[].traffic.total: {rx: , tx: }
        interfaces[].traffic.fiveminute[]: {id: , rx: , tx: , date: {year: , month: , day: }, {time: {hour: , minute: }}}
        interfaces[].traffic.hour[]: SAME_AS_FIVEMINUTE
        interfaces[].traffic.day[]: {id: , rx: , tx: , date: {year: , month: , day: }}
        interfaces[].traffic.month[]: {id: , rx: , tx: , date: {year: , month: }}
        interfaces[].traffic.year[]: {id: , rx: , tx: , date: {year: }}
        interfaces[].traffic.top[]: SAME_AS_DAY
        """
        iflist = check_output(f"{vnstatbin} --dbiflist").split(":", maxsplit=1)[-1].split()
        if not iflist:
            raise NotFound("No interfaces found")

        if ifname not in iflist:
            ifname = iflist[0]  # default interface by name

        data = json.loads(check_output(f"{vnstatbin} -i {ifname} --json --limit {max(limits)}"))

        jsonversion = data.get("jsonversion")
        if jsonversion != "2":
            raise Unsupported(f"Current jsonversion={jsonversion} is not supported")

        this = cls()
        this.vnstatversion = data["vnstatversion"]
        this.iflist = iflist

        interface = data["interfaces"][0]
        traffic = interface["traffic"]

        this.interface = {
            "name": interface["name"],
            "created": cls.strptime(interface["created"]).strftime("%Y-%m-%d"),
            "updated": cls.strptime(interface["updated"]).strftime("%Y-%m-%d %H:%M"),
            "total": traffic["total"],
        }

        # limit
        views = ["fiveminute", "hour", "day", "month", "year", "top"]
        for key, val in zip(views, limits):
            traffic[key] = traffic[key][-val:]

        this.traffic = {
            "fiveminute": cls.parse_traffic(traffic, "fiveminute"),
            "hour": cls.parse_traffic(traffic, "hour"),
            "day": cls.parse_traffic(traffic, "day"),
            "month": cls.parse_traffic(traffic, "month"),
            "year": cls.parse_traffic(traffic, "year"),
            "top": cls.parse_traffic(traffic, "top"),
        }
        return this
