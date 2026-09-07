"""迅雷 Docker 容器（cnk3x/xunlei）Web 接口客户端。

接口细节源自 saaak/xunlei-docker-ext 源码（pan-auth 鉴权 + drive/v1 tasks API），
鉴权无账号密码：pan-auth 头（<unix秒>.<md5(秒+SECRET)>，或 ≥3.21.0 从首页 HTML uiauth 提取）。
部署后若迅雷升级导致接口漂移，以 DevTools 抓包比对为准并更新本文件。
"""
import base64
import hashlib
import json
import re
import time

import httpx

# Docker 版固定前缀（fnOS 原生版为 /cgi/ThirdParty/xunlei/index.cgi，端口 5666）
PREFIX = "/webman/3rdparty/pan-xunlei-com/index.cgi"
# 低版本 token 算法 SECRET（ext utils/request.js L152）
SECRET = (
    "yrjmxtpovrzzdqgtbjdncmsywlpmyqcaawbnruddxucykfebpkuseypjegajzzpplmzrejnavcwtvciupgigyrtomd"
    "ljhtmsljegvutunuizvatwtqdjheituaizfjyfzpbcvhhlaxzfatpgongrqadvixrnvastczwnolznfavqrvmjseios"
    "mvrtcqiapmtzjfihdysqmhaijlpsrssovkpqnjbxuwkhjpfxpoldvqrnlhgdbcpnsilsmydxaxrxjzbdekzmshputmg"
    "kedetrcbmcdgljfkpbprvqncixfkavyxoibbuuyqzvcbzdgvipozeplohmcyfornhxzsadavvimivbzexfzhlndddnby"
    "whsvjrotwzarbycpwydvpeqtuigfwzcvoswgpoakuvgdbykdjdcsdlnqskogpbsyceeyaigbgmrbnzixethpvqvvfvdc"
    "vjbilxikvklfbkcnfprzhijjnuoovulvigiqvbosnbixeplvnewmyipxuzpvocbvidnzgsrdfkejghvvyizkjlofndcu"
    "zvlhdhovpeolsyroljurbplpwbbihmdloahicnqehgjnbthmrljtzovltnlpeibodpjvemhhybmanskbtvdrgkrzoyhs"
    "jcexfrcpddoemazkfjwmrbrcloitmdzzkgxwlhnbfpjffrpryljdzdqsbacrjgohzwgbvzgevnqvxppsxqzczfgpuvig"
    "jbuhzweyeinukeurkogpotdegqhtsztdinmijjowivciviunhcjhtufzhjlmpqlngslimksdeezdzxihtmaywfvipjct"
    "uealhlovmzdodruperyysdhwjbtidwdzusifeepywsmkqbknlgdhextvlheufxivphskqvdtbcjfryxlolujmennakdq"
    "jdhtcxwnhknhzlaatuhyofenhdigojyxrluijjxeywnmopsuicglfcqyybbpynpcsnizupumtakwwnjlkfkuooqoqxhj"
    "nryylklokmzvmmgjsbbvgmwoucpvzedmqpkmazwhhvxqygrexopkmcdyniqocguykphlngjesqohhuvnkcliuawkzcmve"
    "vdbouwzvgmhtavwyhstvqwhcwjluzjopnhuisbsrloavcieskcyqftdhieduduhowgvrkimgdhyszsiknmuzvnrqqlby"
    "kbdlixosgxrdunymbixakkmgppteayqmqivxcwawyidpltevotwoxlkrucmluuluatgeskhfsrsebhniwhujpwrpknjx"
    "ylidtjwebvwmbwayoepootybnlcaoixlgvjmpquxnyomoiopsjxtnorhwnlmonllastiezyvfbbgngjybtgbkxuaqdmku"
    "qwupgzhffuyzgdnahdifaqtfmpysnlesvfoiofxvbtqkiqvdniejbyzugbkursumqddaslhqpkdrjnnsdqfthxtghxhay"
    "lgeqnknhqwpammlfnlkjuqevnxesyqsnpufvrbeohphxfabcduuklpkfoiifsqrrbsxkkmdrnkeboprnksfzwmjymjsp"
    "zsrfjlwneuwzjjwejruubhhqaktxhygtjuhjmtvrklrmxdbbwooxsucmynwgcxhzdctgtchaevmpfiqfwydultmgqnion"
    "uendspvdrcctxldnyjlgnsqxaddadxeyvlcifdxksgdhaatsslhcofnxmilljpzdlumfjvcwvjrxegwbwuuwkguydhoz"
    "qqnuselsoojnsefquuhpijdguofwrcjbuaugyzphkenbyhdstsldybdqsfxjhpgnerbdosbtyzdtrhyvwkzkurnmbgjtz"
    "lzcpfsuxussguelnjttmwejhreptwogekfvdsemlkvklcxeuzlboqwbngddexhsmyzqkztvlbgybbfmzbjroajaucyki"
    "qvhjrirlgawaessusvulngosviecmbpfgevxqptalguchfzkrrpruwxspggiqokepqpocezcewhyajsgxrqqqeuhwvc"
)


def _md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


