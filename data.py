import json
import os
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime
from os import PathLike
from pathlib import Path
from typing import List, Union


def check_output(args: Union[str, list], shell: bool = False):
    stdout = subprocess.check_output(args, shell=shell, stderr=subprocess.STDOUT)
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


class vnStatJson(vnStatData):
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
    def parse_traffic(traffic: dict, mode: str):
        data = []
        for item in traffic[mode]:
            data.append(
                {
                    "dt": vnStatJson.strptime(item),
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
        iflist = check_output([vnstatbin, "--dbiflist"]).split(":", maxsplit=1)[-1].split()
        if not iflist:
            raise NotFound("No interfaces found")

        if ifname not in iflist:
            ifname = iflist[0]  # default interface by name

        vnstatargs = [vnstatbin, "-i", ifname, "--json", "--limit", str(0 if 0 in limits else max(limits))]
        data = json.loads(check_output(vnstatargs))

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
        modes = ["fiveminute", "hour", "day", "month", "year", "top"]
        for mode, lim in zip(modes, limits):
            if mode == "top" and lim > 0:
                traffic[mode] = traffic[mode][:lim]
            else:
                traffic[mode] = traffic[mode][-lim:]

        this.traffic = {m: cls.parse_traffic(traffic, m) for m in modes}
        return this


class SQLite:
    def __init__(self, dbfile: PathLike, **kwargs):
        self.dbfile = dbfile
        self.kwargs = kwargs
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.dbfile, **self.kwargs)
        self.conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.conn.close()

    def queryone(self, *args, **kwargs) -> sqlite3.Row:
        with closing(self.conn.cursor()) as c:
            return c.execute(*args, **kwargs).fetchone()

    def queryall(self, *args, **kwargs) -> List[sqlite3.Row]:
        with closing(self.conn.cursor()) as c:
            return c.execute(*args, **kwargs).fetchall()


class vnStatDB(vnStatData):
    """3.6x faster than vnStatJson"""

    @staticmethod
    def parse_traffic(traffic: dict, mode: str):
        data = []
        for item in traffic[mode]:
            dt = item.pop("datetime", None)
            dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
            data.append({"dt": dt, **item})
        return data

    @classmethod
    def from_db(cls, dbdir: PathLike, ifname: str, limits: List[int]):
        dbfile = Path(dbdir).joinpath("vnstat.db")
        with SQLite(dbfile) as db:
            iflist = [x[0] for x in db.queryall("select name from interface")]
            if not iflist:
                raise NotFound("No interfaces found")

            if ifname not in iflist:
                ifname = iflist[0]

            qstr = "select value from info where name=?"
            dbversion = db.queryone(qstr, ("dbversion",))[0]
            if dbversion != "1":
                raise Unsupported(f"Current dbversion={dbversion} not supported")
            vnstatversion = db.queryone(qstr, ("vnstatversion",))[0]

            interface = dict(db.queryone("select * from interface where name=?", (ifname,)))

        this = cls()
        this.vnstatversion = vnstatversion
        this.iflist = iflist

        this.interface = {
            "name": interface["name"],
            "created": interface["created"],
            "updated": interface["updated"],
            "total": {"rx": interface["rxtotal"], "tx": interface["txtotal"]},
        }

        # traffic with limit
        modes = ["fiveminute", "hour", "day", "month", "year", "top"]
        traffic = {}
        with SQLite(dbfile, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES) as db:
            for tbl, lim in zip(modes, limits):
                if tbl == "top":
                    qstr = "select strftime('%Y-%m-%d %H:%M:%S', date) as datetime,rx,tx from (select * from top where interface=?) order by rx+tx desc"
                    if lim > 0:
                        qstr += f" limit {lim}"
                else:
                    if lim == 0:
                        qstr = f"select strftime('%Y-%m-%d %H:%M:%S', date) as datetime,rx,tx from {tbl} where interface=? order by date asc"
                    else:
                        qstr = f"select strftime('%Y-%m-%d %H:%M:%S', date) as datetime,rx,tx from (select * from {tbl} where interface=? order by date desc limit {lim}) order by date asc"
                traffic[tbl] = [dict(item) for item in db.queryall(qstr, (interface["id"],))]

        this.traffic = {tbl: cls.parse_traffic(traffic, tbl) for tbl in modes}
        return this