class XunleiClient:
    """迅雷容器客户端。transport 参数供测试注入 httpx.MockTransport。"""

    def __init__(self, base: str, basic_user: str = "", basic_pass: str = "",
                 transport: "httpx.BaseTransport | None" = None):
        self.base = (base or "").rstrip("/")
        self.basic_user = basic_user
        self.basic_pass = basic_pass
        self._transport = transport
        self._uiauth_cache: str | None = None
        self._uiauth_ts: float = 0.0  # A4：uiauth 10 分钟过期，失效自动重取

    # ---------- 鉴权 ----------
    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base, transport=self._transport, timeout=15)

    def _version(self, c: httpx.Client) -> str:
        try:
            r = c.get(f"{PREFIX}/launcher/status")
            return str((r.json() or {}).get("running_version", ""))
        except Exception:
            return ""

    def _pan_auth(self, c: httpx.Client) -> str:
        """pan-auth：≥3.21.0 从首页 HTML uiauth 取；否则低版本 md5 算法。"""
        ver = self._version(c)
        if ver and ver >= "3.21.0":
            if self._uiauth_cache and time.time() - self._uiauth_ts < 600:
                return self._uiauth_cache
            try:
                r = c.get(f"{PREFIX}/")
                m = re.search(r'function uiauth\(value\)\s*{\s*return\s*"([^"]+)"\s*}', r.text)
                if m:
                    self._uiauth_cache = m.group(1)
                    self._uiauth_ts = time.time()
                    return self._uiauth_cache
            except Exception:
                pass
        e = int(time.time())
        return f"{e}.{_md5_hex(str(e) + SECRET)}"

    def _headers(self, c: httpx.Client) -> dict:
        h = {
            "DNT": "1",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
            "device-space": "",
            "content-type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "pan-auth": self._pan_auth(c),
        }
        if self.basic_user:
            h["Authorization"] = "Basic " + base64.b64encode(
                f"{self.basic_user}:{self.basic_pass}".encode()).decode()
        return h

    def _get(self, c: httpx.Client, path: str, **params) -> dict:
        r = c.get(f"{PREFIX}{path}", params=params, headers=self._headers(c))
        return r.json()

    def _post(self, c: httpx.Client, path: str, body: dict) -> dict:
        r = c.post(f"{PREFIX}{path}", json=body, headers=self._headers(c))
        return r.json()

    # ---------- 设备与目录 ----------
    def device_id(self, c: httpx.Client) -> str:
        d = self._get(c, "/drive/v1/tasks", **{"type": "user#runner", "device_space": ""})
        tasks = d.get("tasks") or []
        return tasks[0]["params"]["target"] if tasks else ""

    def root_parent_id(self, c: httpx.Client, device: str) -> str:
        d = self._get(c, "/drive/v1/files", **{
            "space": device, "limit": 200, "parent_id": "",
            "filters": '{"kind":{"eq":"drive#folder"}}', "page_token": "", "device_space": ""})
        files = d.get("files") or []
        return files[0].get("parent_id", "") if files else ""

    # ---------- 任务 ----------
    def add_task(self, url: str, name: str, sub_file_index: str = "0") -> dict:
        """提交下载任务（magnet/http 链接）。成功判定 HttpStatus==0。"""
        try:
            with self._client() as c:
                device = self.device_id(c)
                if not device:
                    return {"ok": False, "message": "未获取到迅雷设备 ID（检查容器登录态）"}
                parent = self.root_parent_id(c, device)
                if not parent:
                    return {"ok": False, "message": "未获取到迅雷根目录（文件接口异常）"}
                body = {
                    "type": "user#download-url",
                    "name": name, "file_name": name, "file_size": "0",
                    "space": device,
                    "params": {
                        "target": device, "url": url, "total_file_count": "1",
                        "parent_folder_id": parent, "sub_file_index": sub_file_index, "file_id": "",
                    },
                }
                res = self._post(c, "/drive/v1/task", body)
                ok = (res or {}).get("HttpStatus") == 0
                if ok:
                    return {"ok": True, "message": "已提交迅雷下载"}
                return {"ok": False, "message": str((res or {}).get("error_description") or res)[:200]}
        except Exception as e:
            return {"ok": False, "message": str(e)[:200]}

    def list_tasks(self, phase: str = "active") -> list[dict]:
        """任务列表。phase: active（进行中）/ complete（已完成）。"""
        filters = (
            '{"phase":{"in":"PHASE_TYPE_PENDING,PHASE_TYPE_RUNNING,PHASE_TYPE_PAUSED,'
            'PHASE_TYPE_ERROR"},"type":{"in":"user#download-url,user#download"}}'
            if phase == "active" else
            '{"phase":{"in":"PHASE_TYPE_COMPLETE"},"type":{"in":"user#download-url,user#download"}}'
        )
        try:
            with self._client() as c:
                device = self.device_id(c)
                if not device:
                    return []
                d = self._get(c, "/drive/v1/tasks", **{
                    "space": device, "page_token": "", "limit": 200,
                    "filters": filters, "device_space": ""})
                return d.get("tasks") or []
        except Exception:
            return []

    def test(self) -> dict:
        """连通性自检：版本 + 设备。"""
        try:
            with self._client() as c:
                ver = self._version(c)
                if not verify_base(self.base):
                    return {"ok": False, "message": "迅雷容器不可达"}
                device = self.device_id(c)
            return {"ok": True, "version": ver or "?", "device": device or "未获取"}
        except Exception as e:
            return {"ok": False, "message": str(e)[:200]}


def verify_base(base: str) -> bool:
    import httpx as _h
    try:
        r = _h.get(base.rstrip("/") + "/webman/status", timeout=5)
        return r.status_code == 200 and "hello xlp" in r.text
    except Exception:
        return False
